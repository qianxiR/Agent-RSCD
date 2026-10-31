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
    方法: 注册 ProC-SAM3 分割/变化检测适配器；重复调用幂等。
    出参: 无。
    """
    global _REGISTERED
    target = registry or get_model_registry()
    if _REGISTERED and registry is None:
        return
    from backend.model.change_detection.proc_sam3 import ProCSam3ChangeModel
    from backend.model.segmentation.proc_sam3 import ProCSam3SegmentModel

    target.register(
        ProCSam3SegmentModel,
        name="proc_sam3",
        capabilities=["segmentation"],
        metadata={"engine": "proc_sam3", "domain": "segmentation"},
    )
    target.register(
        ProCSam3ChangeModel,
        name="proc_sam3_change",
        capabilities=["change_detection"],
        metadata={"engine": "proc_sam3", "domain": "change_detection"},
    )
    if registry is None:
        _REGISTERED = True


def ensure_builtin_models(registry: ModelRegistry | None = None) -> ModelRegistry:
    """注册内置模型并返回注册表，便于调用方链式使用。"""
    target = registry or get_model_registry()
    register_builtin_models(target)
    return target
