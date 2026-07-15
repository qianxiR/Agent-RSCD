"""
数据工具集 — GeoServer 操作 + 数据库操作 (Model 层 / 工具箱)

GeoServer:
  1. list_geoserver_services  — 查询 GeoServer 服务与图层列表
  2. download_raster_layer   — 下载栅格影像 (显式栅格下载, 仍可用)
  3. upload_raster_layer     — 上传栅格影像
  4. download_shapefile_layer — 下载矢量 Shapefile (显式矢量下载, 仍可用)

注意: download_geoserver_layer (analysis_tools.py) 已支持 auto/geotiff/geojson/shapefile
多格式下载, 自动检测图层类型。上述 download_raster_layer / download_shapefile_layer
保留用于显式指定栅格/矢量的场景。

★ v2.3: 文件保存到 agent-files/geoserver/generate/{project_id}/{conversation_id}/

数据库:
  5. list_database_tables    — 查询数据库表列表
  6. read_table_statistics   — 读取表数据并统计
  7. query_and_render_table  — 查询表数据并渲染为前端表格
"""
import json
import os
import re
import logging
import hashlib
import subprocess
from pathlib import Path
from typing import Dict, Any

from langchain_core.tools import tool
from backend.model.tools.tool_registry import register_tool
from backend.model.tools._paths import session_subdirs
from backend.model.tools._result import build_success, build_error, build_frontend_action
from backend.config import settings

logger = logging.getLogger(__name__)


def _get_download_dir() -> Path:
    """★ v2.4: GeoServer 下载目录 → agent-files/geoserver/generate/{project_id}/{conversation_id}/"""
    proj, conv = session_subdirs()
    d = Path(settings.geoserver_download_root).resolve() / proj / conv
    d.mkdir(parents=True, exist_ok=True)
    return d


def _derive_publish_layer_name(geojson_path: Path, requested_name: str = "") -> str:
    """
    入参:
        - geojson_path: 当前待发布 GeoJSON 路径, 用于读取原始文件名。
        - requested_name: 用户显式指定的图层名; 留空时退回文件名。
    方法:
        - 先做 ASCII 安全化, 仅保留 GeoServer/Shapefile 友好的字符。
        - 若原名含中文等非 ASCII 字符导致清洗后高度同质化, 则追加短哈希避免重名碰撞。
        - 最终统一限制长度, 兼顾 Shapefile/GeoServer 命名稳定性。
    出参:
        - 返回可安全发布的 ASCII 图层名, 保证非空且在同目录下稳定可复现。
    """
    raw_name = (requested_name or geojson_path.stem).strip()
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", raw_name).strip("._-")
    short_hash = hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:8]

    if not sanitized:
        sanitized = f"layer_{short_hash}"
    elif re.fullmatch(r"_+", sanitized):
        sanitized = f"layer_{short_hash}"
    elif requested_name.strip() == "" and sanitized != raw_name:
        # 做什么: 文件名被清洗过时追加哈希; 为什么: 中文名常被压成一串下划线, 容易撞名。
        sanitized = f"{sanitized[:40]}_{short_hash}".strip("._-")

    return sanitized[:63] or f"layer_{short_hash}"


def _write_shapefile_from_geojson(geojson_path: Path, shp_base: Path) -> Dict[str, Any]:
    """
    入参:
        - geojson_path: 源 GeoJSON 文件绝对路径。
        - shp_base: 目标 .shp 绝对路径, 父目录应已存在。
    方法:
        - 优先在宿主机用 geopandas 直接转换, 依赖最少且速度快。
        - 宿主机缺 geopandas 时, 回退到沙盒镜像里执行转换, 复用已存在的 GIS 运行时。
        - 两条路径都必须真实生成非空 .shp 文件, 否则视为失败。
    出参:
        - {ok, engine, feature_count} 或 {ok: False, msg}
    """
    try:
        import geopandas as gpd

        gdf = gpd.read_file(str(geojson_path))
        if gdf is None or len(gdf) == 0:
            return {"ok": False, "msg": "GeoJSON 为空, 无要素可发布"}
        gdf.to_file(str(shp_base), driver="ESRI Shapefile", encoding="utf-8")
        if not shp_base.is_file() or shp_base.stat().st_size == 0:
            return {"ok": False, "msg": "Shapefile 写入失败 (.shp 未生成)"}
        return {"ok": True, "engine": "host-geopandas", "feature_count": len(gdf)}
    except ImportError:
        return _write_shapefile_from_geojson_in_sandbox(geojson_path, shp_base)
    except Exception as e:
        return {"ok": False, "msg": f"GeoJSON 读取/转换失败: {e}"}


def _write_shapefile_from_geojson_in_sandbox(geojson_path: Path, shp_base: Path) -> Dict[str, Any]:
    """
    入参:
        - geojson_path: 源 GeoJSON 文件绝对路径。
        - shp_base: 目标 .shp 绝对路径。
    方法:
        - 使用 agent-sandbox 镜像临时运行 geopandas, 将源文件目录挂到 /input,
          将目标目录挂到 /output, 在容器里完成 GeoJSON → Shapefile 转换。
        - 这样即使宿主机 Python 环境缺少 geopandas, 仍能稳定复用沙盒 GIS 依赖。
    出参:
        - {ok, engine, feature_count} 或 {ok: False, msg}
    """
    input_dir = geojson_path.parent.resolve()
    output_dir = shp_base.parent.resolve()
    script = (
        "import geopandas as gpd\n"
        f"gdf = gpd.read_file(r'/input/{geojson_path.name}')\n"
        "print(len(gdf))\n"
        f"gdf.to_file(r'/output/{shp_base.name}', driver='ESRI Shapefile', encoding='utf-8')\n"
    )
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{input_dir}:/input",
        "-v", f"{output_dir}:/output",
        settings.sandbox_docker_image,
        "python3", "-c", script,
    ]
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(settings.sandbox_exec_timeout, 120),
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "msg": "未找到 docker 命令, 无法使用沙盒转换 GeoJSON"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "沙盒转换 GeoJSON 超时"}
    except Exception as e:
        return {"ok": False, "msg": f"沙盒转换异常: {e}"}

    if completed.returncode != 0:
        err = completed.stderr.decode("utf-8", errors="replace").strip()
        return {"ok": False, "msg": f"沙盒转换失败: {err or f'退出码 {completed.returncode}'}"}

    if not shp_base.is_file() or shp_base.stat().st_size == 0:
        return {"ok": False, "msg": "沙盒转换后未生成 .shp 文件"}

    stdout = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
    feature_count = 0
    if stdout:
        try:
            feature_count = int(stdout[0].strip())
        except Exception:
            feature_count = 0
    return {"ok": True, "engine": "sandbox-geopandas", "feature_count": feature_count}

# ============================================================
#  GeoServer 工具
# ============================================================

@register_tool("geoserver")
@tool
def list_geoserver_services() -> Dict[str, Any]:
    """
    查询 GeoServer 服务器上的所有工作空间、图层和 OGC 服务端点。

    入参: 无
    出参: {workspaces: [...], services: {WMS, WFS, WCS, REST}}
    """
    try:
        from backend.data import geoserver_client as gs
        if not gs.geoserver_available():
            return {"type": "error", "msg": "GeoServer 不可用, 请检查服务是否启动"}
        data = gs.list_services()
        total = sum(ws["layer_count"] for ws in data["workspaces"])

        # 构造表格数据：工作空间 → 图层列表
        headers = ["工作空间", "图层数", "图层列表"]
        rows = []
        for ws in data["workspaces"]:
            layer_names = ", ".join(ws.get("layers", [])[:10])
            if len(ws.get("layers", [])) > 10:
                layer_names += f" ... 共{len(ws['layers'])}个"
            rows.append([ws["workspace"], str(ws["layer_count"]), layer_names])

        # 服务端点表格
        svc_headers = ["服务", "端点"]
        svc_rows = [[k, v] for k, v in data.get("services", {}).items()]

        return build_frontend_action(
            action="render_table",
            params={
                "table_name": f"GeoServer 概览 ({len(data['workspaces'])} 个工作空间, {total} 个图层)",
                "headers": headers,
                "rows": rows,
            },
            summary=f"GeoServer: {len(data['workspaces'])} 工作空间, {total} 图层",
            description=f"GeoServer: {len(data['workspaces'])} 工作空间, {total} 图层",
            data={"workspaces": data["workspaces"], "services": data["services"], "total_layers": total},
        )
    except Exception as e:
        return {"type": "error", "msg": str(e)}


# ★ v2.6 下载统一: 此工具已并入 analysis_tools.download_layer, 不再 @register_tool 暴露给 LLM。
#   保留 @tool + 函数体供向后兼容 (download_layer 内部直接调 gs.download_raster, 无需此函数)。
@tool
def download_raster_layer(layer_name: str, workspace: str = "") -> Dict[str, Any]:
    """
    从 GeoServer 下载指定栅格图层为 GeoTIFF 文件。

    入参:
        - layer_name (str): 图层名称, 支持裸名 (如 "my_layer") 或全名 (如 "workspace:my_layer")
        - workspace (str): 工作空间名称, 留空则自动搜索所有工作空间；若 layer_name 含冒号则此参数被忽略
    出参: {status, file_path, file_size}
    """
    try:
        from backend.data import geoserver_client as gs
        if not gs.geoserver_available():
            return {"type": "error", "msg": "GeoServer 不可用"}
        ws = workspace if workspace else None

        # ★ v2.3: 指定输出路径到统一存储
        d = _get_download_dir()
        proj, conv = session_subdirs()
        safe_name = layer_name.replace(":", "_")
        output_path = str(d / f"{safe_name}.tif")
        path = gs.download_raster(layer_name, ws, output_path=output_path)
        if path:
            size = os.path.getsize(path)
            download_url = f"/api/v1/download/geoserver/generate/{proj}/{conv}/{safe_name}.tif"
            return {
                "type": "frontend_action",
                "action": "download_file",
                "instruction": {
                    "type": "download",
                    "action": "download_file",
                    "params": {
                        "layer_name": layer_name,
                        "file_type": "tif",
                        "local_path": path,
                        "download_url": download_url,
                        "file_size": size,
                    },
                },
                "wait_for_result": False,
                "description": f"下载完成: {layer_name}.tif",
                "summary": f"图层 {layer_name} 下载完成, 文件大小 {size // 1024}KB, 路径: {path}",
            }
        return {"type": "error", "msg": f"下载失败: 图层 {layer_name} 不存在或不可访问"}
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("geoserver")
@tool
def upload_raster_layer(file_path: str, layer_name: str = "", workspace: str = "") -> Dict[str, Any]:
    """
    将本地 GeoTIFF 文件上传到 GeoServer 并发布为图层。

    入参:
        - file_path (str): 本地 GeoTIFF 文件的绝对路径
        - layer_name (str): 发布后的图层名称（即保存的影像名称）, 留空则自动使用文件名
        - workspace (str): 目标工作空间, 留空则使用 GeoServer 默认工作空间, 不存在则自动创建
    出参: {status, layer_name, workspace, wms_url}
    """
    try:
        from backend.data import geoserver_client as gs
        if not gs.geoserver_available():
            return {"type": "error", "msg": "GeoServer 不可用"}
        name = layer_name.strip() if layer_name else ""
        ws = workspace if workspace else None
        result = gs.upload_raster(file_path, name or None, ws)
        if result.get("status") == "success":
            # ★ Phase A: 上传成功后登记影像元数据 (#2/#4), 失败不阻塞
            _safe_register_image_meta(file_path, result.get("layer_name", ""), result.get("workspace", ""))
            return {
                "type": "success",
                "summary": f"上传成功! 图层已发布: {result['layer_name']} (工作空间: {result['workspace']})",
                "data": result,
            }
        return {"type": "error", "msg": result.get("msg", "上传失败")}
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("geoserver")
@tool
def upload_shapefile_layer(file_path: str, layer_name: str = "", workspace: str = "") -> Dict[str, Any]:
    """
    将本地 Shapefile 矢量数据上传到 GeoServer 并发布为图层。

    入参:
        - file_path (str): Shapefile 的 .shp 文件绝对路径
        - layer_name (str): 发布后的图层名称（即保存的矢量名称）, 留空则自动使用文件名
        - workspace (str): 目标工作空间, 留空则使用 GeoServer 默认工作空间, 不存在则自动创建
    出参: {status, layer_name, workspace, wfs_url}
    """
    try:
        from backend.data import geoserver_client as gs
        if not gs.geoserver_available():
            return {"type": "error", "msg": "GeoServer 不可用"}
        name = layer_name.strip() if layer_name else ""
        ws = workspace if workspace else None
        result = gs.upload_shapefile(file_path, name or None, ws)
        if result.get("status") == "success":
            skip_msg = " (已存在, 跳过)" if result.get("skipped") else ""
            # ★ Phase A: 上传成功后登记矢量图层元数据 (#2)
            _safe_register_vector_meta(file_path, result.get("layer_name", ""), result.get("workspace", ""), kind="administrative")
            return {
                "type": "success",
                "summary": f"上传成功! 矢量图层: {result['layer_name']} (工作空间: {result['workspace']}){skip_msg}",
                "data": result,
            }
        return {"type": "error", "msg": result.get("msg", "上传失败")}
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("geoserver")
@tool
def publish_geojson_layer(
    geojson_path: str,
    layer_name: str = "",
    workspace: str = "",
    show_immediately: bool = True,
) -> Dict[str, Any]:
    """
    将本地 GeoJSON 矢量发布为 GeoServer 图层, 并可选立即在前端显示。

    ★ 一站式解决"GeoJSON → GeoServer → 前端可视化"链路, 不需要手动转 Shapefile / 上传 / 显示三步。
      内部流程: geopandas 读 GeoJSON → 写临时 Shapefile (ASCII 文件名, 避免 Fiona 中文问题)
      → 调 upload_shapefile 发布到 GeoServer → 可选触发前端显示。
    ★ 适用场景: 分割/变化检测/连通域分析产出的 GeoJSON 需要在界面可视化时。

    入参:
        - geojson_path (str): GeoJSON 文件绝对路径 (segment_image/detect_change/analyze_connected_components 的产物)
        - layer_name (str): 发布后的图层名称, 留空则用 GeoJSON 文件名 (中文会被 ASCII 化)
        - workspace (str): 目标工作空间, 留空用默认
        - show_immediately (bool): 是否立即推 frontend_action 让前端显示该图层, 默认 True
    出参: {type, summary, data:{layer_name, workspace, wfs_url, shp_path, frontend_action?}}

    ★ 成功契约:
      1. type == "success" (而非 "error")
      2. data.layer_name 非空 + data.workspace 非空
      3. 图层在 GeoServer 中真实存在 (upload_shapefile 返回 status==success)
      show_immediately=True 时还会附带 frontend_action 让前端立即加载
    """
    import tempfile
    from pathlib import Path

    geojson_p = Path(geojson_path)
    if not geojson_p.is_file():
        return {"type": "error", "msg": f"GeoJSON 不存在: {geojson_path}"}
    if geojson_p.suffix.lower() not in (".geojson", ".json"):
        return {"type": "error", "msg": f"不是 GeoJSON 文件: {geojson_path}"}

    # 1) GeoJSON → 临时 Shapefile (优先宿主机, 失败时回退沙盒)
    name = _derive_publish_layer_name(geojson_p, layer_name)

    # 写临时 Shapefile (ASCII 路径, 避免 Fiona 中文静默失败)
    tmp_dir = tempfile.mkdtemp(prefix="geojson_pub_")
    shp_base = Path(tmp_dir) / f"{name}.shp"
    convert_result = _write_shapefile_from_geojson(geojson_p, shp_base)
    if not convert_result.get("ok"):
        return {"type": "error", "msg": convert_result.get("msg", "Shapefile 转换失败")}

    # 2) 调 upload_shapefile 发布到 GeoServer
    from backend.data import geoserver_client as gs
    if not gs.geoserver_available():
        return {"type": "error", "msg": "GeoServer 不可用"}

    ws = workspace.strip() if workspace.strip() else None
    result = gs.upload_shapefile(str(shp_base), name, ws)

    if result.get("status") != "success":
        return {"type": "error", "msg": f"GeoServer 发布失败: {result.get('msg', '未知错误')}"}

    published_name = result.get("layer_name", name)
    published_ws = result.get("workspace", ws or "default")

    # 3) 登记 + 可选触发前端显示
    try:
        _safe_register_vector_meta(str(geojson_p), published_name, published_ws, kind="analytical")
    except Exception:
        pass

    response = {
        "type": "success",
        "summary": (
            f"GeoJSON 已发布为 GeoServer 图层: {published_ws}:{published_name}"
            f" ({convert_result.get('feature_count', 0)} 个要素)\n"
            f"转换引擎: {convert_result.get('engine', 'unknown')}\n"
            f"前端可视化: {'已触发显示' if show_immediately else '未触发 (show_immediately=False)'}"
        ),
        "data": {
            "layer_name": published_name,
            "workspace": published_ws,
            "feature_count": convert_result.get("feature_count", 0),
            "convert_engine": convert_result.get("engine", "unknown"),
            "wfs_url": result.get("wfs_url", ""),
            "shp_path": str(shp_base),
        },
    }

    if show_immediately:
        response["frontend_action"] = {
            "type": "render_image",
            "action": "render_image",
            "instruction": {
                "type": "layer_display",
                "action": "layer_display",
                "params": {
                    "layer_name": published_name,
                    "workspace": published_ws,
                    "bbox": None,
                },
            },
            "wait_for_result": False,
            "description": f"正在显示图层: {published_name}",
        }

    return response


@register_tool("geoserver")
@tool
def delete_geoserver_layer(
    layer_name: str,
    workspace: str = "",
    delete_store: bool = True,
) -> Dict[str, Any]:
    """
    删除 GeoServer 中已发布的图层 (★ v2.5 新增, 补齐 GeoServer 增删查改的"删")。

    ★ 自动检测图层类型 (栅格/矢量), 调用对应的 REST 端点删除:
      - 栅格: DELETE /workspaces/{ws}/coveragestores/{layer}?recurse=true
      - 矢量: DELETE /workspaces/{ws}/datastores/{layer}?recurse=true
      - 兜底: DELETE /layers/{ws}:{layer}

    入参:
        - layer_name (str): 图层名 (裸名, 如 "my_layer", 不要带 workspace 前缀)
        - workspace (str): 工作空间, 留空用默认 settings.geoserver_workspace
        - delete_store (bool): 是否同时删除底层 store (彻底删除含数据存储), 默认 True
          False = 只删发布层, 保留 store (可重新发布)

    出参: {type:"success", summary, data:{layer_name, workspace, layer_type}}
          失败: {type:"error", msg}

    ★ 成功契约: 删除后 layer_exists 返回 False (图层真实消失)。
    """
    from backend.data import geoserver_client

    if not layer_name:
        return build_error(msg="layer_name 不能为空")

    result = geoserver_client.delete_layer(
        layer_name=layer_name,
        workspace=workspace or None,
        delete_store=delete_store,
        recurse=delete_store,  # delete_store=True 时递归删除关联资源
    )

    if result.get("status") == "success":
        return build_success(
            summary=result.get("msg", f"已删除图层 {layer_name}"),
            data={
                "layer_name": result.get("layer_name", layer_name),
                "workspace": result.get("workspace", ""),
                "layer_type": result.get("layer_type", "unknown"),
                "delete_store": delete_store,
            },
        )
    return build_error(
        msg=result.get("msg", "删除失败"),
        data={
            "layer_name": result.get("layer_name", layer_name),
            "workspace": result.get("workspace", ""),
            "layer_type": result.get("layer_type", "unknown"),
        },
    )


# ★ v2.6 下载统一: 此工具已并入 analysis_tools.download_layer, 不再 @register_tool 暴露给 LLM。
#   保留 @tool + 函数体供向后兼容 (download_layer 内部直接调 gs.download_shapefile, 无需此函数)。
@tool
def download_shapefile_layer(layer_name: str, workspace: str = "") -> Dict[str, Any]:
    """
    从 GeoServer 下载指定矢量图层为 Shapefile ZIP 文件。

    入参:
        - layer_name (str): 图层名称, 支持裸名 (如 "my_layer") 或全名 (如 "workspace:my_layer")
        - workspace (str): 工作空间名称, 裸名时用于搜索, 留空则自动搜索所有工作空间
    出参: {status, file_path, file_size}
    """
    try:
        from backend.data import geoserver_client as gs
        if not gs.geoserver_available():
            return {"type": "error", "msg": "GeoServer 不可用"}
        ws = workspace if workspace else None

        # ★ v2.3: 指定输出路径到统一存储
        d = _get_download_dir()
        proj, conv = session_subdirs()
        safe_name = layer_name.replace(":", "_")
        output_path = str(d / f"{safe_name}.zip")
        path = gs.download_shapefile(layer_name, ws, output_path=output_path)
        if path:
            size = os.path.getsize(path)
            download_url = f"/api/v1/download/geoserver/generate/{proj}/{conv}/{safe_name}.zip"
            return {
                "type": "frontend_action",
                "action": "download_file",
                "instruction": {
                    "type": "download",
                    "action": "download_file",
                    "params": {
                        "layer_name": layer_name,
                        "file_type": "zip",
                        "local_path": path,
                        "download_url": download_url,
                        "file_size": size,
                    },
                },
                "wait_for_result": False,
                "description": f"矢量下载完成: {layer_name}.zip",
                "summary": f"矢量图层 {layer_name} 下载完成, 文件大小 {size // 1024}KB, 路径: {path}",
            }
        return {"type": "error", "msg": f"下载失败: 矢量图层 {layer_name} 不存在或不可访问"}
    except Exception as e:
        return {"type": "error", "msg": str(e)}


# ============================================================
#  数据库工具
# ============================================================

@register_tool("database")
@tool
def list_database_tables() -> Dict[str, Any]:
    """
    查询 PostgreSQL 数据库中所有用户表及其列数和大小。

    入参: 无
    出参: {tables: [{table_name, column_count, size}, ...]}
    """
    try:
        from backend.data import business_db as db
        if not db.db_available():
            return {"type": "error", "msg": "数据库不可用, 请检查连接配置"}
        tables = db.list_tables()
        headers = ["表名", "列数", "大小"]
        rows = [[t["table_name"], str(t["column_count"]), t["size"]] for t in tables]
        return build_frontend_action(
            action="render_table",
            params={
                "table_name": f"数据库表列表 (共 {len(tables)} 张)",
                "headers": headers,
                "rows": rows,
            },
            summary=f"数据库: {len(tables)} 张表",
            description=f"数据库: {len(tables)} 张表",
            data={"tables": tables, "total": len(tables)},
        )
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("database")
@tool
def read_table_statistics(table_name: str) -> Dict[str, Any]:
    """
    读取指定数据库表的统计信息 (行数、列信息、数值列的 min/max/avg)。

    入参:
        - table_name (str): 表名
    出参: 前端表格渲染 + data 包含完整统计
    """
    try:
        from backend.data import business_db as db
        if not db.db_available():
            return {"type": "error", "msg": "数据库不可用, 请检查连接配置"}
        stats = db.get_table_statistics(table_name)
        if stats is None:
            return {"type": "error", "msg": f"表 {table_name} 不存在或查询失败"}

        row_count = stats["row_count"]
        columns = stats["columns"]

        # 构造列信息表
        has_numeric = bool(stats.get("numeric_stats"))
        if has_numeric:
            headers = ["列名", "类型", "最小值", "最大值", "平均值"]
            num_stats = stats["numeric_stats"]
            rows = []
            for c in columns:
                ns = num_stats.get(c["name"])
                if ns:
                    rows.append([
                        c["name"], c["type"],
                        _fmt_num(ns.get("min")), _fmt_num(ns.get("max")), _fmt_num(ns.get("avg")),
                    ])
                else:
                    rows.append([c["name"], c["type"], "-", "-", "-"])
        else:
            headers = ["列名", "类型"]
            rows = [[c["name"], c["type"]] for c in columns]

        return build_frontend_action(
            action="render_table",
            params={
                "table_name": f"表 {table_name} ({row_count} 行, {len(columns)} 列)",
                "headers": headers,
                "rows": rows,
            },
            summary=f"表统计: {table_name} ({row_count} 行, {len(columns)} 列)",
            description=f"表统计: {table_name}",
            data=stats,
        )
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("database")
@tool
def query_and_render_table(table_name: str, limit: int = 20) -> Dict[str, Any]:
    """
    查询数据库表的实际数据并在前端渲染为表格。

    入参:
        - table_name (str): 表名
        - limit (int): 返回行数上限, 默认 20
    出参: frontend_action 指令, 前端渲染 Canvas 表格
    """
    try:
        from backend.data import business_db as db
        if not db.db_available():
            return {"type": "error", "msg": "数据库不可用, 无法查询表数据"}

        df = db.read_table_data(table_name, limit=limit)
        if df is None or len(df) == 0:
            return {"type": "error", "msg": f"表 {table_name} 为空或查询失败"}

        # 转为可序列化的格式供前端渲染
        headers = list(df.columns)
        rows = []
        for _, row in df.head(limit).iterrows():
            rows.append([_safe_serialize(v) for v in row.values])

        return build_frontend_action(
            action="render_table",
            params={
                "table_name": table_name,
                "headers": headers,
                "rows": rows,
            },
            summary=f"表 {table_name} 共 {len(rows)} 行 (显示前 {limit} 行), {len(headers)} 列",
            description=f"渲染表: {table_name} ({len(rows)} 行)",
        )
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("database")
@tool
def insert_data(table_name: str, data: dict) -> Dict[str, Any]:
    """
    向数据库表插入一行数据。

    入参:
        - table_name (str): 表名
        - data (dict): 列名→值, 如 {"name": "青海湖", "area": 4500}
    出参: {status, table_name, row_count}
    """
    try:
        from backend.data import business_db as db
        if not db.db_available():
            return {"type": "error", "msg": "数据库不可用"}
        result = db.insert_data(table_name, data)
        if result["status"] == "success":
            return {
                "type": "success",
                "summary": result["message"],
                "data": result,
            }
        return {"type": "error", "msg": result["msg"]}
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("database")
@tool
def update_data(table_name: str, data: dict, condition: str = "",
                condition_params: list = None) -> Dict[str, Any]:
    """
    更新数据库表中符合条件的行。

    入参:
        - table_name (str): 表名
        - data (dict): 要更新的列名→新值
        - condition (str): WHERE 条件, 如 "id = %s"
        - condition_params (list): 条件参数值, 如 [1]
    出参: {status, table_name, row_count}
    """
    try:
        from backend.data import business_db as db
        if not db.db_available():
            return {"type": "error", "msg": "数据库不可用"}
        cond = condition if condition else None
        result = db.update_data(table_name, data, cond, condition_params or [])
        if result["status"] == "success":
            return {
                "type": "success",
                "summary": result["message"],
                "data": result,
            }
        return {"type": "error", "msg": result["msg"]}
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("database")
@tool
def delete_data(table_name: str, condition: str = "",
                condition_params: list = None) -> Dict[str, Any]:
    """
    删除数据库表中符合条件的行。

    入参:
        - table_name (str): 表名
        - condition (str): WHERE 条件, 如 "id = %s"
        - condition_params (list): 条件参数值, 如 [1]
    出参: {status, table_name, row_count}
    """
    try:
        from backend.data import business_db as db
        if not db.db_available():
            return {"type": "error", "msg": "数据库不可用"}
        cond = condition if condition else None
        result = db.delete_data(table_name, cond, condition_params or [])
        if result["status"] == "success":
            return {
                "type": "success",
                "summary": result["message"],
                "data": result,
            }
        return {"type": "error", "msg": result["msg"]}
    except Exception as e:
        return {"type": "error", "msg": str(e)}


# ============================================================
#  辅助函数
# ============================================================

def _fmt_num(v):
    """格式化数值，None 返回 '-'"""
    if v is None:
        return "-"
    if isinstance(v, float):
        return round(v, 4)
    return v


def _safe_serialize(v):
    """安全序列化值 (处理 numpy/日期等)"""
    try:
        import numpy as np
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return round(float(v), 6)
        if isinstance(v, (np.ndarray,)):
            return v.tolist()
    except ImportError:
        pass
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


# ==================== Phase A: 元数据登记辅助 + 检索工具 (#2/#4) ====================

def _safe_register_image_meta(file_path: str, layer_name: str, workspace: str) -> None:
    """读 GeoTIFF 元数据 → 登记到 image_metadata (失败静默)."""
    try:
        from backend.data import business_db
        import rasterio
        with rasterio.open(file_path) as src:
            b = src.bounds
            bbox = (b.left, b.bottom, b.right, b.top)
            try:
                srid = int(src.crs.to_epsg()) if src.crs else None
            except Exception:
                srid = None
            # ★ to_epsg() 对 Web Mercator Auxiliary Sphere 等返回 None, 用 bounds 兜底
            if srid is None:
                srid = 3857 if (abs(b.left) > 180 or abs(b.bottom) > 90) else 4490
            res = float(src.res[0]) if src.res else None
            w, h, bands = src.width, src.height, src.count
        business_db.register_image_metadata(
            layer_name=layer_name or Path(file_path).stem,
            workspace=workspace or None,
            file_path=file_path,
            bbox=bbox,
            srid=srid,
            resolution=res,
            width=w,
            height=h,
            band_count=bands,
        )
    except Exception as e:
        logger.debug(f"[data_tools] 影像元数据登记跳过 ({file_path}): {e}")


def _safe_register_vector_meta(file_path: str, layer_name: str, workspace: str, kind: str = "change") -> None:
    """读矢量元数据 → 登记到 vector_layer (失败静默)."""
    try:
        from backend.data import business_db
        import geopandas as gpd
        gdf = gpd.read_file(file_path)
        if gdf.empty or gdf.crs is None:
            return
        try:
            target_crs = "EPSG:4490"
            gdf_wgs = gdf.to_crs(target_crs)
        except Exception:
            gdf_wgs = gdf
        bounds = gdf_wgs.total_bounds  # (minx, miny, maxx, maxy)
        try:
            srid = int(gdf.crs.to_epsg()) if gdf.crs else 4490
        except Exception:
            srid = 4490
        business_db.register_vector_layer(
            layer_name=layer_name or Path(file_path).stem,
            kind=kind,
            workspace=workspace or None,
            file_path=file_path,
            bbox=tuple(float(x) for x in bounds),
            srid=srid,
            feature_count=len(gdf),
        )
    except Exception as e:
        logger.debug(f"[data_tools] 矢量元数据登记跳过 ({file_path}): {e}")


@register_tool("database")
@tool
def search_images_by_time(start_date: str = "", end_date: str = "", limit: int = 50) -> Dict[str, Any]:
    """
    按获取时间范围检索已登记的影像元数据 (#4 时间检索)。

    入参:
        - start_date (str): 起始日期 (YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS), 留空不限
        - end_date (str): 结束日期, 留空不限
        - limit (int): 最多返回条数, 默认 50
    出参: {status, summary, data:{count, images:[...]}}
          images 含 layer_name/获取时间/分辨率/覆盖范围(bbox)/波段数等
    示例:
        search_images_by_time("2024-01-01", "2024-12-31")
        search_images_by_time("", "2025-06-01", 10)
    """
    try:
        from backend.data import business_db
        if not business_db.db_available():
            return {"type": "error", "msg": "业务数据库不可用, 元数据功能未启用"}
        images = business_db.query_images_by_time(
            start=start_date or None,
            end=end_date or None,
            limit=limit,
        )
        if not images:
            return {
                "type": "success",
                "summary": f"时间范围 {start_date or '不限'} ~ {end_date or '不限'} 内无影像记录",
                "data": {"count": 0, "images": []},
            }
        lines = [f"  - {im['layer_name']}: 获取={im.get('acquired_at') or '未知'}, 分辨率={_fmt_num(im.get('resolution'))}m, 波段={im.get('band_count')}, 尺寸={im.get('width')}x{im.get('height')}" for im in images]
        return {
            "type": "success",
            "summary": f"检索到 {len(images)} 条影像记录:\n" + "\n".join(lines),
            "data": {"count": len(images), "images": images},
        }
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("database")
@tool
def search_images_by_region(minx: float, miny: float, maxx: float, maxy: float, limit: int = 50) -> Dict[str, Any]:
    """
    按空间范围 (bbox) 检索已登记的影像元数据 (#4 区域检索)。

    入参:
        - minx/miny/maxx/maxy (float): 查询框范围 (经纬度, CGCS2000/EPSG:4490)
        - limit (int): 最多返回条数, 默认 50
    出参: {status, summary, data:{count, bbox, images:[...]}}
          images 含 layer_name/获取时间/分辨率/覆盖范围等
    示例:
        search_images_by_region(104.0, 30.5, 104.1, 30.6)  # 成都某区域
    """
    try:
        from backend.data import business_db
        if not business_db.db_available():
            return {"type": "error", "msg": "业务数据库不可用, 元数据功能未启用"}
        bbox = (float(minx), float(miny), float(maxx), float(maxy))
        images = business_db.query_images_by_region(bbox, limit=limit)
        if not images:
            return {
                "type": "success",
                "summary": f"区域 bbox={bbox} 内无影像记录",
                "data": {"count": 0, "bbox": list(bbox), "images": []},
            }
        lines = [f"  - {im['layer_name']}: 获取={im.get('acquired_at') or '未知'}, 分辨率={_fmt_num(im.get('resolution'))}m" for im in images]
        return {
            "type": "success",
            "summary": f"区域 bbox={bbox} 内检索到 {len(images)} 条影像:\n" + "\n".join(lines),
            "data": {"count": len(images), "bbox": list(bbox), "images": images},
        }
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("database")
@tool
def list_image_catalog(limit: int = 100) -> Dict[str, Any]:
    """
    列出全部已登记的影像元数据目录 (#4 数据目录), 按登记时间倒序。

    入参:
        - limit (int): 最多返回条数, 默认 100
    出参: {status, summary, data:{count, images:[...]}}
    """
    try:
        from backend.data import business_db
        if not business_db.db_available():
            return {"type": "error", "msg": "业务数据库不可用, 元数据功能未启用"}
        images = business_db.list_image_metadata(limit=limit)
        if not images:
            return {
                "type": "success",
                "summary": "当前影像目录为空 (尚未登记任何影像)",
                "data": {"count": 0, "images": []},
            }
        lines = [f"  - {im['layer_name']}: 登记于={im.get('uploaded_at') or '-'}, 分辨率={_fmt_num(im.get('resolution'))}m, 波段={im.get('band_count')}" for im in images]
        return {
            "type": "success",
            "summary": f"影像目录共 {len(images)} 条:\n" + "\n".join(lines),
            "data": {"count": len(images), "images": images},
        }
    except Exception as e:
        return {"type": "error", "msg": str(e)}


@register_tool("database")
@tool
def list_vector_catalog(kind: str = "", limit: int = 100) -> Dict[str, Any]:
    """
    列出已登记的矢量图层目录 (#2/#8 基础, 按类型过滤)。

    入参:
        - kind (str): 图层类型, 留空=全部, 可选: rule / change / administrative
        - limit (int): 最多返回条数, 默认 100
    出参: {status, summary, data:{count, layers:[...]}}
    """
    try:
        from backend.data import business_db
        if not business_db.db_available():
            return {"type": "error", "msg": "业务数据库不可用"}
        layers = business_db.list_vector_layers(kind=kind or None, limit=limit)
        if not layers:
            return {
                "type": "success",
                "summary": f"无 kind={kind or '全部'} 的矢量图层记录",
                "data": {"count": 0, "layers": []},
            }
        lines = [f"  - {l['layer_name']} (kind={l['kind']}): 要素={l.get('feature_count') or '-'}, 面积={_fmt_num(l.get('total_area_m2'))}m²" for l in layers]
        return {
            "type": "success",
            "summary": f"矢量图层共 {len(layers)} 条:\n" + "\n".join(lines),
            "data": {"count": len(layers), "layers": layers},
        }
    except Exception as e:
        return {"type": "error", "msg": str(e)}
