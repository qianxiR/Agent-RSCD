# -*- coding: utf-8 -*-
"""ProC-SAM3 双时相变化检测适配器。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Optional

from backend.data.base import AssetRef
from backend.model.base import InterpretationModel, ModelRequest, ModelResult


class ProCSam3ChangeModel(InterpretationModel):
    """把 SamSeg.runner.run_change_detection 包装成统一模型协议。"""

    name = "proc_sam3_change"
    capabilities = {"change_detection"}

    def __init__(self, runner: Optional[Any] = None) -> None:
        self._runner = runner

    def _get_runner(self):
        """延迟导入 runner。"""
        if self._runner is not None:
            return self._runner
        from backend.model.SamSeg import runner
        return runner

    def warmup(self) -> None:
        """检查依赖；实际权重在 predict 时加载。"""
        runner = self._get_runner()
        if hasattr(runner, "samseg_available") and not runner.samseg_available():
            raise RuntimeError("SamSeg 不可用：torch 或模型权重缺失")

    def predict(self, request: ModelRequest) -> ModelResult:
        """入参: ModelRequest(inputs=[T1,T2])。出参: ModelResult。"""
        if len(request.inputs) < 2:
            return ModelResult(status="failed", error="变化检测需要 T1/T2 两期影像")
        t1_path = request.inputs[0].uri
        t2_path = request.inputs[1].uri
        for path in (t1_path, t2_path):
            if not Path(path).is_file():
                return ModelResult(status="failed", error=f"影像不存在: {path}")

        out_dir = Path(str(request.params.get("output_dir") or tempfile.mkdtemp(prefix="proc_sam3_change_")))
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = Path(str(request.params.get("output_path") or (out_dir / "change.png")))
        vector_path = Path(str(request.params.get("vector_output_path") or (out_dir / "change.geojson")))

        try:
            result = self._get_runner().run_change_detection(
                t1_path=t1_path,
                t2_path=t2_path,
                output_path=str(output_path),
                classes=request.classes or None,
                vector_output_path=str(vector_path),
                save_mask=bool(request.params.get("save_mask", False)),
            )
        except Exception as exc:
            return ModelResult(status="failed", error=f"变化检测失败: {exc}")

        if not result:
            return ModelResult(status="failed", error="变化检测未返回结果")
        data = result if isinstance(result, dict) else {"output_path": str(result)}

        vector_uri = str(data.get("vector_path") or vector_path)
        outputs = []
        if Path(vector_uri).is_file():
            outputs.append(AssetRef(
                asset_id=f"change_vector_{Path(vector_uri).stem}",
                kind="vector",
                uri=vector_uri,
                metadata={"model": self.name, "classes": request.classes},
            ))
        if output_path.is_file():
            outputs.append(AssetRef(
                asset_id=f"change_image_{output_path.stem}",
                kind="image",
                uri=str(output_path),
                metadata={"model": self.name},
            ))

        return ModelResult(
            status="passed",
            outputs=outputs,
            vectors=[item for item in outputs if item.kind == "vector"],
            data=data,
            stats=data.get("stats") if isinstance(data.get("stats"), dict) else {},
            verification=data.get("verification") if isinstance(data.get("verification"), dict) else {},
            model_info={"name": self.name, "engine": "proc_sam3"},
        )

    def release(self) -> None:
        """释放 ProC-SAM3 全局模型缓存。"""
        try:
            from backend.model.ProC_SAM3 import engine
            engine.release()
        except Exception:
            pass
