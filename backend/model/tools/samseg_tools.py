"""
SamSeg 遥感分析工具集 (Model 层 / 工具箱)
- 入参: 图片路径 (前端上传后返回的绝对路径)、类别名
- 方法: 调用 SamSeg 做语义分割 / 变化检测, 输出彩色 PNG
- 出参: frontend_action(render_image), 前端右侧面板展示结果

工具特点:
  - 依赖重 (torch + 3.3GB 权重), 通过 runner.samseg_available() 检测, 不可用时返回明确错误
  - 推理耗时几秒~几十秒, 同步执行 (照搬现有 download_raster 阻塞模式)
  - 类别支持中英文 (建筑/道路/building/water...), 留空用默认 7 类
  - 图片来源: 前端上传到后端的 /api/v1/samseg/upload 端点, 返回的路径直接作为 image_path 传入

依赖方向: model.tools.samseg_tools → model.SamSeg.runner (本层), 无跨层依赖。
"""
import logging
import re
import time
from pathlib import Path
from typing import Dict, Any, Optional

from langchain_core.tools import tool
from backend.model.tools.tool_registry import register_tool
from backend.model.tools._paths import (
    session_subpath, session_subdirs, short_conv, rand_suffix,
)
from backend.config import settings

logger = logging.getLogger(__name__)


# ★ v2.3: 按来源维度 — SamSeg 专属目录
def _get_output_root() -> Path:
    return Path(settings.samseg_output_root).resolve()

def _get_upload_root() -> Path:
    return Path(settings.samseg_upload_root).resolve()

# ★ P1 栅格→矢量: 矢量 GeoJSON 输出目录 (与 PNG 结果对齐)
def _get_vector_root() -> Path:
    return Path(settings.samseg_vector_root).resolve()


def _ensure_output_dir() -> Path:
    """
    ★ v2.4: 确保结果输出目录存在 (目录顺序统一为 {proj}/{conv})。
    路径: agent-files/samseg/generate/{project_id}/{conversation_id}/
    """
    out_dir = _get_output_root() / session_subpath()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _ensure_vector_dir() -> Path:
    """
    ★ P1: 确保矢量输出目录存在 (目录顺序统一为 {proj}/{conv})。
    路径: agent-files/samseg/vector/{project_id}/{conversation_id}/
    """
    vec_dir = _get_vector_root() / session_subpath()
    vec_dir.mkdir(parents=True, exist_ok=True)
    return vec_dir


def _classes_tag(classes: Optional[str], max_len: int = 40) -> str:
    """
    把用户传入的类别字符串清洗成文件名安全的短标签, 用于结果图命名。
    - "建筑,道路" → "建筑-道路"
    - "building,water" → "building-water"
    - 空 → "default"
    - 非法字符替换为 _; 超长截断
    目的: 让不同类别的分割结果文件名不同, 避免互相覆盖 (修复"不同批次显示同一结果"的 bug)
    """
    if not classes or not classes.strip():
        return "default"
    # 统一中文逗号, 用 - 连接各类
    parts = [p.strip() for p in classes.replace("，", ",").split(",") if p.strip()]
    if not parts:
        return "default"
    tag = "-".join(parts)
    # 清洗: 文件名非法字符 → _, 限制长度
    tag = re.sub(r'[\\/:*?"<>|]', "_", tag)
    if len(tag) > max_len:
        tag = tag[:max_len]
    return tag


def _build_image_url(filename: str) -> str:
    """
    ★ v2.4: 构造结果 PNG 的前端可访问 URL (目录顺序统一为 {proj}/{conv})。
    路径: /api/v1/download/samseg/generate/{project_id}/{conversation_id}/{filename}
    """
    proj, conv = session_subdirs()
    rel = f"samseg/generate/{proj}/{conv}"
    return f"/api/v1/download/{rel}/{filename}"


def _build_vector_url(vector_path: Optional[str]) -> Optional[str]:
    """
    入参: vector_path 已生成的矢量文件绝对路径，可为空。
    方法: 校验文件真实存在后，基于统一文件根目录构造受控下载 URL。
    出参: 前端可访问的 /api/v1/download URL；文件缺失时返回 None。
    """
    if not vector_path or not Path(vector_path).is_file():
        return None
    from backend.model.tools._paths import build_download_url
    file_root = Path(settings.file_storage_root).resolve()
    return build_download_url(Path(vector_path).resolve(), file_root)



def _build_input_image_url(image_path: str) -> Optional[str]:
    """
    ★ v2.3: 把上传的输入影像绝对路径转成前端可访问的 URL。

    优先级:
      1. 路径在 samseg/send/ 下 → 走 /api/v1/upload/ 端点 (用户上传原图的标准路径)
      2. 路径在 agent-files/ 其它子目录下 (如 geoserver/generate/ 下载的影像,
         samseg/generate/ 分割结果等) → 走 /api/v1/download/ 端点 (该端点服务整个 agent-files/)
      3. 都不匹配 → 返回 None

    - 入参: 如 "E:/.../agent-files/samseg/send/{conv_id}/photo.png"
             或 "E:/.../agent-files/geoserver/generate/{proj}/{conv}/r000_c006_t1.tif"
    - 出参: 如 "/api/v1/upload/{conv_id}/photo.png"
             或 "/api/v1/download/geoserver/generate/{proj}/{conv}/r000_c006_t1.tif"
    """
    from backend.model.tools._paths import build_download_url
    from backend.config import settings

    try:
        p = Path(image_path).resolve()
    except (ValueError, OSError):
        logger.warning(f"无法解析输入影像路径: {image_path}")
        return None

    # 优先 1: samseg/send/ → /api/v1/upload/
    try:
        upload_root = _get_upload_root()
        rel = p.relative_to(upload_root)
        return "/api/v1/upload/" + rel.as_posix()
    except (ValueError, OSError):
        pass

    # 优先 2: agent-files/ 下任意子目录 → /api/v1/download/ (会自动 TIFF→PNG 转码)
    file_root = Path(settings.file_storage_root).resolve()
    download_url = build_download_url(p, file_root)
    if download_url:
        return download_url

    logger.warning(f"无法把输入影像路径转成 URL (不在 samseg/send/ 也非 agent-files/ 下): {image_path}")
    return None


def _build_polygon_layer_params(result: dict, cls_tag: str) -> dict:
    """
    把分割/变化检测的面数据发布到 GeoServer, 返回前端渲染所需的 polygon_layer 参数。

    ★ 策略:
      1. 把面 SHP 发布到 GeoServer → 返回 {layer_name, workspace, bbox, wms_url} (前端走 WMS)
      2. 失败/无数据: 返回 {}

    入参:
      - result: runner 返回的 dict (含 shp_path/shp_zip_path)
      - cls_tag: 类别短标签 (用于 GeoServer 图层命名)

    出参:
      - {layer_name, workspace, bbox, wms_url}    发布成功 (WMS 路径)
      - {}                                          无面数据 / 发布失败
    """
    shp_path = result.get("shp_path")
    shp_dir = result.get("shp_zip_path")

    # ★ 优先发布到 GeoServer (需 .shp 主文件存在)
    shp_main = None
    if shp_path and Path(shp_path).is_file():
        shp_main = shp_path
    elif shp_dir and Path(shp_dir).is_dir():
        candidates = sorted(Path(shp_dir).glob("*.shp"))
        if candidates:
            shp_main = str(candidates[0])

    if shp_main:
        try:
            from backend.data import geoserver_client as gs
            from backend.config import settings
            conv = short_conv() or "anon"
            # ★ GeoServer 图层名必须 ASCII (中文会出问题); 用 conv+类别+随机后缀
            ascii_name = re.sub(r"[^a-zA-Z0-9._-]", "", f"{conv}_{cls_tag}_poly") or f"{conv}_poly"
            ws = settings.geoserver_workspace or "samseg"
            pub = gs.upload_shapefile(shp_main, ascii_name, ws if ws else None)
            if pub and pub.get("status") == "success":
                layer_name = pub.get("layer_name", ascii_name)
                workspace = pub.get("workspace", ws)
                # 取图层 bbox 供前端精确 fit
                bbox = None
                try:
                    bb = gs.get_layer_bbox(f"{workspace}:{layer_name}")
                    if bb and len(bb) == 4:
                        bbox = bb
                except Exception:
                    pass
                # 为面图层设置蓝色样式 (#2C6FBD = GeoAI Copilot 标题色, 与边缘线同色)
                style_name = gs._ensure_samseg_polygon_style()
                if style_name:
                    gs._set_layer_default_style(workspace, layer_name, style_name)
                logger.info(f"[Polygon] 面 SHP 已发布到 GeoServer: {workspace}:{layer_name}")
                return {
                    "layer_name": layer_name,
                    "workspace": workspace,
                    "bbox": bbox,
                    "wms_url": settings.geoserver_wms_url,
                }
            logger.warning(f"[Polygon] GeoServer 发布失败: {pub}")
        except Exception as e:
            logger.warning(f"[Polygon] GeoServer 发布异常: {e}")

    return {}


def _build_edge_layer_params(result: dict, cls_tag: str, polygon_bbox: Optional[dict] = None) -> dict:
    """
    把 runner 产出的边缘线 GeoJSON 发布到 GeoServer, 返回前端渲染所需的 edge_layer 参数。

    ★ 策略:
      1. 把边线 SHP 发布到 GeoServer → 返回 {layer_name, workspace, bbox, wms_url} (前端走 WMS)
      2. 失败/无数据: 返回 {}

    ★ bbox 由发布后自行查询 GeoServer 获取, 不再依赖 polygon_bbox (面 SHP 不再发布)。

    入参:
      - result: runner 返回的 dict (含 edge_shp_path)
      - cls_tag: 类别短标签 (用于 GeoServer 图层命名)
      - polygon_bbox: (保留兼容, 不再使用) 面图层的 bbox dict

    出参:
      - {layer_name, workspace, bbox, wms_url}    发布成功 (WMS 路径)
      - {}                                          无边缘线 / 发布失败
    """
    edge_shp_path = result.get("edge_shp_path")

    # ★ 用边缘线 SHP 发布到 GeoServer
    if edge_shp_path and Path(edge_shp_path).is_file():
        try:
            from backend.data import geoserver_client as gs
            from backend.config import settings
            conv = short_conv() or "anon"
            ascii_name = re.sub(r"[^a-zA-Z0-9._-]", "", f"{conv}_{cls_tag}_edge") or f"{conv}_edge"
            ws = settings.geoserver_workspace or "samseg"
            pub = gs.upload_shapefile(edge_shp_path, ascii_name, ws if ws else None)
            if pub and pub.get("status") == "success":
                layer_name = pub.get("layer_name", ascii_name)
                workspace = pub.get("workspace", ws)
                # 发布后自行查询 bbox, 不再依赖 polygon_layer
                bbox = None
                try:
                    bb = gs.get_layer_bbox(f"{workspace}:{layer_name}")
                    if bb and len(bb) == 4:
                        bbox = bb
                except Exception:
                    pass
                # 为边缘线设置蓝色样式 (#2C6FBD = GeoAI Copilot 标题色)
                style_name = gs._ensure_samseg_edge_style()
                if style_name:
                    gs._set_layer_default_style(workspace, layer_name, style_name)
                logger.info(f"[Edge] 边缘线 SHP 已发布到 GeoServer: {workspace}:{layer_name}")
                return {
                    "layer_name": layer_name,
                    "workspace": workspace,
                    "bbox": bbox,
                    "wms_url": settings.geoserver_wms_url,
                }
            logger.warning(f"[Edge] GeoServer 发布失败: {pub}")
        except Exception as e:
            logger.warning(f"[Edge] GeoServer 发布异常: {e}")

    return {}


def _build_base_layer_params(image_path: str, cls_tag: str) -> dict:
    """
    同步上传原始影像到 GeoServer, 返回 WMS 图层参数。

    入参:
      - image_path: 原始影像绝对路径
      - cls_tag: 类别短标签 (用于图层命名, 如 building-road)

    方法:
      1. 检查 GeoServer 可用性
      2. 构造图层名 {conv}_{cls_tag}_original
      3. 同步调用 upload_raster 上传原始影像
      4. 上传成功/图层已存在 → 返回 WMS 图层参数; 失败 → 返回 {}

    出参:
      - {layer_name, workspace, wms_url}  上传成功或图层已存在
      - {}                                 GeoServer 不可用或上传失败
    """
    try:
        from backend.data import geoserver_client as gs
        from backend.config import settings
    except ImportError:
        return {}

    if not gs.geoserver_available():
        logger.warning("[BaseLayer] GeoServer 不可用, 无法上传原始影像")
        return {}

    conv = short_conv() or "anon"
    ascii_name = re.sub(r"[^a-zA-Z0-9._-]", "", f"{conv}_{cls_tag}_original") or f"{conv}_original"
    ws = settings.geoserver_workspace or "samseg"

    result = gs.upload_raster(image_path, ascii_name, ws)
    if result.get("status") == "success":
        if result.get("skipped"):
            logger.info(f"[BaseLayer] 原始影像 GeoServer 图层已存在: {ws}:{ascii_name}")
        else:
            logger.info(f"[BaseLayer] 原始影像已发布到 GeoServer: {ws}:{ascii_name}")
        return {
            "layer_name": ascii_name,
            "workspace": ws,
            "wms_url": settings.geoserver_wms_url,
        }
    else:
        logger.warning(f"[BaseLayer] 原始影像 GeoServer 发布失败: {result.get('msg', '未知')}")
        return {}


@register_tool("samseg")
@tool
def segment_image(
    image_path: str,
    classes: str = "",
    referring_expression: str = "",
) -> Dict[str, Any]:
    """
    对一张图片做语义分割, 按指定地物类别生成彩色掩膜图, 在前端展示分割结果。

    ★ 触发场景: 用户**明确要求分割/提取/标注地物** (如"分割这张图""提取建筑和道路""标注水体")。
    ★ 不触发场景: 用户只是问"图里有什么/描述一下/这是什么地物"等**理解类**问题 ——
      那是影像理解需求, 本工具只会按类别画掩膜, 无法回答"有什么", 不要调用。
    ★ 耗时: 推理通常 5~30 秒 (取决于图片大小和类别数), 首次调用需加载模型 (约几十秒)。
    ★ 依赖: 需 torch + SamSeg 模型权重就绪, 否则返回不可用错误。
    ★ 图片来源: 前端上传后返回的绝对路径 (如 .../agent-files/send/{conv_id}/xxx.png)。

    入参:
        - image_path (str): 要分割的图片绝对路径 (用户上传后获得)
        - classes (str): 要识别的类别, 逗号分隔, 支持中文/英文。
          常用: 建筑/道路/水/植被/农田/裸地 或 building/road/water/vegetation/farmland/bareland。
          留空则用默认 7 类 (建筑/道路/水/裸地/植被/农田)。
        - referring_expression (str): 可选完整指代表达。用于选择单个实例，例如
          "最大的建筑"、"左上角的建筑"、"道路北侧最大的建筑"。使用时 classes 应只填目标类别。
    出参: frontend_action 指令, 前端右侧面板展示彩色分割结果

    示例:
        segment_image("E:/.../agent-files/send/{conv}/photo.png", "建筑,道路,水")
        segment_image("E:/.../agent-files/send/{conv}/photo.png", "建筑", "道路北侧最大的建筑")
        segment_image("E:/.../agent-files/send/{conv}/photo.png")  # 用默认全部类别
    """
    from backend.model.SamSeg import runner
    from backend.model.SamSeg.geoio import assert_image_has_crs

    if not runner.samseg_available():
        return {"type": "error", "msg": "SamSeg 不可用: 未安装 torch 或模型权重缺失"}

    if not Path(image_path).is_file():
        return {"type": "error", "msg": f"图片不存在: {image_path}"}

    # ★ 系统只接受带坐标影像: 推理前预检 CRS (覆盖 agent 传入任意工作区路径的场景)
    ok, reason = assert_image_has_crs(image_path)
    if not ok:
        return {"type": "error",
                "msg": f"无法分割：该影像无地理坐标(CRS)，系统仅接受带坐标的遥感影像(GeoTIFF/COG)。({reason})"}

    try:
        out_dir = _ensure_output_dir()
        cls_tag = _classes_tag(classes)
        # ★ v2.4 命名精简: 文件名 = {conv短}_{类别}_seg_{随机4}.png (去 stem 和时间戳, 避免套娃变长)
        #   stem 只用于 caption/summary 展示原图名, 不进文件名
        stem = Path(image_path).stem
        #   - 同会话同图同类别 → 随机后缀不同 → 不覆盖
        #   - 同会话同图不同类别 → 类别标签不同 → 不覆盖
        conv = short_conv() or "anon"
        suffix = rand_suffix(4)
        out_file = f"{conv}_{cls_tag}_seg_{suffix}.png"
        out_path = str(out_dir / out_file)

        # ★ P1 矢量化: 准备 GeoJSON 输出路径 (与 PNG 同名换扩展名)
        vec_dir = _ensure_vector_dir()
        # GeoJSON 使用 ASCII 文件名，规避 Windows GDAL/Fiona 对中文文件名的兼容差异。
        from backend.model.SamSeg.geoio import ascii_stem
        vec_stem = ascii_stem(Path(out_file).stem, fallback=f"{conv}_seg_{suffix}")
        vec_file = f"{vec_stem}.geojson"
        vec_path = str(vec_dir / vec_file)

        t0 = time.time()
        result = runner.run_segment(
            image_path=image_path,
            output_path=out_path,
            classes=classes or None,
            vector_output_path=vec_path,
            referring_expression=referring_expression or None,
        )
        elapsed = time.time() - t0

        if not result:
            return {"type": "error", "msg": f"分割失败: {image_path}"}

        size = Path(out_path).stat().st_size
        # ★ 输入影像 URL: 让前端能从服务器读取原图, 与结果对比展示
        input_image_url = _build_input_image_url(image_path)
        cls_desc = classes if classes else "(建筑/道路/水/裸地/植被/农田)"
        referring_metadata = result.get("referring")
        stats = result.get("stats", {})
        legend = result.get("legend", [])  # ★ 颜色→类别图注 (与 PNG 颜色严格一致)
        # ★ P1: 矢量统计 (面积/图斑数, 供摘要)
        vector_stats = result.get("vector_stats")
        vector_path = result.get("vector_path")
        vector_url = _build_vector_url(vector_path)

        # ★ 构建详细摘要供 LLM 综合描述
        per_class_lines = []
        for c in stats.get("per_class", []):
            per_class_lines.append(
                f"    - {c['name']}: {c['regions']} 个区域, "
                f"面积 {c['area_px']} 像素 ({c['area_percent']}%)"
            )
        per_class_text = "\n".join(per_class_lines) if per_class_lines else "    (无)"

        # ★ P1: 矢量统计追加到摘要 (真实面积, 比 像素面积 更直观)
        vector_summary = ""
        if vector_stats:
            vector_summary = (
                f"\n矢量图斑统计 (真实面积):\n"
                f"  总图斑数: {vector_stats.get('total_features', 0)} 个\n"
                f"  总面积: {vector_stats.get('total_area_m2', 0)} m²"
                f" ({vector_stats.get('total_area_m2', 0)/10000:.2f} 公顷)\n"
            )
            for pc in vector_stats.get("per_class", [])[:5]:
                vector_summary += (
                    f"    - {pc['name']}: {pc['count']} 个图斑, "
                    f"{pc['area_m2']} m² ({pc['area_m2']/10000:.2f} 公顷)\n"
                )

        # ★ GeoTIFF 带 CRS 掩膜 (前端地图容器用它做半透明叠加图层, 替代旧 overlay 混色图)
        has_crs = result.get("has_crs", False)
        mask_tif_path = result.get("mask_tif_path")
        mask_tif_url = None
        if mask_tif_path and Path(mask_tif_path).is_file():
            mask_tif_file = Path(mask_tif_path).name
            mask_tif_url = _build_image_url(mask_tif_file) + "?t=" + str(int(time.time() * 1000))

        # ★ overlay 叠加图 / edge 边缘 GeoTIFF 已不再生成 (前端地图容器用图层透明度叠加实现)
        #   如需边缘描线, AI 可单独调用 mask_tools.overlay_edge_on_image 工具

        # ★ Shapefile (实际分割结果, 供 GIS 直接打开) — 保留为目录
        shp_path = result.get("shp_path")
        shp_dir_path = result.get("shp_zip_path")  # 现为 SHP 目录而非 zip 文件
        shp_exists = shp_dir_path and Path(shp_dir_path).is_dir()

        geo_summary = (
            f"\n坐标系: {'已带 (GeoTIFF 掩膜可被 GIS 直接打开)' if has_crs else '无 (源影像无 CRS, 仅像素坐标)'}\n"
            f"掩膜 GeoTIFF (前端地图叠加层): {'已生成' if mask_tif_url else '未生成'}\n"
            f"GeoJSON 矢量图斑: 不加入工作区 (可下载/叠加到地图)\n"
            f"Shapefile (GIS 可直接打开): {'已生成' if shp_exists else '未生成'}\n"
        )

        summary = (
            f"已对图片 {stem} 完成语义分割"
            f"（类别: {cls_desc}, 耗时 {elapsed:.1f}s, 结果 {size//1024}KB）。\n"
            f"分割统计:\n"
            f"  总区域数: {stats.get('total_regions', 0)} 个\n"
            f"  被分类像素: {stats.get('changed_pixels', 0)}"
            f" ({stats.get('changed_percent', 0)}%)\n"
            f"各类别详情:\n"
            f"{per_class_text}\n"
            f"{vector_summary}"
            f"{geo_summary}"
            f"请根据以上统计对分割结果进行定量描述，"
            f"说明各类别的覆盖面积（像素和百分比）以及区域数量，"
            f"指出占主导地位的地物类型。"
        )

        # ★ 工作区布局: 原图层 (底图) + 变化面 (GeoServer WMS) + 边缘线 (GeoServer WMS)
        #   面 SHP 与边缘线 SHP 颜色统一为 #2C6FBD (GeoAI Copilot 标题色)。
        polygon_layer = _build_polygon_layer_params(result, cls_tag)
        edge_layer = _build_edge_layer_params(result, cls_tag, None)
        # ★ 原图底图层: 同步上传到 GeoServer → 前端从 WMS 加载 (支持属性查询 GetFeatureInfo)
        base_layer = _build_base_layer_params(image_path, cls_tag)
        params = {
            # ★ 主图 = 输入原图 URL (地图容器据此加载底图层 + 查坐标定位)
            "image_url": input_image_url,
            "caption": f"语义分割结果: {stem}",
            # ★ 变化面: 发布到 GeoServer (polygon_layer)
            "polygon_layer": polygon_layer if polygon_layer else None,
            # ★ 边缘线: GeoServer WMS 图层 (edge_layer)
            "edge_layer": edge_layer if edge_layer else None,
            # ★ 原图底图层: GeoServer WMS (支持属性查询; 不可用时前端回退到本地文件)
            "base_layer": base_layer if base_layer else None,
            # ★ 输入影像 URL (供前端展示原图, 与结果对比)
            "input_image_url": input_image_url,
            "input_caption": f"输入影像: {stem}",
            "input_image_path": image_path,  # ★ 宿主机路径, 前端据此查坐标显示
            # ★ 图注: 颜色→类别 (与 PNG 颜色严格一致)
            "legend": legend,
            # ★ 是否带 CRS (前端据此决定真实定位 or 拒绝渲染)
            "has_crs": has_crs,
            "vector_url": vector_url,
        }
        # ★ 掩膜 GeoTIFF 不再自动渲染叠加 (仅保留为可下载产物); artifact_path 指向边缘线供下载
        if mask_tif_path:
            params["artifact_path"] = mask_tif_path

        return {
            "type": "frontend_action",
            "action": "render_image",
            "instruction": {
                "type": "render_image",
                "action": "render_image",
                "params": params,
            },
            "wait_for_result": False,
            "description": f"⏳ 分割完成, 正在加载结果 ({elapsed:.1f}s)",
            "summary": summary,
            "data": {
                "image_path": image_path,
                "classes": cls_desc,
                "referring_expression": referring_expression or None,
                "referring": referring_metadata,
                "elapsed": round(elapsed, 1),
                "performance": result.get("performance", {}),
                "stats": stats,
                "vector_stats": vector_stats,
                "vector_path": vector_path,
                "vector_url": vector_url,
                "has_crs": has_crs,
                "mask_tif_path": mask_tif_path,
                "mask_tif_url": mask_tif_url,
                "polygon_layer": polygon_layer,
                "base_layer": base_layer,
                "shp_path": shp_path,
                "shp_dir_path": shp_dir_path,
            },
        }
    except Exception as e:
        logger.error(f"[SamSeg] segment_image 失败 {image_path}: {e}", exc_info=True)
        return {"type": "error", "msg": f"分割执行失败: {e}"}


@register_tool("samseg")
@tool
def detect_change(t1_path: str, t2_path: str, classes: str = "") -> Dict[str, Any]:
    """
    对两张不同时期的图片 (双时相) 做变化检测, 找出哪些区域发生了地物变化, 在前端展示变化图。

    ★ 触发场景: 用户**明确要求做变化检测/对比变化** (如"对比这两张图的变化""检测这两期影像的变化""哪里发生了改变")。
    ★ 不触发场景: 用户只是想"了解/描述"某张图 —— 那是影像理解需求, 不要用本工具。
    ★ 耗时: 两张图各自分割 + 变化对比, 通常 10~60 秒, 首次调用需加载模型。
    ★ 依赖: 需 torch + SamSeg 模型权重就绪, 否则返回不可用错误。
    ★ 图片来源: 前端上传两张图片后返回的绝对路径。

    入参:
        - t1_path (str): 前一时相 (T1) 图片绝对路径
        - t2_path (str): 后一时相 (T2) 图片绝对路径
        - classes (str): 关注的类别, 逗号分隔, 支持中文/英文。留空用默认 7 类。
    出参: frontend_action 指令, 前端右侧面板展示变化检测结果
           (变化区域按变化后的地物类别上色, 未变化区域为白色背景)

    示例:
        detect_change("E:/.../t1_2020.png", "E:/.../t2_2024.png", "建筑,植被")
    """
    from backend.model.SamSeg import runner
    from backend.model.SamSeg.geoio import assert_image_has_crs

    if not runner.samseg_available():
        return {"type": "error", "msg": "SamSeg 不可用: 未安装 torch 或模型权重缺失"}

    if not Path(t1_path).is_file():
        return {"type": "error", "msg": f"T1 图片不存在: {t1_path}"}
    if not Path(t2_path).is_file():
        return {"type": "error", "msg": f"T2 图片不存在: {t2_path}"}

    # ★ 系统只接受带坐标影像: 推理前预检 CRS (两期影像都需带坐标)
    ok_t1, reason_t1 = assert_image_has_crs(t1_path)
    if not ok_t1:
        return {"type": "error",
                "msg": f"无法变化检测：T1 影像无地理坐标(CRS)，系统仅接受带坐标的遥感影像(GeoTIFF/COG)。({reason_t1})"}
    ok_t2, reason_t2 = assert_image_has_crs(t2_path)
    if not ok_t2:
        return {"type": "error",
                "msg": f"无法变化检测：T2 影像无地理坐标(CRS)，系统仅接受带坐标的遥感影像(GeoTIFF/COG)。({reason_t2})"}

    try:
        out_dir = _ensure_output_dir()
        cls_tag = _classes_tag(classes)
        # ★ v2.4 命名精简: 文件名 = {conv短}_{类别}_change_{随机4}.png (去 t1_t2 拼接和时间戳)
        #   stem 只用于 caption 展示 "T1_T2" 原图名, 不进文件名
        stem = f"{Path(t1_path).stem}_{Path(t2_path).stem}"
        conv = short_conv() or "anon"
        suffix = rand_suffix(4)
        out_file = f"{conv}_{cls_tag}_change_{suffix}.png"
        out_path = str(out_dir / out_file)

        # ★ P1 矢量化: 准备变化图斑 GeoJSON 输出路径
        vec_dir = _ensure_vector_dir()
        # 与分割流程保持同一产物契约：ASCII GeoJSON + 同源 Shapefile。
        from backend.model.SamSeg.geoio import ascii_stem
        vec_stem = ascii_stem(Path(out_file).stem, fallback=f"{conv}_change_{suffix}")
        vec_file = f"{vec_stem}.geojson"
        vec_path = str(vec_dir / vec_file)

        t0 = time.time()
        result = runner.run_change_detection(
            t1_path=t1_path,
            t2_path=t2_path,
            output_path=out_path,
            classes=classes or None,
            vector_output_path=vec_path,
        )
        elapsed = time.time() - t0

        if not result:
            return {"type": "error", "msg": f"变化检测失败: {t1_path} vs {t2_path}"}

        # ★ 双时相输入影像 URL: 让前端能从服务器读取 T1/T2 原图, 与结果对比展示
        input_image_url = _build_input_image_url(t1_path)
        input_image_url_t2 = _build_input_image_url(t2_path)
        cls_desc = classes if classes else "7类"
        stats = result.get("stats", {})
        legend = result.get("legend", [])  # ★ 颜色→类别图注 (与 PNG 颜色严格一致)
        # ★ P1: 矢量变化图斑统计 (面积/图斑数, 供摘要)
        vector_stats = result.get("vector_stats")
        vector_path = result.get("vector_path")
        vector_url = _build_vector_url(vector_path)

        # 业务类型判读必须在本地确定性完成，避免外部 VLM 网络请求阻塞检测任务终态。
        present_classes = [item.get("name", "") for item in stats.get("per_class", [])]
        change_type_info = runner.classify_change_type_by_rule(present_classes, stats)
        change_type_info["source"] = "rule"

        # ★ 构建详细摘要供 LLM 综合描述
        per_class_lines = []
        for c in stats.get("per_class", []):
            per_class_lines.append(
                f"    - {c['name']}: {c['regions']} 个变化区域, "
                f"面积 {c['area_px']} 像素 ({c['area_percent']}%)"
            )
        per_class_text = "\n".join(per_class_lines) if per_class_lines else "    (无)"

        # ★ P1: 矢量图斑统计 (真实面积 m²/公顷, 比像素面积更直观, 是规则套合/报表的基础)
        vector_summary = ""
        if vector_stats:
            vector_summary = (
                f"\n变化图斑矢量统计 (真实面积):\n"
                f"  总图斑数: {vector_stats.get('total_features', 0)} 个\n"
                f"  总面积: {vector_stats.get('total_area_m2', 0)} m²"
                f" ({vector_stats.get('total_area_m2', 0)/10000:.2f} 公顷)\n"
            )
            for pc in vector_stats.get("per_class", [])[:5]:
                vector_summary += (
                    f"    - {pc['name']}: {pc['count']} 个变化图斑, "
                    f"{pc['area_m2']} m² ({pc['area_m2']/10000:.2f} 公顷)\n"
                )

        # ★ Phase B: 业务类型判读摘要 (VLM/规则)
        change_type_summary = ""
        if change_type_info:
            ct = change_type_info.get("change_type", "未知")
            conf = change_type_info.get("confidence", 0)
            reason = change_type_info.get("reason", "")
            src = change_type_info.get("source", "rule")
            src_label = "VLM判读" if src == "vlm" else "规则推断"
            change_type_summary = (
                f"\n业务变化类型 ({src_label}): {ct} (置信度 {conf})\n"
                f"  判读依据: {reason}\n"
            )

        # ★ GeoTIFF 带 CRS 掩膜 (前端地图容器用它做半透明叠加图层, 替代旧 overlay 混色图)
        has_crs = result.get("has_crs", False)
        mask_tif_path = result.get("mask_tif_path")
        mask_tif_url = None
        if mask_tif_path and Path(mask_tif_path).is_file():
            mask_tif_file = Path(mask_tif_path).name
            mask_tif_url = _build_image_url(mask_tif_file) + "?t=" + str(int(time.time() * 1000))

        # ★ overlay 叠加图 / edge 边缘 GeoTIFF 已不再生成 (前端地图容器用图层透明度叠加实现)
        #   如需变化边缘描线, AI 可单独调用 mask_tools.overlay_edge_on_image 工具

        # ★ 新产物: Shapefile (变化图斑, 供 GIS 直接打开) — 保留为目录
        shp_path = result.get("shp_path")
        shp_dir_path = result.get("shp_zip_path")  # 现为 SHP 目录而非 zip 文件
        shp_exists = shp_dir_path and Path(shp_dir_path).is_dir()

        geo_summary = (
            f"\n坐标系: {'已带 (GeoTIFF 掩膜可被 GIS 直接打开)' if has_crs else '无 (源影像无 CRS, 仅像素坐标)'}\n"
            f"掩膜 GeoTIFF (前端地图叠加层): {'已生成' if mask_tif_url else '未生成'}\n"
            f"GeoJSON 矢量变化图斑: 不加入工作区 (可下载/叠加到地图)\n"
            f"Shapefile (GIS 可直接打开): {'已生成' if shp_exists else '未生成'}\n"
        )

        summary = (
            f"已完成 {Path(t1_path).stem} 与 {Path(t2_path).stem} 的变化检测"
            f"（类别: {cls_desc}, 耗时 {elapsed:.1f}s）。\n"
            f"变化区域统计:\n"
            f"  总变化区域: {stats.get('total_regions', 0)} 个\n"
            f"  总变化面积: {stats.get('changed_pixels', 0)} 像素"
            f" ({stats.get('changed_percent', 0)}%)\n"
            f"各类别详情:\n"
            f"{per_class_text}\n"
            f"{vector_summary}"
            f"{change_type_summary}"
            f"{geo_summary}"
            f"请根据以上统计对变化检测结果进行综合描述，"
            f"重点说明变化业务类型及依据、哪些地物类型变化最显著、变化区域的面积占比等。"
        )

        # ★ 工作区布局: T1 原图层 (底图) + 变化面 (GeoServer WMS) + 边缘线 (矢量描边)
        #   面 SHP 与边缘线 SHP 颜色统一为 #2C6FBD (GeoAI Copilot 标题色)。
        polygon_layer = _build_polygon_layer_params(result, cls_tag)
        edge_layer = _build_edge_layer_params(result, cls_tag, None)
        # ★ 原图底图层 (T1): 同步上传到 GeoServer → 前端从 WMS 加载 (支持属性查询 GetFeatureInfo)
        base_layer = _build_base_layer_params(t1_path, cls_tag)

        params = {
            # ★ 主图 = T1 输入原图 URL (地图容器据此加载底图层 + 查坐标定位)
            "image_url": input_image_url,
            "caption": f"变化检测结果: {stem}",
            # ★ 变化面: 发布到 GeoServer (polygon_layer)
            "polygon_layer": polygon_layer if polygon_layer else None,
            # ★ 边缘线: GeoServer WMS 图层 (edge_layer)
            "edge_layer": edge_layer if edge_layer else None,
            # ★ 原图底图层: GeoServer WMS (支持属性查询; 不可用时前端回退到本地文件)
            "base_layer": base_layer if base_layer else None,
            # ★ 双时相输入影像 URL (供前端展示原图, 与结果对比)
            "input_image_url": input_image_url,
            "input_image_url_t2": input_image_url_t2,
            "input_caption": f"输入影像 T1: {Path(t1_path).stem}",
            "input_caption_t2": f"输入影像 T2: {Path(t2_path).stem}",
            "input_image_path": t1_path,  # ★ 宿主机路径, 前端据此查坐标显示
            # ★ 图注: 颜色→类别 (与 PNG 颜色严格一致)
            "legend": legend,
            # ★ 是否带 CRS (前端据此决定真实定位 or 拒绝渲染)
            "has_crs": has_crs,
            "vector_url": vector_url,
        }
        # ★ 掩膜 GeoTIFF 不再自动渲染叠加 (仅保留为可下载产物); artifact_path 指向掩膜供下载
        if mask_tif_path:
            params["artifact_path"] = mask_tif_path

        return {
            "type": "frontend_action",
            "action": "render_image",
            "instruction": {
                "type": "render_image",
                "action": "render_image",
                "params": params,
            },
            "wait_for_result": False,
            "description": f"⏳ 变化检测完成, 正在加载结果 ({elapsed:.1f}s)",
            "summary": summary,
            "data": {
                "t1_path": t1_path,
                "t2_path": t2_path,
                "classes": cls_desc,
                "elapsed": round(elapsed, 1),
                "stats": stats,
                "vector_stats": vector_stats,
                "vector_path": vector_path,
                "vector_url": vector_url,
                "change_type": change_type_info,
                "has_crs": has_crs,
                "mask_tif_path": mask_tif_path,
                "mask_tif_url": mask_tif_url,
                "polygon_layer": polygon_layer,
                "base_layer": base_layer,
                "shp_path": shp_path,
                "shp_dir_path": shp_dir_path,
            },
        }
    except Exception as e:
        logger.error(f"[SamSeg] detect_change 失败 {t1_path} vs {t2_path}: {e}", exc_info=True)
        return {"type": "error", "msg": f"变化检测执行失败: {e}"}


# ★ C5 清理: 训练数据集生成脚本, 对"在线分割/变化检测"业务无用, 反而诱导 LLM 误选。
#   默认不向 LLM 暴露 (移除 @register_tool); 保留 @tool + 函数体供需要时手动调用。
@tool
def prepare_cd_dataset_by_fishnet(
    t1_path: str = "",
    t2_path: str = "",
    output_dir: str = "",
    tile_size: int = 512,
    overwrite: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    对双时相 GeoTIFF 做渔网切片并输出为 train/val/test × t1/t2 目录。

    固定规则：
    - 无重叠
    - 随机种子 42
    - train:val:test = 7:1:2
    - 默认保留源坐标系

    入参:
        - t1_path (str): 前一时相 GeoTIFF 路径；留空则默认使用 SamSeg resources/XD2023.tif
        - t2_path (str): 后一时相 GeoTIFF 路径；留空则默认使用 SamSeg resources/XD2025.tif
        - output_dir (str): 输出根目录；留空则输出到安全目录 resources/cdcd_generated
        - tile_size (int): 瓦片边长，默认 512
        - overwrite (bool): 是否覆盖输出目录已有切片，默认否
        - dry_run (bool): 是否仅计算划分方案不写文件，默认否
    出参:
        - success: 返回网格规模、train/val/test 数量、输出目录
        - error: 参数或写出失败时返回错误信息
    """
    from backend.model.SamSeg.fishnet import (
        DEFAULT_CDCD_OUTPUT_DIR,
        DEFAULT_T1_SRC,
        DEFAULT_T2_SRC,
        fishnet_split_bitemporal_dataset,
        format_split_summary,
    )

    try:
        result = fishnet_split_bitemporal_dataset(
            t1_path=t1_path or DEFAULT_T1_SRC,
            t2_path=t2_path or DEFAULT_T2_SRC,
            output_dir=output_dir or DEFAULT_CDCD_OUTPUT_DIR,
            tile_size=tile_size,
            seed=42,
            overlap=0,
            preserve_crs=True,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        summary = (
            f"双时相渔网分割已{'完成' if not dry_run else '完成 dry-run 计算'}。"
            f"{format_split_summary(result)}；输出目录: {result['output_dir']}"
        )
        return {
            "type": "success",
            "summary": summary,
            "data": result,
        }
    except Exception as e:
        logger.error(f"[SamSeg] prepare_cd_dataset_by_fishnet 失败: {e}", exc_info=True)
        return {"type": "error", "msg": f"渔网分割失败: {e}"}


# ==================== 视觉理解 (影像解译分析) ====================
# 图片后缀 → MIME 类型 (base64 data URI 用)
_VISION_IMG_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "tif": "image/tiff", "tiff": "image/tiff", "bmp": "image/bmp",
    "webp": "image/webp",
}
# 单张图大小上限 (base64 编码前), 避免请求过大失败
_VISION_MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _image_to_data_uri(image_path: str) -> Optional[str]:
    """
    读取本地图片文件 → base64 data URI (data:image/png;base64,...)。
    文件不存在/过大/读取失败 → 返回 None。
    """
    import base64
    p = Path(image_path)
    if not p.is_file():
        return None
    try:
        data = p.read_bytes()
    except Exception as e:
        logger.warning(f"[Vision] 读取图片失败: {image_path} ({e})")
        return None
    if len(data) > _VISION_MAX_IMAGE_BYTES:
        logger.warning(
            f"[Vision] 图片过大 ({len(data)//1024//1024}MB > "
            f"{_VISION_MAX_IMAGE_BYTES//1024//1024}MB): {image_path}"
        )
        return None
    ext = p.suffix.lstrip(".").lower()
    mime = _VISION_IMG_MIME.get(ext, "image/png")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


@register_tool("vlm")
@tool
def understand_image(image_path: str, question: str = "请描述这张影像中的地物内容和空间分布特征。") -> Dict[str, Any]:
    """
    对一张遥感影像进行视觉理解与解译分析: 真正"看图"回答用户关于影像内容的问题。

    ★ 适用场景 (影像理解类, 与分割/变化检测区分):
      - "这张图里有什么/描述一下/这是什么地物/分析一下这张影像"
      - 识别地物类型、空间分布、色调纹理、区域用途等
      - 任何需要"看懂"影像内容而非"按类别画掩膜"的问题
    ★ 不适用场景: 明确要求分割/提取/标注地物 → 用 segment_image; 双时相变化对比 → 用 detect_change。
    ★ 实现: 内部调用 qwen 视觉多模态模型 (settings.vision_model), 把图片以 base64 形式喂给模型,
      返回模型对 question 的视觉理解文本。
    ★ 耗时: 通常 5~30 秒 (取决于图片大小和问题复杂度)。

    入参:
        - image_path (str): 影像绝对路径 (用户上传后获得)
        - question (str): 想问的问题, 默认"描述地物内容和空间分布"。
          可自定义, 如 "图中哪些区域是建筑?" "这条河流的形态如何?"
    出参: {type, summary} summary 为模型的视觉解译结论文本, 直接呈现给用户
    """
    if not Path(image_path).is_file():
        return {"type": "error", "msg": f"图片不存在: {image_path}"}

    data_uri = _image_to_data_uri(image_path)
    if not data_uri:
        return {"type": "error", "msg": f"图片无法读取或过大 (>{_VISION_MAX_IMAGE_BYTES//1024//1024}MB): {image_path}"}

    # 延迟导入避免循环依赖 (llm_client ↔ tools 链路)
    from backend.model.llm_client import create_llm
    from langchain_core.messages import HumanMessage

    # 视觉理解系统指令: 引导模型以遥感解译分析师的视角回答
    vision_sys = (
        "你是遥感影像解译分析师。请基于提供的影像进行专业解译分析, "
        "识别地物类型(建筑/道路/水体/植被/裸地/农田等)、描述空间分布与形态纹理特征, "
        "给出客观、准确的解译结论。用中文回答。"
    )

    # 构造多模态消息: [文本问题 + 图片]
    human = HumanMessage(content=[
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": data_uri}},
    ])

    # 用视觉模型调用 (不带工具绑定, 纯文本回复)
    vision_llm = create_llm(model=settings.vision_model, temperature=0.3)
    from langchain_core.messages import SystemMessage
    messages = [SystemMessage(content=vision_sys), human]

    logger.info(f"[Vision] understand_image 调用: img={Path(image_path).name} question={question[:40]}")
    try:
        resp = vision_llm.invoke(messages)
        answer = resp.content if hasattr(resp, "content") else str(resp)
        answer = answer.strip()
        logger.info(f"[Vision] understand_image 完成, 回复长度={len(answer)}")
        return {
            "type": "success",
            "summary": answer,
        }
    except Exception as e:
        logger.error(f"[Vision] understand_image 失败 {image_path}: {e}", exc_info=True)
        return {"type": "error", "msg": f"视觉理解失败: {e}"}
