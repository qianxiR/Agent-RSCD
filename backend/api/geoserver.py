"""GeoServer 代理与图层管理路由 (/api/v1/geoserver)。

WMS GetFeatureInfo / GetMap 代理 (绕过浏览器 CORS) + 图层管理面板直连端点
(让前端面板直接拉数据/操作图层, 不必每次走 AI)。
全部复用 backend.data.geoserver_client 已有能力, 不引入新数据层逻辑。
"""
import os

from fastapi import APIRouter, Request, Response

from backend.api.deps import geoserver_client
from backend.api.schemas import PublishLayerRequest
from backend.config import settings

router = APIRouter()


@router.get("/api/v1/geoserver/feature-info")
async def get_feature_info(
    layer_name: str,
    bbox: str,       # "minx,miny,maxx,maxy"
    width: int,
    height: int,
    i: int,
    j: int,
    workspace: str = None,
):
    """
    WMS GetFeatureInfo 代理 —— 前端点击遥感影像时获取该像素的属性数据。
    代理原因: 避免浏览器 CORS 限制, 统一 GeoServer 认证。
    """
    try:
        bbox_parts = [float(x) for x in bbox.split(",")]
        if len(bbox_parts) != 4:
            return {"success": False, "msg": "bbox 格式错误, 应为 minx,miny,maxx,maxy"}
    except ValueError:
        return {"success": False, "msg": "bbox 解析失败, 所有值必须为数字"}

    result = geoserver_client.get_feature_info(
        layer_name=layer_name,
        bbox=(bbox_parts[0], bbox_parts[1], bbox_parts[2], bbox_parts[3]),
        width=width,
        height=height,
        i=i,
        j=j,
        workspace=workspace,
    )
    return result


@router.get("/api/v1/geoserver/wms")
async def proxy_geoserver_wms(request: Request):
    """
    WMS GetMap/GetLegendGraphic 通用代理 —— 前端 OpenLayers TileWMS 瓦片请求经此转发。
    代理原因: 与 feature-info 同 —— 前端直连 GeoServer (默认 http://localhost:8080/geoserver/wms)
      会因浏览器到 GeoServer 的地址/CORS 限制而加载失败 (表现为“WMS 加载超时”)。
      走后端代理后: ① 地址随服务端 settings.geoserver_url, 不再硬编码 localhost;
                    ② 后端内网访问 + 统一认证, 绕过浏览器 CORS。
    方法: 把前端 query 原样转发到 settings.geoserver_wms_url, 透传响应体与 Content-Type。
    出参: 原始瓦片字节流 (image/png 等), 带原始 Content-Type; GeoServer 不可用时返回 502。
    """
    # ★ GeoServer 不可用直接 502, 避免前端逐瓦片空等触发 10s 超时
    if not geoserver_client.geoserver_available():
        return Response(
            content='{"error": "GeoServer 不可用"}',
            media_type="application/json",
            status_code=502,
        )

    # 把前端的 query 参数原样转发给 GeoServer WMS (含 LAYERS/SLD_BODY/FORMAT 等)
    # ★ 用 httpx async, 避免在 async 端点里用同步 requests 阻塞事件循环
    #   (并发瓦片请求多, 阻塞会拖慢同连接上的其他请求)。
    # ★ trust_env=False: 后端访问本机 GeoServer (127.0.0.1:8080) 属内网固定地址,
    #   不应走任何系统代理。Windows 开启系统代理(Clash/v2ray 等)时, httpx 默认
    #   trust_env=True 会读取注册表代理把本机请求劫持到代理端口 → "All connection
    #   attempts failed" (2s 超时) → 502。requests 库默认不读注册表代理, 故不受影响。
    params = dict(request.query_params)
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            resp = await client.get(
                settings.geoserver_wms_url,
                params=params,
                auth=(settings.geoserver_username, settings.geoserver_password),
            )
    except Exception as e:
        return Response(
            content=f'{{"error": "GeoServer 请求失败: {e}"}}',
            media_type="application/json",
            status_code=502,
        )

    if resp.status_code != 200:
        return Response(
            content=resp.content,
            media_type=resp.headers.get("Content-Type", "application/json"),
            status_code=resp.status_code,
        )
    # 透传原始字节 + Content-Type (通常是 image/png)
    return Response(
        content=resp.content,
        media_type=resp.headers.get("Content-Type", "image/png"),
    )


# ==================== GeoServer 图层管理 (供前端图层管理面板直接调用) ====================

@router.get("/api/v1/geoserver/services")
async def list_geoserver_services_tree():
    """
    列出 GeoServer 所有工作空间及其图层树 (供前端图层管理面板渲染)。

    返回: {status, workspaces:[{name, layer_count, layers:[{name, full_name, type}]}], services:{...}}
      - type: "raster" / "vector" / "unknown" (前端据此刻画不同图标 + 决定 WMS/WFS 渲染方式)
    """
    try:
        if not geoserver_client.geoserver_available():
            return {"status": "error", "msg": "GeoServer 不可用, 请检查服务是否启动"}
        data = geoserver_client.list_services()

        # ★ 给每个图层补 type: 复用 get_layer_type (REST GET /layers/{ws}:{layer}.json 判定栅格/矢量)
        workspaces = data.get("workspaces", [])
        for ws in workspaces:
            layer_names = ws.get("layers", [])
            typed_layers = []
            for ln in layer_names:
                full = f"{ws['workspace']}:{ln}"
                try:
                    ltype = geoserver_client.get_layer_type(full)
                except Exception:
                    ltype = "unknown"
                typed_layers.append({"name": ln, "full_name": full, "type": ltype or "unknown"})
            ws["layers_typed"] = typed_layers

        total = sum(ws.get("layer_count", len(ws.get("layers", []))) for ws in workspaces)
        return {
            "status": "success",
            "workspaces": workspaces,
            "services": data.get("services", {}),
            "total_layers": total,
        }
    except Exception as e:
        return {"status": "error", "msg": str(e)}


@router.get("/api/v1/geoserver/layers/{workspace}/{layer_name}/bbox")
async def get_layer_bbox(workspace: str, layer_name: str):
    """
    获取指定图层的 BBOX (供前端"定位到图层"功能)。
    复用 geoserver_client.get_layer_bbox (WMS GetCapabilities)。

    返回: {status, bbox:{minx,miny,maxx,maxy}} 或 {status:"error", msg}
    """
    try:
        if not geoserver_client.geoserver_available():
            return {"status": "error", "msg": "GeoServer 不可用"}
        full = f"{workspace}:{layer_name}"
        bbox = geoserver_client.get_layer_bbox(full)
        if bbox is None:
            return {"status": "error", "msg": f"无法获取图层范围: {full}"}
        return {"status": "success", "bbox": bbox, "full_name": full}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


@router.delete("/api/v1/geoserver/layers/{workspace}/{layer_name}")
async def delete_geoserver_layer_rest(workspace: str, layer_name: str):
    """
    删除指定图层 (供前端图层面板"删除图层"按钮直接调用)。
    复用 geoserver_client.delete_layer (REST DELETE coveragestores/datastores, 含真实删除校验)。

    返回: {status:"success", layer_name, workspace, layer_type, msg} 或 {status:"error", msg}
    """
    try:
        if not geoserver_client.geoserver_available():
            return {"status": "error", "msg": "GeoServer 不可用"}
        # ★ delete_layer 签名: (layer_name 裸名, workspace, delete_store) — 分开传, 不拼全名
        result = geoserver_client.delete_layer(layer_name, workspace=workspace, delete_store=True)
        return result  # delete_layer 已返回标准 {status, layer_name, workspace, layer_type, msg}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


@router.post("/api/v1/geoserver/layers")
async def publish_geoserver_layer_rest(req: PublishLayerRequest):
    """
    发布本地文件为 GeoServer 图层 (供前端图层面板直接调用, 不经过 LLM)。

    ★ 工作空间按图层类型固定 (前端无 workspace 传参入口):
      - 栅格 (.tif/.tiff/.cog) → cd_raster
      - 矢量 (.shp)            → cd_shp
    路由到底层 upload_raster / upload_shapefile。

    返回: {status, layer_name, workspace, wms_url, msg} 或 {status:"error", msg}
    """
    # ★ 按图层类型固定的目标工作空间 (前端无传参入口, 后端决策; 避免透传空串给 GeoServer
    #   触发 "workspace name must not be null")
    WS_BY_TYPE = {"raster": "cd_raster", "vector": "cd_shp"}

    try:
        if not geoserver_client.geoserver_available():
            return {"status": "error", "msg": "GeoServer 不可用"}

        if not os.path.isfile(req.file_path):
            return {"status": "error", "msg": f"文件不存在: {req.file_path}"}

        # ★ 类型推断: 显式指定 > 扩展名 (仅栅格/矢量两类, 不支持 geojson 直接上传)
        ext = os.path.splitext(req.file_path)[1].lower()
        if req.layer_type == "raster" or ext in (".tif", ".tiff", ".cog"):
            layer_type = "raster"
        elif req.layer_type == "vector" or ext in (".shp",):
            layer_type = "vector"
        else:
            return {"status": "error", "msg": f"不支持的文件类型: {ext} (支持 .tif/.tiff/.cog/.shp)"}

        target_ws = WS_BY_TYPE[layer_type]
        if layer_type == "raster":
            result = geoserver_client.upload_raster(req.file_path, req.layer_name or None, target_ws)
        else:
            result = geoserver_client.upload_shapefile(req.file_path, req.layer_name or None, target_ws)

        if result.get("status") == "success":
            return {"status": "success", **{k: v for k, v in result.items() if k != "status"},
                    "msg": result.get("msg", "")}
        return {"status": "error", "msg": result.get("msg", "发布失败")}
    except Exception as e:
        return {"status": "error", "msg": str(e)}
