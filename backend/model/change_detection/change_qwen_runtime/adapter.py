# -*- coding: utf-8 -*-
"""ChangeQwenNet ONNX 模型适配器（可选注册，不改变现有 detect_change 工具）。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from backend.data.base import AssetRef
from backend.model.base import InterpretationModel, ModelRequest, ModelResult


class ChangeQwenModel(InterpretationModel):
    name = "change_qwen"
    capabilities = {"change_detection"}

    def predict(self, request: ModelRequest) -> ModelResult:
        if len(request.inputs) < 2:
            return ModelResult(status="failed", error="变化检测需要 T1/T2 两期影像")

        t1_path = request.inputs[0].uri
        t2_path = request.inputs[1].uri
        for path in (t1_path, t2_path):
            if not Path(path).is_file():
                return ModelResult(status="failed", error=f"影像不存在: {path}")

        text = (request.classes or "").lower()
        model_key = str(request.params.get("model_key") or "").lower()
        if not model_key:
            if "building" in text:
                model_key = "cdcd"
            elif "landslide" in text:
                model_key = "jzg"
            else:
                model_key = "jzg"

        out_dir = Path(str(request.params.get("output_dir") or tempfile.mkdtemp(prefix="change_qwen_")))
        out_dir.mkdir(parents=True, exist_ok=True)

        from .runtime import predict_change

        try:
            result = predict_change(
                t1_path,
                t2_path,
                model=model_key,
                output_dir=str(out_dir),
                threshold=float(request.params.get("threshold", 0.5)),
                tile_size=int(request.params.get("tile_size", 336)),
                stride=int(request.params.get("stride", 168)),
                batch_size=int(request.params.get("batch_size", 0)),
                device=str(request.params.get("device", "auto")),
                min_area_px=int(request.params.get("min_area_px", 0)),
                min_area_m2=float(request.params.get("min_area_m2", 50.0)),
                save_prob=bool(request.params.get("save_prob", True)),
            )
        except Exception as exc:  # noqa: BLE001
            return ModelResult(status="failed", error=f"变化检测失败: {exc}")

        outputs = []
        vector_path = result.get("vector_path")
        if vector_path and Path(vector_path).is_file():
            outputs.append(AssetRef(
                asset_id=f"change_vector_{Path(vector_path).stem}",
                kind="vector",
                uri=vector_path,
                metadata={"model": self.name, "model_key": model_key},
            ))
        for key in ("color_path", "mask_path", "prob_path"):
            path = result.get(key)
            if path and Path(path).is_file():
                outputs.append(AssetRef(
                    asset_id=f"change_{key}_{Path(path).stem}",
                    kind="image",
                    uri=path,
                    metadata={"model": self.name, "model_key": model_key},
                ))

        return ModelResult(
            status="passed",
            outputs=outputs,
            vectors=[item for item in outputs if item.kind == "vector"],
            data=result,
            stats=result.get("stats") if isinstance(result.get("stats"), dict) else {},
            model_info={"name": self.name, "engine": "onnxruntime", "model_key": model_key},
        )

    def release(self) -> None:
        try:
            from .runtime import _SESSIONS
            _SESSIONS.clear()
        except Exception:
            pass
