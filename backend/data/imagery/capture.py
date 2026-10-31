"""框选取图: 把地图上的矩形范围抓成带 CRS 的 GeoTIFF, 供 SamSeg 解译。

数据源:
- esri     : ArcGIS World Imagery `export` 接口 (官方 bbox 导出, 无 VPN)
- tianditu : 天地图 img_w WMTS 瓦片拼接 (z≤18)

两者都输出 GeoTIFF:
- Esri      → EPSG:4326
- 天地图    → EPSG:3857 (WebMercator, 与瓦片网格一致)
产物落 `agent-files/imagery/captures/`, 并登记 `image_metadata`。
"""
from __future__ import annotations

import asyncio
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from backend.config import settings
from backend.data import business_db
from backend.data.imagery.providers import ImageryError

# ★ 本机环境变量 PROJ_LIB 指向 PostGIS 自带旧版 proj.db (LAYOUT.MINOR=2),
#   与当前 GDAL 期望的 >=3 冲突, 写 GeoTIFF 时 CRS 解析会报
#   "The WKT could not be parsed. OGR Error code 6"。
#   这里优先切到 rasterio 自带的 proj_data, 只在写文件时生效。
try:
    import rasterio as _rasterio

    _PROJ_DATA = Path(_rasterio.__file__).resolve().parent / "proj_data"
    if (_PROJ_DATA / "proj.db").is_file():
        os.environ["PROJ_DATA"] = str(_PROJ_DATA)
        os.environ["PROJ_LIB"] = str(_PROJ_DATA)
except Exception:  # noqa: BLE001
    _PROJ_DATA = None

ESRI_EXPORT_HOSTS = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export",
    "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export",
)
ESRI_TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TDT_TILE = "https://t{sub}.tianditu.gov.cn/img_w/wmts"
TDT_ATTR = "天地图"
ESRI_ATTR = "Esri, Maxar, Earthstar Geographics"
WEB_MERCATOR_HALF = 20037508.342789244
WEB_MERCATOR_RES0 = 156543.03392804097


def _validate_bbox(bbox: Sequence[float]) -> Tuple[float, float, float, float]:
    if len(bbox) != 4:
        raise ImageryError("bbox 必须为 [minx, miny, maxx, maxy]")
    minx, miny, maxx, maxy = [float(v) for v in bbox]
    if not (-180 <= minx < maxx <= 180 and -85.05 <= miny < maxy <= 85.05):
        raise ImageryError("框选范围超出经纬度范围或顺序错误")
    area = (maxx - minx) * (maxy - miny)
    if area > float(settings.imagery_capture_max_area_deg2):
        raise ImageryError(
            f"框选范围 {area:.4f}°² 过大，请控制在 "
            f"{settings.imagery_capture_max_area_deg2}°² 以内"
        )
    return minx, miny, maxx, maxy


def _target_size(bbox: Tuple[float, float, float, float], gsd: float,
                 max_size: int) -> Tuple[int, int]:
    minx, miny, maxx, maxy = bbox
    lat = (miny + maxy) / 2.0
    # ★ 宽高必须与 bbox 的“经纬度跨度”同比例：Esri export 会按 size 比例调整 bbox，
    #   若用米制宽高（x 乘 cos(lat)、y 不乘）会得到非等比尺寸 → 写回原始 bbox 时整体错位。
    px_per_deg = 111320.0 * max(0.2, math.cos(math.radians(lat))) / max(0.1, gsd)
    width = max(1, math.ceil((maxx - minx) * px_per_deg))
    height = max(1, math.ceil((maxy - miny) * px_per_deg))
    # 先按上限缩放，再按最小边放大；两步都保持 bbox 的经纬度宽高比
    scale = min(1.0, float(max_size) / max(width, height))
    width, height = max(1, int(width * scale)), max(1, int(height * scale))
    upscale = max(1.0, 128.0 / max(width, height))
    return max(1, int(width * upscale)), max(1, int(height * upscale))


def _safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text or "").strip("_")
    return cleaned or "capture"


# ==================== GeoTIFF 写出 ====================

def _write_geotiff(path: Path, array, transform, epsg: int, gsd: float,
                   nodata: Optional[int] = None) -> None:
    import numpy as np
    import rasterio
    from rasterio.crs import CRS

    arr = np.asarray(array)
    if arr.ndim == 2:
        arr = arr[np.newaxis, :, :]
    if arr.shape[0] > 3:
        arr = arr[:3]
    if arr.dtype != np.uint8:
        arr = arr.astype("uint8")
    profile = {
        "driver": "GTiff",
        "width": int(arr.shape[2]),
        "height": int(arr.shape[1]),
        "count": int(arr.shape[0]),
        "dtype": "uint8",
        "crs": CRS.from_epsg(int(epsg)),
        "transform": transform,
        "compress": "LZW",
    }
    if arr.shape[0] == 3:
        profile["photometric"] = "RGB"
    if nodata is not None:
        profile["nodata"] = nodata
    proj_env = ({"PROJ_DATA": str(_PROJ_DATA), "PROJ_LIB": str(_PROJ_DATA)}
                if _PROJ_DATA else {})
    with rasterio.Env(**proj_env):
        with rasterio.open(str(path), "w", **profile) as dst:
            dst.write(arr)


def _write_esri_geotiff(content: bytes, bbox: Tuple[float, float, float, float],
                        target_path: Path) -> Tuple[int, int, int]:
    import numpy as np
    from io import BytesIO
    from PIL import Image
    from rasterio.transform import from_bounds

    # ★ 不用 rasterio 直接开 Esri TIFF: 其内嵌 WKT 与本机 PROJ 版本偶发冲突
    #   (The WKT could not be parsed), 统一按 PNG 解码后自行写 GeoTIFF。
    image = Image.open(BytesIO(content)).convert("RGB")
    width, height = image.size
    array = np.array(image).transpose(2, 0, 1)
    if array.size == 0:
        raise ImageryError("Esri 返回空影像")
    transform = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], width, height)
    _write_geotiff(target_path, array, transform, 4326,
                   (bbox[2] - bbox[0]) * 111320.0 / max(1, width))
    return width, height, int(array.shape[0])


# ==================== 数据源抓取 ====================

async def _capture_esri(bbox: Tuple[float, float, float, float], gsd: float,
                        max_size: int, dest: Path) -> Dict[str, Any]:
    width, height = _target_size(bbox, gsd, max_size)
    params = {
        "bbox": ",".join(f"{v:.8f}" for v in bbox),
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{width},{height}",
        "format": "png32",
        "f": "image",
        "interpolation": "RSP_BilinearInterpolation",
        # ★ 禁止服务端按 size 比例调整 bbox；否则返回影像的实际范围与请求 bbox 不一致，
        #   我们按请求 bbox 写 GeoTIFF 时会产生整体偏移（解译矢量与底图错位）。
        "adjustAspectRatio": "false",
    }
    resp = None
    last_error: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=15,
                                 follow_redirects=True,
                                 headers={"User-Agent": "RSCD-Imagery/1.0"}) as client:
        for host in ESRI_EXPORT_HOSTS:
            try:
                candidate = await client.get(host, params=params)
                if candidate.status_code < 500:
                    resp = candidate
                    break
                last_error = ImageryError(f"Esri 取图失败 (HTTP {candidate.status_code})")
            except httpx.TransportError as exc:
                last_error = exc
                await asyncio.sleep(0.8)
    if resp is None:
        raise ImageryError(f"Esri 连接失败: {last_error or '未知网络错误'}")
    if resp.status_code >= 400:
        raise ImageryError(f"Esri 取图失败 (HTTP {resp.status_code})")
    content_type = (resp.headers.get("content-type") or "").lower()
    if "json" in content_type or "html" in content_type:
        raise ImageryError("Esri 取图返回错误页")
    w, h, bands = await asyncio.to_thread(_write_esri_geotiff, resp.content, bbox, dest)
    lat = (bbox[1] + bbox[3]) / 2.0
    resolution = (bbox[2] - bbox[0]) * 111320.0 * math.cos(math.radians(lat)) / max(1, w)
    return {
        "crs": "EPSG:4326",
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
        "width": w,
        "height": h,
        "band_count": bands,
        "resolution": round(resolution, 3),
        "attribution": ESRI_ATTR,
    }


# ==================== Esri 瓦片拼接（export 失败时的快速兜底） ====================
async def _fetch_esri_tile(client: httpx.AsyncClient, zoom: int, x: int, y: int) -> Any:
    resp = await client.get(
        ESRI_TILE.format(z=zoom, y=y, x=x),
        headers={"User-Agent": "Mozilla/5.0 RSCD/1.0"},
    )
    if resp.status_code >= 400 or not resp.content:
        raise ImageryError(f"Esri 瓦片获取失败 ({x},{y},{zoom})")
    from io import BytesIO
    from PIL import Image
    return Image.open(BytesIO(resp.content)).convert("RGB")


async def _capture_esri_tiles(bbox: Tuple[float, float, float, float], dest: Path,
                              target_gsd: float = 0.6) -> Dict[str, Any]:
    """直接拼接 Esri World Imagery XYZ 瓦片，避免 export 端点不可达时长时间等待。"""
    plan = _tile_plan(bbox, target_gsd)
    zoom, x0, y0, x1, y1 = plan["zoom"], plan["x0"], plan["y0"], plan["x1"], plan["y1"]
    cols, rows = plan["cols"], plan["rows"]
    from PIL import Image

    async with httpx.AsyncClient(
        timeout=12, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 RSCD/1.0"},
    ) as client:
        semaphore = asyncio.Semaphore(12)

        async def _limited(x: int, y: int):
            async with semaphore:
                return await _fetch_esri_tile(client, zoom, x, y)

        tiles = await asyncio.gather(*[
            _limited(x, y)
            for y in range(y0, y1 + 1)
            for x in range(x0, x1 + 1)
        ])
    mosaic = Image.new("RGB", (cols * 256, rows * 256))
    for index, tile in enumerate(tiles):
        mosaic.paste(tile, ((index % cols) * 256, (index // cols) * 256))

    left_px = plan["x0f"] * 256.0
    top_px = plan["y0f"] * 256.0
    right_px = plan["x1f"] * 256.0
    bottom_px = plan["y1f"] * 256.0
    crop = (
        max(0, int(round(left_px - x0 * 256))),
        max(0, int(round(top_px - y0 * 256))),
        min(cols * 256, int(round(right_px - x0 * 256))),
        min(rows * 256, int(round(bottom_px - y0 * 256))),
    )
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        crop = (0, 0, cols * 256, rows * 256)
    image = mosaic.crop(crop)
    width, height = image.size
    res = WEB_MERCATOR_RES0 / (2 ** zoom)
    origin_x = -WEB_MERCATOR_HALF + x0 * 256 * res
    origin_y = WEB_MERCATOR_HALF - y0 * 256 * res
    from rasterio.transform import Affine
    transform = Affine(res, 0, origin_x + crop[0] * res, 0, -res,
                       origin_y - crop[1] * res)
    bounds = (
        origin_x + crop[0] * res, origin_y - crop[3] * res,
        origin_x + crop[2] * res, origin_y - crop[1] * res,
    )
    import numpy as np
    await asyncio.to_thread(
        _write_geotiff, dest, np.array(image).transpose(2, 0, 1), transform, 3857, res,
    )
    return {
        "crs": "EPSG:3857",
        "bbox": [round(v, 3) for v in bounds],
        "width": width, "height": height, "band_count": 3,
        "resolution": round(res, 3), "attribution": ESRI_ATTR, "zoom": zoom,
    }


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> Tuple[float, float]:
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat = max(-85.05112878, min(85.05112878, lat))
    y = (1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _tile_plan(bbox: Tuple[float, float, float, float], target_gsd: float) -> Dict[str, Any]:
    minx, miny, maxx, maxy = bbox
    lat = (miny + maxy) / 2.0
    want = math.ceil(math.log2(
        WEB_MERCATOR_RES0 * max(0.2, math.cos(math.radians(lat))) / max(0.2, target_gsd)
    ))
    zoom = max(1, min(18, want))
    max_size = int(settings.imagery_capture_max_size)
    max_tiles = int(settings.imagery_capture_max_tiles)
    while zoom > 1:
        x0f, y0f = _lonlat_to_tile(minx, maxy, zoom)
        x1f, y1f = _lonlat_to_tile(maxx, miny, zoom)
        x0, y0 = int(math.floor(x0f)), int(math.floor(y0f))
        x1, y1 = int(math.floor(x1f)), int(math.floor(y1f))
        cols, rows = x1 - x0 + 1, y1 - y0 + 1
        if cols * 256 <= max_size and rows * 256 <= max_size and cols * rows <= max_tiles:
            return {
                "zoom": zoom, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "cols": cols, "rows": rows, "x0f": x0f, "y0f": y0f,
                "x1f": x1f, "y1f": y1f,
            }
        zoom -= 1
    raise ImageryError("框选范围过大，无法在限制内拼接天地图影像")


async def _fetch_tdt_tile(client: httpx.AsyncClient, zoom: int, x: int, y: int) -> Any:
    key = settings.tianditu_key
    if not key:
        raise ImageryError("未配置 TIANDITU_KEY，无法使用天地图取图")
    sub = (x + y) % 8
    params = {
        "SERVICE": "WMTS", "REQUEST": "GetTile", "VERSION": "1.0.0",
        "LAYER": "img", "TILEMATRIXSET": "w", "FORMAT": "tiles",
        "TILEMATRIX": str(zoom), "TILEROW": str(y), "TILECOL": str(x),
        "tk": key,
    }
    resp = await client.get(TDT_TILE.format(sub=sub), params=params)
    if resp.status_code >= 400 or not resp.content:
        raise ImageryError(f"天地图瓦片获取失败 ({x},{y},{zoom})")
    from io import BytesIO
    from PIL import Image
    return Image.open(BytesIO(resp.content)).convert("RGB")


async def _capture_tianditu(bbox: Tuple[float, float, float, float], dest: Path,
                            target_gsd: float = 0.6) -> Dict[str, Any]:
    plan = _tile_plan(bbox, target_gsd)
    zoom, x0, y0, x1, y1 = plan["zoom"], plan["x0"], plan["y0"], plan["x1"], plan["y1"]
    cols, rows = plan["cols"], plan["rows"]
    from PIL import Image

    async with httpx.AsyncClient(
        timeout=settings.imagery_http_timeout,
        follow_redirects=True,
        # 天地图浏览器端 Key 会校验 Referer/Origin；服务端 Key 也兼容这组头
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RSCD/1.0",
            "Referer": settings.tianditu_referer,
            "Origin": settings.tianditu_referer.rstrip("/"),
        },
    ) as client:
        semaphore = asyncio.Semaphore(12)

        async def _limited(x: int, y: int):
            async with semaphore:
                return await _fetch_tdt_tile(client, zoom, x, y)

        tiles = await asyncio.gather(*[
            _limited(x, y)
            for y in range(y0, y1 + 1)
            for x in range(x0, x1 + 1)
        ])
    mosaic = Image.new("RGB", (cols * 256, rows * 256))
    for index, tile in enumerate(tiles):
        dx = index % cols
        dy = index // cols
        mosaic.paste(tile, (dx * 256, dy * 256))

    left_px = plan["x0f"] * 256.0
    top_px = plan["y0f"] * 256.0
    right_px = plan["x1f"] * 256.0
    bottom_px = plan["y1f"] * 256.0
    crop = (
        max(0, int(round(left_px - x0 * 256))),
        max(0, int(round(top_px - y0 * 256))),
        min(cols * 256, int(round(right_px - x0 * 256))),
        min(rows * 256, int(round(bottom_px - y0 * 256))),
    )
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        crop = (0, 0, cols * 256, rows * 256)
    image = mosaic.crop(crop)
    width, height = image.size
    res = WEB_MERCATOR_RES0 / (2 ** zoom)
    origin_x = -WEB_MERCATOR_HALF + x0 * 256 * res
    origin_y = WEB_MERCATOR_HALF - y0 * 256 * res
    from rasterio.transform import Affine
    transform = Affine(
        res, 0, origin_x + crop[0] * res,
        0, -res, origin_y - crop[1] * res,
    )
    bounds = (
        origin_x + crop[0] * res,
        origin_y - crop[3] * res,
        origin_x + crop[2] * res,
        origin_y - crop[1] * res,
    )
    import numpy as np
    await asyncio.to_thread(
        _write_geotiff, dest, np.array(image).transpose(2, 0, 1),
        transform, 3857, res,
    )
    return {
        "crs": "EPSG:3857",
        "bbox": [round(v, 3) for v in bounds],
        "width": width,
        "height": height,
        "band_count": 3,
        "resolution": round(res, 3),
        "attribution": TDT_ATTR,
        "zoom": zoom,
    }


# ==================== 对外服务 ====================

class CaptureService:
    def __init__(self) -> None:
        self.root = Path(settings.file_storage_root).resolve() / "imagery"
        self.capture_dir = self.root / "captures"
        self.capture_dir.mkdir(parents=True, exist_ok=True)

    async def capture(self, source: str, bbox: Sequence[float],
                      target_gsd: float = 0.6) -> Dict[str, Any]:
        source = (source or "esri").lower()
        if source not in ("esri", "tianditu"):
            raise ImageryError(f"不支持的取图源: {source}")
        minx, miny, maxx, maxy = _validate_bbox(bbox)
        target_gsd = max(0.2, min(float(target_gsd or 0.6), 10.0))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = self.capture_dir / f"capture_{source}_{stamp}.tif"
        if dest.exists():
            dest = self.capture_dir / f"capture_{source}_{stamp}_{int(datetime.now().timestamp() % 1000)}.tif"

        fallback = None
        if source == "esri":
            try:
                info = await _capture_esri((minx, miny, maxx, maxy), target_gsd,
                                           int(settings.imagery_capture_max_size), dest)
                sensor = "Esri World Imagery"
                srid = 4326
            except ImageryError as exc:
                if not settings.tianditu_key:
                    raise
                # Esri 偶发连接失败 → 自动回退天地图, 保证框选解译不中断
                source = "tianditu"
                dest = self.capture_dir / f"capture_tianditu_{stamp}.tif"
                info = await _capture_tianditu((minx, miny, maxx, maxy), dest, target_gsd)
                sensor = "天地图影像"
                srid = 3857
                fallback = {"from": "esri", "reason": str(exc)}
        else:
            info = await _capture_tianditu((minx, miny, maxx, maxy), dest, target_gsd)
            sensor = "天地图影像"
            srid = 3857

        layer_name = _safe_name(dest.stem[:64])
        registered = await asyncio.to_thread(
            business_db.register_image_metadata,
            layer_name, "cd_raster", str(dest), tuple(info["bbox"]), srid,
            info["resolution"], info["width"], info["height"], info["band_count"],
            None, sensor, "bbox_capture",
        )
        rel = dest.relative_to(Path(settings.file_storage_root).resolve()).as_posix()
        return {
            "status": "success",
            "source": source,
            "source_label": sensor,
            "attribution": info["attribution"],
            "file_name": dest.name,
            "file_path": str(dest),
            "rel_path": rel,
            "preview_url": f"/api/v1/download/{rel}?preview=1",
            "download_url": f"/api/v1/download/{rel}",
            "layer_name": layer_name,
            "workspace": "cd_raster",
            "registered": bool(registered),
            "bbox": info["bbox"],
            "input_bbox": [minx, miny, maxx, maxy],
            "crs": info["crs"],
            "width": info["width"],
            "height": info["height"],
            "band_count": info["band_count"],
            "gsd": info["resolution"],
            "zoom": info.get("zoom"),
            "fallback": fallback,
        }


    async def georeference(
        self,
        content: bytes,
        bbox: Sequence[float],
        *,
        name: str = "",
        date: str = "",
        sensor: str = "Esri Wayback",
    ) -> Dict[str, Any]:
        """把浏览器拼接好的 WebMercator 截图按 bbox 写成带 CRS 的 GeoTIFF。

        浏览器直连 Esri Wayback 瓦片，避免了服务端到 Wayback 的网络不稳定；
        这里只做“图片 + 范围 → GeoTIFF + 元数据登记”的确定性工作。
        """
        from io import BytesIO
        from PIL import Image
        from rasterio.transform import from_bounds
        from rasterio.warp import transform_bounds

        minx, miny, maxx, maxy = _validate_bbox(bbox)
        if not content:
            raise ImageryError("空图片数据")
        image = Image.open(BytesIO(content)).convert("RGB")
        width, height = image.size
        if width < 1 or height < 1:
            raise ImageryError("图片尺寸无效")
        merc = transform_bounds("EPSG:4326", "EPSG:3857", minx, miny, maxx, maxy)
        transform = from_bounds(merc[0], merc[1], merc[2], merc[3], width, height)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = _safe_name(name or f"wayback_{date}_{stamp}")
        dest = self.capture_dir / f"{safe}_{stamp}.tif"
        import numpy as np
        await asyncio.to_thread(
            _write_geotiff, dest, np.array(image).transpose(2, 0, 1), transform, 3857,
            (merc[2] - merc[0]) / max(1, width),
        )
        resolution = (merc[2] - merc[0]) / max(1, width)
        layer_name = _safe_name(dest.stem[:64])
        registered = await asyncio.to_thread(
            business_db.register_image_metadata,
            layer_name, "cd_raster", str(dest), tuple(merc), 3857,
            round(resolution, 3), width, height, 3,
            date or None, sensor, "wayback_capture",
        )
        rel = dest.relative_to(Path(settings.file_storage_root).resolve()).as_posix()
        return {
            "status": "success",
            "source": "wayback",
            "source_label": sensor,
            "attribution": ESRI_ATTR,
            "file_name": dest.name,
            "file_path": str(dest),
            "rel_path": rel,
            "preview_url": f"/api/v1/download/{rel}?preview=1",
            "download_url": f"/api/v1/download/{rel}",
            "layer_name": layer_name,
            "workspace": "cd_raster",
            "registered": bool(registered),
            "bbox": [minx, miny, maxx, maxy],
            "crs": "EPSG:3857",
            "width": width,
            "height": height,
            "band_count": 3,
            "gsd": round(resolution, 3),
            "acquired_at": date or None,
        }


capture_service = CaptureService()
