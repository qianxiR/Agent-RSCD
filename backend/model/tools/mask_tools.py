# -*- coding: utf-8 -*-
"""
掩膜分析工具集 (Model 层 / 工具箱)
- analyze_connected_components: 掩膜连通域分析 + 几何特征提取 (可选导出带坐标系的 GeoJSON)

★ 重工具白名单 (HEAVY_TOOL_CATEGORIES 含 "analysis"), 调用会触发 ai_task 持久化.
★ 依赖策略: 全部走宿主机 (sam3 环境), 缺失时返回 error.
  - numpy/cv2: 连通域分析核心 (samseg 已依赖, 通常在位)
  - rasterio/geopandas/shapely: 仅在 export_geojson=True 且提供 source_image_path 时需要
    (用于把像素几何转成带 CRS 的地理几何, 复用 data.vector.polygonize_mask)

依赖方向: model.tools.mask_tools → data.mask (连通域) / data.vector (矢量化+CRS)
"""
import re
import time
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from langchain_core.tools import tool
from backend.model.tools.tool_registry import register_tool
from backend.model.tools._verification import verify_by_path
from backend.model.tools._paths import session_subpath, short_conv, rand_suffix, build_download_url
from backend.model.tools._result import build_success, build_error, build_frontend_action
from backend.model.tools._shapefile import ascii_stem
from backend.config import settings

logger = logging.getLogger(__name__)


# ==================== 运行时目录辅助 (复用 report_tools 模式) ====================

def _get_mask_output_dir() -> Path:
    """★ v2.4: 掩膜分析产物输出目录: agent-files/analysis/{proj}/{conv}/"""
    d = Path(settings.file_storage_root).resolve() / "analysis" / session_subpath()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_download_url(file_path: Path) -> str:
    """把 agent-files 下的文件转成 /api/v1/download 可访问 URL.
    ★ C4 归一: 复用 _paths.build_download_url 统一实现 (消除重复的 relative_to 逻辑).
    """
    from backend.model.tools._paths import build_download_url as _unified
    return _unified(file_path, Path(settings.file_storage_root))


# ==================== 掩膜读取 ====================

def _load_mask_as_index(mask_path: str):
    """
    读取掩膜文件为 (H, W) 类别索引图 (int32).
    支持 .tif/.tiff (rasterio 单波段) 与 .png/.jpg/.bmp (PIL 灰度).
    彩色 PNG 取灰度; 多波段 TIF 取第一波段.
    """
    import numpy as np
    ext = Path(mask_path).suffix.lower()
    try:
        if ext in (".tif", ".tiff", ".geotiff"):
            import rasterio
            with rasterio.open(mask_path) as src:
                arr = src.read(1)
        else:
            from PIL import Image
            img = Image.open(mask_path)
            if img.mode != "L":
                img = img.convert("L")
            arr = np.asarray(img)
        return arr
    except Exception as e:
        logger.warning(f"[MaskAnalyze] 掩膜读取失败 {mask_path}: {e}")
        return None


# ==================== 工具: 连通域分析 ====================

@register_tool("analysis")
@tool
def analyze_connected_components(
    mask_path: str,
    min_area: int = 10,
    source_image_path: str = "",
    export_geojson: bool = False,
    class_names: str = "",
) -> Dict[str, Any]:
    """
    对分割/变化检测掩膜做连通域分析, 输出每个独立区域的几何特征
    (面积/OBB 旋转框/多边形顶点/空间方位), 可选导出带坐标系的 GeoJSON.

    ★ 用途: 把像素级 mask 转成"一个个对象", 统计数量/面积/方位.
       典型场景: 变化检测后想知道"有几块独立的变化区域""每块多大/在图的什么位置".
    ★ 输入: 分割/变化检测产出的 mask 栅格文件 (.tif/.png/.jpg)
       —— 单类别二值 mask, 或像素值=类别 ID 的多类 mask (会逐类做连通域).
    ★ 坐标系处理 (重要):
       - mask PNG 本身无坐标系; 不传 source_image_path 时结果用像素坐标 (面积=像素数).
       - 传 source_image_path (源影像 .tif) 时, 从中读取 transform/CRS,
         导出的 GeoJSON 会带真实地理坐标 + CRS, 面积按 UTM 投影算成平方米。
       推荐配合 detect_change / segment_image 的原始输入影像一起调用.

    入参:
        - mask_path (str): 掩膜文件绝对路径 (分割/变化检测产物)
        - min_area (int): 最小连通域面积过滤 (像素数), 低于此值丢弃, 默认 10
        - source_image_path (str): 源影像路径 (用于读坐标系/transform, 让导出矢量带地理参考),
                                   不传则结果用像素坐标. 可选.
        - export_geojson (bool): 是否导出 GeoJSON (带坐标系), 默认 False
        - class_names (str): 类别名称映射, 逗号分隔, 索引=类别ID.
                             如 "背景,建筑,道路". 留空用 class_<id>.

    出参: {type, summary, data:{total, has_crs, components:[...], geojson_path?, geojson_url?, verification?}}

    ★ 成功契约 (全部满足才算完成, 否则视为失败):
      1. type == "success" 且 data.total > 0 (至少识别到 1 个连通域)
      2. export_geojson=True 时: data.verification.ok == True (GeoJSON 可被 geopandas 读回)
      3. 无源影像时 has_crs=False 是正常的 (不视为失败), 但需在回复中说明用像素坐标
      任一不满足 → 必须按失败处理, 不可向用户汇报"分析完成"
    示例:
        analyze_connected_components("E:/.../change_mask.png")
        analyze_connected_components("E:/.../change_mask.png", min_area=50,
                                     source_image_path="E:/.../t1.tif",
                                     export_geojson=True)
    """
    # ---- 依赖检查 ----
    from backend.data.mask import region_analysis_available
    if not region_analysis_available():
        return {"type": "error", "msg": "numpy/cv2 未安装, 连通域分析不可用"}

    # ---- 入参校验 ----
    if not mask_path or not Path(mask_path).is_file():
        return {"type": "error", "msg": f"掩膜文件不存在: {mask_path}"}

    # ---- 读掩膜 ----
    import numpy as np
    mask_arr = _load_mask_as_index(mask_path)
    if mask_arr is None:
        return {"type": "error", "msg": f"掩膜读取失败: {mask_path}"}
    if mask_arr.ndim > 2:
        mask_arr = mask_arr[:, :, 0]
    img_h, img_w = mask_arr.shape[:2]

    # ---- 类别名映射 ----
    cls_list = [c.strip() for c in class_names.split(",") if c.strip()] if class_names else []

    # ---- 逐类别连通域分析 ----
    from backend.data.mask import extract_connected_components, compute_spatial_location
    unique_ids = [int(c) for c in np.unique(mask_arr) if int(c) != 0]

    all_components: List[Dict[str, Any]] = []
    label_seq = 0
    for cid in unique_ids:
        binary_cid = ((mask_arr == cid).astype(np.uint8)) * 255
        comps = extract_connected_components(binary_cid, min_area=min_area)
        cls_name = cls_list[cid] if cls_list and cid < len(cls_list) else f"class_{cid}"
        for c in comps:
            label_seq += 1
            (rcx, rcy), (rw, rh), rangle = c["rotated_rect"]
            all_components.append({
                "label_id": label_seq,
                "class_id": cid,
                "class_name": cls_name,
                "area_px": c["area"],
                "bbox_xywh": list(c["crop_bbox"]),                 # 像素 (x, y, w, h)
                "spatial_location": compute_spatial_location(c["crop_bbox"], img_w, img_h),
                "obb_center_px": [round(float(rcx), 2), round(float(rcy), 2)],
                "obb_size_px": [round(float(rw), 2), round(float(rh), 2)],
                "obb_angle_deg": round(float(rangle), 2),
                "obb_norm": list(c["obb_norm"]),                   # 归一化 (cx,cy,w,h,angle)
                "polygon_px": c["approx_poly"],                    # 像素多边形顶点
                "num_vertices": c["num_approx_points"],
            })

    if not all_components:
        return {
            "type": "success",
            "summary": f"连通域分析完成: 0 个有效区域 (min_area={min_area} 像素, 全被过滤)",
            "data": {"total": 0, "has_crs": False, "components": [], "image_size": [img_w, img_h]},
        }

    # ---- 可选: 导出带坐标系的 GeoJSON ----
    geojson_path: Optional[str] = None
    geojson_url: Optional[str] = None
    geojson_verification: Optional[Dict[str, Any]] = None
    has_crs = False
    if export_geojson:
        try:
            from backend.data.vector import polygonize_mask
            # 读源影像 transform/crs (无源影像时 transform=None, 走像素坐标)
            transform, crs = None, None
            if source_image_path and Path(source_image_path).is_file():
                try:
                    import rasterio
                    with rasterio.open(source_image_path) as src:
                        transform = src.transform
                        crs = src.crs
                        src_w, src_h = src.width, src.height
                except Exception as e:
                    logger.warning(f"[MaskAnalyze] 读源影像地理信息失败: {e}")

                # mask 与源影像尺寸不一致 (resize 过) → 重算 transform 适配 mask 尺寸
                if transform is not None and mask_arr.shape[:2] != (src_h, src_w):
                    from affine import Affine
                    px_w = (transform.a * src_w) / mask_arr.shape[1]
                    px_h = (-transform.e * src_h) / mask_arr.shape[0]
                    transform = Affine(px_w, 0, transform.c, 0, -px_h, transform.f)

            gdf = polygonize_mask(
                mask_arr, transform, crs,
                class_names=cls_list or None,
                min_area_m2=0.0,           # 像素级 min_area 已在上面过滤过, 这里不再按地理面积过滤
                simplify_tolerance=0.0,
            )
            has_crs = crs is not None

            if gdf is not None and len(gdf) > 0:
                out_dir = _get_mask_output_dir()
                # ★ v2.4 命名精简: {conv短}_components_{随机4}.geojson (去 stem 和时间戳)
                rs = rand_suffix(4)
                safe_stem = (short_conv() or "anon") + "_components"
                out_path = out_dir / f"{safe_stem}_{rs}.geojson"
                gdf.to_file(str(out_path), driver="GeoJSON", encoding="utf-8")
                # ★ 成功契约校验: GeoJSON 必须可被 geopandas 读回且要素数 > 0
                geojson_verification = verify_by_path(out_path)
                if geojson_verification["ok"]:
                    geojson_path = str(out_path)
                    geojson_url = _build_download_url(out_path) + f"?t={rs}"
                else:
                    logger.warning(f"[MaskAnalyze] GeoJSON 校验失败: {geojson_verification.get('reason')}")
            else:
                logger.info("[MaskAnalyze] polygonize_mask 无有效要素, 跳过 GeoJSON 导出")
        except Exception as e:
            logger.warning(f"[MaskAnalyze] GeoJSON 导出失败 (不影响连通域分析结果): {e}")

    # ---- 汇总 ----
    total_area_px = sum(c["area_px"] for c in all_components)
    # 按类别聚合
    per_class: Dict[str, Dict[str, Any]] = {}
    for c in all_components:
        key = c["class_name"]
        if key not in per_class:
            per_class[key] = {"name": key, "count": 0, "area_px": 0}
        per_class[key]["count"] += 1
        per_class[key]["area_px"] += c["area_px"]
    per_class_list = sorted(per_class.values(), key=lambda x: -x["area_px"])

    summary = (
        f"连通域分析完成: 共 {len(all_components)} 个独立区域"
        f" (图像 {img_w}×{img_h}, min_area={min_area}px)\n"
        f"坐标参考: {'有 (' + str(crs) + ')' if has_crs else '无 (像素坐标)'}\n"
        f"按类别:\n"
    )
    for pc in per_class_list[:8]:
        summary += f"  - {pc['name']}: {pc['count']} 个区域, {pc['area_px']} 像素\n"
    if geojson_path:
        summary += f"已导出 GeoJSON: {Path(geojson_path).name} (带坐标系, 可用 GIS 打开)"

    result = {
        "type": "success",
        "summary": summary,
        "data": {
            "total": len(all_components),
            "has_crs": has_crs,
            "crs": str(crs) if has_crs else None,
            "image_size": [img_w, img_h],
            "min_area_px": min_area,
            "per_class": per_class_list,
            "components": all_components,
        },
    }
    if geojson_path:
        result["data"]["geojson_path"] = geojson_path
        result["data"]["geojson_url"] = geojson_url
        result["verification"] = geojson_verification
    elif export_geojson:
        # 用户要求导出但未成功 → 在 verification 里说明, 让 AI 据此判断
        result["verification"] = geojson_verification or {
            "ok": False, "reason": "GeoJSON 导出被跳过 (可能 polygonize_mask 无有效要素或依赖缺失)",
            "evidence": "连通域分析本身成功, 但矢量导出未完成",
        }
    return result


# ==================== 掩膜 → Shapefile 导出 (★ 新增) ====================
# ★ _ascii_safe 已统一为 backend.model.tools._shapefile.ascii_stem (复用 geoio 公共函数)


@register_tool("analysis")
@tool
def export_mask_to_shapefile(
    mask_path: str,
    source_image_path: str = "",
    output_dir: str = "",
    min_area_m2: float = 0.0,
    class_names: str = "",
    simplify_tolerance: float = 0.0,
) -> Dict[str, Any]:
    """
    把分割/变化检测掩膜矢量化并导出为 ESRI Shapefile (ZIP 打包, 含 .shp/.dbf/.shx/.prj),
    供下游 GIS 系统 (ArcGIS / QGIS) 直接使用。

    ★ 用途: analyze_connected_components 只出 GeoJSON, 而 GIS 用户更常用 Shapefile。
      本工具把"实际分割结果"落盘成 .shp (带坐标系), 是 analyze_connected_components
      的 Shapefile 互补工具。

    ★ 坐标系处理 (与 analyze_connected_components 一致):
      - mask PNG 本身无坐标系; 不传 source_image_path 时要素用像素坐标 (CRS 缺失)。
      - 传 source_image_path (源影像 .tif) 时, 从中读 transform/CRS,
        导出的 Shapefile 会带真实地理坐标 + .prj, 面积按 UTM 投影算成平方米。
      推荐: mask 是某张源影像的分割结果时, 把那张源影像传进来。

    入参:
        - mask_path (str): 掩膜文件绝对路径 (分割/变化检测产物, .tif/.png/.jpg)
        - source_image_path (str): 源影像路径 (读坐标系/transform, 让 .shp 带地理参考), 可选
        - output_dir (str): 自定义输出目录, 留空则用工作区目录 agent-files/analysis/{proj}/{conv}/
        - min_area_m2 (float): 最小面积过滤 (平方米), 低于此值的图斑丢弃, 默认 0=不过滤
        - class_names (str): 类别名称映射, 逗号分隔, 索引=类别ID. 如 "背景,建筑,道路"
        - simplify_tolerance (float): Douglas-Peucker 简化容差 (地理单位), 默认 0=不简化

    出参: {type, summary, data:{shp_path, zip_path, zip_url?, feature_count, has_crs, crs, verification}}

    ★ 成功契约 (全部满足才算完成, 否则视为失败):
      1. type == "success" 且 data.feature_count > 0 (至少导出 1 个图斑)
      2. ZIP 内必须含 .shp/.dbf/.shx 三个核心文件 (verify_zip_integrity 校验)
      3. 传了 source_image_path 时 data.has_crs == True (.prj 真实落地)
      任一不满足 → 必须按失败处理, 不可向用户汇报"已导出"

    示例:
        export_mask_to_shapefile("E:/.../建筑_seg.png",
                                 source_image_path="E:/.../r000_c006_t1.tif")
        export_mask_to_shapefile("E:/.../mask.tif", min_area_m2=20,
                                 class_names="背景,建筑,道路")
    """
    # ---- 依赖检查 ----
    from backend.data.vector import vectorize_available
    if not vectorize_available():
        return build_error(msg="rasterio/geopandas/shapely 未安装, Shapefile 导出不可用")

    # ---- 入参校验 ----
    if not mask_path or not Path(mask_path).is_file():
        return build_error(msg=f"掩膜文件不存在: {mask_path}")

    # ---- 读掩膜 ----
    import numpy as np
    mask_arr = _load_mask_as_index(mask_path)
    if mask_arr is None:
        return build_error(msg=f"掩膜读取失败: {mask_path}")
    if mask_arr.ndim > 2:
        mask_arr = mask_arr[:, :, 0]

    # ---- 读源影像 transform/crs (无源影像时 transform=None, 走像素坐标) ----
    transform, crs = None, None
    if source_image_path and Path(source_image_path).is_file():
        try:
            import rasterio
            with rasterio.open(source_image_path) as src:
                transform, crs = src.transform, src.crs
                src_w, src_h = src.width, src.height
        except Exception as e:
            logger.warning(f"[MaskShp] 读源影像地理信息失败: {e}")

        # mask 与源影像尺寸不一致 (resize 过) → 重算 transform 适配 mask 尺寸
        if transform is not None and mask_arr.shape[:2] != (src_h, src_w):
            from affine import Affine
            px_w = (transform.a * src_w) / mask_arr.shape[1]
            px_h = (-transform.e * src_h) / mask_arr.shape[0]
            transform = Affine(px_w, 0, transform.c, 0, -px_h, transform.f)
    has_crs = crs is not None

    # ---- 矢量化 ----
    cls_list = [c.strip() for c in class_names.split(",") if c.strip()] if class_names else []
    try:
        from backend.data.vector import polygonize_mask
        gdf = polygonize_mask(
            mask_arr, transform, crs,
            class_names=cls_list or None,
            min_area_m2=min_area_m2,
            simplify_tolerance=simplify_tolerance,
        )
    except Exception as e:
        return build_error(msg=f"矢量化失败: {e}")

    if gdf is None or len(gdf) == 0:
        return build_error(
            msg="掩膜中无有效图斑 (可能全为背景/或都被 min_area_m2 过滤)",
            verification={"ok": False, "reason": "矢量化无有效要素",
                          "evidence": "polygonize_mask 返回空"},
        )

    # ---- 写 Shapefile 多文件 → ZIP 打包 ----
    import zipfile
    import shutil
    if output_dir:
        out_dir = Path(output_dir).expanduser().resolve()
    else:
        out_dir = _get_mask_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    conv8 = short_conv() or "anon"
    rs = rand_suffix(4)
    # ★ Fiona ESRI Shapefile 驱动对中文 stem 会静默写空文件, 必须用 ASCII stem
    safe_stem = ascii_stem(conv8, fallback="anon") + "_shp"

    shp_dir = out_dir / f"{safe_stem}_{rs}_shp"
    shp_dir.mkdir(parents=True, exist_ok=True)
    shp_base = shp_dir / f"{safe_stem}.shp"

    try:
        gdf.to_file(str(shp_base), driver="ESRI Shapefile", encoding="utf-8")
    except Exception as e:
        shutil.rmtree(str(shp_dir), ignore_errors=True)
        return build_error(msg=f"Shapefile 写入失败: {e}")

    # ★ 写出后立即校验 .shp 文件真实落地 (Fiona 静默失败时不会生成)
    if not shp_base.is_file() or shp_base.stat().st_size == 0:
        shutil.rmtree(str(shp_dir), ignore_errors=True)
        return build_error(
            msg=(f"Shapefile 写入失败: .shp 文件未生成或为空 "
                 f"(可能因字段名超长/几何无效/编码问题, stem={safe_stem})"),
            verification={"ok": False, "reason": ".shp 文件未落地",
                          "evidence": "Fiona 静默失败, ZIP 打包前已拦截"},
        )

    # 检查 .prj 是否落地 (有 CRS 时应有 .prj)
    prj_path = shp_dir / f"{safe_stem}.prj"
    prj_ok = prj_path.is_file() and prj_path.stat().st_size > 0
    if has_crs and not prj_ok:
        logger.warning(f"[MaskShp] 有 CRS 但 .prj 未落地 (CRS={crs})")

    # ZIP 打包 (含 .shp/.shx/.dbf/.prj/.cpg 全部组件)
    zip_path = out_dir / f"{safe_stem}_{rs}.zip"
    try:
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for f in shp_dir.iterdir():
                if f.is_file():
                    zf.write(str(f), f.name)
    except Exception as e:
        return build_error(msg=f"ZIP 打包失败: {e}")
    # ★ 保留解压后的 .shp 目录 (供 upload_shapefile_layer / GIS 直接打开, 不清理)

    # ---- 成功契约校验: ZIP 完整性 (.shp/.dbf/.shx) ----
    verification = verify_by_path(zip_path)
    if not verification["ok"]:
        return build_error(
            msg=f"Shapefile 导出失败: {verification.get('reason', '校验未通过')}",
            verification=verification,
            data={"zip_path": str(zip_path), "feature_count": len(gdf), "has_crs": has_crs},
        )

    # ---- 构造返回 ----
    # 按类别聚合统计
    per_class: Dict[str, Dict[str, Any]] = {}
    for _, row in gdf.iterrows():
        key = str(row.get("class_name", "unknown"))
        if key not in per_class:
            per_class[key] = {"name": key, "count": 0, "area_m2": 0.0}
        per_class[key]["count"] += 1
        per_class[key]["area_m2"] += float(row.get("area_m2", 0.0))
    per_class_list = sorted(per_class.values(), key=lambda x: -x["area_m2"])

    summary = (
        f"已把掩膜矢量化并导出为 Shapefile: {zip_path.name}\n"
        f"- 图斑数: {len(gdf)}, 坐标系: {'有 (' + str(crs) + ')' if has_crs else '无 (像素坐标)'}\n"
        f"- ZIP 内含 .shp/.shx/.dbf{'+ .prj' if prj_ok else ''} (校验通过, GIS 可直接打开)\n"
        f"- 解压后的 .shp 在: {shp_dir.name}/\n"
        f"按类别:\n"
    )
    for pc in per_class_list[:8]:
        summary += f"  - {pc['name']}: {pc['count']} 个图斑, {pc['area_m2']:.1f} m²\n"

    data = {
        "shp_path": str(shp_base),       # 解压后的 .shp (可直接用于 GIS/upload_shapefile_layer)
        "zip_path": str(zip_path),       # ZIP 打包件
        "feature_count": len(gdf),
        "has_crs": has_crs,
        "crs": str(crs) if has_crs else None,
        "prj_ok": prj_ok,
        "per_class": per_class_list,
        "shp_components": sorted(f.name for f in shp_dir.iterdir() if f.is_file()),
    }

    # 工作区目录下还给出前端可下载 URL
    if not output_dir:
        from backend.model.tools._paths import build_download_url
        storage_root = Path(settings.file_storage_root).resolve()
        data["zip_url"] = build_download_url(zip_path, storage_root) + f"?t={rs}"

    return build_success(summary=summary, data=data, verification=verification)


# ==================== 边缘算子叠加 (★ v2.5 新增) ====================

@register_tool("analysis")
@tool
def overlay_edge_on_image(
    source_image_path: str,
    mask_path: str,
    method: str = "distance",
    edge_color: str = "255,0,0",
    edge_thickness: int = 2,
    mask_alpha: float = 0.0,
    export_geotiff: bool = True,
) -> Dict[str, Any]:
    """
    用边缘算子从掩码中提取类别边界, 叠加到原图并可视化, 同时保留坐标系导出 GeoTIFF。

    ★ 用途: 让用户直观看到"每个分割图斑/连通域的精确边界在哪里"。
      输入掩码 → 边缘算子提取边界 → 边界线叠加到原图 → 渲染到前端卡片墙。
      另外把"叠加后的结果"按源影像的坐标系/transform 写出 GeoTIFF (.tif),
      便于在 GIS 里与原图叠放 (PNG 不带地理参考, GeoTIFF 才保留)。

    支持的边缘算子 (移植自 E:/1代码/系统/Tools/DataProcessing/mask_postprocess/edge_utils.py):
      - "distance" (推荐): 欧几里得距离变换, 最精确
      - "canny": Canny 边缘检测
      - "sobel": Sobel 梯度

    入参:
        - source_image_path (str): 原图绝对路径 (segment_image 的输入图, 或 data.input_image_path)
          ★ 同时作为坐标系来源: 从中读 transform/CRS 写入 GeoTIFF。推荐传 GeoTIFF 原图。
        - mask_path (str): 掩码绝对路径。支持:
            * mask.tif (segment_image 产出的 GeoTIFF 掩码, 推荐用这个, 含单类或多类)
            * 彩色 PNG (segment_image 的 color_mask, 按颜色分离各类别边缘)
            * 连通域结果 (analyze_connected_components 的 mask)
        - method (str): 边缘算子, "distance" (推荐) / "canny" / "sobel", 默认 distance
        - edge_color (str): 边缘线 RGB 色, 逗号分隔, 默认 "255,0,0" (红色)
        - edge_thickness (int): 边缘线粗细 (膨胀核), 默认 2
        - mask_alpha (float): 0=只画边缘线; >0 (如 0.3)=额外半透明填充掩膜区域, 默认 0
        - export_geotiff (bool): 是否同时写出保留坐标系的 GeoTIFF (默认 True)。
          关闭则只出 PNG (适合纯可视化, 不需要 GIS 叠放时)。

    出参: {type:"frontend_action", instruction:{type:"render_image", params:{image_url, caption}},
           data:{artifact_path(PNG), geotiff_path?, ...}}
          失败: {type:"error", msg}

    ★ 成功契约: PNG 叠加图真实生成 (verify_by_path 校验); export_geotiff=True 时
      GeoTIFF 也真实落地且 CRS 与源影像一致。

    典型用法 (工作流):
      segment_image → overlay_edge_on_image (用 mask.tif 提取边缘叠加到原图)
      analyze_connected_components → overlay_edge_on_image (连通域边界叠加)
    """
    try:
        import numpy as np
        from PIL import Image
        from backend.model.SamSeg.edge_utils import extract_edge, overlay_edge_on_image as _overlay
    except ImportError as e:
        return build_error(msg=f"依赖缺失 (numpy/cv2/scipy/PIL): {e}")

    src = Path(source_image_path)
    mask_p = Path(mask_path)
    if not src.is_file():
        return build_error(msg=f"原图不存在: {source_image_path}")
    if not mask_p.is_file():
        return build_error(msg=f"掩码不存在: {mask_path}")

    # 解析边缘颜色
    try:
        color = tuple(int(x) for x in edge_color.split(","))
        if len(color) != 3:
            raise ValueError
    except (ValueError, AttributeError):
        color = (255, 0, 0)

    # 加载原图
    try:
        orig_img = Image.open(src).convert("RGB")
        orig_arr = np.array(orig_img)
    except Exception as e:
        return build_error(msg=f"原图读取失败: {e}")

    # 加载掩码并提取边缘
    try:
        mask_img = Image.open(mask_p)
        # 判断掩码类型: 彩色 PNG (3通道) vs 单通道 (tif/灰度)
        if mask_img.mode == "RGB":
            # 彩色掩码: 用灰度化提取所有类别的边缘 (非黑区域=前景)
            mask_gray = np.array(mask_img.convert("L"))
            binary = (mask_gray > 10).astype(np.uint8) * 255
        else:
            # 单通道掩码
            mask_arr = np.array(mask_img.convert("L"))
            binary = (mask_arr > 10).astype(np.uint8) * 255

        if binary.sum() == 0:
            return build_error(msg="掩码全黑, 无有效前景区域可提取边缘")

        # 边缘提取
        edge = extract_edge(binary, method=method)

        # 叠加到原图
        result_arr = _overlay(orig_arr, edge, color=color, thickness=edge_thickness)

        # 额外半透明填充 (可选)
        if mask_alpha > 0:
            mask_bool = binary > 0
            blend = orig_arr.astype(np.float32) * (1 - mask_alpha) + \
                    np.array(color, dtype=np.float32) * mask_alpha
            result_arr = result_arr.astype(np.float32)
            result_arr[mask_bool] = blend[mask_bool]
            result_arr = np.clip(result_arr, 0, 255).astype(np.uint8)

    except Exception as e:
        return build_error(msg=f"边缘提取/叠加失败: {e}")

    # 保存 PNG 叠加图
    out_dir = _get_mask_output_dir()
    conv8 = (short_conv() or "anon")
    rs = rand_suffix(4)
    out_file = f"{conv8}_edge_{rs}.png"
    out_path = out_dir / out_file
    try:
        Image.fromarray(result_arr).save(str(out_path))
    except Exception as e:
        return build_error(msg=f"边缘叠加图保存失败: {e}")

    # 校验 PNG (前端预览图)
    png_verification = verify_by_path(out_path)

    # ---- 主产物: 写出保留坐标系的 GeoTIFF (与 PNG 同内容, 带 transform/CRS) ----
    #   ★ GeoTIFF 是磁盘最终产物 (供 GIS 叠放); PNG 仅作浏览器预览 (浏览器无法渲染 TIF)。
    geotiff_path: Optional[str] = None
    geotiff_crs: Optional[str] = None
    geotiff_verification: Dict[str, Any] = {}
    if export_geotiff:
        try:
            import rasterio
            from rasterio.transform import Affine
            # 从源影像读 transform/CRS
            transform, crs = None, None
            try:
                with rasterio.open(src) as s:
                    transform, crs = s.transform, s.crs
                    src_w, src_h = s.width, s.height
            except Exception as e:
                logger.warning(f"[MaskEdge] 读源影像地理信息失败, GeoTIFF 不带坐标系: {e}")

            arr = result_arr
            # mask 与源影像尺寸不一致 (resize 过) → 重算 transform 适配结果尺寸
            if transform is not None and arr.shape[:2] != (src_h, src_w):
                px_w = (transform.a * src_w) / arr.shape[1]
                px_h = (-transform.e * src_h) / arr.shape[0]
                transform = Affine(px_w, 0, transform.c, 0, -px_h, transform.f)

            tif_file = f"{conv8}_edge_{rs}.tif"
            tif_path = out_dir / tif_file
            count = arr.shape[2] if arr.ndim == 3 else 1
            with rasterio.open(
                str(tif_path), "w",
                driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                count=count, dtype="uint8",
                crs=crs, transform=transform,
            ) as dst:
                if count == 3:
                    for b in range(3):
                        dst.write(arr[:, :, b], b + 1)
                else:
                    dst.write(arr, 1)
            if tif_path.is_file():
                geotiff_path = str(tif_path)
                geotiff_crs = str(crs) if crs is not None else None
                geotiff_verification = verify_by_path(tif_path)
            else:
                logger.warning("[MaskEdge] GeoTIFF 未落地")
        except Exception as e:
            logger.warning(f"[MaskEdge] GeoTIFF 导出失败: {e}")

    # 构造 URL + 渲染指令
    #   ★ image_url 指向 PNG 预览 (浏览器原生可渲染); 主产物 TIF 路径在 data 里
    from backend.model.tools._paths import build_download_url
    storage_root = Path(settings.file_storage_root).resolve()
    image_url = build_download_url(out_path, storage_root) + f"?t={rs}"

    # 成功契约: TIF 是主产物 → 以 TIF 校验结果为准; 无 TIF 时回退 PNG 校验
    verification = geotiff_verification if geotiff_path else png_verification

    summary = (
        f"已用 {method} 边缘算子从掩码提取边界, 叠加到原图 {src.name}。\n"
        f"- 边缘线颜色: RGB{color}, 粗细 {edge_thickness}\n"
    )
    if geotiff_path:
        summary += (
            f"- 主产物 GeoTIFF (保留坐标系): {Path(geotiff_path).name} "
            f"(CRS={geotiff_crs}, {geotiff_verification.get('size_bytes', 0)} bytes)\n"
            f"- 预览图 PNG (浏览器渲染): {out_file} ({png_verification.get('size_bytes', 0)} bytes)\n"
            f"- 用户可清晰看到每个图斑的精确边界, GIS 可直接打开 TIF 叠放"
        )
    else:
        summary += (
            f"- 产物 PNG: {out_file} ({png_verification.get('size_bytes', 0)} bytes)\n"
            f"- (源影像无 CRS, 未导出带坐标系的 GeoTIFF)"
        )

    data: Dict[str, Any] = {
        "geotiff_path": geotiff_path,           # ★ 主产物 (None 时未导出)
        "geotiff_crs": geotiff_crs,
        "preview_png_path": str(out_path),       # ★ 浏览器预览图
        "edge_method": method,
        "edge_color": list(color),
        "input_image": str(src),
        "input_mask": str(mask_p),
    }

    from backend.model.tools._result import build_frontend_action
    return build_frontend_action(
        action="render_image",
        params={
            "image_url": image_url,              # ★ PNG 预览 (浏览器可渲染)
            "caption": f"边缘叠加图 ({method}, 线色 RGB{color}): {src.name}",
            "input_image_path": str(src),
            "geotiff_path": geotiff_path,         # ★ 主产物路径 (供前端下载/ GIS 打开)
        },
        summary=summary,
        description=f"正在生成边缘叠加图 ({method})...",
        data=data,
        verification=verification,
    )
