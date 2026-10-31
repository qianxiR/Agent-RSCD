"""开源遥感影像检索路由。

- GET  /api/v1/imagery/providers  数据源与集合清单
- POST /api/v1/imagery/search     按 bbox + 时间 + 云量 + 分辨率检索
- GET  /api/v1/imagery/thumbnail  快视图代理（磁盘缓存）
- POST /api/v1/imagery/download   AOI 裁剪下载 GeoTIFF（当前支持 Planetary Computer / GEE）
- POST /api/v1/imagery/georeference      浏览器拼接的 Esri 影像 + bbox → 带 CRS GeoTIFF
- POST /api/v1/imagery/change-captures   (legacy) 同一 AOI 两个时相各取一景 Sentinel-2

数据源均为公开开放数据，不经过 LLM；下载产物落 agent-files/imagery/downloads，
并登记到业务库 image_metadata（workspace=cd_raster），可继续发布为 GeoServer 图层。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.config import settings
from backend.data.imagery import ImageryError, imagery_service
from backend.data.imagery.capture import capture_service

router = APIRouter(tags=["imagery"])
logger = logging.getLogger(__name__)


class SearchBody(BaseModel):
    providers: list[str] = Field(default_factory=lambda: ["pc"])
    collections: list[str] = Field(default_factory=list)
    bbox: list[float]
    start: str
    end: str
    cloud_min: float | None = 0
    cloud_max: float | None = 30
    platform: str | None = None
    orbit_direction: str | None = None
    processing_level: str | None = None
    sun_elevation_min: float | None = None
    max_gsd: float | None = None
    limit: int = 24
    sort: str = "date_desc"


class DownloadBody(BaseModel):
    provider: str
    collection: str
    item: str
    bbox: list[float] | None = None
    name: str | None = None


class CaptureBody(BaseModel):
    source: str = "esri"
    bbox: list[float]
    target_gsd: float = 0.6


class ChangeCaptureBody(BaseModel):
    bbox: list[float]
    t1_date: str
    t2_date: str
    window_days: int = 60
    cloud_max: float = 30.0
    collection: str = "sentinel-2-l2a"
    max_gsd: float | None = 20.0


@router.get("/api/v1/imagery/providers")
async def imagery_providers():
    """返回数据源清单（含可用状态、集合、能力矩阵）。"""
    return {
        "status": "success",
        "providers": imagery_service.providers(),
        "max_area_deg2": settings.imagery_download_max_area_deg2,
        "capture_max_area_deg2": settings.imagery_capture_max_area_deg2,
        "capture_max_size": settings.imagery_capture_max_size,
    }


@router.post("/api/v1/imagery/search")
async def imagery_search(body: SearchBody):
    """多源影像检索（并发聚合 + 统一归一化）。"""
    try:
        result = await imagery_service.search(
            providers=body.providers,
            collections=body.collections,
            bbox=body.bbox,
            start=body.start,
            end=body.end,
            cloud_min=body.cloud_min,
            cloud_max=body.cloud_max,
            platform=body.platform or "",
            orbit_direction=body.orbit_direction or "",
            processing_level=body.processing_level or "",
            sun_elevation_min=body.sun_elevation_min,
            max_gsd=body.max_gsd,
            limit=body.limit,
            sort=body.sort,
        )
        return {"status": "success", **result}
    except ImageryError as exc:
        return {"status": "error", "msg": str(exc), "items": [], "count": 0, "errors": {}}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[imagery] 检索异常: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"status": "error", "msg": f"影像检索服务异常: {exc}", "items": [], "count": 0},
        )


@router.get("/api/v1/imagery/thumbnail")
async def imagery_thumbnail(
    provider: str = Query(...),
    collection: str = Query(...),
    item: str = Query(...),
    w: int = Query(480, ge=160, le=1200),
):
    """快视图代理：服务端拉取并缓存到磁盘，避免浏览器跨域与重复下载。"""
    try:
        path, media = await imagery_service.thumbnail(provider, collection, item, w)
        return Response(
            content=path.read_bytes(),
            media_type=media,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except ImageryError as exc:
        return JSONResponse(status_code=404, content={"status": "error", "msg": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[imagery] 快视图代理失败: %s", exc)
        return JSONResponse(status_code=502, content={"status": "error", "msg": "快视图获取失败"})


@router.post("/api/v1/imagery/change-captures")
async def imagery_change_captures(body: ChangeCaptureBody):
    """变化检测取图：同一 AOI 按两个目标日期各取一景 Sentinel-2，返回两期 GeoTIFF。"""
    try:
        return await imagery_service.capture_change(
            bbox=body.bbox,
            t1_date=body.t1_date,
            t2_date=body.t2_date,
            window_days=body.window_days,
            cloud_max=body.cloud_max,
            collection=body.collection,
            max_gsd=body.max_gsd,
        )
    except ImageryError as exc:
        return {"status": "error", "msg": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[imagery] 变化检测取图异常: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"status": "error", "msg": f"变化检测取图失败: {exc}"},
        )


@router.post("/api/v1/imagery/georeference")
async def imagery_georeference(
    file: UploadFile = File(...),
    bbox: str = Form(...),
    name: str = Form(""),
    date: str = Form(""),
):
    """浏览器拼接的 Esri Wayback 截图 → 带 CRS GeoTIFF（变化检测两期影像）。"""
    try:
        import json
        values = json.loads(bbox)
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            return {"status": "error", "msg": "截图文件超过 20MB"}
        return await capture_service.georeference(
            content,
            values,
            name=name or (file.filename or "wayback"),
            date=date,
        )
    except ImageryError as exc:
        return {"status": "error", "msg": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[imagery] 截图配准异常: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"status": "error", "msg": f"截图配准失败: {exc}"},
        )


@router.post("/api/v1/imagery/capture")
async def imagery_capture(body: CaptureBody):
    """框选取图：Esri 导出 / 天地图瓦片拼接 → 带 CRS GeoTIFF（供 SamSeg 解译）。"""
    try:
        return await capture_service.capture(
            source=body.source,
            bbox=body.bbox,
            target_gsd=body.target_gsd,
        )
    except ImageryError as exc:
        return {"status": "error", "msg": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[imagery] 框选取图异常: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"status": "error", "msg": f"框选取图失败: {exc}"},
        )


@router.post("/api/v1/imagery/download")
async def imagery_download(body: DownloadBody):
    """按 AOI 裁剪下载 GeoTIFF，并登记业务库元数据。"""
    try:
        result = await imagery_service.download(
            provider_key=body.provider,
            collection=body.collection,
            item_id=body.item,
            bbox=body.bbox,
            name=body.name,
        )
        return result
    except ImageryError as exc:
        return {"status": "error", "msg": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[imagery] 下载异常: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"status": "error", "msg": f"影像下载服务异常: {exc}"},
        )
