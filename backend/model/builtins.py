# -*- coding: utf-8 -*-
"""内置模型注册入口。

只注册适配器工厂，不加载 torch/权重；首次 predict 时才加载。
应用启动或工具初始化时调用 ensure_builtin_models() 即可。
"""

from __future__ import annotations

from backend.model.registry import ModelRegistry, get_model_registry


_REGISTERED = False


def register_builtin_models(registry: ModelRegistry | None = None) -> None:
    """
    入参: registry 目标注册表，None 用进程默认。
    方法: 注册 SamSeg 分割适配器与 change_qwen 变化检测适配器；重复调用幂等。
    出参: 无。
    """
    global _REGISTERED
    target = registry or get_model_registry()
    if _REGISTERED and registry is None:
        return
    from backend.model.change_detection.change_qwen_runtime import ChangeQwenModel
    from backend.model.segmentation.sam3_native import Sam3NativeSegmentModel

    target.register(
        Sam3NativeSegmentModel,
        name="sam3_native",
        capabilities=["segmentation"],
        metadata={"engine": "sam3_native", "domain": "segmentation"},
    )
    target.register(
        ChangeQwenModel,
        name="change_qwen",
        capabilities=["change_detection"],
        metadata={"engine": "onnxruntime", "domain": "change_detection"},
    )
    if registry is None:
        _REGISTERED = True


def ensure_builtin_models(registry: ModelRegistry | None = None) -> ModelRegistry:
    """注册内置模型并返回注册表，便于调用方链式使用。"""
    target = registry or get_model_registry()
    register_builtin_models(target)
    return target
