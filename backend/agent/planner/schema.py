# -*- coding: utf-8 -*-
"""
PlanGraph 计划图结构 (Agent 层 / Planner)

把"执行前计划"从线性 step 列表升级为可校验的 DAG：
- 节点声明依赖、可并行组、验收标准、备选工具；
- 节点状态、证据、修复计划在执行期回填；
- 可序列化为 JSON，便于落 ai_task / 日志 / 版本链。

本模块只定义结构，不做校验和执行；校验见 validator.py，执行见 executor/dag_executor.py。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    """返回秒级 ISO 时间，供计划审计字段统一使用。"""
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class PlanNode:
    """计划图中的一个可执行节点。"""

    node_id: str
    goal: str
    executor: str = "tool"              # tool / internal / worker
    tool: str = ""
    capability: str = ""                # 模型能力名，如 segmentation/change_detection
    depends_on: List[str] = field(default_factory=list)
    parallel_group: str = ""
    required: bool = True
    inputs: Dict[str, Any] = field(default_factory=dict)
    acceptance: List[str] = field(default_factory=list)
    fallback_tools: List[str] = field(default_factory=list)

    # 执行期状态
    status: str = "pending"             # pending/running/completed/failed/skipped/blocked
    attempts: int = 0
    outputs: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    repair_plan: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanNode":
        """从 dict 还原节点；未知字段忽略，缺失字段用默认值。"""
        if not isinstance(data, dict):
            raise ValueError("PlanNode 必须是 dict")
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class PlanGraph:
    """一次执行前计划的完整 DAG。"""

    plan_id: str
    goal: str
    task_type: str = "unknown"
    version: int = 1
    parent_plan_id: Optional[str] = None
    status: str = "pending"             # pending/running/completed/blocked/failed
    nodes: List[PlanNode] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=lambda: {
        "max_nodes": 20,
        "max_tool_calls": 40,
        "max_seconds": 1800,
    })
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def node_map(self) -> Dict[str, PlanNode]:
        """返回 node_id → PlanNode 映射。"""
        return {node.node_id: node for node in self.nodes}

    def node(self, node_id: str) -> Optional[PlanNode]:
        """按 ID 取节点，不存在返回 None。"""
        return self.node_map().get(node_id)

    def required_nodes(self) -> List[PlanNode]:
        """返回所有必需节点。"""
        return [node for node in self.nodes if node.required]

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        data = asdict(self)
        data["nodes"] = [node.to_dict() for node in self.nodes]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanGraph":
        """从 dict 还原计划图。"""
        if not isinstance(data, dict):
            raise ValueError("PlanGraph 必须是 dict")
        nodes = [PlanNode.from_dict(item) for item in (data.get("nodes") or [])]
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {key: value for key, value in data.items() if key in known and key != "nodes"}
        return cls(nodes=nodes, **kwargs)

    def touch(self) -> None:
        """刷新 updated_at。"""
        self.updated_at = _now_iso()
