"""样本制作标注路由。

- POST /api/v1/samples/annotations  保存 annotations.json（同时复制抓图影像）
- GET  /api/v1/samples/annotations  读回 annotations.json
- GET  /api/v1/samples/tasks        列出项目的样本任务
- GET  /api/v1/samples/export       导出 COCO / SHP
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.api.deps import SAMPLES_ROOT
from backend.model.sample.export import export_coco, export_dataset, export_shp
from backend.model.sample.storage import SampleStore

router = APIRouter(tags=["samples"])
logger = logging.getLogger(__name__)

store = SampleStore(SAMPLES_ROOT)


class SaveBody(BaseModel):
    project: str = "default"
    task_dir: str
    annotations: Dict[str, Any]
    source_images: List[str] = Field(default_factory=list)


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "msg": message})


@router.post("/api/v1/samples/annotations")
def save_annotations(body: SaveBody):
    try:
        result = store.save(
            body.project,
            body.task_dir,
            body.annotations,
            body.source_images,
        )
    except ValueError as exc:
        return _error(400, str(exc))
    except OSError as exc:
        logger.exception("[samples] 保存标注失败: %s", exc)
        return _error(500, f"保存标注失败: {exc}")
    return {"status": "success", **result}


@router.get("/api/v1/samples/annotations")
def load_annotations(
    project: str = Query("default"),
    task_dir: str = Query(...),
):
    try:
        annotations = store.load(project, task_dir)
        previews = store.phase_previews(project, task_dir, annotations)
    except ValueError as exc:
        return _error(400, str(exc))
    except FileNotFoundError as exc:
        return _error(404, str(exc))
    return {"status": "success", "annotations": annotations, "phase_previews": previews}


@router.get("/api/v1/samples/tasks")
def list_tasks(project: str = Query("default")):
    try:
        tasks = store.list_tasks(project)
    except ValueError as exc:
        return _error(400, str(exc))
    return {"status": "success", "tasks": tasks}


@router.get("/api/v1/samples/export")
def export_annotations(
    project: str = Query("default"),
    task_dir: str = Query(...),
    format: str = Query("coco"),
):
    try:
        annotations = store.load(project, task_dir)
        task_path = store.task_dir(project, task_dir)
        kind = format.strip().lower()
        if kind == "coco":
            out = export_coco(task_path, annotations)
            return FileResponse(
                out,
                media_type="application/json",
                filename=f"coco_{Path(task_dir).name}.json",
            )
        if kind in {"shp", "zip"}:
            out = export_shp(task_path, annotations)
            return FileResponse(
                out,
                media_type="application/zip",
                filename=f"{Path(task_dir).name}_shp.zip",
            )
        if kind == "dataset":
            out = export_dataset(task_path, annotations)
            return FileResponse(
                out,
                media_type="application/zip",
                filename=f"{Path(task_dir).name}_dataset.zip",
            )
        return _error(400, f"不支持的导出格式: {format}")
    except ValueError as exc:
        return _error(400, str(exc))
    except FileNotFoundError as exc:
        return _error(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[samples] 导出失败: %s", exc)
        return _error(500, f"导出失败: {exc}")
