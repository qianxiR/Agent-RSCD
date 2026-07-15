"""
GeoServer 客户端 (数据层)
- 入参: 图层名、workspace、请求参数
- 方法: WMS GetMap / GetFeatureInfo、REST API
- 出参: 栅格数据 (numpy array)、元数据 dict

若 GeoServer 不可用, 返回 mock 数据。
"""
import logging
from typing import Optional, Dict, Any, Tuple
from io import BytesIO

import requests
from requests.auth import HTTPBasicAuth

from backend.config import settings

logger = logging.getLogger(__name__)


def _auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(settings.geoserver_username, settings.geoserver_password)


def geoserver_available() -> bool:
    """检查 GeoServer 是否可连通"""
    try:
        r = requests.get(
            f"{settings.geoserver_url}/rest/about/version.json",
            auth=_auth(),
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def get_layer_bbox(layer_name: str) -> Optional[Dict[str, float]]:
    """
    通过 WMS 1.3.0 GetCapabilities 获取图层的 BBOX。
    layer_name 格式: workspace:layername（全名）或 layername（裸名）。

    方法:
      - 用 local_tag 剥离命名空间前缀，递归遍历 Layer 子树（兼容层级嵌套）
      - ★ 匿名容器层（无 <Name>）不会被跳过，会递归搜索其子层
      - ★ 优先 EX_GeographicBoundingBox（WGS84 经纬度边界）
      - ★ 其次 BoundingBox（匹配 EPSG:4326）
      - ★ workspace 专属端点失败时回退到全局 WMS 端点

    返回: {minx, miny, maxx, maxy} 或 None
    """
    name_parts = layer_name.split(':')
    target_leaf = name_parts[-1]
    workspace_from_name = name_parts[0] if len(name_parts) >= 2 else None

    # 使用 workspace 专属 WMS 端点
    if workspace_from_name:
        wms_endpoint = f"{settings.geoserver_url}/{workspace_from_name}/wms"
    else:
        wms_endpoint = settings.geoserver_wms_url

    def _try_get_capabilities(endpoint: str) -> Optional[Dict[str, float]]:
        """尝试从指定 WMS 端点获取图层 BBOX，返回 BBOX dict 或 None"""
        try:
            r = requests.get(
                endpoint,
                params={"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"},
                auth=_auth(),
                timeout=30,
            )
            if r.status_code != 200:
                logger.debug(f"GetCapabilities HTTP {r.status_code} from {endpoint}")
                return None

            from xml.etree import ElementTree as ET

            root = ET.fromstring(r.content)

            # 剥离命名空间前缀：{http://www.opengis.net/wms}Layer → Layer
            def local_tag(el):
                tag_str = el.tag
                return tag_str.rsplit('}', 1)[-1] if '}' in tag_str else tag_str

            def find_layer(el, depth=0):
                """
                递归搜索 Layer 子树。
                - depth: 当前在 name_parts 层次中的深度
                - 匿名层（无 <Name>）视为容器，递归搜索子层但深度不增
                """
                for child in el:
                    if local_tag(child) != 'Layer':
                        continue
                    name_text = None
                    for sub in child:
                        if local_tag(sub) == 'Name':
                            name_text = (sub.text or '').strip()
                            break

                    # ★ 匿名容器层: 递归搜索子层，深度保持不变
                    if name_text is None:
                        result = find_layer(child, depth)
                        if result is not None:
                            return result
                        continue

                    # 到达目标深度 → 匹配 target_leaf（支持裸名和 workspace:name 两种格式）
                    if depth >= len(name_parts) - 1:
                        if name_text == target_leaf or name_text == layer_name:
                            return child
                        # 也可能是嵌套更深的容器层，继续搜索
                        result = find_layer(child, depth)
                        if result is not None:
                            return result
                    else:
                        # 未到目标深度 → 匹配层级名称
                        if name_text == name_parts[depth]:
                            result = find_layer(child, depth + 1)
                            if result is not None:
                                return result
                return None

            layer_el = find_layer(root)

            # 平扫兜底（兼容扁平或非标准嵌套结构）
            if layer_el is None:
                for el in root.iter():
                    if local_tag(el) != 'Layer':
                        continue
                    for sub in el:
                        if local_tag(sub) == 'Name':
                            name_text = (sub.text or '').strip()
                            # ★ 支持裸名和 workspace:name 两种匹配
                            if name_text == target_leaf or name_text == layer_name:
                                layer_el = el
                                break
                    if layer_el is not None:
                        break

            if layer_el is None:
                return None  # 此端点未找到，由调用方决定是否回退

            # 优先 EX_GeographicBoundingBox
            geo_bbox = None
            for child in layer_el:
                if local_tag(child) == 'EX_GeographicBoundingBox':
                    geo_bbox = child
                    break

            if geo_bbox is not None:
                bounds = {}
                for el in geo_bbox:
                    tag_l = local_tag(el)
                    if tag_l in ('westBoundLongitude', 'eastBoundLongitude',
                                 'southBoundLatitude', 'northBoundLatitude'):
                        bounds[tag_l] = float(el.text)
                if len(bounds) == 4:
                    west = bounds['westBoundLongitude']
                    east = bounds['eastBoundLongitude']
                    south = bounds['southBoundLatitude']
                    north = bounds['northBoundLatitude']
                    if west < east and south < north:
                        logger.info(f"BBOX [{layer_name}] (EX_GeographicBoundingBox): {west},{south},{east},{north}")
                        return {"minx": west, "miny": south, "maxx": east, "maxy": north}

            # 其次 BoundingBox（匹配 EPSG:4326）
            for child in layer_el:
                if local_tag(child) == 'BoundingBox':
                    bbox_crs = child.get('CRS', '')
                    if '4326' in bbox_crs:
                        minx = float(child.get('minx'))
                        miny = float(child.get('miny'))
                        maxx = float(child.get('maxx'))
                        maxy = float(child.get('maxy'))
                        if minx < maxx and miny < maxy:
                            logger.info(f"BBOX [{layer_name}] (BoundingBox): {minx},{miny},{maxx},{maxy}")
                            return {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy}

            logger.debug(f"图层 {layer_name} 在 {endpoint} 中找到但无有效 BBOX")
            return None

        except Exception as e:
            logger.debug(f"GetCapabilities 异常 [{layer_name}] from {endpoint}: {e}")
            return None

    # ★ 主流程: 先试 workspace 专属端点，失败则回退到全局 WMS
    result = _try_get_capabilities(wms_endpoint)
    if result is not None:
        return result

    # 回退: 如果使用了 workspace 专属端点且失败，尝试全局 WMS 端点
    if workspace_from_name and wms_endpoint != settings.geoserver_wms_url:
        logger.info(f"workspace 专属端点未找到图层 [{layer_name}]，回退到全局 WMS 端点")
        result = _try_get_capabilities(settings.geoserver_wms_url)
        if result is not None:
            return result

    logger.warning(f"在 GetCapabilities 中未找到图层: {layer_name}")
    return None


def sample_raster(
    layer_name: str,
    workspace: str = None,
    width: int = 200,
    height: int = 200,
    bbox: Tuple[float, float, float, float] = None,
) -> Optional["np.ndarray"]:
    """
    通过 WMS GetMap 采样栅格数据, 返回 numpy 数组及元信息。
    返回: (ndarray shape=(height, width), metadata_dict)
    元信息: {bbox, crs, layer_name, statistics: {min, max, mean, std}}
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        logger.warning("numpy 或 Pillow 未安装")
        return None, None

    # ★ 解析 layer_name: 去除已有 workspace 前缀，避免双重拼接
    ws = workspace or settings.geoserver_workspace
    bare_name = layer_name
    if ':' in layer_name:
        parts = layer_name.split(':', 1)
        ws = parts[0]  # 优先使用 layer_name 中的 workspace
        bare_name = parts[1]

    # 获取 BBOX
    if bbox is None:
        bbox_info = get_layer_bbox(f"{ws}:{bare_name}")
        if bbox_info:
            bbox = (bbox_info["minx"], bbox_info["miny"], bbox_info["maxx"], bbox_info["maxy"])
        else:
            # fallback: 青海湖大致范围
            bbox = (99.5, 36.2, 100.8, 37.1)
    else:
        bbox_info = None

    try:
        r = requests.get(
            f"{settings.geoserver_wms_url}",
            params={
                "service": "WMS",
                "version": "1.3.0",
                "request": "GetMap",
                "layers": f"{ws}:{bare_name}",
                "styles": "",
                "crs": "EPSG:4326",
                "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
                "width": width,
                "height": height,
                "format": "image/png",
                "transparent": "true",
            },
            auth=_auth(),
            timeout=30,
        )
        if r.status_code != 200:
            logger.warning(f"WMS GetMap 失败 [{layer_name}]: HTTP {r.status_code}")
            return None, None

        img = Image.open(BytesIO(r.content))
        arr = np.array(img)

        # 如果是 RGBA, 提取单波段 (取 R 通道或灰度)
        if arr.ndim == 3 and arr.shape[2] >= 3:
            gray = np.mean(arr[:, :, :3], axis=2).astype(np.float32) / 255.0
        else:
            gray = arr.astype(np.float32) / 255.0 if arr.max() > 1 else arr.astype(np.float32)

        stats = {
            "min": float(np.min(gray)),
            "max": float(np.max(gray)),
            "mean": float(np.mean(gray)),
            "std": float(np.std(gray)),
        }

        metadata = {
            "bbox": list(bbox),
            "width": width,
            "height": height,
            "layer_name": layer_name,
            "workspace": ws,
            "statistics": stats,
        }

        logger.info(f"WMS 采样完成 [{layer_name}]: shape={gray.shape}, mean={stats['mean']:.4f}")
        return gray, metadata

    except Exception as e:
        logger.warning(f"WMS 采样异常 [{layer_name}]: {e}")
        return None, None


def list_layers(workspace: str = None) -> list:
    """列出 GeoServer 工作空间下的所有图层，返回图层名称列表"""
    ws = workspace or settings.geoserver_workspace
    try:
        r = requests.get(
            f"{settings.geoserver_rest_url}/workspaces/{ws}/layers.json",
            auth=_auth(),
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            layers_container = data.get("layers", {})
            # GeoServer 空列表可能返回字符串 ""
            if not isinstance(layers_container, dict):
                layers_container = {}
            layer_list = layers_container.get("layer", [])
            # 单个图层时返回 dict，包装为 list
            if isinstance(layer_list, dict):
                layer_list = [layer_list]
            if not isinstance(layer_list, list):
                return []
            return [l["name"] for l in layer_list]
        return []
    except Exception as e:
        logger.warning(f"列出图层失败 [{ws}]: {e}")
        return []


def list_all_layers() -> list:
    """列出所有工作空间下的所有图层，返回 [{name, workspace, full_name}, ...]"""
    all_layers = []
    workspaces = list_workspaces()
    for ws in workspaces:
        ws_name = ws["name"]
        layer_names = list_layers(ws_name)
        for ln in layer_names:
            all_layers.append({
                "name": ln,
                "workspace": ws_name,
                "full_name": f"{ws_name}:{ln}",
            })
    return all_layers


def resolve_layer_name(name: str) -> Optional[str]:
    """
    解析图层名：裸名自动搜索所有 workspace，返回 workspace:layername 全名。
    若已含冒号则直接返回；找不到则返回 None。
    """
    if ':' in name:
        return name

    all_layers = list_all_layers()
    matches = [l for l in all_layers if l["name"] == name]

    if len(matches) == 0:
        logger.warning(f"未在任何工作空间中找到图层: '{name}'")
        return None

    if len(matches) > 1:
        logger.warning(f"图层名 '{name}' 在多个工作空间中存在: {[m['full_name'] for m in matches]}")
        # 返回第一个匹配（可后续优化为让用户选择）
        return matches[0]["full_name"]

    full_name = matches[0]["full_name"]
    logger.info(f"自动解析图层名: {name} → {full_name}")
    return full_name


def list_workspaces() -> list:
    """列出所有 GeoServer 工作空间"""
    try:
        r = requests.get(
            f"{settings.geoserver_rest_url}/workspaces.json",
            auth=_auth(),
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return [
                {"name": w["name"], "href": w["href"]}
                for w in data.get("workspaces", {}).get("workspace", [])
            ]
        return []
    except Exception as e:
        logger.warning(f"列出工作空间失败: {e}")
        return []


def list_services() -> Dict[str, Any]:
    """列出 GeoServer 支持的 OGC 服务 (WMS/WFS/WCS) 及各工作空间下的图层

    ★ 工作空间白名单: 仅返回名称以 cd / samseg 开头的工作空间 (大小写不敏感),
      覆盖所有"展示用"消费者 (REST /services 端点 + AI 工具 list_geoserver_services);
      与前端图层管理面板 (layer-panel.js) 的显示过滤规则保持一致。
      边界: 仅作用于本展示接口, 不影响 list_workspaces / resolve_layer_name 等图层操作
      (下载/删除/类型检测) 使用的全量工作空间查询。
    """
    result = {"workspaces": [], "services": {}}

    # 工作空间
    workspaces = list_workspaces()

    for ws in workspaces:
        ws_name = ws["name"]
        # ★ 白名单过滤: 仅保留 cd* / samseg* 工作空间 (大小写不敏感), 剔除其余以减少展示干扰
        if not ws_name.lower().startswith(("cd", "samseg")):
            continue
        layers = list_layers(ws_name)
        result["workspaces"].append({"workspace": ws_name, "layers": layers, "layer_count": len(layers)})

    # OGC 服务端点
    result["services"] = {
        "WMS": f"{settings.geoserver_url}/wms",
        "WFS": f"{settings.geoserver_url}/wfs",
        "WCS": f"{settings.geoserver_url}/wcs",
        "REST": f"{settings.geoserver_url}/rest",
    }

    return result


def download_raster(layer_name: str, workspace: str = None, output_path: str = None) -> Optional[str]:
    """
    通过 WMS GetMap 下载栅格数据为 GeoTIFF 文件。
    layer_name 支持:
      - 裸名 (如 "my_layer"), 配合 workspace 参数使用, workspace 为空则自动搜索所有工作空间
      - 全名 (如 "workspace:my_layer"), 自动解析工作空间
    返回下载文件的路径, 失败返回 None。
    """
    import tempfile

    # 解析图层名：裸名自动搜索所有 workspace，已含冒号则直接使用
    full_name = resolve_layer_name(layer_name)
    if full_name is None:
        # resolve_layer_name 失败时，用默认 workspace 兜底
        ws = workspace or settings.geoserver_workspace
        full_name = f"{ws}:{layer_name}" if ':' not in layer_name else layer_name

    # 获取图层 BBOX（传入全名，get_layer_bbox 内部解析 workspace 层级）
    bbox_info = get_layer_bbox(full_name)
    if bbox_info is None:
        logger.warning(f"无法获取图层 BBOX [{full_name}], 下载中止")
        return None

    bbox_str = ",".join(str(bbox_info[k]) for k in ["minx", "miny", "maxx", "maxy"])

    try:
        r = requests.get(
            f"{settings.geoserver_wms_url}",
            params={
                "service": "WMS",
                "version": "1.1.0",
                "request": "GetMap",
                "layers": full_name,
                "styles": "",
                "bbox": bbox_str,
                "width": "512",
                "height": "512",
                "srs": "EPSG:4326",
                "format": "image/geotiff",
            },
            auth=_auth(),
            timeout=60,
        )

        if r.status_code != 200:
            logger.warning(f"WMS 下载失败 [{full_name}]: HTTP {r.status_code}")
            return None

        # 校验返回的是图像数据而非 XML 错误响应
        content_type = r.headers.get('Content-Type', '')
        if 'image' not in content_type and 'tiff' not in content_type:
            logger.warning(f"WMS 返回非图像数据 [{full_name}]: content_type={content_type}")
            logger.warning(f"响应内容前300字节: {r.content[:300]}")
            return None

        if len(r.content) < 1000:
            logger.warning(f"WMS 返回数据过小 [{full_name}]: {len(r.content)} bytes, 可能为错误响应")
            logger.warning(f"响应内容前300字节: {r.content[:300]}")
            return None

        fname = output_path or tempfile.mktemp(suffix=f"_{full_name.replace(':', '_')}.tif")
        with open(fname, "wb") as f:
            f.write(r.content)

        logger.info(f"下载完成 [{full_name}]: {fname} ({len(r.content)} bytes)")
        return fname

    except Exception as e:
        logger.warning(f"WMS 下载异常 [{full_name}]: {e}")
        return None


def upload_raster(file_path: str, layer_name: str = None, workspace: str = None) -> Dict[str, Any]:
    """
    通过 GeoServer REST API 上传 GeoTIFF 并发布为图层。

    入参:
      - file_path: 本地 GeoTIFF 文件的绝对路径
      - layer_name: 发布后的图层名称(保存的名称), 默认从文件名推断
      - workspace: 目标工作空间, 不存在则自动创建

    出参: {status, layer_name, workspace, wms_url} 或 {status: "error", msg}
    """
    import os
    ws = workspace or settings.geoserver_workspace

    if not os.path.isfile(file_path):
        return {"status": "error", "msg": f"文件不存在: {file_path}"}

    if not layer_name:
        layer_name = os.path.splitext(os.path.basename(file_path))[0]

    store_name = layer_name

    logger.info(f"开始上传 GeoTIFF: {os.path.basename(file_path)}")
    logger.info(f"  目标工作空间: {ws}")
    logger.info(f"  图层名称: {layer_name}")

    try:
        # 确保工作空间存在（不存在则创建）
        check_url = f"{settings.geoserver_rest_url}/workspaces/{ws}.json"
        r = requests.get(check_url, auth=_auth(), timeout=10)

        if r.status_code == 200:
            logger.info(f"工作空间已存在: {ws}")
        elif r.status_code == 404:
            logger.info(f"工作空间不存在，正在创建: {ws}")
            r = requests.post(
                f"{settings.geoserver_rest_url}/workspaces",
                auth=_auth(),
                json={"workspace": {"name": ws}},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if r.status_code not in (200, 201):
                return {"status": "error", "msg": f"创建工作空间失败: HTTP {r.status_code}, {r.text[:200]}"}
            logger.info(f"工作空间创建成功: {ws}")
        else:
            return {"status": "error", "msg": f"检查工作空间失败: HTTP {r.status_code}"}

        # 检查图层是否已存在
        if layer_exists(ws, layer_name):
            logger.warning(f"图层已存在，跳过上传: {ws}:{layer_name}")
            return {
                "status": "success",
                "skipped": True,
                "layer_name": layer_name,
                "workspace": ws,
                "wms_url": f"{settings.geoserver_wms_url}?service=WMS&request=GetMap&layers={ws}:{layer_name}",
                "msg": f"图层 {ws}:{layer_name} 已存在, 跳过上传",
            }

        # 读取文件并上传
        file_size = os.path.getsize(file_path)
        logger.info(f"  文件大小: {file_size / 1024 / 1024:.2f} MB")

        with open(file_path, "rb") as f:
            r = requests.put(
                f"{settings.geoserver_rest_url}/workspaces/{ws}/coveragestores/{store_name}/file.geotiff",
                auth=_auth(),
                data=f,
                headers={"Content-Type": "image/tiff"},
                timeout=300,
            )

        if r.status_code in (200, 201):
            logger.info(f"上传成功: {ws}:{layer_name}")
            return {
                "status": "success",
                "layer_name": layer_name,
                "workspace": ws,
                "store_name": store_name,
                "file_size": file_size,
                "wms_url": f"{settings.geoserver_wms_url}?service=WMS&request=GetMap&layers={ws}:{layer_name}",
            }
        else:
            return {"status": "error", "msg": f"上传失败 HTTP {r.status_code}: {r.text[:300]}"}

    except Exception as e:
        logger.error(f"上传异常: {e}", exc_info=True)
        return {"status": "error", "msg": str(e)}


def layer_exists(workspace: str, layer_name: str) -> bool:
    """检查图层是否已在 GeoServer 中发布"""
    try:
        r = requests.get(
            f"{settings.geoserver_rest_url}/layers/{workspace}:{layer_name}.json",
            auth=_auth(),
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _get_datastore_published_name(workspace: str, store_name: str) -> Optional[str]:
    """
    查询一个 datastore 下真正发布的 featuretype 名 (REST API)。

    ★ 背景: PUT .../datastores/{store}/file.shp 上传 shapefile 时,
      GeoServer 用 .shp 文件名作为 featuretype 名, 而非 datastore store 名。
      本函数用于上传后回查真实发布名, 避免 layer_name 与 GetCapabilities 不一致。

    返回: featuretype 名 (裸名), 失败返回 None。
    """
    try:
        r = requests.get(
            f"{settings.geoserver_rest_url}/workspaces/{workspace}/datastores/{store_name}/featuretypes.json",
            auth=_auth(),
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        fts = data.get("featureTypes", {})
        if not isinstance(fts, dict):
            return None
        ft_list = fts.get("featureType", [])
        if isinstance(ft_list, dict):
            ft_list = [ft_list]
        if ft_list:
            return ft_list[0].get("name")
        return None
    except Exception as e:
        logger.debug(f"回查 datastore featuretypes 失败 [{workspace}:{store_name}]: {e}")
        return None


def upload_shapefile(file_path: str, layer_name: str = None, workspace: str = None) -> Dict[str, Any]:
    """
    通过 GeoServer REST API 上传 Shapefile 并发布为矢量图层。

    入参:
      - file_path: Shapefile 的 .shp 文件绝对路径
      - layer_name: 发布后的图层名称(保存的名称), 默认从文件名推断
      - workspace: 目标工作空间, 不存在则自动创建

    出参: {status, layer_name, workspace, store_name, published_name, wfs_url} 或 {status: "error", msg}

    ★ 重要: GeoServer 用 PUT .../datastores/{store}/file.shp 上传 shapefile 时,
      发布出来的 featuretype/layer 名 = .shp 文件名 (而非 datastore store 名)。
      所以调用方不能假定 layer_name == 期望的 layer_name, 必须以返回的
      published_name 为准 (它才是 GetCapabilities 里真正能查到的图层名)。
    """
    import zipfile
    import io
    from pathlib import Path

    ws = workspace or settings.geoserver_workspace
    shp_path = Path(file_path)

    if not shp_path.exists():
        return {"status": "error", "msg": f"文件不存在: {file_path}"}

    if shp_path.suffix.lower() != '.shp':
        return {"status": "error", "msg": f"不是 Shapefile (.shp): {file_path}"}

    if not layer_name:
        layer_name = shp_path.stem

    store_name = layer_name
    # ★ GeoServer 发布 featuretype 名 = .shp 文件 stem; 记下来上传后回查校验
    shp_stem = shp_path.stem

    logger.info(f"开始上传 Shapefile: {shp_path.name}")
    logger.info(f"  目标工作空间: {ws}")
    logger.info(f"  图层名称: {layer_name}")

    try:
        # 确保工作空间存在
        check_url = f"{settings.geoserver_rest_url}/workspaces/{ws}.json"
        r = requests.get(check_url, auth=_auth(), timeout=10)

        if r.status_code == 200:
            logger.info(f"工作空间已存在: {ws}")
        elif r.status_code == 404:
            logger.info(f"工作空间不存在，正在创建: {ws}")
            r = requests.post(
                f"{settings.geoserver_rest_url}/workspaces",
                auth=_auth(),
                json={"workspace": {"name": ws}},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if r.status_code not in (200, 201):
                return {"status": "error", "msg": f"创建工作空间失败: HTTP {r.status_code}"}
            logger.info(f"工作空间创建成功: {ws}")
        else:
            return {"status": "error", "msg": f"检查工作空间失败: HTTP {r.status_code}"}

        # 检查 datastore 是否已存在 (按 store 名查); 若存在, 回查其发布名并跳过上传
        #   ★ 用 datastore 端点而非 /layers/, 因为 PUT 上传时 store 名是调用方给的,
        #     但发布的 featuretype 名取自 .shp 文件名, 两者可能不一致。
        existing_published = _get_datastore_published_name(ws, store_name)
        if existing_published:
            logger.warning(f"图层已存在，跳过上传: {ws}:{existing_published} (store={store_name})")
            return {
                "status": "success",
                "skipped": True,
                "layer_name": existing_published,
                "published_name": existing_published,
                "workspace": ws,
                "wfs_url": f"{settings.geoserver_url}/{ws}/wfs?service=WFS&request=GetFeature&typeName={ws}:{existing_published}",
                "msg": f"图层 {ws}:{existing_published} 已存在, 跳过上传",
            }

        # 收集 Shapefile 相关文件
        base_path = shp_path.parent / shp_path.stem
        required_exts = ['.shp', '.shx', '.dbf']
        optional_exts = ['.prj', '.cpg', '.sbn', '.sbx', '.qix']

        shapefile_components = []
        for ext in required_exts + optional_exts:
            fp = Path(str(base_path) + ext)
            if fp.exists():
                shapefile_components.append(fp)
            elif ext in required_exts:
                return {"status": "error", "msg": f"缺少必要文件: {fp.name}"}

        logger.info(f"  找到 {len(shapefile_components)} 个文件")

        # 打包为 ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fp in shapefile_components:
                zf.write(fp, arcname=fp.name)

        zip_data = zip_buffer.getvalue()
        logger.info(f"  ZIP 包大小: {len(zip_data) / 1024:.2f} KB")

        # 上传
        r = requests.put(
            f"{settings.geoserver_rest_url}/workspaces/{ws}/datastores/{store_name}/file.shp",
            auth=_auth(),
            data=zip_data,
            headers={"Content-Type": "application/zip"},
            timeout=120,
        )

        if r.status_code in (200, 201):
            # ★ 上传成功后, 回查该 datastore 下真正发布的 featuretype 名。
            #   原因: PUT .../datastores/{store}/file.shp 时, GeoServer 用 .shp 文件名
            #   作为 featuretype 名 (而非 store 名)。若两者不一致, 之前直接返回 store 名
            #   会导致前端 WMS 请求一个不存在的图层 (GetCapabilities 查不到 → 出图失败)。
            published_name = _get_datastore_published_name(ws, store_name) or layer_name
            if published_name != layer_name:
                logger.info(f"  发布图层名与 store 名不一致: store={store_name} → published={published_name}")
            logger.info(f"上传成功: {ws}:{published_name} (store={store_name})")
            return {
                "status": "success",
                "layer_name": published_name,  # ★ 用回查到的真实发布名
                "published_name": published_name,
                "workspace": ws,
                "store_name": store_name,
                "files": [f.name for f in shapefile_components],
                "zip_size": len(zip_data),
                "wfs_url": f"{settings.geoserver_url}/{ws}/wfs?service=WFS&request=GetFeature&typeName={ws}:{published_name}",
            }
        else:
            return {"status": "error", "msg": f"上传失败 HTTP {r.status_code}: {r.text[:300]}"}

    except Exception as e:
        logger.error(f"上传 Shapefile 异常: {e}", exc_info=True)
        return {"status": "error", "msg": str(e)}


def upload_geojson(file_path: str, layer_name: str = None, workspace: str = None) -> Dict[str, Any]:
    """
    通过 GeoServer REST API 上传 GeoJSON 文件并发布为矢量图层。

    ★ 用途: 把矢量 GeoJSON (如 SamSeg 边缘线) 发布为 WMS 图层, 前端可属性查看/显隐。
    ★ 与 upload_shapefile 同样的回查机制: 发布名可能与 store 名不一致, 必须以
      返回的 published_name 为准。

    入参:
      - file_path: GeoJSON 文件的绝对路径
      - layer_name: datastore 名 (调用方给的), 默认从文件名推断
      - workspace: 目标工作空间, 不存在则自动创建

    出参: {status, layer_name(=published_name), published_name, workspace, store_name, wms_url} 或 {status: "error", msg}
    """
    from pathlib import Path

    ws = workspace or settings.geoserver_workspace
    gj_path = Path(file_path)

    if not gj_path.exists():
        return {"status": "error", "msg": f"文件不存在: {file_path}"}

    if gj_path.suffix.lower() not in ('.geojson', '.json'):
        return {"status": "error", "msg": f"不是 GeoJSON 文件: {file_path}"}

    if not layer_name:
        layer_name = gj_path.stem

    store_name = layer_name

    logger.info(f"开始上传 GeoJSON: {gj_path.name}")
    logger.info(f"  目标工作空间: {ws}")
    logger.info(f"  datastore 名称: {layer_name}")

    try:
        # 确保工作空间存在
        check_url = f"{settings.geoserver_rest_url}/workspaces/{ws}.json"
        r = requests.get(check_url, auth=_auth(), timeout=10)

        if r.status_code == 200:
            logger.info(f"工作空间已存在: {ws}")
        elif r.status_code == 404:
            logger.info(f"工作空间不存在，正在创建: {ws}")
            r = requests.post(
                f"{settings.geoserver_rest_url}/workspaces",
                auth=_auth(),
                json={"workspace": {"name": ws}},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if r.status_code not in (200, 201):
                return {"status": "error", "msg": f"创建工作空间失败: HTTP {r.status_code}"}
            logger.info(f"工作空间创建成功: {ws}")
        else:
            return {"status": "error", "msg": f"检查工作空间失败: HTTP {r.status_code}"}

        # 检查 datastore 是否已存在 → 跳过
        existing_published = _get_datastore_published_name(ws, store_name)
        if existing_published:
            logger.warning(f"图层已存在，跳过上传: {ws}:{existing_published} (store={store_name})")
            return {
                "status": "success",
                "skipped": True,
                "layer_name": existing_published,
                "published_name": existing_published,
                "workspace": ws,
                "wfs_url": f"{settings.geoserver_url}/{ws}/wfs?service=WFS&request=GetFeature&typeName={ws}:{existing_published}",
                "msg": f"图层 {ws}:{existing_published} 已存在, 跳过上传",
            }

        # 上传 GeoJSON (PUT .../datastores/{store}/file.geojson)
        with open(gj_path, "rb") as f:
            r = requests.put(
                f"{settings.geoserver_rest_url}/workspaces/{ws}/datastores/{store_name}/file.geojson",
                auth=_auth(),
                data=f,
                headers={"Content-Type": "application/json"},
                timeout=120,
            )

        if r.status_code in (200, 201):
            published_name = _get_datastore_published_name(ws, store_name) or layer_name
            if published_name != layer_name:
                logger.info(f"  发布图层名与 store 名不一致: store={store_name} → published={published_name}")
            logger.info(f"上传成功: {ws}:{published_name} (store={store_name})")
            return {
                "status": "success",
                "layer_name": published_name,
                "published_name": published_name,
                "workspace": ws,
                "store_name": store_name,
                "wms_url": f"{settings.geoserver_wms_url}?service=WMS&request=GetMap&layers={ws}:{published_name}",
                "wfs_url": f"{settings.geoserver_url}/{ws}/wfs?service=WFS&request=GetFeature&typeName={ws}:{published_name}",
            }
        else:
            return {"status": "error", "msg": f"上传失败 HTTP {r.status_code}: {r.text[:300]}"}

    except Exception as e:
        logger.error(f"上传 GeoJSON 异常: {e}", exc_info=True)
        return {"status": "error", "msg": str(e)}


def download_shapefile(layer_name: str, workspace: str = None, output_path: str = None) -> Optional[str]:
    """
    通过 WFS GetFeature 下载矢量图层为 Shapefile ZIP。

    入参:
      - layer_name: 图层名称, 支持裸名或 "workspace:layer_name" 全名
      - workspace: 工作空间, 裸名时使用
      - output_path: 输出文件路径, 不指定则使用临时文件

    出参: 下载文件路径, 失败返回 None
    """
    import tempfile

    full_name = resolve_layer_name(layer_name)
    if full_name is None:
        ws = workspace or settings.geoserver_workspace
        full_name = f"{ws}:{layer_name}" if ':' not in layer_name else layer_name

    try:
        r = requests.get(
            f"{settings.geoserver_url}/wfs",
            params={
                "service": "WFS",
                "version": "1.0.0",
                "request": "GetFeature",
                "typeName": full_name,
                "outputFormat": "SHAPE-ZIP",
            },
            auth=_auth(),
            timeout=120,
        )

        if r.status_code != 200 or len(r.content) < 100:
            logger.warning(f"WFS 下载失败 [{full_name}]: HTTP {r.status_code}, size={len(r.content)}")
            return None

        fname = output_path or tempfile.mktemp(suffix=f"_{full_name.replace(':', '_')}.zip")
        with open(fname, "wb") as f:
            f.write(r.content)

        logger.info(f"矢量下载完成 [{full_name}]: {fname} ({len(r.content)} bytes)")
        return fname

    except Exception as e:
        logger.warning(f"WFS 下载异常 [{layer_name}]: {e}")
        return None


def get_layer_type(layer_name: str) -> Optional[str]:
    """
    通过 GeoServer REST API 检测图层类型 (raster / vector)。

    入参:
      - layer_name: 图层名, 支持裸名或 "workspace:layer" 全名
    方法:
      依次用候选名列表 GET /rest/layers/{name}.json, 取首个 200 响应:
        1. 传入的原名 (可能是全名或裸名)
        2. resolve_layer_name 解析出的全名 (裸名场景的兜底)
      命中后据 layer.type (一手声明, 最可靠) + resource.@class/@href (辅助)
      判定: RASTER / coverage → raster; 否则 vector。
    出参:
      - "raster" / "vector": 成功识别
      - None: 所有候选名都查不到 (调用方自行决定兜底, 如 delete_layer 通用端点)
    """
    candidates = [layer_name]
    resolved = resolve_layer_name(layer_name)
    if resolved and resolved not in candidates:
        candidates.append(resolved)

    for name in candidates:
        try:
            r = requests.get(
                f"{settings.geoserver_rest_url}/layers/{name}.json",
                auth=_auth(),
                timeout=10,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            layer_info = data.get("layer", {})
            # GeoServer REST 一手类型声明 type 字段为 RASTER/VECTOR, 是最可靠判据;
            # resource 的 @class/@href 作辅助 (coveragestore=栅格, datastore=矢量),
            # 兼容部分请求场景下 @href 被省略时仅靠 type 兜底。
            raw_type = layer_info.get("type", "").upper()
            resource = layer_info.get("resource", {}) or {}
            res_hint = (resource.get("@class", "") + " " + resource.get("@href", "")).lower()
            is_raster = raw_type == "RASTER" or "coverage" in res_hint
            result = "raster" if is_raster else "vector"
            logger.info(f"图层类型检测 [{name}]: {result} (type={raw_type}, hint={res_hint})")
            return result
        except Exception as e:
            logger.debug(f"图层类型检测 REST 失败 [{name}]: {e}")

    logger.warning(f"无法检测图层类型: {layer_name}")
    return None


def delete_layer(layer_name: str, workspace: str = None,
                 delete_store: bool = True, recurse: bool = True) -> Dict[str, Any]:
    """
    ★ v2.5 新增: 通过 GeoServer REST API 删除已发布的图层。

    入参:
      - layer_name: 图层名 (裸名, 如 "my_layer")
      - workspace: 工作空间, 默认用 settings.geoserver_workspace
      - delete_store: 是否同时删除底层 store (coveragestore/datastore)
        True=彻底删除 (含数据存储); False=只删发布, 保留 store
      - recurse: GeoServer REST 的 recurse 参数, 递归删除关联资源

    方法 (★ 栅格和矢量的 REST 端点不同):
      1. get_layer_type 判定类型 (raster/vector)
      2. 栅格: DELETE /rest/workspaces/{ws}/coveragestores/{store}/coverages/{layer}?recurse=true
      3. 矢量: DELETE /rest/workspaces/{ws}/datastores/{store}/featuretypes/{layer}?recurse=true
      4. 兜底: 直接 DELETE /rest/layers/{ws}:{layer} (只删发布层)

    出参: {status: "success"|"error", layer_name, workspace, layer_type, msg}
    """
    ws = workspace or settings.geoserver_workspace
    if not ws:
        return {"status": "error", "msg": "未指定 workspace, 且 settings.geoserver_workspace 为空"}

    # 先判定图层类型 (决定用哪个 REST 端点)
    full_name = f"{ws}:{layer_name}" if ":" not in layer_name else layer_name
    layer_type = get_layer_type(full_name)
    if layer_type is None:
        # 无法判定类型, 仍尝试用通用 layers 端点删除
        layer_type = "unknown"

    logger.info(f"[delete_layer] 开始删除 {full_name} (type={layer_type}, delete_store={delete_store})")

    deleted_endpoints = []
    errors = []

    try:
        if layer_type == "raster":
            # 栅格: coveragestores/{store}/coverages/{layer}
            if delete_store:
                # 删整个 coveragestore (含所有 coverage)
                url = (f"{settings.geoserver_rest_url}/workspaces/{ws}/"
                       f"coveragestores/{layer_name}?recurse={'true' if recurse else 'false'}")
            else:
                # 只删 coverage, 保留 store
                url = (f"{settings.geoserver_rest_url}/workspaces/{ws}/"
                       f"coveragestores/{layer_name}/coverages/{layer_name}")
            r = requests.delete(url, auth=_auth(), timeout=30)
            deleted_endpoints.append(url)
            # 200/202=删除成功; 404=本就不存在 (幂等成功); 其它=真错误
            if r.status_code not in (200, 202, 404):
                errors.append(f"raster endpoint {r.status_code}: {r.text[:200]}")

        elif layer_type == "vector":
            # 矢量: datastores/{store}/featuretypes/{layer}
            if delete_store:
                url = (f"{settings.geoserver_rest_url}/workspaces/{ws}/"
                       f"datastores/{layer_name}?recurse={'true' if recurse else 'false'}")
            else:
                url = (f"{settings.geoserver_rest_url}/workspaces/{ws}/"
                       f"datastores/{layer_name}/featuretypes/{layer_name}")
            r = requests.delete(url, auth=_auth(), timeout=30)
            deleted_endpoints.append(url)
            if r.status_code not in (200, 202, 404):  # 404=幂等成功
                errors.append(f"vector endpoint {r.status_code}: {r.text[:200]}")

        # 兜底: 通用 layers 端点 (无论类型、无论主端点成功与否都执行, 确保 layer 资源被清理)
        url = f"{settings.geoserver_rest_url}/layers/{full_name}"
        r = requests.delete(url, auth=_auth(), timeout=30)
        deleted_endpoints.append(url)
        if r.status_code not in (200, 202, 404):
            errors.append(f"layers endpoint {r.status_code}: {r.text[:200]}")

        # 校验: 删除后 layer_exists 应为 False
        # ★ 但要先确认 GeoServer 可连通 (否则 layer_exists 连接失败也返回 False, 会误判成功)
        if errors and not any("连" in e or "connection" in e.lower() for e in errors):
            # 有非连接类错误 (如 403 权限), 直接返回错误
            return {
                "status": "error",
                "layer_name": layer_name,
                "workspace": ws,
                "layer_type": layer_type,
                "msg": f"删除请求失败。端点: {deleted_endpoints}; 错误: {errors}",
            }
        # 确认 GeoServer 可连通后再校验 layer_exists
        if not geoserver_available():
            return {"status": "error", "layer_name": layer_name, "workspace": ws,
                    "layer_type": layer_type, "msg": "GeoServer 不可连通, 无法删除"}
        still_exists = layer_exists(ws, layer_name)
        if still_exists:
            return {
                "status": "error",
                "layer_name": layer_name,
                "workspace": ws,
                "layer_type": layer_type,
                "msg": f"删除请求已发送但图层仍存在 (可能权限不足或 recurse 未生效)。端点: {deleted_endpoints}; 错误: {errors}",
            }

        logger.info(f"[delete_layer] 成功删除 {full_name} (type={layer_type})")
        return {
            "status": "success",
            "layer_name": layer_name,
            "workspace": ws,
            "layer_type": layer_type,
            "msg": f"已删除图层 {full_name} (类型: {layer_type}, 删 store: {delete_store})",
        }

    except requests.exceptions.ConnectionError:
        return {"status": "error", "layer_name": layer_name, "workspace": ws,
                "layer_type": layer_type, "msg": "GeoServer 连接失败 (服务未启动?)"}
    except Exception as e:
        logger.error(f"[delete_layer] 异常 {full_name}: {e}")
        return {"status": "error", "layer_name": layer_name, "workspace": ws,
                "layer_type": layer_type, "msg": f"删除异常: {e}"}


def download_geojson(layer_name: str, workspace: str = None, output_path: str = None) -> Optional[str]:
    """
    通过 WFS 2.0 GetFeature 下载矢量图层为 GeoJSON 文件。

    入参:
      - layer_name: 图层名称, 支持裸名或 "workspace:layer_name" 全名
      - workspace: 工作空间, 裸名时使用
      - output_path: 输出文件路径, 不指定则使用临时文件

    出参: 下载文件路径 (.geojson), 失败返回 None
    """
    import tempfile

    full_name = resolve_layer_name(layer_name)
    if full_name is None:
        ws = workspace or settings.geoserver_workspace
        full_name = f"{ws}:{layer_name}" if ':' not in layer_name else layer_name

    try:
        r = requests.get(
            f"{settings.geoserver_url}/wfs",
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeName": full_name,
                "outputFormat": "application/json",
            },
            auth=_auth(),
            timeout=120,
        )

        if r.status_code != 200:
            logger.warning(f"WFS GeoJSON 下载失败 [{full_name}]: HTTP {r.status_code}")
            return None

        if len(r.content) < 2:
            logger.warning(f"WFS GeoJSON 返回数据过小 [{full_name}]: {len(r.content)} bytes")
            return None

        # 验证返回内容是 JSON
        content_type = r.headers.get('Content-Type', '')
        if 'json' not in content_type:
            try:
                import json
                json.loads(r.content)
            except Exception:
                logger.warning(f"WFS GeoJSON 返回非 JSON 数据 [{full_name}]: content_type={content_type}")
                return None

        fname = output_path or tempfile.mktemp(suffix=f"_{full_name.replace(':', '_')}.geojson")
        with open(fname, "wb") as f:
            f.write(r.content)

        logger.info(f"GeoJSON 下载完成 [{full_name}]: {fname} ({len(r.content)} bytes)")
        return fname

    except Exception as e:
        logger.warning(f"WFS GeoJSON 下载异常 [{layer_name}]: {e}")
        return None


def get_feature_info(
    layer_name: str,
    bbox: Tuple[float, float, float, float],
    width: int,
    height: int,
    i: int,
    j: int,
    workspace: str = None,
) -> Dict[str, Any]:
    """
    通过 WMS GetFeatureInfo 获取指定像素位置的栅格/矢量属性数据。

    入参:
      - layer_name: 图层名称（裸名或 workspace:layername 全名）
      - bbox: 当前影像的边界框 (minx, miny, maxx, maxy)
      - width: WMS GetMap 请求的图像宽度（像素）
      - height: WMS GetMap 请求的图像高度（像素）
      - i: 点击位置的 X 像素坐标（0-based，从左到右）
      - j: 点击位置的 Y 像素坐标（0-based，从上到下）
      - workspace: 工作空间（已从 layer_name 解析可不传）

    出参:
      {
        "success": True/False,
        "layer_name": "...",
        "pixel": [i, j],
        "lon": ...,  # 点击位置对应的经度
        "lat": ...,  # 点击位置对应的纬度
        "values": { ... },      # 有数据的波段名→值
        "raw_response": "...",  # 原始文本响应（前 500 字符）
        "msg": "..."  # 失败时的人类可读消息
      }
    """
    # 解析图层名
    ws = workspace
    if ':' in layer_name:
        parts = layer_name.split(':', 1)
        ws = parts[0]
        ln = parts[1]
    else:
        ln = layer_name
        ws = ws or settings.geoserver_workspace

    full_name = f"{ws}:{ln}"

    # 计算点击位置的经纬度
    lon = bbox[0] + (bbox[2] - bbox[0]) * (i + 0.5) / width
    lat = bbox[3] - (bbox[3] - bbox[1]) * (j + 0.5) / height  # WMS y 轴从顶到底

    try:
        r = requests.get(
            settings.geoserver_wms_url,
            params={
                "service": "WMS",
                "version": "1.1.1",
                "request": "GetFeatureInfo",
                "layers": full_name,
                "query_layers": full_name,
                "styles": "",
                "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
                "width": width,
                "height": height,
                "srs": "EPSG:4326",
                "x": i,
                "y": j,
                "info_format": "application/json",
            },
            auth=_auth(),
            timeout=15,
        )

        if r.status_code != 200:
            logger.warning(f"GetFeatureInfo 失败 [{full_name}]: HTTP {r.status_code}")
            return {
                "success": False,
                "layer_name": full_name,
                "pixel": [i, j],
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "msg": f"GeoServer 返回 HTTP {r.status_code}",
            }

        # 尝试解析 JSON 响应
        content_type = r.headers.get("Content-Type", "")
        try:
            if "json" in content_type or r.text.strip().startswith("{"):
                data = r.json()
                values = _extract_feature_values(data)
                return {
                    "success": True,
                    "layer_name": full_name,
                    "pixel": [i, j],
                    "lon": round(lon, 6),
                    "lat": round(lat, 6),
                    "values": values,
                    "raw_response": r.text[:500],
                }
            else:
                # 非 JSON 响应（XML/GML/text）
                return {
                    "success": True,
                    "layer_name": full_name,
                    "pixel": [i, j],
                    "lon": round(lon, 6),
                    "lat": round(lat, 6),
                    "values": {},
                    "raw_response": r.text[:500],
                    "msg": "响应格式非 JSON，请查看 raw_response",
                }
        except Exception:
            return {
                "success": True,
                "layer_name": full_name,
                "pixel": [i, j],
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "values": {},
                "raw_response": r.text[:500],
                "msg": "无法解析响应为 JSON，请查看 raw_response",
            }

    except Exception as e:
        logger.warning(f"GetFeatureInfo 异常 [{full_name}]: {e}")
        return {
            "success": False,
            "layer_name": full_name,
            "pixel": [i, j],
            "lon": round(lon, 6),
            "lat": round(lat, 6),
            "msg": f"请求异常: {e}",
        }


def _extract_feature_values(data: dict) -> dict:
    """
    从 GeoServer GetFeatureInfo JSON 响应中提取波段/属性值。
    支持两种结构:
      - features[0].properties (GeoServer 常见格式)
      - 直接在根对象下的键值对
    """
    values = {}
    try:
        features = data.get("features", [])
        if features and isinstance(features, list):
            props = features[0].get("properties", {})
            if isinstance(props, dict):
                for key, val in props.items():
                    if val is not None and val != "":
                        values[str(key)] = val
        if not values:
            # 尝试直接从根对象取（排除元数据键）
            skip_keys = {"type", "features", "totalFeatures", "crs", "geometry"}
            for key, val in data.items():
                if key not in skip_keys and val is not None and val != "":
                    values[str(key)] = val
    except Exception:
        pass
    return values


def _ensure_samseg_edge_style() -> Optional[str]:
    """
    确保 GeoServer 中存在线样式 "edge_line_blue" (颜色 #2C6FBD, 与 GeoAI Copilot 标题同色)。
    若不存在则创建。

    出参: 样式名 "edge_line_blue" 或 None (失败)
    """
    style_name = "edge_line_blue"
    sld_body = """<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.0.0">
  <NamedLayer>
    <Name>edge_line_blue</Name>
    <UserStyle>
      <Name>edge_line_blue</Name>
      <Title>SamSeg Edge (GeoAI Blue)</Title>
      <FeatureTypeStyle>
        <Rule>
          <LineSymbolizer>
            <Stroke>
              <CssParameter name="stroke">#2C6FBD</CssParameter>
              <CssParameter name="stroke-width">2</CssParameter>
            </Stroke>
          </LineSymbolizer>
          <PolygonSymbolizer>
            <Fill>
              <CssParameter name="fill-opacity">0</CssParameter>
            </Fill>
            <Stroke>
              <CssParameter name="stroke">#2C6FBD</CssParameter>
              <CssParameter name="stroke-width">2</CssParameter>
            </Stroke>
          </PolygonSymbolizer>
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>"""

    try:
        check_url = f"{settings.geoserver_rest_url}/styles/{style_name}.json"
        r = requests.get(check_url, auth=_auth(), timeout=10)
        if r.status_code == 200:
            return style_name

        create_url = f"{settings.geoserver_rest_url}/styles"
        headers = {"Content-Type": "application/xml"}
        post_data = f"<style><name>{style_name}</name><filename>{style_name}.sld</filename></style>"
        r = requests.post(create_url, auth=_auth(), data=post_data, headers=headers, timeout=10)

        if r.status_code not in (200, 201):
            logger.warning(f"创建 GeoServer 样式 {style_name} 失败, HTTP {r.status_code}")
            return None

        sld_url = f"{settings.geoserver_rest_url}/styles/{style_name}"
        r = requests.put(sld_url, auth=_auth(), data=sld_body,
                         headers={"Content-Type": "application/vnd.ogc.sld+xml"}, timeout=10)

        if r.status_code in (200, 201):
            logger.info(f"GeoServer 样式 {style_name} 创建成功 (stroke=#2C6FBD)")
            return style_name

        logger.warning(f"上传 SLD 失败: {style_name}, HTTP {r.status_code}")
        return None

    except Exception as e:
        logger.warning(f"确保 GeoServer 样式 {style_name} 异常: {e}")
        return None


def _ensure_samseg_polygon_style() -> Optional[str]:
    """
    确保 GeoServer 中存在面样式 "polygon_blue" (fill=#2C6FBD 30%透明, stroke=#2C6FBD)。
    若不存在则创建。

    出参: 样式名 "polygon_blue" 或 None (失败)
    """
    style_name = "polygon_blue"
    sld_body = """<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.0.0">
  <NamedLayer>
    <Name>polygon_blue</Name>
    <UserStyle>
      <Name>polygon_blue</Name>
      <Title>SamSeg Polygon (GeoAI Blue)</Title>
      <FeatureTypeStyle>
        <Rule>
          <PolygonSymbolizer>
            <Fill>
              <CssParameter name="fill">#2C6FBD</CssParameter>
              <CssParameter name="fill-opacity">0.3</CssParameter>
            </Fill>
            <Stroke>
              <CssParameter name="stroke">#2C6FBD</CssParameter>
              <CssParameter name="stroke-width">2</CssParameter>
            </Stroke>
          </PolygonSymbolizer>
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>"""

    try:
        check_url = f"{settings.geoserver_rest_url}/styles/{style_name}.json"
        r = requests.get(check_url, auth=_auth(), timeout=10)
        if r.status_code == 200:
            return style_name

        create_url = f"{settings.geoserver_rest_url}/styles"
        headers = {"Content-Type": "application/xml"}
        post_data = f"<style><name>{style_name}</name><filename>{style_name}.sld</filename></style>"
        r = requests.post(create_url, auth=_auth(), data=post_data, headers=headers, timeout=10)

        if r.status_code not in (200, 201):
            logger.warning(f"创建 GeoServer 样式 {style_name} 失败, HTTP {r.status_code}")
            return None

        sld_url = f"{settings.geoserver_rest_url}/styles/{style_name}"
        r = requests.put(sld_url, auth=_auth(), data=sld_body,
                         headers={"Content-Type": "application/vnd.ogc.sld+xml"}, timeout=10)

        if r.status_code in (200, 201):
            logger.info(f"GeoServer 样式 {style_name} 创建成功 (fill=#2C6FBD, stroke=#2C6FBD)")
            return style_name

        logger.warning(f"上传 SLD 失败: {style_name}, HTTP {r.status_code}")
        return None

    except Exception as e:
        logger.warning(f"确保 GeoServer 样式 {style_name} 异常: {e}")
        return None


def _set_layer_default_style(workspace: str, layer_name: str, style_name: str) -> bool:
    """
    将指定图层的默认样式设置为指定样式名。

    入参:
      - workspace: 工作空间名
      - layer_name: 图层名 (裸名)
      - style_name: GeoServer 样式名

    出参: True 成功, False 失败
    """
    try:
        url = f"{settings.geoserver_rest_url}/layers/{workspace}:{layer_name}"
        data = f"<layer><defaultStyle><name>{style_name}</name></defaultStyle></layer>"
        headers = {"Content-Type": "application/xml"}
        r = requests.put(url, auth=_auth(), data=data, headers=headers, timeout=10)
        if r.status_code in (200, 201, 202):
            logger.info(f"图层 {workspace}:{layer_name} 默认样式已设置为 {style_name}")
            return True
        logger.warning(f"设置图层 {workspace}:{layer_name} 样式失败, HTTP {r.status_code}")
        return False
    except Exception as e:
        logger.warning(f"设置图层样式异常: {e}")
        return False
