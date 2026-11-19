# -*- coding: utf-8 -*-
"""原生 SAM3 分割适配器。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from backend.data.base import AssetRef
from backend.model.base import InterpretationModel, ModelRequest, ModelResult


class Sam3NativeSegmentModel(InterpretationModel):
    """把 sam3native.service.segment 包装成统一解译模型协议。

    感知走原生 SAM3（文本接地头逐实例推理 + 语义图）；调色板/PNG/GeoTIFF/
    矢量/统计仍由 SamSeg.runner 的地理 IO 层完成，产物契约不变。
    """

    name = "sam3_native"
    capabilities = {"segmentation"}

    def __init__(self, runner=None) -> None:
        """runner 可注入（测试用）；缺省延迟导入 SamSeg.runner。"""
        self._runner = runner

    def predict(self, request: ModelRequest) -> ModelResult:
        """入参: ModelRequest(inputs[0]=影像)。出参: ModelResult。"""
        if not request.inputs:
            return ModelResult(status="failed", error="缺少输入影像")
        image_path = request.inputs[0].uri
        if not Path(image_path).is_file():
            return ModelResult(status="failed", error=f"影像不存在: {image_path}")

        out_dir = Path(str(request.params.get("output_dir") or tempfile.mkdtemp(prefix="sam3_native_")))
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = Path(str(request.params.get("output_path") or (out_dir / "mask.png")))
        vector_path = Path(str(request.params.get("vector_output_path") or (out_dir / "vector.geojson")))

        try:
            result = self._run(image_path, output_path, vector_path, request)
        except Exception as exc:
            return ModelResult(status="failed", error=f"分割失败: {exc}")

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
            model_info={"name": self.name, "engine": "sam3_native"},
        )

    def _run(self, image_path, output_path, vector_path, request):
        """调 runner.run_segment，参数从 ModelRequest 归一。

        ★ runner 缺省时延迟导入 SamSeg.runner（与工具链路同一实现）；
          地物提取只走 SamSeg 本体（SegEarth-OV3），原生 SAM3 归样本制作功能。
        """
        runner = self._runner
        if runner is None:
            from backend.model.SamSeg import runner as runner_module

            runner = runner_module
        return runner.run_segment(
            image_path=image_path,
            output_path=str(output_path),
            classes=request.classes or None,
            vector_output_path=str(vector_path),
            save_mask=bool(request.params.get("save_mask", False)),
        )

    def release(self) -> None:
        """释放原生 SAM3 全局服务与显存。"""
        try:
            from backend.model.sam3native import service
            service.reset_service()
        except Exception:
            pass
