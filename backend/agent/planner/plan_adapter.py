# -*- coding: utf-8 -*-
"""
PlanGraph → 现有 plan state 适配器

让 Plan-first 计划可以进入当前 ReAct 执行闭环：
- 节点转换为 plan.steps（step_id=node_id），携带 depends_on/acceptance/capability；
- internal 节点不进入执行步骤（校验在构建计划时完成）；
- 可选节点标记 required=False，不阻塞完成门。
"""
from __future__ import annotations

from typing import Any, Dict, List

from backend.agent.memory.task_state import append_plan_step, create_plan_state
from backend.agent.planner.schema import PlanGraph
from backend.agent.runtime.task_spec import build_task_spec


def graph_to_plan_state(
    graph: PlanGraph,
    conversation_id: str,
    context_inputs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    入参:
      - graph: 已校验的 PlanGraph。
      - conversation_id: 当前会话。
      - context_inputs: 已确认输入（写入 task_spec）。
    方法:
      - 创建 plan state；
      - 只把 executor=tool/worker 的节点转成步骤；
      - 依赖中的 internal 节点会被过滤（已在计划构建阶段完成校验）。
    出参:
      - 可持久化的 plan state dict。
    """
    plan = create_plan_state(conversation_id, graph.goal)
    plan["plan_id"] = graph.plan_id
    plan["task_type"] = graph.task_type
    plan["task_spec"] = build_task_spec(
        goal=graph.goal,
        context_inputs=context_inputs or {},
        selection=None,
        conversation_id=conversation_id,
    )

    executable = [node for node in graph.nodes if node.executor in {"tool", "worker"}]
    executable_ids = {node.node_id for node in executable}
    for node in executable:
        deps: List[str] = [
            dep for dep in node.depends_on
            if dep in executable_ids
        ]
        append_plan_step(
            plan,
            goal=node.goal,
            executor="tool" if node.executor == "tool" else "report_agent",
            inputs=node.inputs,
            expected_outputs=list(node.acceptance),
            required=node.required,
            tool_name=node.tool,
            step_id=node.node_id,
            depends_on=deps,
            parallel_group=node.parallel_group,
            acceptance=list(node.acceptance),
            node_id=node.node_id,
            capability=node.capability,
        )
    return plan
