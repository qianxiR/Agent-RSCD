"""样本标注任务存储。

目录约定: {samples_output_root}/{project_id}/{task_dir}/
- source_a.tif / source_b.tif: 抓图副本，任务目录自包含
- annotations.json: 标注真值
- meta.json: 任务摘要
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from backend.config import settings
from backend.model.sample.annotations import normalize_annotations

_ILLEGAL_SEGMENT = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_ANNOTATION_FILE = "annotations.json"
_META_FILE = "meta.json"


def _safe_segment(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} 不能为空")
    if text in {".", ".."}:
        raise ValueError(f"{field} 不能是 . 或 ..")
    if _ILLEGAL_SEGMENT.search(text):
        raise ValueError(f"{field} 含非法字符: {text!r}")
    return text[:80]


class SampleStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_root = Path(settings.file_storage_root).resolve()

    # ==================== 路径 ====================

    def task_dir(self, project: str, task_dir: str) -> Path:
        project_name = _safe_segment(project or "default", "project")
        task_name = _safe_segment(task_dir, "task_dir")
        path = (self.root / project_name / task_name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("task_dir 越界")
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ==================== 读写 ====================

    def save(
        self,
        project: str,
        task_dir: str,
        annotations: Dict[str, Any],
        source_images: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        normalized = normalize_annotations(annotations)
        path = self.task_dir(project, task_dir)
        normalized["task"]["name"] = str(task_dir)
        normalized["task"]["project"] = str(project or "default")
        if not normalized["task"]["created_at"]:
            normalized["task"]["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._copy_sources(path, normalized, list(source_images or []))

        annotation_path = path / _ANNOTATION_FILE
        annotation_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        meta = {
            "project": normalized["task"]["project"],
            "task": normalized["task"]["name"],
            "instance_count": len(normalized["instances"]),
            "class_count": len(normalized["classes"]),
            "phases": normalized["image"].get("phases") or [],
            "width": normalized["image"]["width"],
            "height": normalized["image"]["height"],
            "crop_px": normalized["image"].get("crop_px"),
            "source_width": normalized["image"].get("source_width"),
            "source_height": normalized["image"].get("source_height"),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (path / _META_FILE).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return {
            "path": str(annotation_path),
            "instance_count": meta["instance_count"],
            "task_dir": path.name,
        }

    def load(self, project: str, task_dir: str) -> Dict[str, Any]:
        path = self.task_dir(project, task_dir) / _ANNOTATION_FILE
        if not path.is_file():
            raise FileNotFoundError(f"annotations.json 不存在: {project}/{task_dir}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_tasks(self, project: str) -> List[Dict[str, Any]]:
        project_dir = self.root / _safe_segment(project or "default", "project")
        if not project_dir.is_dir():
            return []
        tasks: List[Dict[str, Any]] = []
        for item in project_dir.iterdir():
            if not item.is_dir() or not (item / _ANNOTATION_FILE).is_file():
                continue
            meta: Dict[str, Any] = {}
            meta_path = item / _META_FILE
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    meta = {}
            tasks.append({
                "name": item.name,
                "instance_count": int(meta.get("instance_count") or 0),
                "class_count": int(meta.get("class_count") or 0),
                "saved_at": str(meta.get("saved_at") or ""),
                "width": int(meta.get("width") or 0),
                "height": int(meta.get("height") or 0),
                "phase_dates": [
                    str(p.get("date") or "")
                    for p in (meta.get("phases") or [])
                    if isinstance(p, dict)
                ],
                # 源影像还在才允许重新打开：退出后可直接接着标注
                "restorable": any(item.glob("source_*")),
            })
        tasks.sort(key=lambda row: row["saved_at"], reverse=True)
        return tasks

    # ==================== 历史任务影像 ====================

    def phase_previews(self, project: str, task_dir: str, annotations: Dict[str, Any]) -> List[Dict[str, str]]:
        """把任务目录里的源影像换成浏览器可预览的下载地址。"""
        task_path = self.task_dir(project, task_dir)
        previews: List[Dict[str, str]] = []
        for phase in annotations.get("image", {}).get("phases") or []:
            if not isinstance(phase, dict):
                continue
            name = str(phase.get("path") or "")
            candidate = (task_path / name).resolve() if name else None
            preview_url = ""
            if candidate and candidate.is_file() and candidate.is_relative_to(self.file_root):
                rel = candidate.relative_to(self.file_root).as_posix()
                preview_url = f"/api/v1/download/{rel}?preview=1"
            previews.append({
                "date": str(phase.get("date") or ""),
                "path": name,
                "preview_url": preview_url,
            })
        return previews

    # ==================== 源影像复制 ====================

    def _resolve_source(self, value: Any, task_path: Path) -> Path | None:
        text = str(value or "").strip()
        if not text:
            return None
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = task_path / candidate
        path = candidate.resolve()
        if not path.is_file():
            return None
        if not (path.is_relative_to(self.file_root) or path.is_relative_to(self.root)):
            raise ValueError(f"源影像不在 agent-files 内: {path}")
        return path

    def _copy_sources(
        self,
        task_path: Path,
        annotations: Dict[str, Any],
        source_images: Sequence[str],
    ) -> None:
        image = annotations["image"]
        phases = image.get("phases") or []
        if not phases and image.get("path"):
            phases = [{"date": "", "path": image["path"]}]

        for index, phase in enumerate(phases):
            source_value = phase.get("path") or (
                source_images[index] if index < len(source_images) else ""
            )
            source = self._resolve_source(source_value, task_path)
            if source is None:
                continue
            suffix = source.suffix.lower() or ".tif"
            target = task_path / f"source_{chr(ord('a') + index)}{suffix}"
            if source != target.resolve():
                shutil.copy2(source, target)
            phase["path"] = target.name

        if phases:
            image["path"] = phases[0]["path"]
        image["phases"] = phases
