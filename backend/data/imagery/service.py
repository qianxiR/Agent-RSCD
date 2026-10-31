"""影像检索编排: 多源检索、快视图缓存、AOI 裁剪下载与元数据登记。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import httpx

from backend.config import settings
from backend.data import business_db
from backend.data.imagery.providers import (
    PROVIDERS,
    ImageryError,
    SearchQuery,
    list_providers,
)

logger = logging.getLogger(__name__)

MAX_THUMB_BYTES = 12 * 1024 * 1024
THUMB_WIDTH = 480


def _with_param(url: str, key: str, value: Any) -> str:
    """给 URL 追加/覆盖一个查询参数 (保留原有参数顺序)。"""
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != key]
    query.append((key, str(value)))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _norm_filter(value: Any) -> str:
    """归一化平台/轨道/处理级别，便于大小写与连字符兼容匹配。"""
    return re.sub(r"[\s_\-]+", "", str(value or "").lower())


def _safe_name(text: str, limit: int = 48) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text or "").strip("_")
    return (cleaned[:limit].rstrip("_") or "image")


class ImageryService:
    """影像检索服务单例 (无状态请求 + 轻量内存缓存)。"""

    def __init__(self) -> None:
        root = Path(settings.file_storage_root).resolve() / "imagery"
        self.root = root
        self.download_dir = root / "downloads"
        self.thumb_dir = root / "thumbs"
        for folder in (self.download_dir, self.thumb_dir):
            folder.mkdir(parents=True, exist_ok=True)
        # (provider, collection, item_id) -> (缓存时间, 归一化 item)
        self._item_cache: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}
        self._item_cache_ttl = 6 * 3600

    # ==================== 数据源 ====================

    def providers(self) -> List[Dict[str, Any]]:
        return list_providers()

    # ==================== 检索 ====================

    async def search(
        self,
        *,
        providers: Optional[Sequence[str]] = None,
        collections: Optional[Sequence[str]] = None,
        bbox: Sequence[float],
        start: str,
        end: str,
        cloud_min: Optional[float] = 0.0,
        cloud_max: Optional[float] = 30.0,
        platform: str = "",
        orbit_direction: str = "",
        processing_level: str = "",
        sun_elevation_min: Optional[float] = None,
        max_gsd: Optional[float] = None,
        limit: int = 24,
        sort: str = "date_desc",
    ) -> Dict[str, Any]:
        if not settings.imagery_enabled:
            raise ImageryError("影像检索功能已关闭 (IMAGERY_ENABLED=false)")
        if len(bbox) != 4:
            raise ImageryError("bbox 必须为 [minx, miny, maxx, maxy]")
        minx, miny, maxx, maxy = [float(v) for v in bbox]
        if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
            raise ImageryError("bbox 超出经纬度范围或顺序错误")

        selected = [p for p in (providers or ["pc"]) if p in PROVIDERS]
        if not selected:
            raise ImageryError(f"未知数据源: {', '.join(providers or [])}")
        limit = max(1, min(int(limit), int(settings.imagery_search_max_items)))
        per_provider_limit = max(6, min(limit, 30))
        query = SearchQuery(
            bbox=(minx, miny, maxx, maxy),
            start=start,
            end=end,
            cloud_min=cloud_min,
            cloud_max=cloud_max,
            platform=platform,
            orbit_direction=orbit_direction,
            processing_level=processing_level,
            sun_elevation_min=sun_elevation_min,
            limit=per_provider_limit,
            collections=list(collections or []),
        )

        tasks = [self._search_one(key, query) for key in selected]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        items: List[Dict[str, Any]] = []
        errors: Dict[str, str] = {}
        for key, result in zip(selected, results):
            if isinstance(result, Exception):
                message = str(result) or result.__class__.__name__
                errors[key] = message
                logger.warning("[imagery] %s 检索失败: %s", key, message)
                continue
            items.extend(result)

        # 去重 (同一数据源同 id) + 云量/分辨率兜底过滤
        seen = set()
        unique: List[Dict[str, Any]] = []
        for item in items:
            marker = (item.get("provider"), item.get("id"))
            if not item.get("id") or marker in seen:
                continue
            seen.add(marker)
            cloud = item.get("cloud_cover")
            if cloud_min is not None and cloud is not None and float(cloud) < float(cloud_min):
                continue
            if cloud_max is not None and cloud is not None and cloud > float(cloud_max):
                continue
            if platform:
                value = item.get("platform")
                if value and _norm_filter(platform) not in _norm_filter(value):
                    continue
            if orbit_direction:
                value = item.get("orbit_direction")
                if value and _norm_filter(orbit_direction) not in _norm_filter(value):
                    continue
            if processing_level:
                value = item.get("processing_level")
                if value and _norm_filter(processing_level) not in _norm_filter(value):
                    continue
            if sun_elevation_min is not None and item.get("sun_elevation") is not None:
                try:
                    if float(item["sun_elevation"]) < float(sun_elevation_min):
                        continue
                except (TypeError, ValueError):
                    pass
            gsd = item.get("gsd")
            if max_gsd is not None and gsd is not None and gsd > float(max_gsd):
                continue
            unique.append(item)

        unique.sort(key=lambda it: self._sort_key(it, sort))
        total = len(unique)
        unique = unique[:limit]

        public = [self._public_item(item) for item in unique]
        return {
            "items": public,
            "count": len(public),
            "total": total,
            "errors": errors,
            "providers": [key for key in selected],
        }

    async def _search_one(self, key: str, query: SearchQuery) -> List[Dict[str, Any]]:
        provider = PROVIDERS[key]
        items = await provider.search(query)
        now = time.time()
        for item in items:
            if item.get("id"):
                self._item_cache[(key, item["collection"], item["id"])] = (now, item)
        # 简单容量控制: 超过 2000 条时清掉最旧的一批, 避免长期运行内存膨胀
        if len(self._item_cache) > 2000:
            for stale in list(self._item_cache)[:500]:
                self._item_cache.pop(stale, None)
        return items

    @staticmethod
    def _sort_key(item: Dict[str, Any], sort: str):
        def _ts(value: Any) -> float:
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                return 0.0

        dt = _ts(item.get("datetime"))
        cloud = item.get("cloud_cover")
        gsd = item.get("gsd")
        if sort == "cloud_asc":
            return (cloud is None, cloud if cloud is not None else 999.0, -dt)
        if sort == "gsd_asc":
            return (gsd is None, gsd if gsd is not None else 9999.0, -dt)
        # 默认按时间倒序 (最新在前)
        return (-dt,)

    def _public_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        data = {k: v for k, v in item.items() if not k.startswith("_")}
        source = data.pop("thumbnail_source", None)
        thumbnail = None
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            thumbnail = (
                "/api/v1/imagery/thumbnail"
                f"?provider={item['provider']}&collection={quote(item['collection'])}"
                f"&item={quote(item['id'])}"
            )
        data["thumbnail"] = thumbnail
        data.pop("thumbnail_source", None)
        return data

    # ==================== 快视图代理 ====================

    async def _get_item(self, provider_key: str, collection: str, item_id: str) -> Dict[str, Any]:
        key = (provider_key, collection, item_id)
        cached = self._item_cache.get(key)
        if cached and time.time() - cached[0] < self._item_cache_ttl:
            return cached[1]
        provider = PROVIDERS.get(provider_key)
        if not provider:
            raise ImageryError(f"未知数据源: {provider_key}")
        raw = await provider.get_item(collection, item_id)
        parser = getattr(provider, "parse_item", None)
        item = parser(raw, collection) if callable(parser) else None
        if not item:
            raise ImageryError("影像元数据解析失败")
        self._item_cache[key] = (time.time(), item)
        return item

    async def thumbnail(self, provider_key: str, collection: str, item_id: str,
                        width: int = THUMB_WIDTH) -> Tuple[Path, str]:
        item = await self._get_item(provider_key, collection, item_id)
        source = item.get("thumbnail_source")
        if not isinstance(source, str) or not source.startswith(("http://", "https://")):
            raise ImageryError("该影像没有可用快视图")
        if provider_key == "pc":
            # ★ PC 的 preview 端点用 max_size 等比缩放; 传 width 会按原图宽度裁切 (体积仍很大)
            source = _with_param(source, "max_size", max(240, min(int(width), 1200)))

        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:32]
        for cached in self.thumb_dir.glob(f"{digest}.*"):
            if time.time() - cached.stat().st_mtime < settings.imagery_thumbnail_ttl:
                media = "image/png" if cached.suffix.lower() == ".png" else "image/jpeg"
                return cached, media

        async with httpx.AsyncClient(
            timeout=settings.imagery_http_timeout, follow_redirects=True,
            headers={"User-Agent": "RSCD-Imagery/1.0"},
        ) as client:
            async with client.stream("GET", source) as resp:
                if resp.status_code >= 400:
                    raise ImageryError(f"快视图获取失败 (HTTP {resp.status_code})")
                media = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
                if not (media.startswith("image/") or media in
                        ("application/octet-stream", "binary/octet-stream")):
                    raise ImageryError("快视图返回的不是图片")
                suffix = ".png" if "png" in media else ""
                temp = self.thumb_dir / f"{digest}.part"
                size = 0
                with temp.open("wb") as handle:
                    async for chunk in resp.aiter_bytes(256 * 1024):
                        size += len(chunk)
                        if size > MAX_THUMB_BYTES:
                            temp.unlink(missing_ok=True)
                            raise ImageryError("快视图超过大小上限")
                        handle.write(chunk)
        if not suffix:
            # 部分数据源 (如 CREODIAS) 用 application/octet-stream 返回 JPEG, 按文件头判定
            with temp.open("rb") as handle:
                head = handle.read(8)
            suffix = ".png" if head.startswith(b"\x89PNG") else ".jpg"
        target = self.thumb_dir / f"{digest}{suffix}"
        temp.replace(target)
        media_type = "image/png" if suffix == ".png" else "image/jpeg"
        return target, media_type

    # ==================== 下载 ====================

    async def capture_change(
        self,
        *,
        bbox: Sequence[float],
        t1_date: str,
        t2_date: str,
        window_days: int = 60,
        cloud_max: float = 30.0,
        collection: str = "sentinel-2-l2a",
        max_gsd: Optional[float] = 20.0,
    ) -> Dict[str, Any]:
        """变化检测取图：同一 AOI 的两个目标日期 → 各选一景 → 裁剪两期 GeoTIFF。

        选取规则：目标日期 ±window_days 内、云量 ≤cloud_max、能完整覆盖 AOI 的 PC 影像；
        综合“日期接近度 + 云量”排序，优先同季、少云的景。两期都下载后再交给 SamSeg 变化检测。
        """
        if not settings.imagery_enabled:
            raise ImageryError("影像检索功能已关闭 (IMAGERY_ENABLED=false)")
        if len(bbox) != 4:
            raise ImageryError("bbox 必须为 [minx, miny, maxx, maxy]")
        minx, miny, maxx, maxy = [float(v) for v in bbox]
        if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
            raise ImageryError("bbox 超出经纬度范围或顺序错误")
        area = (maxx - minx) * (maxy - miny)
        max_area = float(settings.imagery_capture_max_area_deg2)
        if area > max_area:
            raise ImageryError(
                f"变化检测范围 {area:.4f}°² 过大，请控制在 {max_area}°² 以内"
            )
        try:
            target_t1 = datetime.strptime(str(t1_date)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            target_t2 = datetime.strptime(str(t2_date)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ImageryError("时相日期格式应为 YYYY-MM-DD") from exc
        if target_t1 >= target_t2:
            raise ImageryError("T1 必须早于 T2")
        window = max(3, min(int(window_days or 60), 90))

        async def _pick(target: datetime, phase: str) -> Dict[str, Any]:
            start = (target - timedelta(days=window)).strftime("%Y-%m-%d")
            end = (target + timedelta(days=window)).strftime("%Y-%m-%d")
            result = await self.search(
                providers=["pc"],
                collections=[collection],
                bbox=(minx, miny, maxx, maxy),
                start=start,
                end=end,
                cloud_min=0,
                cloud_max=cloud_max,
                max_gsd=max_gsd,
                limit=40,
                sort="date_desc",
            )
            candidates = [
                item for item in result.get("items", [])
                if self._item_covers(item.get("bbox"), (minx, miny, maxx, maxy))
            ]
            if not candidates:
                raise ImageryError(
                    f"{phase} 目标日期 {target.date()} ±{window} 天内没有完整覆盖该范围的 "
                    f"{collection} 影像（云量 ≤{cloud_max:g}%），请换日期或缩小范围"
                )

            def score(item: Dict[str, Any]) -> float:
                try:
                    acquired = datetime.fromisoformat(
                        str(item.get("datetime") or "").replace("Z", "+00:00")
                    )
                    days = abs((acquired - target).total_seconds()) / 86400.0
                except (TypeError, ValueError):
                    days = float(window)
                cloud = item.get("cloud_cover")
                cloud_value = float(cloud) if cloud is not None else 100.0
                return days / max(1, window) + cloud_value / 100.0

            chosen = sorted(candidates, key=score)[0]
            provider_key = str(chosen.get("provider") or "pc")
            chosen_collection = str(chosen.get("collection") or collection)
            download = await self.download(
                provider_key=provider_key,
                collection=chosen_collection,
                item_id=str(chosen["id"]),
                bbox=(minx, miny, maxx, maxy),
                name=f"change_{phase}_{target.strftime('%Y%m%d')}",
            )
            if not download.get("file_path"):
                raise ImageryError(f"{phase} 影像下载失败")
            download["phase"] = phase
            download["target_date"] = target.strftime("%Y-%m-%d")
            download["thumbnail"] = chosen.get("thumbnail")
            download["cloud_cover"] = chosen.get("cloud_cover")
            download["preview_url"] = f"{download['download_url']}?preview=1"
            return download

        t1 = await _pick(target_t1, "t1")
        t2 = await _pick(target_t2, "t2")
        if t1.get("item_id") and t1.get("item_id") == t2.get("item_id"):
            raise ImageryError("两期选中了同一景影像，请把 T1/T2 日期拉开一些")
        acquired_t1 = self._parse_acquired(t1.get("acquired_at"))
        acquired_t2 = self._parse_acquired(t2.get("acquired_at"))
        if acquired_t1 and acquired_t2 and acquired_t1 >= acquired_t2:
            raise ImageryError(
                "实际取到的两期影像日期顺序反了（T1 不早于 T2），请调整 T1/T2 日期后重试"
            )
        return {
            "status": "success",
            "source": "sentinel-2-l2a",
            "label": "Sentinel-2 L2A · 10m",
            "bbox": [minx, miny, maxx, maxy],
            "window_days": window,
            "cloud_max": cloud_max,
            "t1": t1,
            "t2": t2,
        }

    @staticmethod
    def _parse_acquired(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in (
            "%Y/%m/%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _item_covers(item_bbox: Any, aoi: Sequence[float]) -> bool:
        try:
            ib = [float(v) for v in item_bbox]
            if len(ib) != 4:
                return False
            return (ib[0] <= aoi[0] + 1e-6 and ib[1] <= aoi[1] + 1e-6
                    and ib[2] >= aoi[2] - 1e-6 and ib[3] >= aoi[3] - 1e-6)
        except (TypeError, ValueError):
            return False

    async def download(
        self,
        *,
        provider_key: str,
        collection: str,
        item_id: str,
        bbox: Optional[Sequence[float]] = None,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        provider = PROVIDERS.get(provider_key)
        if not provider:
            raise ImageryError(f"未知数据源: {provider_key}")
        if not provider.supports_download:
            hint = provider.note or "该数据源不支持服务端下载"
            raise ImageryError(f"{provider.label} 不支持在这里下载：{hint}")
        coll = provider.collection(collection)
        if not coll:
            raise ImageryError(f"未知影像集合: {collection}")

        cached = self._item_cache.get((provider_key, collection, item_id))
        if cached and time.time() - cached[0] < self._item_cache_ttl:
            item = cached[1]
        else:
            item = await self._get_item(provider_key, collection, item_id)

        target_bbox = self._resolve_bbox(bbox, item.get("bbox"))
        minx, miny, maxx, maxy = target_bbox
        max_area = float(getattr(settings, "imagery_download_max_area_deg2", 0.5))
        # 面积上限按「用户请求范围」判定, 避免大范围被景边界裁剪后悄悄通过
        requested = list(bbox) if bbox else list(item.get("bbox") or target_bbox)
        if len(requested) == 4:
            requested_area = abs((float(requested[2]) - float(requested[0]))
                                 * (float(requested[3]) - float(requested[1])))
            if requested_area > max_area:
                raise ImageryError(
                    f"下载范围 {requested_area:.2f}°² 过大，请控制在 {max_area:.2f}°² 以内"
                    f"（约 {(max_area ** 0.5) * 111:.0f}km × {(max_area ** 0.5) * 111:.0f}km）"
                )
        area = (maxx - minx) * (maxy - miny)
        if area > max_area:
            raise ImageryError(
                f"下载范围 {area:.2f}°² 过大，请控制在 {max_area:.2f}°² 以内"
                f"（约 {(max_area ** 0.5) * 111:.0f}km × {(max_area ** 0.5) * 111:.0f}km）"
            )

        acquired = str(item.get("datetime") or "")
        day = re.sub(r"[^0-9]", "", acquired[:10]) or datetime.now().strftime("%Y%m%d")
        stem = _safe_name(name) if name else _safe_name(f"{provider_key}_{coll.key}_{item_id}", 64)
        filename = f"{stem}_{day}.tif"
        destination = self.download_dir / filename
        temp = destination.with_suffix(".tif.part")
        max_bytes = max(1, int(settings.imagery_download_max_mb)) * 1024 * 1024

        if provider_key == "pc":
            url = provider.clip_url(coll, item_id, target_bbox)
        elif provider_key == "gee":
            url = await provider.download_url(item_id, target_bbox, coll.gsd)
        else:
            raise ImageryError(f"{provider.label} 暂不支持服务端下载")

        size = await self._stream_to_file(url, temp, max_bytes)
        temp.replace(destination)

        meta = await asyncio.to_thread(self._read_raster_meta, destination)
        layer_name = destination.stem
        registered = await asyncio.to_thread(
            business_db.register_image_metadata,
            layer_name,
            "cd_raster",
            str(destination),
            target_bbox,
            4326,
            meta.get("resolution"),
            meta.get("width"),
            meta.get("height"),
            meta.get("band_count"),
            acquired or None,
            item.get("satellite") or provider.label,
            "imagery_search",
        )

        sidecar = destination.with_suffix(".json")
        sidecar.write_text(json.dumps({
            "provider": provider_key,
            "collection": collection,
            "item_id": item_id,
            "item": {k: v for k, v in item.items() if k != "thumbnail_source"},
            "bbox": target_bbox,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        rel_path = destination.relative_to(Path(settings.file_storage_root).resolve()).as_posix()
        return {
            "status": "success",
            "provider": provider_key,
            "collection": collection,
            "item_id": item_id,
            "file_name": destination.name,
            "file_path": str(destination),
            "rel_path": rel_path,
            "download_url": f"/api/v1/download/{rel_path}",
            "size_bytes": size,
            "bbox": target_bbox,
            "width": meta.get("width"),
            "height": meta.get("height"),
            "band_count": meta.get("band_count"),
            "crs": meta.get("crs"),
            "resolution": meta.get("resolution"),
            "layer_name": layer_name,
            "workspace": "cd_raster",
            "acquired_at": acquired or None,
            "satellite": item.get("satellite"),
            "registered": bool(registered),
        }

    @staticmethod
    def _resolve_bbox(bbox: Optional[Sequence[float]],
                      item_bbox: Optional[Sequence[float]]) -> Tuple[float, float, float, float]:
        values = list(bbox) if bbox else (list(item_bbox) if item_bbox else None)
        if not values or len(values) != 4:
            raise ImageryError("缺少下载范围 bbox")
        minx, miny, maxx, maxy = [float(v) for v in values]
        if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
            raise ImageryError("下载范围 bbox 无效")
        if item_bbox and len(item_bbox) == 4:
            iminx, iminy, imaxx, imaxy = [float(v) for v in item_bbox]
            minx, miny = max(minx, iminx), max(miny, iminy)
            maxx, maxy = min(maxx, imaxx), min(maxy, imaxy)
            if minx >= maxx or miny >= maxy:
                raise ImageryError("下载范围与影像覆盖范围无交集")
        return (minx, miny, maxx, maxy)

    @staticmethod
    async def _stream_to_file(url: str, temp: Path, max_bytes: int) -> int:
        total = 0
        async with httpx.AsyncClient(
            timeout=settings.imagery_http_timeout, follow_redirects=True,
            headers={"User-Agent": "RSCD-Imagery/1.0"},
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ImageryError(f"影像下载失败 (HTTP {resp.status_code})")
                media = (resp.headers.get("content-type") or "").lower()
                if "json" in media or "html" in media:
                    raise ImageryError("影像下载返回了错误页，请稍后重试")
                with temp.open("wb") as handle:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            handle.close()
                            temp.unlink(missing_ok=True)
                            raise ImageryError(
                                f"影像超过大小上限 {settings.imagery_download_max_mb}MB，请缩小范围"
                            )
                        handle.write(chunk)
        return total

    @staticmethod
    def _read_raster_meta(path: Path) -> Dict[str, Any]:
        try:
            import rasterio

            with rasterio.open(str(path)) as src:
                bounds = src.bounds
                transform = src.transform
                resolution = abs(transform.a) if transform and transform.a else None
                epsg = None
                try:
                    epsg = src.crs.to_epsg() if src.crs else None
                except Exception:  # noqa: BLE001
                    epsg = None
                # 统一以米记录分辨率（PC 裁剪产物为 EPSG:4326，度 → 米按纬度 111.32km 估算）
                if epsg == 4326 and resolution:
                    resolution = resolution * 111320.0
                return {
                    "width": src.width,
                    "height": src.height,
                    "band_count": src.count,
                    "crs": f"EPSG:{epsg}" if epsg else (str(src.crs) if src.crs else None),
                    "resolution": round(float(resolution), 2) if resolution else None,
                    "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("[imagery] 读取影像元数据失败: %s", exc)
            return {}


imagery_service = ImageryService()
