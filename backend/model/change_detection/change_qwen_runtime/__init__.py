# -*- coding: utf-8 -*-
"""ChangeQwenNet ONNX 运行时：权重与推理代码，不含训练模型源码。"""
from __future__ import annotations

from typing import Any, Dict

__all__ = ["ChangeQwenModel", "predict_change"]


def predict_change(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    from .runtime import predict_change as _predict

    return _predict(*args, **kwargs)


from .adapter import ChangeQwenModel  # noqa: E402,F401
