"""开源遥感影像数据源适配层。

设计原则:
- 每个数据源一个 Provider, 统一输入 (bbox / 时间 / 云量 / 条数) 与统一输出 (归一化 item);
- 只依赖公开开放数据, 不引入商业密钥; 需要账号的数据源 (CDSE 整景 / GEE) 显式标注;
- 检索 (STAC/OData) 与预览 (瓦片/快视图)、下载 (AOI 裁剪) 解耦, 由 service 统一编排。

数据源:
- pc   : Microsoft Planetary Computer, STAC 1.0 + TiTiler 数据 API, 免密钥
- es   : AWS Earth Search (Element 84), STAC 1.0, 免密钥
- cdse : Copernicus Data Space Ecosystem, STAC 1.0, 检索免登录
- gee  : Google Earth Engine, 兜底 (需服务账号 + 科学上网)
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_DATA = "https://planetarycomputer.microsoft.com/api/data/v1"
ES_STAC = "https://earth-search.aws.element84.com/v1"
CDSE_STAC = "https://catalogue.dataspace.copernicus.eu/stac"
CDSE_BROWSER = "https://browser.dataspace.copernicus.eu/"

REQUEST_HEADERS = {
    "User-Agent": "RSCD-Imagery/1.0 (+https://localhost) httpx",
    "Accept": "application/geo+json, application/json",
}


class ImageryError(RuntimeError):
    """数据源调用/参数错误 (面向用户的中文提示)。"""


# ==================== 数据模型 ====================

@dataclass(frozen=True)
class ImageryCollection:
    """一个可检索的影像集合 (卫星/产品)。

    - key:   数据源内的集合 id (如 sentinel-2-l2a)
    - gsd:   标称空间分辨率 (米), 用于前端分辨率筛选与展示
    - tile_params: PC 数据 API 的渲染参数; 其他源为空
    - clip_params: PC AOI 裁剪下载的资产参数; 其他源为空
    - note:  展示给用户的补充说明
    """
    key: str
    label: str
    gsd: Optional[float]
    kind: str = "optical"  # optical / sar / dem / aerial
    tile_params: Optional[str] = None
    clip_params: Optional[str] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "gsd": self.gsd,
            "kind": self.kind,
            "note": self.note,
        }


@dataclass
class SearchQuery:
    bbox: Tuple[float, float, float, float]
    start: str
    end: str
    cloud_min: Optional[float] = 0.0
    cloud_max: Optional[float] = 30.0
    platform: str = ""
    orbit_direction: str = ""
    processing_level: str = ""
    sun_elevation_min: Optional[float] = None
    limit: int = 24
    collections: Sequence[str] = field(default_factory=list)


# ==================== 工具函数 ====================

def _parse_day(value: str) -> datetime:
    """解析 YYYY-MM-DD 或 ISO8601, 统一返回 UTC 时间。"""
    text = (value or "").strip()
    if not text:
        raise ImageryError("时间参数不能为空")
    try:
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ImageryError(f"时间格式无效: {value} (应为 YYYY-MM-DD)") from exc


def stac_interval(start: str, end: str) -> str:
    """STAC datetime 区间 (严格 RFC3339, 兼容 Earth Search 的解析)。"""
    s = _parse_day(start).strftime("%Y-%m-%dT%H:%M:%SZ")
    e = _parse_day(end).strftime("%Y-%m-%dT23:59:59Z")
    if s > e:
        raise ImageryError("开始时间不能晚于结束时间")
    return f"{s}/{e}"


def stac_filter_query(query: "SearchQuery", *, allow_cloud: bool = True) -> Dict[str, Any]:
    """把官方 STAC query 字段转成查询条件（cloud/platform/sun elevation）。"""
    out: Dict[str, Any] = {}
    if allow_cloud:
        cloud_cond: Dict[str, float] = {}
        if query.cloud_min is not None and float(query.cloud_min) > 0:
            cloud_cond["gte"] = float(query.cloud_min)
        if query.cloud_max is not None and float(query.cloud_max) < 100:
            cloud_cond["lt"] = float(query.cloud_max)
        if cloud_cond:
            out["eo:cloud_cover"] = cloud_cond
    return out


def _bbox_of(raw: Dict[str, Any]) -> Optional[List[float]]:
    bbox = raw.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            return [round(float(v), 6) for v in bbox]
        except (TypeError, ValueError):
            return None
    geom = raw.get("geometry") or {}
    coords = geom.get("coordinates") if isinstance(geom, dict) else None
    if not coords:
        return None
    xs: List[float] = []
    ys: List[float] = []

    def _walk(node):
        if isinstance(node, (list, tuple)) and len(node) >= 2 and \
                isinstance(node[0], (int, float)) and isinstance(node[1], (int, float)):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)

    _walk(coords)
    if not xs or not ys:
        return None
    return [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)]


def _pretty_platform(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未知卫星"
    low = text.lower()
    mapping = {
        "sentinel-2a": "Sentinel-2A",
        "sentinel-2b": "Sentinel-2B",
        "sentinel-2c": "Sentinel-2C",
        "sentinel-1a": "Sentinel-1A",
        "sentinel-1b": "Sentinel-1B",
        "landsat-8": "Landsat 8",
        "landsat-9": "Landsat 9",
        "landsat-7": "Landsat 7",
    }
    if low in mapping:
        return mapping[low]
    if low.startswith("sentinel-2"):
        return "Sentinel-2" + low[-1:].upper()
    return text[:1].upper() + text[1:]


def _cloud_of(props: Dict[str, Any]) -> Optional[float]:
    for key in ("eo:cloud_cover", "cloud_cover", "cloudCover"):
        value = props.get(key)
        if value is not None:
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                continue
    return None


def _asset_href(raw: Dict[str, Any], *names: str) -> Optional[str]:
    assets = raw.get("assets") or {}
    for name in names:
        asset = assets.get(name)
        if isinstance(asset, dict):
            href = asset.get("href")
            if href:
                return str(href)
    return None


def _common_item(
    raw: Dict[str, Any],
    provider: "BaseProvider",
    collection: ImageryCollection,
    *,
    thumb_src: Optional[str],
    satellite: Optional[str] = None,
    cloud: Optional[float] = None,
    gsd: Optional[float] = None,
) -> Dict[str, Any]:
    props = raw.get("properties") or {}
    dt = props.get("datetime") or props.get("start_datetime") or props.get("created")
    return {
        "provider": provider.key,
        "provider_label": provider.label,
        "collection": collection.key,
        "collection_label": collection.label,
        "id": str(raw.get("id") or ""),
        "satellite": satellite or _pretty_platform(props.get("platform") or props.get("constellation")),
        "platform": props.get("platform") or props.get("constellation"),
        "orbit_direction": (props.get("sar:orbit_state") or props.get("orbitDirection")
                            or props.get("orbit_direction")),
        "processing_level": (props.get("processing:level") or props.get("processingLevel")
                             or props.get("productType") or props.get("processing_level")),
        "sun_elevation": (props.get("view:sun_elevation") or props.get("sunElevation")
                          or props.get("sun_elevation")),
        "datetime": dt,
        "cloud_cover": _cloud_of(props) if cloud is None else cloud,
        "gsd": gsd if gsd is not None else collection.gsd,
        "bbox": _bbox_of(raw),
        "footprint": raw.get("geometry"),
        "thumbnail_source": thumb_src,
        "tile_url": None,
        "downloadable": False,
        "download_mode": None,
        "source_url": None,
        "note": collection.note,
    }


# ==================== Provider 基类 ====================

class BaseProvider:
    key: str = ""
    label: str = ""
    homepage: str = ""
    note: str = ""
    auth: str = "none"  # none / account / vpn
    supports_tiles: bool = False
    supports_clip: bool = False
    supports_download: bool = False
    collections: Tuple[ImageryCollection, ...] = ()

    def configured(self) -> bool:
        return True

    def config_hint(self) -> str:
        return ""

    def collection(self, key: str) -> Optional[ImageryCollection]:
        for item in self.collections:
            if item.key == key:
                return item
        return None

    async def search(self, query: SearchQuery) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_item(self, collection: str, item_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def status(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "homepage": self.homepage,
            "note": self.note,
            "auth": self.auth,
            "configured": self.configured(),
            "config_hint": self.config_hint(),
            "supports": {
                "search": True,
                "tiles": self.supports_tiles,
                "clip": self.supports_clip,
                "download": self.supports_download,
            },
            "collections": [c.to_dict() for c in self.collections],
        }


# ==================== Microsoft Planetary Computer ====================

class PlanetaryComputerProvider(BaseProvider):
    key = "pc"
    label = "Planetary Computer"
    homepage = "https://planetarycomputer.microsoft.com/"
    note = "微软行星计算机，免密钥；支持地图瓦片预览与 AOI 裁剪 GeoTIFF 下载"
    auth = "none"
    supports_tiles = True
    supports_clip = True
    supports_download = True
    collections = (
        ImageryCollection(
            "sentinel-2-l2a", "Sentinel-2 L2A", 10, "optical",
            tile_params="assets=visual&asset_bidx=visual%7C1%2C2%2C3",
            clip_params="assets=visual&asset_bidx=visual%7C1%2C2%2C3",
            note="10m 多光谱，全球覆盖，约 5 天重访",
        ),
        ImageryCollection(
            "landsat-c2-l2", "Landsat 8/9 L2", 30, "optical",
            # ★ PC 数据 API 多资产必须重复 assets 参数; 单值 "red,green,blue" 会被判非法
            tile_params=(
                "assets=red&assets=green&assets=blue"
                "&color_formula=gamma%20RGB%202.7%2C%20saturation%201.5%2C%20sigmoidal%20RGB%2015%200.55"
            ),
            clip_params="assets=red&assets=green&assets=blue",
            note="30m 多光谱，1982 年至今",
        ),
        ImageryCollection(
            "sentinel-1-rtc", "Sentinel-1 RTC", 10, "sar",
            tile_params="assets=vv&rescale=0,0.3&colormap_name=gray",
            clip_params="assets=vv",
            note="10m 雷达，全天候，不受云雨影响",
        ),
        ImageryCollection(
            "naip", "NAIP 航空影像", 0.6, "aerial",
            tile_params="assets=image&asset_bidx=image%7C1%2C2%2C3",
            clip_params="assets=image&asset_bidx=image%7C1%2C2%2C3",
            note="0.6m 航空影像，仅美国本土",
        ),
    )

    def _tile_url(self, collection: ImageryCollection, item_id: str) -> Optional[str]:
        if not collection.tile_params:
            return None
        return (
            f"{PC_DATA}/item/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}@1x"
            f"?collection={collection.key}&item={item_id}&{collection.tile_params}"
        )

    async def search(self, query: SearchQuery) -> List[Dict[str, Any]]:
        colls = [c for c in query.collections if self.collection(c)] or [c.key for c in self.collections]
        # ★ PC 的 STAC 后端不允许跨 collection 检索 (不同 collection 可能落在不同后端),
        #   因此按集合拆成并发子请求, 任一集合失败不影响其他集合。
        async with httpx.AsyncClient(timeout=settings.imagery_http_timeout,
                                     headers=REQUEST_HEADERS, follow_redirects=True) as client:
            async def _one(coll_key: str):
                body: Dict[str, Any] = {
                    "collections": [coll_key],
                    "bbox": list(query.bbox),
                    "datetime": stac_interval(query.start, query.end),
                    "limit": max(1, min(query.limit, 50)),
                    "sortby": [{"field": "properties.datetime", "direction": "desc"}],
                }
                stac_query: Dict[str, Any] = {}
                if coll_key != "naip":
                    cloud_cond: Dict[str, float] = {}
                    if query.cloud_min is not None and float(query.cloud_min) > 0:
                        cloud_cond["gte"] = float(query.cloud_min)
                    if query.cloud_max is not None and float(query.cloud_max) < 100:
                        cloud_cond["lt"] = float(query.cloud_max)
                    if cloud_cond:
                        stac_query["eo:cloud_cover"] = cloud_cond
                if stac_query:
                    body["query"] = stac_query
                return await client.post(f"{PC_STAC}/search", json=body)

            responses = await asyncio.gather(
                *(_one(key) for key in colls), return_exceptions=True
            )

        items: List[Dict[str, Any]] = []
        failures: List[str] = []
        for coll_key, resp in zip(colls, responses):
            if isinstance(resp, Exception):
                failures.append(f"{coll_key}: {resp}")
                continue
            if resp.status_code >= 400:
                failures.append(f"{coll_key}: HTTP {resp.status_code}")
                continue
            for raw in (resp.json().get("features") or []):
                item = self.parse_item(raw, coll_key)
                if item:
                    items.append(item)
        if failures and not items:
            raise ImageryError("Planetary Computer 检索失败（" + "；".join(failures) + "）")
        return items

    def parse_item(self, raw: Dict[str, Any],
                   collection_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        coll = self.collection((collection_key or raw.get("collection") or "").lower())
        if not coll:
            return None
        item = _common_item(
            raw, self, coll,
            thumb_src=_asset_href(raw, "rendered_preview", "thumbnail"),
            cloud=_cloud_of(raw.get("properties") or {}),
            gsd=coll.gsd,
        )
        item["id"] = str(raw.get("id") or "")
        if not item["id"]:
            return None
        item["tile_url"] = self._tile_url(coll, item["id"])
        item["downloadable"] = bool(coll.clip_params)
        item["download_mode"] = "clip" if coll.clip_params else None
        item["source_url"] = f"{PC_STAC}/collections/{coll.key}/items/{item['id']}"
        return item

    async def get_item(self, collection: str, item_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.imagery_http_timeout,
                                     headers=REQUEST_HEADERS, follow_redirects=True) as client:
            resp = await client.get(f"{PC_STAC}/collections/{collection}/items/{item_id}")
            if resp.status_code >= 400:
                raise ImageryError(f"影像元数据获取失败 (HTTP {resp.status_code})")
            return resp.json()

    def clip_url(self, collection: ImageryCollection, item_id: str,
                 bbox: Tuple[float, float, float, float]) -> str:
        minx, miny, maxx, maxy = bbox
        query = f"collection={collection.key}&item={item_id}"
        if collection.clip_params:
            query += f"&{collection.clip_params}"
        return (
            f"{PC_DATA}/item/bbox/{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f}.tif"
            f"?{query}"
        )


# ==================== AWS Earth Search ====================

class EarthSearchProvider(BaseProvider):
    key = "es"
    label = "Earth Search (AWS)"
    homepage = "https://earth-search.aws.element84.com/v1"
    note = "AWS 开放数据 STAC 目录，免密钥；检索 + 快视图，地图预览请用 Planetary Computer"
    auth = "none"
    supports_download = False
    collections = (
        ImageryCollection("sentinel-2-l2a", "Sentinel-2 L2A", 10, "optical",
                          note="10m 多光谱，AWS 开放数据"),
        ImageryCollection("sentinel-1-grd", "Sentinel-1 GRD", 10, "sar",
                          note="10m 雷达，全天候"),
        ImageryCollection("landsat-c2-l2", "Landsat 8/9 L2", 30, "optical",
                          note="30m 多光谱"),
    )

    async def search(self, query: SearchQuery) -> List[Dict[str, Any]]:
        colls = [c for c in query.collections if self.collection(c)] or [c.key for c in self.collections]
        body: Dict[str, Any] = {
            "collections": colls,
            "bbox": list(query.bbox),
            "datetime": stac_interval(query.start, query.end),
            "limit": max(1, min(query.limit * 3, 100)),
        }
        stac_q = stac_filter_query(query)
        if stac_q:
            body["query"] = stac_q
        async with httpx.AsyncClient(timeout=settings.imagery_http_timeout,
                                     headers=REQUEST_HEADERS, follow_redirects=True) as client:
            resp = await client.post(f"{ES_STAC}/search", json=body)
            if resp.status_code >= 400:
                raise ImageryError(f"Earth Search 检索失败 (HTTP {resp.status_code})")
            data = resp.json()

        items: List[Dict[str, Any]] = []
        for raw in data.get("features") or []:
            props = raw.get("properties") or {}
            coll_key = (raw.get("collection") or "").lower()
            coll = self.collection(coll_key)
            if not coll:
                continue
            cloud = _cloud_of(props)
            if query.cloud_max is not None and cloud is not None and cloud > query.cloud_max:
                continue
            item = self.parse_item(raw, coll_key)
            if item:
                items.append(item)
        return items

    def parse_item(self, raw: Dict[str, Any],
                   collection_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        coll = self.collection((collection_key or raw.get("collection") or "").lower())
        if not coll:
            return None
        item = _common_item(
            raw, self, coll,
            thumb_src=_asset_href(raw, "thumbnail", "rendered_preview"),
            cloud=_cloud_of(raw.get("properties") or {}),
            gsd=coll.gsd,
        )
        if not item["id"]:
            return None
        item["source_url"] = f"{ES_STAC}/collections/{coll.key}/items/{item['id']}"
        return item

    async def get_item(self, collection: str, item_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.imagery_http_timeout,
                                     headers=REQUEST_HEADERS, follow_redirects=True) as client:
            resp = await client.get(f"{ES_STAC}/collections/{collection}/items/{item_id}")
            if resp.status_code >= 400:
                raise ImageryError(f"影像元数据获取失败 (HTTP {resp.status_code})")
            return resp.json()


# ==================== Copernicus Data Space ====================

class CopernicusProvider(BaseProvider):
    key = "cdse"
    label = "Copernicus Data Space"
    homepage = "https://dataspace.copernicus.eu/"
    note = "欧空局哥白尼数据空间，检索免登录；整景下载需免费账号，请用 Copernicus Browser"
    auth = "account"
    supports_download = False
    collections = (
        ImageryCollection("sentinel-2-l2a", "Sentinel-2 L2A", 10, "optical",
                          note="10m 多光谱，官方分发"),
        ImageryCollection("sentinel-1-grd", "Sentinel-1 GRD", 10, "sar",
                          note="10m 雷达，官方分发"),
    )

    async def search(self, query: SearchQuery) -> List[Dict[str, Any]]:
        colls = [c for c in query.collections if self.collection(c)] or [c.key for c in self.collections]
        body: Dict[str, Any] = {
            "collections": colls,
            "bbox": list(query.bbox),
            "datetime": stac_interval(query.start, query.end),
            "limit": max(1, min(query.limit * 3, 100)),
        }
        stac_q = stac_filter_query(query)
        if stac_q:
            body["query"] = stac_q
        async with httpx.AsyncClient(timeout=settings.imagery_http_timeout,
                                     headers=REQUEST_HEADERS, follow_redirects=True) as client:
            resp = await client.post(f"{CDSE_STAC}/search", json=body)
            if resp.status_code >= 400:
                raise ImageryError(f"Copernicus 检索失败 (HTTP {resp.status_code})")
            data = resp.json()

        items: List[Dict[str, Any]] = []
        for raw in data.get("features") or []:
            props = raw.get("properties") or {}
            coll_key = (raw.get("collection") or "").lower()
            coll = self.collection(coll_key)
            if not coll:
                continue
            cloud = _cloud_of(props)
            if query.cloud_max is not None and cloud is not None and cloud > query.cloud_max:
                continue
            item = self.parse_item(raw, coll_key)
            if item:
                items.append(item)
        return items

    def parse_item(self, raw: Dict[str, Any],
                   collection_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        coll = self.collection((collection_key or raw.get("collection") or "").lower())
        if not coll:
            return None
        item = _common_item(
            raw, self, coll,
            thumb_src=_asset_href(raw, "thumbnail", "quicklook"),
            cloud=_cloud_of(raw.get("properties") or {}),
            gsd=coll.gsd,
        )
        if not item["id"]:
            return None
        item["source_url"] = CDSE_BROWSER
        return item

    async def get_item(self, collection: str, item_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.imagery_http_timeout,
                                     headers=REQUEST_HEADERS, follow_redirects=True) as client:
            resp = await client.get(f"{CDSE_STAC}/collections/{collection}/items/{item_id}")
            if resp.status_code >= 400:
                raise ImageryError(f"影像元数据获取失败 (HTTP {resp.status_code})")
            return resp.json()


# ==================== Google Earth Engine (兜底) ====================

GEE_COLLECTIONS: Dict[str, Dict[str, Any]] = {
    "sentinel-2-l2a": {
        "asset": "COPERNICUS/S2_SR_HARMONIZED",
        "label": "Sentinel-2 L2A",
        "gsd": 10,
        "bands": ["B4", "B3", "B2"],
        "min": 0,
        "max": 3000,
        "cloud_prop": "CLOUDY_PIXEL_PERCENTAGE",
    },
    "landsat-c2-l2": {
        "asset": ["LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"],
        "label": "Landsat 8/9 L2",
        "gsd": 30,
        "bands": ["SR_B4", "SR_B3", "SR_B2"],
        "min": 0,
        "max": 30000,
        "cloud_prop": "CLOUD_COVER",
    },
}


class GeeProvider(BaseProvider):
    key = "gee"
    label = "Google Earth Engine"
    homepage = "https://earthengine.google.com/"
    note = "兜底数据源：需要 Google 账号 + 服务账号密钥，且需科学上网；默认未启用"
    auth = "vpn"
    supports_tiles = True
    supports_clip = True
    supports_download = True
    collections = (
        ImageryCollection("sentinel-2-l2a", "Sentinel-2 L2A (GEE)", 10, "optical",
                          note="GEE 在线计算，适合大范围时序筛选"),
        ImageryCollection("landsat-c2-l2", "Landsat 8/9 L2 (GEE)", 30, "optical",
                          note="GEE 在线计算"),
    )
    _ee = None

    def configured(self) -> bool:
        if importlib.util.find_spec("ee") is None:
            return False
        if settings.gee_key_file and os.path.isfile(settings.gee_key_file):
            return bool(settings.gee_service_account or settings.gee_key_file)
        return bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or settings.gee_service_account)

    def config_hint(self) -> str:
        if importlib.util.find_spec("ee") is None:
            return "未安装 earthengine-api：pip install earthengine-api"
        return "未配置服务账号：设置 GEE_SERVICE_ACCOUNT + GEE_KEY_FILE，并确保可访问 Google"

    def _init(self):
        if self._ee is not None:
            return self._ee
        import ee  # noqa: WPS433

        credentials = None
        if settings.gee_key_file and settings.gee_service_account:
            credentials = ee.ServiceAccountCredentials(
                settings.gee_service_account, settings.gee_key_file
            )
        try:
            ee.Initialize(credentials=credentials, project=settings.gee_project or None)
        except Exception as exc:  # noqa: BLE001
            raise ImageryError(f"Earth Engine 初始化失败: {exc}") from exc
        self._ee = ee
        return ee

    async def search(self, query: SearchQuery) -> List[Dict[str, Any]]:
        if not self.configured():
            raise ImageryError("Earth Engine 未配置（需服务账号 + 科学上网）")
        return await asyncio.to_thread(self._search_sync, query)

    def _search_sync(self, query: SearchQuery) -> List[Dict[str, Any]]:
        ee = self._init()
        geom = ee.Geometry.Rectangle(list(query.bbox), proj="EPSG:4326", geodesic=False)
        colls = [c for c in query.collections if c in GEE_COLLECTIONS] or list(GEE_COLLECTIONS)
        results: List[Dict[str, Any]] = []
        for coll_key in colls:
            spec = GEE_COLLECTIONS[coll_key]
            assets = spec["asset"] if isinstance(spec["asset"], list) else [spec["asset"]]
            for asset in assets:
                collection = ee.ImageCollection(asset).filterBounds(geom).filterDate(
                    query.start, query.end
                )
                if query.cloud_max is not None and query.cloud_max < 100:
                    collection = collection.filter(
                        ee.Filter.lte(spec["cloud_prop"], float(query.cloud_max))
                    )
                collection = collection.sort("system:time_start", False).limit(query.limit)
                try:
                    features = collection.getInfo().get("features") or []
                except Exception as exc:  # noqa: BLE001
                    raise ImageryError(f"Earth Engine 检索失败: {exc}") from exc
                for feat in features:
                    props = feat.get("properties") or {}
                    image_id = feat.get("id") or ""
                    if not image_id:
                        continue
                    millis = props.get("system:time_start")
                    dt = None
                    if millis:
                        try:
                            dt = datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc).isoformat()
                        except (TypeError, ValueError):
                            dt = None
                    coll = self.collection(coll_key)
                    item = {
                        "provider": self.key,
                        "provider_label": self.label,
                        "collection": coll_key,
                        "collection_label": coll.label if coll else spec["label"],
                        "id": image_id,
                        "satellite": _pretty_platform(props.get("SPACECRAFT_ID") or props.get("SATELLITE")),
                        "datetime": dt or props.get("DATE_ACQUIRED"),
                        "cloud_cover": (round(float(props.get(spec["cloud_prop"])), 2)
                                        if props.get(spec["cloud_prop"]) is not None else None),
                        "gsd": spec["gsd"],
                        "bbox": list(query.bbox),
                        "footprint": None,
                        "thumbnail_source": None,
                        "tile_url": None,
                        "downloadable": True,
                        "download_mode": "clip",
                        "source_url": f"https://code.earthengine.google.com/",
                        "note": "Earth Engine 在线计算",
                    }
                    try:
                        image = ee.Image(image_id)
                        map_id = image.getMapId({
                            "bands": spec["bands"], "min": spec["min"], "max": spec["max"],
                        })
                        item["tile_url"] = map_id["tile_fetcher"].url_format
                        item["thumbnail_source"] = image.getThumbURL({
                            "region": geom,
                            "dimensions": 480,
                            "format": "jpg",
                        })
                    except Exception:  # noqa: BLE001
                        logger.warning("[imagery] GEE 预览生成失败: %s", image_id)
                    results.append(item)
        return results

    async def get_item(self, collection: str, item_id: str) -> Dict[str, Any]:
        raise ImageryError("Earth Engine 不支持元数据回查，请直接使用检索结果")

    async def download_url(self, item_id: str, bbox: Tuple[float, float, float, float],
                           gsd: Optional[float]) -> str:
        if not self.configured():
            raise ImageryError("Earth Engine 未配置")

        def _url():
            ee = self._init()
            geom = ee.Geometry.Rectangle(list(bbox), proj="EPSG:4326", geodesic=False)
            image = ee.Image(item_id)
            return image.getDownloadURL({
                "region": geom,
                "scale": float(gsd or 10),
                "format": "GEO_TIFF",
            })

        return await asyncio.to_thread(_url)


# ==================== 注册表 ====================

PROVIDERS: Dict[str, BaseProvider] = {
    "pc": PlanetaryComputerProvider(),
    "es": EarthSearchProvider(),
    "cdse": CopernicusProvider(),
    "gee": GeeProvider(),
}


def list_providers() -> List[Dict[str, Any]]:
    return [provider.status() for provider in PROVIDERS.values()]
