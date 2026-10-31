# -*- coding: utf-8 -*-
"""ProC-SAM3 语义分割适配器。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from backend.model.base import InterpretationModel, ModelRequest, ModelResult
from backend.data.base import AssetRef


class ProCSam3SegmentModel(InterpretationModel):
    """把现有 SamSeg.runner 包装成统一解译模型协议。

    runner 仍负责权重加载、推理、掩膜/矢量写出；本适配器只做：
    - 参数归一化（输入 AssetRef → 本地路径）；
    - 输出路径管理；
    - 统一 ModelResult / AssetRef 返回。
    """

    name = "proc_sam3"
    capabilities = {"segmentation"}

    def __init__(self, runner: Optional[Any] = None) -> None:
        self._runner = runner

    def _get_runner(self):
        """延迟导入 torch/runner，避免注册表初始化时加载重依赖。"""
        if self._runner is not None:
            return self._runner
        from backend.model.SamSeg import runner
        return runner

    def warmup(self) -> None:
        """预热由首次 predict 触发；这里只检查依赖可用性。"""
        runner = self._get_runner()
        if hasattr(runner, "samseg_available") and not runner.samseg_available():
            raise RuntimeError("SamSeg 不可用：torch 或模型权重缺失")

    def predict(self, request: ModelRequest) -> ModelResult:
        """入参: ModelRequest(inputs[0]=影像)。出参: ModelResult。"""
        if not request.inputs:
            return ModelResult(status="failed", error="缺少输入影像")
        image_path = request.inputs[0].uri
        if not Path(image_path).is_file():
            return ModelResult(status="failed", error=f"影像不存在: {image_path}")

        out_dir = Path(str(request.params.get("output_dir") or tempfile.mkdtemp(prefix="proc_sam3_")))
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = Path(str(request.params.get("output_path") or (out_dir / "mask.png")))
        vector_path = Path(str(request.params.get("vector_output_path") or (out_dir / "vector.geojson")))

        try:
            result = self._get_runner().run_segment(
                image_path=image_path,
                output_path=str(output_path),
                classes=request.classes or None,
                vector_output_path=str(vector_path),
                save_mask=bool(request.params.get("save_mask", False)),
            )
        except Exception as exc:
            return ModelResult(status="failed", error=f"分割失败: {exc}")

        if not result:
            return ModelResult(status="failed", error="分割未返回结果")

        outputs = []
        if output_path.is_file():
            outputs.append(AssetRef(
                asset_id=f"seg_mask_{output_path.stem}",
                kind="image",
                uri=str(output_path),
                metadata={"model": self.name, "classes": request.classes},
            ))
        if vector_path.is_file():
            outputs.append(AssetRef(
                asset_id=f"seg_vector_{vector_path.stem}",
                kind="vector",
                uri=str(vector_path),
                metadata={"model": self.name, "classes": request.classes},
            ))

        return ModelResult(
            status="passed",
            outputs=outputs,
            vectors=[item for item in outputs if item.kind == "vector"],
            data=result if isinstance(result, dict) else {},
            stats=(result.get("stats") if isinstance(result, dict) else {}) or {},
            verification=(result.get("verification") if isinstance(result, dict) else {}) or {},
            model_info={"name": self.name, "engine": "proc_sam3"},
        )

    def release(self) -> None:
        """释放 ProC-SAM3 全局模型缓存。"""
        try:
            from backend.model.ProC_SAM3 import engine
            engine.release()
        except Exception:
            pass
