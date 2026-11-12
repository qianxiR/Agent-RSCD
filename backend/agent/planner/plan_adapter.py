# -*- coding: utf-8 -*-
"""
PlanGraph → 现有 plan state 适配器

让 Plan-first 计划可以进入当前 ReAct 执行闭环：
- 节点转换为 plan.steps（step_id=node_id），携带 depends_on/acceptance/capability；
- internal 节点不进入执行步骤（校验在构建计划时完成）；
- 可选节点标记 required=False，不阻塞完成门。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.agent.memory.task_state import append_plan_step, create_plan_state
from backend.agent.planner.schema import PlanGraph, PlanNode
from backend.agent.runtime.task_spec import build_task_spec


def graph_to_plan_state(
    graph: PlanGraph,
    conversation_id: str,
    context_inputs: Dict[str, Any] | None = None,
    keep_completed_from: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    入参:
      - graph: 已校验的 PlanGraph。
      - conversation_id: 当前会话。
      - context_inputs: 已确认输入（写入 task_spec）。
      - keep_completed_from: 重规划前的旧计划；已完成的步骤原样保留。
    方法:
      - 创建 plan state；
      - 只把 executor=tool/worker 的节点转成步骤；
      - 依赖中的 internal 节点会被过滤（已在计划构建阶段完成校验）；
      - 传入旧计划时，先保留其 completed 步骤，再追加新节点。
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

    if isinstance(keep_completed_from, dict):
        kept = [
            dict(step)
            for step in (keep_completed_from.get("steps") or [])
            if step.get("status") == "completed"
        ]
        plan["steps"] = kept
        if keep_completed_from.get("selected_skill"):
            plan["selected_skill"] = keep_completed_from["selected_skill"]
        if keep_completed_from.get("skill_fallback_rule"):
            plan["skill_fallback_rule"] = keep_completed_from["skill_fallback_rule"]

    existing_ids = {step.get("step_id") for step in plan.get("steps") or []}
    executable = [node for node in graph.nodes if node.executor in {"tool", "worker"}]
    executable_ids = {node.node_id for node in executable}
    for node in executable:
        if node.node_id in existing_ids:
            continue
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


def skill_contract_to_graph(
    selection: Dict[str, Any],
    goal: str,
    context_inputs: Dict[str, Any] | None = None,
    conversation_id: str = "",
) -> Optional[PlanGraph]:
    """
    入参:
      - selection: select_skill 的结果，必须含合法 contract。
      - goal: 用户目标。
      - context_inputs: 已确认输入。
      - conversation_id: 仅用于 task_spec 推断。
    方法:
      - 把契约 steps 按声明顺序转成线性 DAG（后一步依赖前一步）；
      - 输入只填入已确认值，缺失保持占位，由执行阶段请求用户补充。
    出参:
      - PlanGraph；契约不合法返回 None。
    """
    selected = (selection or {}).get("selected_skill") if isinstance(selection, dict) else None
    if not isinstance(selected, dict):
        return None
    contract = selected.get("contract") if isinstance(selected.get("contract"), dict) else {}
    contract_steps = contract.get("steps") if isinstance(contract.get("steps"), list) else []
    if not contract_steps:
        return None
    inputs = context_inputs if isinstance(context_inputs, dict) else {}
    task_spec = build_task_spec(
        goal=goal,
        context_inputs=inputs,
        selection=selection,
        conversation_id=conversation_id,
    )
    nodes: List[PlanNode] = []
    previous_id = ""
    for index, step in enumerate(contract_steps):
        if not isinstance(step, dict) or not step.get("tool_name"):
            return None
        node_id = str(step.get("step_id") or f"n{index + 1}")
        step_inputs = {
            name: inputs[name]
            for name in (step.get("required_inputs") or [])
            if name in inputs
        }
        nodes.append(PlanNode(
            node_id=node_id,
            goal=str(step.get("goal") or step.get("tool_name") or "执行技能步骤"),
            executor="tool",
            tool=str(step.get("tool_name")),
            depends_on=[previous_id] if previous_id else [],
            required=bool(step.get("required", True)),
            inputs=step_inputs,
            acceptance=list(step.get("expected_outputs") or [])
            + list(step.get("verification_rules") or []),
            capability=str(selected.get("name") or ""),
        ))
        previous_id = node_id
    return PlanGraph(
        plan_id=f"plan_skill_{abs(hash(goal)) % 10 ** 10}",
        goal=goal or "技能任务",
        task_type=task_spec.get("task_type") or "unknown",
        nodes=nodes,
        metadata={"source": "skill_contract", "skill": selected.get("name")},
    )
