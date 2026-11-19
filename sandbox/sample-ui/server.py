# -*- coding: utf-8 -*-
"""标注编辑沙盒服务（独立进程，不进系统）。

只保留标注编辑：载入影像 → 画多边形 / 顶点增删改 → 保存 annotations.json
→ 导出 COCO / SHP（浏览器下载）。不加载任何模型。

运行:
    D:/anaconda3/envs/sam3/python.exe sandbox/sample-ui/server.py
    浏览器打开 http://127.0.0.1:8765/
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import time
import traceback
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi import Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
WORK_ROOT = APP_DIR / "work"
UPLOAD_DIR = WORK_ROOT / "uploads"
WORK_ROOT.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="标注编辑沙盒")


class SaveBody(BaseModel):
    task_dir: str
    annotations: Dict[str, Any]


def _safe_task_dir(task_dir: str) -> Path:
    path = (WORK_ROOT / task_dir).resolve()
    if WORK_ROOT not in path.parents and path != WORK_ROOT:
        raise HTTPException(status_code=400, detail="task_dir 越界")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _image_size(path: Path) -> tuple:
    with Image.open(path) as image:
        return image.size


def _image_bounds(path: Path) -> Optional[List[float]]:
    """读 GeoTIFF 真实地理范围（EPSG:4326）；无 CRS 返回 None。"""
    try:
        import rasterio
        from rasterio.warp import transform_bounds
        with rasterio.open(path) as src:
            if src.crs is None:
                return None
            left, bottom, right, top = src.bounds
            if src.crs.is_geographic:
                pass  # 已是经纬度
            else:
                left, bottom, right, top = transform_bounds(
                    src.crs, "EPSG:4326", left, bottom, right, top)
            if not all(map(lambda v: abs(v) <= 1e4, (left, bottom, right, top))):
                return None
            return [round(left, 8), round(bottom, 8), round(right, 8), round(top, 8)]
    except Exception:
        return None

def _default_image() -> Optional[Path]:
    """默认影像：优先 SAMPLE_IMAGE 环境变量，否则取 agent-files/imagery/captures 最新 GeoTIFF。"""
    import os

    explicit = os.environ.get("SAMPLE_IMAGE")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    root = Path(__file__).resolve().parents[2]
    captures = root / "agent-files" / "imagery" / "captures"
    if captures.is_dir():
        candidates = sorted(captures.glob("*.tif"), key=lambda x: x.stat().st_mtime, reverse=True)
        for item in candidates:
            if _image_bounds(item) is not None:
                return item
    uploads = sorted(UPLOAD_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
    return uploads[0] if uploads else None


@app.get("/api/scene")
def scene():
    """返回默认影像的基本信息（尺寸、真实地理范围、预览地址）。"""
    image_path = _default_image()
    if image_path is None:
        return JSONResponse(status_code=200, content={
            "status": "error", "msg": "未找到默认影像：请设置 SAMPLE_IMAGE 或把影像放进 agent-files/imagery/captures"})
    width, height = _image_size(image_path)
    bounds = _image_bounds(image_path)
    return {
        "status": "success",
        "image_path": str(image_path),
        "file_name": image_path.name,
        "width": width,
        "height": height,
        "image_bounds": bounds,
        "has_crs": bounds is not None,
        "preview_url": "/api/preview?path=" + image_path.as_posix(),
    }


@app.get("/api/preview")
def preview(path: str):
    image_path = Path(path)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="影像不存在")
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        image.thumbnail((1600, 1600), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@app.post("/api/annotations")
def save_annotations(body: SaveBody):
    task_dir = _safe_task_dir(body.task_dir)
    payload = dict(body.annotations)
    payload["version"] = 1
    payload.setdefault("instances", [])
    path = task_dir / "annotations.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (task_dir / "meta.json").write_text(json.dumps({
        "instance_count": len(payload["instances"]),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "path": str(path),
            "instance_count": len(payload["instances"])}


@app.get("/api/annotations")
def load_annotations(task_dir: str):
    path = _safe_task_dir(task_dir) / "annotations.json"
    if not path.is_file():
        return {"status": "error", "msg": "annotations.json 不存在"}
    return {"status": "success",
            "annotations": json.loads(path.read_text(encoding="utf-8"))}


@app.get("/api/health")
def health():
    return {"status": "success", "work_root": str(WORK_ROOT), "service": "sample-ui"}

# ==================== 导出（浏览器下载） ====================

def _instance_points(item) -> List[List[float]]:
    """兼容 points=[[x,y],...] 与旧 polygons=[扁平数组]。"""
    points = item.get("points")
    if points:
        return [[float(p[0]), float(p[1])] for p in points if len(p) >= 2]
    polygons = item.get("polygons") or []
    flat = polygons[0] if polygons else []
    return [[float(flat[i]), float(flat[i + 1])] for i in range(0, len(flat) - 1, 2)]


def _rasterize(instances, width: int, height: int):
    """多边形（像素域）→ 逐实例二值掩码。"""
    import cv2
    masks = []
    for item in instances:
        mask = np.zeros((height, width), dtype=np.uint8)
        points = _instance_points(item)
        if len(points) >= 3:
            pts = np.asarray(points, dtype=np.float32)
            cv2.fillPoly(mask, [pts.astype(np.int32)], 1)
        masks.append((item, mask.astype(bool)))
    return masks


def _load_task(task_dir: str) -> Dict[str, Any]:
    path = _safe_task_dir(task_dir) / "annotations.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="请先保存标注")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/download/coco")
def download_coco(task_dir: str):
    """生成 COCO 并作为附件下载。"""
    annotations = _load_task(task_dir)
    image_info = annotations.get("image") or {}
    width = int(image_info.get("width") or 0)
    height = int(image_info.get("height") or 0)
    name = Path(str(image_info.get("path") or "image.tif")).name
    instances = annotations.get("instances") or []
    if not width or not height:
        raise HTTPException(status_code=400, detail="annotations.json 缺少影像宽高")
    try:
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from backend.model.sam3native.coco_export import write_coco
        items = [{"mask": mask, "name": item.get("class_name") or item.get("label"),
                  "bbox": item.get("bbox_px"), "score": item.get("score")}
                 for item, mask in _rasterize(instances, width, height)]
        out = _safe_task_dir(task_dir) / "coco.json"
        write_coco(str(out), name, width, height, items)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="COCO 生成失败: %s" % exc)
    return FileResponse(out, media_type="application/json",
                        filename="coco_%s.json" % Path(task_dir).name)


@app.get("/api/download/shp")
def download_shp(task_dir: str):
    """按标注的经纬度坐标导出整套 Shapefile（shp/shx/dbf/prj/cpg）并打包下载。"""
    annotations = _load_task(task_dir)
    image_info = annotations.get("image") or {}
    width = int(image_info.get("width") or 0)
    height = int(image_info.get("height") or 0)
    image_path = Path(str(image_info.get("path") or ""))
    instances = annotations.get("instances") or []
    if not instances:
        raise HTTPException(status_code=400, detail="没有标注可导出")

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from backend.integrations.vendor.spatial.export_utils import export_shapefile
    from shapely.geometry import Polygon

    features = []
    for item in instances:
        coords = item.get("coordinates")
        if not coords:
            # 兼容旧标注：无经纬度时用影像 transform 从像素点反算
            if not width or not height or not image_path.is_file():
                raise HTTPException(status_code=400, detail="标注缺少经纬度，且无法从影像反算")
            import rasterio as rio
            points = _instance_points(item)
            with rio.open(image_path) as src:
                if src.crs is None:
                    raise HTTPException(status_code=400, detail="影像无坐标系，无法导出 SHP")
                from rasterio.warp import transform as rio_transform
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                maps = [src.transform * (x, y) for x, y in zip(xs, ys)]
                lons, lats = rio_transform(src.crs, "EPSG:4326",
                                           [m[0] for m in maps], [m[1] for m in maps])
                coords = [[round(lon, 8), round(lat, 8)] for lon, lat in zip(lons, lats)]
        ring = [(float(p[0]), float(p[1])) for p in coords]
        if len(ring) < 3:
            continue
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        polygon = Polygon(ring)
        if polygon.is_empty:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "class_name": item.get("class_name") or item.get("label"),
                "score": item.get("score"),
                "area_px": item.get("area_px"),
            },
            "geometry": polygon.__geo_interface__,
        })

    if not features:
        raise HTTPException(status_code=400, detail="没有可导出的要素")

    geojson = {"type": "FeatureCollection", "features": features}
    task_path = _safe_task_dir(task_dir)
    out = task_path / "annotations.shp"
    try:
        export_shapefile(geojson, out)
        (task_path / "annotations.geojson").write_text(
            json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")
        archive = task_path / "annotations_shp.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".geojson"):
                part = task_path / ("annotations" + suffix)
                if part.is_file():
                    bundle.write(part, part.name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="SHP 生成失败: %s" % exc)
    return FileResponse(archive, media_type="application/zip",
                        filename="annotations_%s.zip" % Path(task_dir).name)


OL_DIST = Path(__file__).resolve().parents[2] / "frontend-vue" / "node_modules" / "ol"
if OL_DIST.is_dir():
    app.mount("/vendor/ol", StaticFiles(directory=str(OL_DIST)), name="ol")

app.mount("/", StaticFiles(directory=str(APP_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
