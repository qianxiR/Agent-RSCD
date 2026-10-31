# -*- coding: utf-8 -*-
"""
PlanGraph DAG 执行器 (Agent 层 / Executor)

职责：
- 按 depends_on 计算 ready 节点；
- 解析 {{node_id.field}} 输入占位符；
- 并发执行同一批 ready 节点（受 max_parallel 限制）；
- 回填 status/outputs/evidence；
- required 节点失败时停止并标记 blocked，optional 节点失败降级为 skipped。

执行器不关心工具细节：调用方注入 runner(node, inputs) -> NodeResult。
"""
from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.agent.planner.schema import PlanGraph, PlanNode
from backend.agent.planner.validator import validate_plan_graph


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_.]+)\s*\}\}")


@dataclass
class NodeResult:
    """节点执行结果；runner 可返回本对象或同结构 dict。"""

    status: str = "passed"               # passed / failed
    outputs: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "NodeResult":
        """把 runner 返回值规范化为 NodeResult。"""
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                status=str(value.get("status") or ("passed" if value.get("ok") else "failed")),
                outputs=value.get("outputs") if isinstance(value.get("outputs"), dict) else {},
                evidence=value.get("evidence") if isinstance(value.get("evidence"), dict) else {},
                error=str(value.get("error") or value.get("msg") or ""),
            )
        if value is None:
            return cls(status="passed")
        return cls(status="passed", outputs={"value": value})


NodeRunner = Callable[[PlanNode, Dict[str, Any]], Awaitable[Any] | Any]


def _lookup_path(data: Dict[str, Any], path: str) -> Any:
    """按 a.b.c 取值；不存在返回 None。"""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def resolve_node_inputs(node: PlanNode, graph: PlanGraph) -> Dict[str, Any]:
    """
    入参:
      - node: 当前节点。
      - graph: 计划图（提供上游节点 outputs）。
    方法:
      - 递归解析 inputs 中的 {{node_id.field}} 占位符；
      - 上游字段缺失时抛 ValueError，让节点失败而不是带着 None 执行。
    出参:
      - 已解析的 inputs dict。
    """
    node_map = graph.node_map()

    def resolve(value: Any) -> Any:
        if isinstance(value, str):
            match = _PLACEHOLDER_RE.fullmatch(value.strip())
            if not match:
                return value
            source_id, path = match.groups()
            source = node_map.get(source_id)
            if source is None:
                raise ValueError(f"占位符引用了不存在的节点: {source_id}")
            resolved = _lookup_path(source.outputs, path)
            if resolved is None:
                raise ValueError(f"节点 {source_id} 缺少输出字段: {path}")
            return resolved
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        return value

    return {key: resolve(value) for key, value in (node.inputs or {}).items()}


def _dependencies_satisfied(node: PlanNode, graph: PlanGraph) -> bool:
    """依赖节点必须 completed 或 skipped（optional 降级）。"""
    node_map = graph.node_map()
    for dep in node.depends_on:
        dep_node = node_map.get(dep)
        if dep_node is None or dep_node.status not in {"completed", "skipped"}:
            return False
    return True


def _dependency_blocked(node: PlanNode, graph: PlanGraph) -> bool:
    """任一依赖 failed/blocked 且节点必需时，节点不应再执行。"""
    node_map = graph.node_map()
    return any(
        node_map.get(dep) is not None
        and node_map[dep].status in {"failed", "blocked"}
        for dep in node.depends_on
    )


async def _call_runner(runner: NodeRunner, node: PlanNode, inputs: Dict[str, Any]) -> NodeResult:
    """执行 runner，兼容同步/异步实现。"""
    value = runner(node, inputs)
    if inspect.isawaitable(value):
        value = await value
    return NodeResult.from_value(value)


async def run_plan(
    graph: PlanGraph,
    runner: NodeRunner,
    *,
    max_parallel: int = 4,
    stop_on_required_failure: bool = True,
    on_update: Optional[Callable[[PlanGraph, PlanNode], None]] = None,
) -> PlanGraph:
    """
    入参:
      - graph: 已通过结构校验的计划图。
      - runner: 节点执行函数，返回 NodeResult / dict / None。
      - max_parallel: 同一批 ready 节点最大并发数。
      - stop_on_required_failure: required 节点失败后是否停止整图。
      - on_update: 每个节点状态变更后的回调（用于日志/WS）。
    方法:
      - 循环取 ready 节点并执行，直到完成/阻塞；
      - 失败节点保留 repair_plan/error，后续可由 Replanner 替换子图；
      - 预算 max_tool_calls 超限时停止并标记 blocked。
    出参:
      - 原地更新后的 PlanGraph。
    """
    validate_plan_graph(graph)
    graph.status = "running"
    graph.touch()
    node_map = graph.node_map()
    max_calls = int(graph.budget.get("max_tool_calls") or 0)
    total_calls = 0

    while True:
        pending = [node for node in graph.nodes if node.status in {"pending"}]

        # 依赖失败且必需的节点直接 blocked，避免死等
        for node in pending:
            if node.required and _dependency_blocked(node, graph):
                node.status = "blocked"
                node.error = "依赖节点失败，无法执行"
                graph.touch()
                if on_update:
                    on_update(graph, node)

        pending = [node for node in graph.nodes if node.status == "pending"]
        if not pending:
            break

        ready = [node for node in pending if _dependencies_satisfied(node, graph)]
        if not ready:
            # 还有 pending 但没有 ready：依赖链已失败/不可能满足
            for node in pending:
                node.status = "blocked"
                node.error = "依赖未满足，计划无法继续"
                if on_update:
                    on_update(graph, node)
            graph.touch()
            break

        if max_calls and total_calls >= max_calls:
            graph.status = "blocked"
            graph.metadata["budget_exceeded"] = "max_tool_calls"
            graph.touch()
            break

        batch = ready[:max(1, max_parallel)]
        for node in batch:
            node.status = "running"
            node.attempts += 1
            node.error = ""
            total_calls += 1
            graph.touch()
            if on_update:
                on_update(graph, node)

        results = await asyncio.gather(
            *[_run_node_safe(runner, node, graph) for node in batch],
            return_exceptions=True,
        )

        required_failed = False
        for node, result in zip(batch, results):
            if isinstance(result, Exception):
                node.status = "failed"
                node.error = str(result)
            else:
                node.outputs = result.outputs
                if result.evidence:
                    node.evidence.append(result.evidence)
                if result.status == "passed":
                    node.status = "completed"
                else:
                    node.status = "failed"
                    node.error = result.error or "节点执行失败"
            if node.required and node.status == "failed":
                required_failed = True
            elif not node.required and node.status == "failed":
                # optional 节点失败降级，不阻塞主链路
                node.status = "skipped"
                node.outputs = {}
            graph.touch()
            if on_update:
                on_update(graph, node)

        if required_failed and stop_on_required_failure:
            for node in graph.nodes:
                if node.status == "pending":
                    node.status = "blocked"
                    node.error = "上游必需节点失败，计划终止"
            graph.status = "blocked"
            graph.touch()
            break

    required = graph.required_nodes()
    if all(node.status == "completed" for node in required) and required:
        graph.status = "completed"
    elif graph.status != "blocked":
        graph.status = "failed"
    graph.touch()
    return graph


async def _run_node_safe(runner: NodeRunner, node: PlanNode, graph: PlanGraph) -> NodeResult:
    """解析输入并执行；把异常交给 run_plan 统一落状态。"""
    inputs = resolve_node_inputs(node, graph)
    return await _call_runner(runner, node, inputs)
