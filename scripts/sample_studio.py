"""样本制作临时验证台（Sample Studio）。

单文件 FastAPI 应用：一张页面跑完「框选 → 原生 SAM3 接地头候选 → 人工增删改类别
→ 手绘补漏 → annotations.json → COCO 导出」，用于在正式弹窗落地前把流程钉死。

本文件是**临时验证工具**，不属于生产链路：正式实现见
``backend/model/sample/`` 与 ``backend/api/samples.py``（设计稿
``docs/agent/method/sample-data-pipeline.md``）。验证完成后可整体删除。

启动（conda 环境 sam3）::

    D:\\anaconda3\\envs\\sam3\\python.exe -u scripts\\sample_studio.py

浏览器打开 http://127.0.0.1:8031 。
"""

from __future__ import annotations

import base64
import gc
import io
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image

from backend.config import settings
from backend.model.sam3native import inst_service as inst_mod

IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
DEFAULT_COLORS = [
    [222, 31, 7], [0, 69, 255], [0, 255, 36], [255, 255, 255],
    [148, 148, 148], [75, 181, 73], [255, 0, 255], [255, 200, 0],
]
DEFAULT_CLASSES = [
    {"name": "building", "prompts": ["building", "house", "roof"]},
    {"name": "road", "prompts": ["road", "highway"]},
    {"name": "water", "prompts": ["water", "river", "pond"]},
    {"name": "bareland", "prompts": ["bareland", "bare soil"]},
    {"name": "vegetation", "prompts": ["vegetation", "tree", "grass"]},
    {"name": "farmland", "prompts": ["farmland", "cropland"]},
]

_SERVICE = None
_STATE: dict = {"image_path": None, "width": 0, "height": 0,
                "transform": None, "crs": None, "resolution": 1008}
_TEMP_DIR = Path(tempfile.mkdtemp(prefix="sample_studio_"))

app = FastAPI(title="Sample Studio (temp)")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def _free_samseg() -> None:
    """释放 SamSeg processor，避免两个 3.3GB 权重同时占显存。"""
    try:
        from backend.model.SamSeg import runner as samseg_runner

        if getattr(samseg_runner, "_processor", None) is not None:
            samseg_runner._processor = None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            log("已释放 SamSeg processor（腾显存给原生 SAM3）")
    except Exception as exc:  # pragma: no cover - 纯尽力而为
        log(f"释放 SamSeg 失败（忽略）：{exc}")


def get_service() -> "inst_mod.NativeInstanceService":
    global _SERVICE
    if _SERVICE is None:
        _free_samseg()
        started = time.time()
        _SERVICE = inst_mod.NativeInstanceService(
            logger=log, device=settings.sam3_native_device or None)
        log(f"NativeInstanceService 创建完成（{time.time() - started:.1f}s）")
    return _SERVICE


def _resolve_image(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (ROOT / candidate).resolve()
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail=f"影像不存在：{candidate}")
    if candidate.suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的影像类型：{candidate.suffix}")
    return candidate


def _read_profile(path: Path) -> dict:
    """读 CRS 与仿射变换（非 GeoTIFF 时返回恒等变换）。"""
    profile = {"crs": None, "transform": [0.0, 1.0, 0.0, 0.0, 0.0, -1.0]}
    if path.suffix.lower() in {".tif", ".tiff"}:
        try:
            import rasterio

            with rasterio.open(path) as dataset:
                t = dataset.transform
                profile["transform"] = [t.a, t.b, t.c, t.d, t.e, t.f]
                if dataset.crs is not None:
                    profile["crs"] = dataset.crs.to_string()
        except Exception as exc:
            log(f"读取栅格 profile 失败（按恒等变换处理）：{exc}")
    return profile


def _pixel_to_world(points, transform, crs):
    """像素坐标（列, 行）→ 世界坐标；无 CRS 时原样返回像素中心。"""
    a, b, c, d, e, f = transform
    out = []
    for px, py in points:
        col, row = float(px) + 0.5, float(py) + 0.5
        out.append([round(c + a * col + b * row, 6),
                    round(f + d * col + e * row, 6)])
    return out


def _mask_polygons(mask: np.ndarray, epsilon: float = 1.0) -> list:
    """二值掩码 → 像素多边形环列表（简化为折线）。"""
    contours, _ = cv2.findContours(np.asarray(mask, dtype=np.uint8),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rings = []
    for contour in contours:
        approx = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) < 3:
            continue
        ring = [[float(pt[0][0]), float(pt[0][1])] for pt in approx]
        rings.append(ring)
    return rings


def _polygons_to_mask(rings, width: int, height: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    for ring in rings or []:
        if len(ring) < 3:
            continue
        pts = np.asarray(ring, dtype=np.float32).reshape(-1, 2)
        cv2.fillPoly(mask, [pts.astype(np.int32)], 1)
    return mask.astype(bool)


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "sample_studio.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "model_loaded": _SERVICE is not None and _SERVICE.is_ready,
        "device": getattr(_SERVICE, "device", settings.sam3_native_device or "auto"),
        "state": {k: v for k, v in _STATE.items() if k != "transform"},
    }


@app.get("/api/files")
def list_files(limit: int = 200) -> dict:
    roots = [ROOT / "agent-files" / "imagery", ROOT / "agent-files" / "samples"]
    items = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if "thumbs" in path.parts or "warmup" in path.name:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append({
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%m-%d %H:%M"),
                "_ts": stat.st_mtime,
                "_capture": "captures" in path.parts,
            })
    items.sort(key=lambda item: (not item["_capture"], -item["_ts"]))
    for item in items:
        item.pop("_ts", None)
        item.pop("_capture", None)
    return {"files": items[:limit]}


@app.get("/api/image")
def get_image(path: str = Query(...)) -> Response:
    image_path = _resolve_image(path)
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-cache"})


@app.post("/api/candidates")
def candidates(body: dict) -> dict:
    if not any(body.get("classes") or []):
        raise HTTPException(status_code=400, detail="类别表为空")

    image_path = _resolve_image(body["image_path"])
    profile = _read_profile(image_path)
    box = body.get("box")
    resolution = int(body.get("resolution") or settings.sam3_native_resolution or 1008)
    min_area = int(body.get("min_area") or 0)
    conf_thd = float(body.get("conf_thd") or 0.1)
    conf_thd = min(max(conf_thd, 0.01), 0.9)

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        full_w, full_h = rgb.size
        if box:
            x0, y0, x1, y1 = [int(round(float(v))) for v in box]
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(full_w, x1), min(full_h, y1)
            if x1 - x0 < 8 or y1 - y0 < 8:
                raise HTTPException(status_code=400, detail="框选范围过小")
            crop = rgb.crop((x0, y0, x1, y1))
            crop_path = _TEMP_DIR / f"crop_{int(time.time() * 1000)}.png"
            crop.save(crop_path)
            offset_x, offset_y = x0, y0
            rgb_crop_w, rgb_crop_h = crop.size
        else:
            crop_path = image_path
            offset_x, offset_y = 0, 0
            rgb_crop_w, rgb_crop_h = full_w, full_h

    classes = [str(item.get("name", "")).strip() for item in body["classes"]]
    classes = [name for name in classes if name]
    prompts = [[str(p).strip() for p in (item.get("prompts") or [name]) if str(p).strip()]
               for item, name in zip(body["classes"], classes)]
    palette = [list(item.get("color") or DEFAULT_COLORS[0]) for item in body["classes"]]
    key = "|".join(classes) + "#" + str(offset_x) + "," + str(offset_y) + "#studio#" + str(conf_thd)

    service = get_service()
    started = time.time()
    try:
        service.set_image(crop_path, resolution)
        # 接地头分数 = sigmoid(pred_logit) × sigmoid(presence)，量级远低于 0.5，
        # 处理器默认阈值 0.5 会把全部候选滤掉，必须按需下调。
        if service.processor is not None:
            service.processor.set_confidence_threshold(float(conf_thd))
        result = service.run_classes(
            {"classes": classes, "palette": palette, "bg_idx": None, "key": key},
            prompts_per_class=prompts, resolution=str(resolution), force=True)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"候选生成失败：{exc}") from exc
    elapsed = time.time() - started

    instances = []
    for index, item in enumerate(result["instances"], start=1):
        mask = item["mask"]
        if int(mask.sum()) < max(min_area, 1):
            continue
        rings = _mask_polygons(mask)
        if not rings:
            continue
        rings = [[[x + offset_x, y + offset_y] for x, y in ring] for ring in rings]
        bbox = item.get("bbox") or [0, 0, 0, 0]
        instances.append({
            "id": f"g{index:04d}",
            "name": item.get("name") or classes[item.get("class_index", 0)],
            "class_index": int(item.get("class_index") or 0),
            "score": round(float(item.get("score") or 0.0), 4),
            "source": "grounding",
            "polygons_px": rings,
            "bbox_px": [round(float(bbox[0]) + offset_x, 1),
                        round(float(bbox[1]) + offset_y, 1),
                        round(float(bbox[2]) + offset_x, 1),
                        round(float(bbox[3]) + offset_y, 1)],
            "area_px": int(mask.sum()),
        })

    log(f"候选完成：{len(instances)} 实例，{elapsed:.1f}s，框选={box}")
    return {
        "image": {"path": str(image_path), "width": full_w, "height": full_h,
                  "crs": profile["crs"], "transform": profile["transform"]},
        "classes": result["classes"],
        "instances": instances,
        "seconds": round(elapsed, 2),
        "crop": [offset_x, offset_y, offset_x + rgb_crop_w, offset_y + rgb_crop_h],
    }


@app.post("/api/reset")
def reset() -> dict:
    global _SERVICE
    if _SERVICE is not None:
        try:
            _SERVICE.release_all()
        except Exception:
            pass
    _SERVICE = None
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return {"ok": True}


@app.post("/api/shutdown")
def shutdown() -> dict:
    """关闭验证台（释放显存后退出进程）。"""
    log("收到关闭指令，准备退出")
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return {"ok": True}


@app.post("/api/save")
def save(body: dict) -> dict:
    # 兼容两种体：{task_dir, payload:{...}}（页面用法）与扁平 {task_dir, image, classes, instances}
    payload = body.get("payload") or body
    raw_dir = Path(str(body.get("task_dir") or ""))
    task_dir = (raw_dir if raw_dir.is_absolute() else (ROOT / raw_dir)).resolve()
    samples_root = (ROOT / "agent-files" / "samples").resolve()
    if samples_root not in task_dir.parents and task_dir != samples_root:
        raise HTTPException(status_code=400,
                            detail=f"任务目录必须在 {samples_root} 下")
    task_dir.mkdir(parents=True, exist_ok=True)

    image = payload.get("image") or {}
    transform = image.get("transform") or [0, 1, 0, 0, 0, -1]
    crs = image.get("crs")
    instances = []
    for item in payload.get("instances") or []:
        rings = item.get("polygons_px") or []
        rings_world = [_pixel_to_world(ring, transform, crs) for ring in rings
                       if len(ring) >= 3]
        if len(rings_world) == 1:
            geometry = {"type": "Polygon", "coordinates": [rings_world[0]]}
        else:
            geometry = {"type": "MultiPolygon",
                        "coordinates": [[ring] for ring in rings_world]}
        instances.append({
            "id": item.get("id"),
            "class_id": int(item.get("class_index", 0)) + 1,
            "class_name": item.get("name"),
            "score": item.get("score"),
            "source": item.get("source") or "grounding",
            "geometry": geometry,
            "polygons_px": rings,
            "bbox_px": item.get("bbox_px"),
            "area_px": item.get("area_px"),
            "edited": bool(item.get("edited")),
        })

    annotations = {
        "version": 1,
        "image": image,
        "classes": payload.get("classes") or [],
        "instances": instances,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    annotations_path = task_dir / "annotations.json"
    annotations_path.write_text(json.dumps(annotations, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    meta = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_image": image.get("path"),
        "width": image.get("width"),
        "height": image.get("height"),
        "crs": crs,
        "classes": payload.get("classes"),
        "instance_count": len(instances),
        "tool": "sample_studio (临时验证台)",
    }
    (task_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    log(f"标注已保存：{annotations_path}（{len(instances)} 实例）")
    return {"path": str(annotations_path), "instance_count": len(instances)}


@app.get("/api/export")
def export(task_dir: str = Query(...), format: str = Query("coco")) -> Response:
    if format.lower() != "coco":
        raise HTTPException(status_code=400, detail="临时验证台只支持 COCO 导出")
    raw_dir = Path(task_dir)
    directory = (raw_dir if raw_dir.is_absolute() else (ROOT / raw_dir)).resolve()
    samples_root = (ROOT / "agent-files" / "samples").resolve()
    if samples_root not in directory.parents:
        raise HTTPException(status_code=400, detail=f"任务目录必须在 {samples_root} 下")
    annotations_path = directory / "annotations.json"
    if not annotations_path.is_file():
        raise HTTPException(status_code=404, detail="任务目录下没有 annotations.json")

    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    image = annotations.get("image") or {}
    width, height = int(image.get("width") or 0), int(image.get("height") or 0)
    items = []
    for instance in annotations.get("instances") or []:
        mask = _polygons_to_mask(instance.get("polygons_px"), width, height)
        if not mask.any():
            continue
        items.append({
            "name": instance.get("class_name") or "object",
            "mask": mask,
            "bbox": instance.get("bbox_px"),
            "score": instance.get("score"),
        })
    from backend.model.sam3native.coco_export import write_coco

    coco_path = directory / "coco.json"
    write_coco(coco_path, image.get("path") or "image", width, height, items)
    log(f"COCO 已导出：{coco_path}（{len(items)} 实例）")
    return FileResponse(coco_path, media_type="application/json",
                        filename=f"{directory.name}_coco.json")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("SAMPLE_STUDIO_PORT", "8031"))
    log(f"Sample Studio 启动：http://127.0.0.1:{port}")
    log(f"工作区：{ROOT}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
