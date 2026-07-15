"""
统一图层下载工具 (Model 层 / 工具箱)
- 入参: source (GeoServer 图层名 或 本地文件路径), output_format (geotiff/geojson/shapefile)
- 方法: 统一经 GeoServer 下载 (WMS/WFS) —— 本地文件先发布到 GeoServer 再下载
- 出参: 下载结果 dict (走 _result.build_download_action 标准结构)

★ v2.6 下载统一改造:
  原 3 个并行下载工具 (download_geoserver_layer / download_raster_layer / download_shapefile_layer)
  功能高度重叠, LLM 难以选择。现归一为单一工具 download_layer:
    - source 是 GeoServer 图层名 (已在 GeoServer) → 直接经 WMS/WFS 下载
    - source 是本地文件路径 (AI 产物 / 上传文件) → 按类型先发布到 GeoServer → 再经 WMS/WFS 下载
  统一原则: 所有下载都走 GeoServer 这一个出口, 坐标系一致、格式规范、坐标系由 GeoServer 保证。

  旧的 data_tools.download_raster_layer / download_shapefile_layer 已移除 @register_tool
  (保留函数体供向后兼容引用, 但不再向 LLM 暴露)。
"""
import logging
import os
from typing import Dict, Any
from pathlib import Path
from langchain_core.tools import tool
from backend.model.tools.tool_registry import register_tool
from backend.model.tools._paths import session_subdirs
from backend.model.tools._result import build_error, build_download_action
from backend.config import settings

logger = logging.getLogger(__name__)

# 专用工作空间: 本地文件统一下载时发布到这里 (与正式业务图层隔离, 避免污染主工作空间)
_DOWNLOADS_WORKSPACE = "downloads"

_gs = None


def _get_gs():
    global _gs
    if _gs is None:
        try:
            from backend.data import geoserver_client as _gs_mod
            _gs = _gs_mod if _gs_mod.geoserver_available() else None
        except Exception:
            _gs = None
    return _gs


def _get_download_dir() -> Path:
    """
    GeoServer 下载目录 (目录顺序统一为 {proj}/{conv})。
    路径: agent-files/geoserver/generate/{project_id}/{conversation_id}/
    """
    proj, conv = session_subdirs()
    d = Path(settings.geoserver_download_root).resolve() / proj / conv
    d.mkdir(parents=True, exist_ok=True)
    return d


# 格式 → (扩展名, 底层下载函数名, 协议) 映射
_FORMAT_MAP = {
    "geotiff":   ("tif",       "download_raster",    "WMS"),
    "geojson":   ("geojson",   "download_geojson",   "WFS"),
    "shapefile": ("zip",       "download_shapefile", "WFS"),
}


def _is_local_file_path(source: str) -> bool:
    """判断 source 是本地文件路径 (而非 GeoServer 图层名)。
    判据: 存在该文件 (绝对路径) → 是本地文件; 否则视为图层名。
    """
    if not source:
        return False
    try:
        return os.path.isfile(os.path.normpath(source))
    except Exception:
        return False


def _publish_local_file_to_geoserver(file_path: str, gs) -> Dict[str, Any]:
    """把本地文件发布到 GeoServer 专用 downloads 工作空间, 返回 {layer_name, workspace} 或 raise。
    按扩展名路由: .tif/.tiff/.cog → upload_raster; .shp → upload_shapefile; .geojson/.json → publish_geojson_layer。
    """
    ext = os.path.splitext(file_path)[1].lower()
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    # ASCII 化图层名 (GeoServer store 名不支持中文等特殊字符)
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in base_name) or "layer"

    if ext in (".tif", ".tiff", ".cog"):
        result = gs.upload_raster(file_path, safe_name, _DOWNLOADS_WORKSPACE)
    elif ext == ".shp":
        result = gs.upload_shapefile(file_path, safe_name, _DOWNLOADS_WORKSPACE)
    elif ext in (".geojson", ".json"):
        # GeoJSON: 复用 data_tools.publish_geojson_layer (GeoJSON→SHP→上传)
        from backend.model.tools.data_tools import publish_geojson_layer
        tool_result = publish_geojson_layer.invoke({
            "geojson_path": file_path,
            "layer_name": safe_name,
            "workspace": _DOWNLOADS_WORKSPACE,
            "show_immediately": False,
        })
        if tool_result.get("type") == "success":
            d = tool_result.get("data", {})
            return {"layer_name": d.get("layer_name", safe_name), "workspace": d.get("workspace", _DOWNLOADS_WORKSPACE)}
        raise RuntimeError(tool_result.get("msg", "GeoJSON 发布失败"))
    else:
        raise ValueError(f"不支持的文件类型: {ext} (支持 .tif/.tiff/.cog/.shp/.geojson)")

    if result.get("status") == "success":
        return {"layer_name": result.get("layer_name", safe_name),
                "workspace": result.get("workspace", _DOWNLOADS_WORKSPACE)}
    raise RuntimeError(result.get("msg", f"发布失败: {file_path}"))


@register_tool("geoserver")
@tool
def download_layer(
    source: str,
    output_format: str = "auto",
    workspace: str = "",
) -> Dict[str, Any]:
    """
    下载图层 (统一经 GeoServer: 本地文件先发布, 再通过 GeoServer WMS/WFS 下载)。

    ★ 这是唯一的下载工具。无论数据来源是 GeoServer 已有图层, 还是本地/AI 产物文件,
      都统一走 GeoServer 这一个出口下载, 保证坐标系与格式一致。

    入参:
        - source (str): 数据来源。两种形态:
            1) GeoServer 图层名: 裸名(如 "my_layer")或全名(如 "workspace:my_layer") — 直接下载
            2) 本地文件绝对路径: .tif/.tiff/.cog/.shp/.geojson — 先发布到 GeoServer 再下载
        - output_format (str): 输出格式。auto(按图层类型自动) / geotiff(栅格) / geojson / shapefile(矢量)
        - workspace (str): 工作空间 (仅 source 为图层名时有用, 留空自动搜索; 含冒号则忽略)

    触发场景: 用户说"下载 <图层名/文件路径>"、"导出 <图层名>"、"把分割结果下载下来"等。
    """
    gs = _get_gs()
    if not gs:
        return build_error("GeoServer 不可用, 无法下载图层 (请检查 GeoServer 服务是否启动)")

    downloads_dir = _get_download_dir()
    proj, conv = session_subdirs()

    # ---- Step 1: 确定 layer_name + workspace (处理"本地文件先发布") ----
    try:
        if _is_local_file_path(source):
            # 本地文件: 先发布到 GeoServer 专用 workspace
            logger.info(f"[download_layer] 本地文件, 先发布到 GeoServer: {source}")
            pub = _publish_local_file_to_geoserver(source, gs)
            layer_name = pub["layer_name"]
            ws = pub["workspace"]
            published_from_local = True
        else:
            # GeoServer 图层名: 直接使用
            layer_name = source
            ws = workspace if workspace else None
            published_from_local = False
    except Exception as e:
        return build_error(f"下载失败 (准备阶段): {e}")

    # ---- Step 2: 确定实际下载格式 (auto → 按图层类型) ----
    full_name = f"{ws}:{layer_name}" if ws else layer_name
    if output_format == "auto":
        try:
            ltype = gs.get_layer_type(full_name)
        except Exception:
            ltype = "raster"  # 检测失败默认尝试栅格
        actual_format = "geojson" if ltype == "vector" else "geotiff"
    else:
        actual_format = output_format

    ext, func_name, protocol = _FORMAT_MAP.get(actual_format, ("tif", "download_raster", "WMS"))
    safe_name = layer_name.replace(":", "_")
    output_path = str(downloads_dir / f"{safe_name}.{ext}")

    # ---- Step 3: 执行下载 (统一经 GeoServer WMS/WFS) ----
    download_fn = getattr(gs, func_name, None)
    if download_fn is None:
        return build_error(f"不支持的下载格式: {actual_format}")

    try:
        path = download_fn(layer_name, ws, output_path=output_path)
    except Exception as e:
        path = None
        logger.warning(f"[download_layer] 下载异常 [{full_name}]: {e}")

    # auto 模式下栅格失败 → 回退 GeoJSON (矢量)
    if not path and output_format == "auto" and actual_format == "geotiff":
        logger.info(f"[download_layer] 栅格下载失败 [{full_name}], 回退 GeoJSON")
        actual_format = "geojson"
        ext = "geojson"
        output_path = str(downloads_dir / f"{safe_name}.{ext}")
        try:
            path = gs.download_geojson(layer_name, ws, output_path=output_path)
        except Exception:
            path = None

    if not path:
        return build_error(
            f"下载失败: {full_name} 不存在或 {protocol} 请求失败。"
            f"{' (注: 本地文件可能发布失败)' if published_from_local else ''}"
        )

    # ---- Step 4: 构造统一返回结构 (走 _result.build_download_action) ----
    size = os.path.getsize(path)
    download_url = f"/api/v1/download/geoserver/generate/{proj}/{conv}/{safe_name}.{ext}"

    return build_download_action(
        params={
            "layer_name": layer_name,
            "file_type": ext,
            "download_url": download_url,
            "file_size": size,
        },
        summary=f"图层 {layer_name} 下载完成 ({actual_format}), {size // 1024}KB"
                + (f" [本地文件已发布到 {ws} 后下载]" if published_from_local else ""),
        data={
            "artifact_path": path,          # 规范命名
            "artifact_url": download_url,   # 规范命名
            "format": actual_format,
            "layer_name": layer_name,
            "workspace": ws,
            "file_size": size,
            "published_from_local": published_from_local,
        },
        description=f"下载完成: {layer_name}.{ext} ({size // 1024}KB)",
    )
