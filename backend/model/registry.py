# -*- coding: utf-8 -*-
"""
解译模型注册表 (Model Layer)

按 capability 注册模型工厂，懒加载实例：
- Agent/工具只调用 resolve("change_detection")，不感知 ProC/SegEarth；
- 同一 capability 可注册多个实现，按 name 选择；
- singleton=False 时每次新建实例（用于轻量/远程模型）。

后续新增模型只需调用 register_model(...)，不改 Agent。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type, Union

from backend.model.base import InterpretationModel, MODEL_TASKS


ModelFactory = Callable[[], InterpretationModel]


@dataclass
class ModelBinding:
    """一个 capability 下的模型注册项。"""

    capability: str
    name: str
    factory: ModelFactory
    singleton: bool = True
    metadata: Optional[Dict[str, Any]] = None


class ModelRegistry:
    """线程安全的模型注册表。"""

    def __init__(self) -> None:
        self._bindings: Dict[str, Dict[str, ModelBinding]] = {}
        self._instances: Dict[str, InterpretationModel] = {}
        self._lock = threading.RLock()

    def register(
        self,
        model: Union[InterpretationModel, Type[InterpretationModel], None] = None,
        *,
        factory: Optional[ModelFactory] = None,
        name: str = "",
        capabilities: Optional[List[str]] = None,
        singleton: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ModelBinding]:
        """
        入参:
          - model: 模型实例或类；与 factory 二选一。
          - factory: 无参工厂，延迟创建实例。
          - name: 模型名；缺省用 model.name 或类名。
          - capabilities: 覆盖模型自带 capabilities。
          - singleton: 同 name 是否复用实例。
          - metadata: 附带的模型信息（版本/分辨率/来源）。
        方法:
          - 解析工厂与能力列表，按 capability 建索引；
          - 同名重复注册会覆盖旧 binding。
        出参:
          - 本次注册的 binding 列表。
        """
        if model is None and factory is None:
            raise ValueError("model 与 factory 至少提供一个")
        if model is not None and factory is not None:
            raise ValueError("model 与 factory 不能同时提供")

        if factory is None:
            if isinstance(model, type):
                factory = model  # type: ignore[assignment]
                default_name = model.__name__
            else:
                instance = model  # type: ignore[assignment]
                factory = lambda: instance  # noqa: E731
                default_name = getattr(instance, "name", type(instance).__name__)
        else:
            default_name = ""

        caps = list(capabilities or getattr(model, "capabilities", []) or [])
        if not caps:
            raise ValueError("模型必须声明 capabilities")
        unknown = [cap for cap in caps if cap not in MODEL_TASKS]
        if unknown:
            raise ValueError(f"不支持的 capability: {unknown}")

        resolved_name = name or default_name
        if not resolved_name:
            raise ValueError("模型必须提供 name")

        bindings: List[ModelBinding] = []
        with self._lock:
            for cap in caps:
                binding = ModelBinding(
                    capability=cap,
                    name=resolved_name,
                    factory=factory,
                    singleton=singleton,
                    metadata=dict(metadata or {}),
                )
                self._bindings.setdefault(cap, {})[resolved_name] = binding
                bindings.append(binding)
        return bindings

    def resolve(self, capability: str, name: str = "") -> InterpretationModel:
        """
        入参:
          - capability: 模型能力，如 segmentation/change_detection。
          - name: 指定实现名；空则取该能力下第一个注册项。
        方法:
          - 懒创建实例；singleton=True 时缓存。
        出参:
          - InterpretationModel 实例。
        """
        if capability not in MODEL_TASKS:
            raise ValueError(f"不支持的 capability: {capability}")
        with self._lock:
            bucket = self._bindings.get(capability) or {}
            if not bucket:
                raise LookupError(f"未注册 capability: {capability}")
            binding = bucket.get(name) if name else next(iter(bucket.values()))
            if binding is None:
                raise LookupError(f"capability={capability} 下未找到模型: {name}")
            cache_key = f"{capability}:{binding.name}"
            if binding.singleton and cache_key in self._instances:
                return self._instances[cache_key]
            instance = binding.factory()
            if binding.singleton:
                self._instances[cache_key] = instance
            return instance

    def list_models(self) -> List[Dict[str, Any]]:
        """列出所有注册项，不创建实例。"""
        with self._lock:
            rows: List[Dict[str, Any]] = []
            for capability, bucket in self._bindings.items():
                for name, binding in bucket.items():
                    rows.append({
                        "capability": capability,
                        "name": name,
                        "singleton": binding.singleton,
                        "metadata": dict(binding.metadata or {}),
                    })
            return rows

    def clear(self) -> None:
        """清空注册表与实例缓存（测试用）。"""
        with self._lock:
            self._bindings.clear()
            self._instances.clear()


_REGISTRY = ModelRegistry()


def get_model_registry() -> ModelRegistry:
    """返回进程级默认注册表。"""
    return _REGISTRY


def register_model(*args, **kwargs) -> List[ModelBinding]:
    """转发到默认注册表，便于模块导入时注册内置模型。"""
    return _REGISTRY.register(*args, **kwargs)
