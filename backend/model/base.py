# -*- coding: utf-8 -*-
"""
解译模型统一契约 (Model Layer)

所有遥感解译模型（分割 / 变化检测 / 影像理解 / 检测 / 分类 / 检索）
实现同一协议，Agent 与工具只依赖 capability，不依赖具体模型。

新增模型步骤：
1. 在 model/<domain>/<name>/ 实现 InterpretationModel；
2. 在 model/registry.py 注册 capability → factory；
3. 无需修改 Agent、工具或 Prompt。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Set, runtime_checkable

from backend.data.base import AssetRef


MODEL_TASKS = {
    "segmentation",
    "change_detection",
    "understanding",
    "detection",
    "classification",
    "embedding",
}


@dataclass
class ModelRequest:
    """一次模型推理请求。"""

    task: str
    inputs: List[AssetRef] = field(default_factory=list)
    classes: str = ""
    bbox: Optional[List[float]] = None
    params: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        data = asdict(self)
        data["inputs"] = [item.to_dict() for item in self.inputs]
        return data


@dataclass
class ModelResult:
    """一次模型推理结果；outputs 统一用 AssetRef 表达产物。"""

    status: str = "passed"                 # passed / failed / warning
    outputs: List[AssetRef] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    masks: List[AssetRef] = field(default_factory=list)
    vectors: List[AssetRef] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)
    model_info: Dict[str, Any] = field(default_factory=dict)
    performance: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        return {
            "status": self.status,
            "outputs": [item.to_dict() for item in self.outputs],
            "data": self.data,
            "masks": [item.to_dict() for item in self.masks],
            "vectors": [item.to_dict() for item in self.vectors],
            "stats": self.stats,
            "verification": self.verification,
            "model_info": self.model_info,
            "performance": self.performance,
            "error": self.error,
        }


@runtime_checkable
class InterpretationModel(Protocol):
    """解译模型协议；实现方可以是本地 torch 模型、远程服务或 mock。"""

    name: str
    capabilities: Set[str]

    def warmup(self) -> None:
        """按需加载权重/连接；允许空实现。"""
        ...

    def predict(self, request: ModelRequest) -> ModelResult:
        """执行推理并返回统一结果。"""
        ...

    def release(self) -> None:
        """释放显存/连接；允许空实现。"""
        ...
