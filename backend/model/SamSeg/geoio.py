# -*- coding: utf-8 -*-
"""
SamSeg 地理 IO 层 (GeoIO / Product Persistence Layer)
=====================================================

职责: 把推理产出的掩膜 转换为「带地理坐标的可交换产物」
      (带 CRS 的 GeoTIFF 掩膜 + 矢量 GeoJSON + ESRI Shapefile)。

★ 与推理核心 (runner.py) 解耦: 本模块只负责"地理坐标转换 + 产物落盘",
  不关心模型/算法/上色。
★ 与可视化外观无关 (彩色 PNG/图注 在 visualize.py)。

依赖:
  - rasterio: 读/写 GeoTIFF (transform/crs)
  - geopandas + backend.data.vector: 矢量化 (polygonize_mask)
  - affine: 坐标变换重算
依赖缺失时优雅降级 (返回 None, 不阻断 PNG/统计主流程)。
"""
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


# ==================== 源影像地理信息读取 ====================

def read_source_geo(source_image_path: str):
    """
    从源影像读 transform/crs/尺寸 (供 GeoTIFF 掩膜与矢量化复用).
    入参: source_image_path 源影像路径
    出参: (transform, crs, src_w, src_h) 或全 None (读失败/非地理影像)
    """
    try:
        import rasterio
        with rasterio.open(source_image_path) as src:
            return src.transform, src.crs, src.width, src.height
    except Exception as e:
        logger.warning(f"[Geo] 读源影像地理信息失败 ({source_image_path}): {e}")
        return None, None, None, None


# ==================== CRS 预检 (系统只接受带坐标影像) ====================

def assert_image_has_crs(image_path: str):
    """
    校验影像是否带地理坐标 (CRS), 用于上传/工具层硬拦截无坐标影像。

    ★ 复用 read_source_geo 的 rasterio 读取逻辑 (单一数据源, 与掩膜/矢量化判定一致)。
    ★ 不抛异常: 调用方按自身约定返回错误结构 (工具层返回 dict, 端点层抛 HTTPException)。

    入参: image_path 影像宿主机路径
    出参: (ok: bool, reason: str)
      - ok=True, reason=""
      - ok=False, reason=中文原因 (供错误提示拼接)
        可能原因: rasterio 未安装 / 非 GeoTIFF 本地影像 (PNG/JPG) / GeoTIFF 无 CRS / 读取失败
    """
    try:
        import rasterio
    except ImportError:
        return False, "rasterio 未安装, 无法校验地理坐标"

    try:
        with rasterio.open(image_path) as src:
            if src.crs is None:
                return False, "影像无 CRS (本地无坐标影像)"
            # CRS 存在但 transform 全 0 也算无地理意义 (防御性)
            t = src.transform
            if t is not None and (t.a == 0 or t.e == 0):
                return False, "影像有 CRS 但 transform 无效 (无真实地理定位)"
            return True, ""
    except ImportError:
        return False, "rasterio 未安装, 无法校验地理坐标"
    except Exception as e:
        # rasterio 读不了 → 多半是 PNG/JPG 等非地理影像
        msg = str(e)
        if "not a valid TIFF" in msg or "unrecognized" in msg.lower():
            return False, "非 GeoTIFF 影像 (无地理坐标)"
        return False, f"地理信息读取失败: {msg}"


# ==================== 彩色掩膜 → 带 CRS 的 GeoTIFF ====================

def save_geotiff_mask(color_mask, source_image_path: str, output_tif_path: str) -> Optional[dict]:
    """
    彩色掩膜 → 带 CRS 的 GeoTIFF (3 波段 RGB).

    ★ 源影像有 CRS 时才生成; 无 CRS (PNG/JPG 等本地影像) 跳过, 返回 None.
    入参:
      - color_mask: (H,W,3) uint8 彩色掩膜
      - source_image_path: 源影像路径 (读 transform/crs)
      - output_tif_path: 输出 GeoTIFF 路径
    出参: {mask_tif_path, has_crs} 或 None (跳过)
    """
    transform, crs, src_w, src_h = read_source_geo(source_image_path)
    if crs is None or transform is None:
        logger.info("[GeoTIFF] 源影像无 CRS, 跳过 GeoTIFF 掩膜生成")
        return None

    h, w = color_mask.shape[:2]
    # 尺寸不一致 → 重算 transform (与 vectorize_mask_to_geojson 同套逻辑)
    if (h, w) != (src_h, src_w):
        from affine import Affine
        px_w = (transform.a * src_w) / w
        px_h = (-transform.e * src_h) / h
        transform = Affine(px_w, 0, transform.c, 0, -px_h, transform.f)

    try:
        import rasterio
        os.makedirs(os.path.dirname(output_tif_path) or ".", exist_ok=True)
        with rasterio.open(
            output_tif_path, "w",
            driver="GTiff", width=w, height=h,
            count=3, dtype="uint8",
            crs=crs, transform=transform,
        ) as dst:
            dst.write(color_mask[:, :, 0], 1)  # R
            dst.write(color_mask[:, :, 1], 2)  # G
            dst.write(color_mask[:, :, 2], 3)  # B
        logger.info(f"[GeoTIFF] 带 CRS 掩膜已生成: {output_tif_path} (crs={crs})")
        return {"mask_tif_path": output_tif_path, "has_crs": True}
    except Exception as e:
        logger.warning(f"[GeoTIFF] 写入失败: {e}")
        return None


# ==================== GeoDataFrame → ESRI Shapefile ====================

def ascii_stem(stem: str, fallback: str = "seg") -> str:
    """
    把文件名 stem 清洗为纯 ASCII (公共函数, 全仓统一)。

    ★ 为什么需要: Fiona 的 ESRI Shapefile 驱动遇到中文/特殊字符 stem 会静默写空文件
      (不报错但 .shp 为 0 字节), 必须先 ASCII 化。

    语义: 保留字母数字和 ._- , 其余字符直接删除 (删除语义, 与历史 mask/vector 实现一致);
          清洗后为空则用 fallback。

    这是全仓 _ascii_safe 三份副本 + geoio 两处内联的统一替代。
    """
    import re
    return re.sub(r"[^a-zA-Z0-9._-]", "", stem or "") or fallback


def export_shp_from_gdf(gdf, output_dir: str, stem: str) -> Optional[dict]:
    """
    把 GeoDataFrame 写成 ESRI Shapefile 多文件目录.

    ★ 与 GeoJSON 同源: 复用 polygonize_mask 已得到的 gdf, 不重复矢量化.
      供 GIS 系统 (ArcGIS/QGIS) 直接打开, 也供 upload_shapefile_layer 使用.

    入参:
      - gdf: geopandas.GeoDataFrame (已带 geometry/area_m2/class_name)
      - output_dir: Shapefile 多文件输出目录
      - stem: 文件名 (★会自动 ASCII 化, 中文/特殊字符将被删除)

    出参: {shp_path, shp_zip_path, shp_components} 或 None (失败)
      - shp_zip_path 实为 shp 目录路径 (不再 ZIP 打包, 保留目录供 GIS 直接打开)
    """
    try:
        import shutil
        safe_stem = ascii_stem(stem)  # ★ 复用公共 ascii_stem
        shp_dir = Path(output_dir) / f"{safe_stem}_shp"
        if shp_dir.exists():
            shutil.rmtree(str(shp_dir), ignore_errors=True)
        shp_dir.mkdir(parents=True, exist_ok=True)
        shp_base = shp_dir / f"{safe_stem}.shp"

        # ESRI Shapefile: 字段名最长 10 字符, 中文需 encoding
        gdf.to_file(str(shp_base), driver="ESRI Shapefile", encoding="utf-8")

        # ★ 写出后立即校验 .shp 真实落地 (Fiona 静默失败时不生成)
        if not shp_base.is_file() or shp_base.stat().st_size == 0:
            shutil.rmtree(str(shp_dir), ignore_errors=True)
            logger.warning(f"[SHP] .shp 文件未落地 (Fiona 静默失败, stem={safe_stem})")
            return None

        # ★ 不再 ZIP 打包, 保留 SHP 目录供 GIS 直接打开
        components = sorted(f.name for f in shp_dir.iterdir() if f.is_file())
        logger.info(f"[SHP] Shapefile 已生成: {shp_base} ({len(gdf)} 要素), 目录: {shp_dir}")
        return {
            "shp_path": str(shp_base),
            "shp_zip_path": str(shp_dir),
            "shp_components": components,
        }
    except Exception as e:
        logger.warning(f"[SHP] Shapefile 导出失败: {e}", exc_info=True)
        return None


# ==================== 类别掩膜 → 矢量 GeoJSON (+ 同源 Shapefile) ====================

def vectorize_mask_to_geojson(
    mask,
    source_image_path: str,
    class_lines: List[str],
    output_geojson_path: str,
    min_area_m2: float = 50.0,
    output_shp_dir: Optional[str] = None,
    shp_stem: Optional[str] = None,
) -> Optional[dict]:
    """
    把类别掩膜矢量化为 GeoJSON 文件 (带真实面积/周长/类别名),
    可选同时导出同源 Shapefile (复用同一个 gdf, 不重复矢量化).

    ★ P1 栅格→矢量主逻辑: 从源影像读 transform/crs → polygonize_mask → 写 GeoJSON.
      依赖缺失 (geopandas/osgeo 等) 时返回 None, 不影响 PNG/统计主流程.
    ★ Shapefile 同源导出: 传 output_shp_dir 时, 用 polygonize_mask 的同一个 gdf
      额外写出 .shp 目录, 供 GIS 直接打开。

    入参:
      - mask: (H,W) 类别索引图 (0=背景/无变化)
      - source_image_path: 源影像路径 (用于读 transform/crs; 非地理影像则 crs=None)
      - class_lines: 类别行列表 (索引=类别ID, ["background","建筑",...])
      - output_geojson_path: 输出 .geojson 路径
      - min_area_m2: 最小面积过滤 (平方米)
      - output_shp_dir: 可选, Shapefile 多文件输出目录 (传 None 则不导出 shp)
      - shp_stem: 可选, Shapefile 文件名 (★必须 ASCII, 中文会静默写空文件)

    出参: {vector_path, vector_stats} 或 None
      - vector_stats: {total_features, total_area_m2, per_class: [{name, count, area_m2}]}
      - 传 output_shp_dir 时额外含: {shp_path, shp_zip_path, shp_components}
    """
    try:
        from backend.data.vector import vectorize_available, polygonize_mask
    except ImportError as e:
        logger.warning(f"[Vectorize] 矢量化模块不可用: {e}")
        return None

    if not vectorize_available():
        logger.info("[Vectorize] 依赖缺失, 跳过矢量化 (PNG/统计不受影响)")
        return None

    # 从源影像读 transform/crs
    try:
        import rasterio
        with rasterio.open(source_image_path) as src:
            transform = src.transform
            crs = src.crs
            src_w, src_h = src.width, src.height
    except Exception as e:
        logger.warning(f"[Vectorize] 读源影像地理信息失败 ({source_image_path}): {e}")
        transform, crs = None, None
        src_w, src_h = None, None

    # mask 与源影像尺寸不一致 (resize 对齐过) → 用 mask 自身尺寸构造 transform
    #   transform 按 mask 像素尺度, offset 复用源影像原点 (近似, 大图变化检测场景够用)
    if transform is not None and mask.shape[:2] != (src_h or 0, src_w or 0):
        from affine import Affine
        # 估算单像素地理尺寸 (源影像分辨率按比例缩放到 mask 尺寸)
        if src_w and src_h:
            px_w = (transform.a * src_w) / mask.shape[1]
            px_h = (-transform.e * src_h) / mask.shape[0]
            transform = Affine(px_w, 0, transform.c, 0, -px_h, transform.f)
            logger.info(
                f"[Vectorize] mask({mask.shape[1]}x{mask.shape[0]}) ≠ 源({src_w}x{src_h}), "
                f"重算 transform: px={px_w:.4f}x{-px_h:.4f}m"
            )

    # 类别名列表 (索引=类别ID)
    class_names = [line.split(",")[0].strip() for line in class_lines]

    gdf = polygonize_mask(
        mask, transform, crs,
        class_names=class_names,
        min_area_m2=min_area_m2,
        simplify_tolerance=0.0,
    )
    if gdf is None or len(gdf) == 0:
        logger.info("[Vectorize] 矢量化无有效要素 (可能 min_area 过大或全背景)")
        return None

    # 写 GeoJSON
    os.makedirs(os.path.dirname(output_geojson_path) or ".", exist_ok=True)
    # ★ 不再写 GeoJSON, 直接出 SHP
    # gdf.to_file(output_geojson_path, driver="GeoJSON", encoding="utf-8")

    # 统计
    per_class = {}
    for _, row in gdf.iterrows():
        key = row["class_name"]
        if key not in per_class:
            per_class[key] = {"name": key, "count": 0, "area_m2": 0.0}
        per_class[key]["count"] += 1
        per_class[key]["area_m2"] += float(row["area_m2"])
    per_class_list = sorted(per_class.values(), key=lambda x: -x["area_m2"])
    for pc in per_class_list:
        pc["area_m2"] = round(pc["area_m2"], 2)

    vector_stats = {
        "total_features": len(gdf),
        "total_area_m2": round(float(gdf["area_m2"].sum()), 2),
        "per_class": per_class_list,
        "has_crs": crs is not None,
    }
    logger.info(
        f"[Vectorize] 矢量化完成: {output_geojson_path} "
        f"({vector_stats['total_features']} 要素, {vector_stats['total_area_m2']} m², "
        f"crs={'有' if vector_stats['has_crs'] else '无'})"
    )
    result = {"vector_stats": vector_stats}

    # ---- 同源 Shapefile 导出 (面 SHP + 边线 SHP, 复用同一个 gdf) ----
    if output_shp_dir:
        base_stem = shp_stem or Path(output_geojson_path).stem
        safe_stem = ascii_stem(base_stem)
        shp_result = export_shp_from_gdf(gdf, output_shp_dir, safe_stem)
        if shp_result:
            result["shp_path"] = shp_result["shp_path"]
            result["shp_zip_path"] = shp_result["shp_zip_path"]
            result["shp_components"] = shp_result["shp_components"]
            logger.info(f"[Vectorize] 面 SHP 已导出: {shp_result['shp_path']}")

        # ★ 边线 SHP: 从面 GDF 直接提取 boundary (不经过 GeoJSON)
        try:
            edge_gdf = gdf.copy()
            edge_gdf["geometry"] = gdf.geometry.boundary
            if edge_gdf is not None and len(edge_gdf) > 0:
                edge_stem = ascii_stem(safe_stem + "_edge")
                edge_shp_result = export_shp_from_gdf(edge_gdf, output_shp_dir, edge_stem)
                if edge_shp_result:
                    result["edge_shp_path"] = edge_shp_result["shp_path"]
                    logger.info(f"[Vectorize] 边线 SHP 已导出: {edge_shp_result['shp_path']}")
        except Exception as e:
            logger.warning(f"[Vectorize] 边线 SHP 导出失败: {e}")

    return result
