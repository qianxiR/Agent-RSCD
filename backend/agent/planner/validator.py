# -*- coding: utf-8 -*-
"""
PlanGraph 计划校验 (Agent 层 / Planner)

Planner 产出的 DAG 不能直接执行，必须先过确定性质量门：
- 节点 ID 唯一、依赖存在、无环；
- 工具存在且在 LLM/执行白名单内；
- 必需节点必须有验收标准；
- 节点数/深度不超过预算。

校验失败抛 PlanValidationError，由调用方决定是重规划还是回退 ReAct。
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Set

from backend.agent.planner.schema import PlanGraph, PlanNode


ALLOWED_EXECUTORS = {"tool", "internal", "worker"}


class PlanValidationError(ValueError):
    """计划图不合法；issues 保存全部可读原因。"""

    def __init__(self, issues: List[str]):
        self.issues = list(issues)
        super().__init__("; ".join(self.issues))


def _find_cycle(graph: PlanGraph) -> Optional[List[str]]:
    """用 Kahn 拓扑排序找环；有环时返回仍留在图中的节点 ID。"""
    node_map = graph.node_map()
    indegree = {node_id: 0 for node_id in node_map}
    children: Dict[str, List[str]] = {node_id: [] for node_id in node_map}
    for node in graph.nodes:
        for dep in node.depends_on:
            if dep in node_map:
                indegree[node.node_id] += 1
                children[dep].append(node.node_id)
    queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited == len(node_map):
        return None
    return [node_id for node_id, degree in indegree.items() if degree > 0]


def _max_depth(graph: PlanGraph) -> int:
    """计算 DAG 最大深度；调用前应保证无环。"""
    node_map = graph.node_map()
    memo: Dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        node = node_map.get(node_id)
        if not node:
            return 0
        value = 1 + max((depth(dep) for dep in node.depends_on if dep in node_map), default=0)
        memo[node_id] = value
        return value

    return max((depth(node_id) for node_id in node_map), default=0)


def collect_plan_issues(
    graph: PlanGraph,
    available_tools: Optional[Iterable[str]] = None,
    tool_whitelist: Optional[Iterable[str]] = None,
    max_nodes: Optional[int] = None,
    max_depth: int = 12,
) -> List[str]:
    """
    入参:
      - graph: 待校验计划图。
      - available_tools: 当前已注册工具名；None 表示不检查工具存在性。
      - tool_whitelist: 允许 LLM/执行器使用的工具名；None 表示不检查白名单。
      - max_nodes: 节点数上限；None 时用 graph.budget.max_nodes。
      - max_depth: DAG 深度上限。
    方法:
      - 逐项检查结构、依赖、环、工具、验收标准、预算。
    出参:
      - issues 列表；空列表表示通过。
    """
    issues: List[str] = []
    if not graph.plan_id:
        issues.append("plan_id 不能为空")
    if not isinstance(graph.goal, str) or not graph.goal.strip():
        issues.append("goal 不能为空")
    if not graph.nodes:
        issues.append("计划至少需要一个节点")

    node_map = graph.node_map()
    if len(node_map) != len(graph.nodes):
        issues.append("node_id 存在重复")

    available = set(available_tools) if available_tools is not None else None
    whitelist = set(tool_whitelist) if tool_whitelist is not None else None

    for node in graph.nodes:
        if not node.node_id:
            issues.append("存在空 node_id")
            continue
        if not node.goal.strip():
            issues.append(f"{node.node_id}: goal 不能为空")
        if node.executor not in ALLOWED_EXECUTORS:
            issues.append(f"{node.node_id}: 不支持 executor={node.executor}")
        if node.executor == "tool" and not node.tool:
            issues.append(f"{node.node_id}: tool executor 必须声明 tool")
        if node.executor == "tool":
            if available is not None and node.tool not in available:
                issues.append(f"{node.node_id}: 工具未注册 {node.tool}")
            if whitelist is not None and node.tool not in whitelist:
                issues.append(f"{node.node_id}: 工具不在白名单 {node.tool}")
        if node.required and node.executor == "tool" and not node.acceptance:
            issues.append(f"{node.node_id}: 必需工具节点必须声明 acceptance")
        for dep in node.depends_on:
            if dep == node.node_id:
                issues.append(f"{node.node_id}: 不能依赖自身")
            elif dep not in node_map:
                issues.append(f"{node.node_id}: 依赖不存在 {dep}")

    cycle_nodes = _find_cycle(graph)
    if cycle_nodes:
        issues.append(f"计划存在环: {', '.join(cycle_nodes)}")

    limit = max_nodes if max_nodes is not None else int(graph.budget.get("max_nodes") or 20)
    if limit > 0 and len(graph.nodes) > limit:
        issues.append(f"节点数 {len(graph.nodes)} 超过上限 {limit}")

    if not cycle_nodes:
        depth = _max_depth(graph)
        if max_depth > 0 and depth > max_depth:
            issues.append(f"计划深度 {depth} 超过上限 {max_depth}")

    return issues


def validate_plan_graph(
    graph: PlanGraph,
    available_tools: Optional[Iterable[str]] = None,
    tool_whitelist: Optional[Iterable[str]] = None,
    max_nodes: Optional[int] = None,
    max_depth: int = 12,
) -> None:
    """
    入参: 同 collect_plan_issues。
    方法: 收集问题，非空时抛 PlanValidationError。
    出参: 无；失败抛异常。
    """
    issues = collect_plan_issues(
        graph,
        available_tools=available_tools,
        tool_whitelist=tool_whitelist,
        max_nodes=max_nodes,
        max_depth=max_depth,
    )
    if issues:
        raise PlanValidationError(issues)
