# -*- coding: utf-8 -*-
"""Plan-and-Execute 外层循环 (Agent 层 / Executor)

Loop{Plan, ReAct}：
- 按依赖取 ready 步骤，交给 step_executor 执行；
- 必需步骤失败时调用 LLM 重新规划剩余步骤，保留已完成步骤；
- 全部必需步骤通过验证后，完成门放行，再由一次无工具调用生成最终回复；
- 预算耗尽、重规划次数耗尽或计划阻塞时退出。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.agent.executor.step_executor import run_step
from backend.agent.memory.task_state import (
    apply_node_outcome,
    can_finalize_plan,
    collect_completed_outputs,
    create_plan_revision,
)
from backend.agent.observability.decision_audit import append_jsonl
from backend.agent.planner.llm_planner import revise_plan
from backend.agent.planner.plan_adapter import graph_to_plan_state
from backend.agent.planner.schema import PlanNode
from backend.agent.planner.validator import PlanValidationError, validate_plan_graph

logger = logging.getLogger(__name__)

EmitFunc = Callable[[dict], Awaitable[None]]

FINALIZE_SYSTEM_PROMPT = """你是遥感解译智能体。计划已执行完毕，请只根据下面的计划状态与证据向用户汇报结果。
要求：
1. 明确列出产物路径、统计结果和未完成的步骤（如果有）。
2. 不得声称完成了没有通过验证的步骤。
3. 不输出 JSON，直接给用户可读的中文回复。"""


@dataclass
class PlanRunResult:
    """外层循环结果。"""

    status: str                        # completed / blocked / budget_exceeded / failed / empty
    plan: Dict[str, Any]
    final_content: str = ""
    reason: str = ""


def _step_dependencies_satisfied(step: Dict[str, Any], plan: Dict[str, Any]) -> bool:
    """依赖步骤必须全部 completed。"""
    status_map = {
        item.get("step_id"): item.get("status")
        for item in plan.get("steps") or []
    }
    return all(status_map.get(dep) == "completed" for dep in (step.get("depends_on") or []))


def next_ready_step(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    入参: plan — 当前计划。
    方法:
      - 取第一个 pending/blocked/failed 且依赖已完成的步骤；
      - 优先必需步骤。
    出参: 步骤 dict；没有可执行步骤返回 None。
    """
    steps = [item for item in plan.get("steps") or [] if item.get("status") in {"pending", "blocked", "failed"}]
    steps.sort(key=lambda item: 0 if item.get("required", True) else 1)
    for step in steps:
        if _step_dependencies_satisfied(step, plan):
            return step
    return None


def _to_plan_node(step: Dict[str, Any]) -> PlanNode:
    """把 plan step 还原为 PlanNode，供单步执行器使用。"""
    return PlanNode(
        node_id=str(step.get("step_id") or step.get("node_id") or ""),
        goal=str(step.get("goal") or ""),
        executor="tool",
        tool=str(step.get("tool_name") or ""),
        depends_on=list(step.get("depends_on") or []),
        required=bool(step.get("required", True)),
        inputs=step.get("inputs") if isinstance(step.get("inputs"), dict) else {},
        acceptance=list(step.get("acceptance") or step.get("expected_outputs") or []),
        status=str(step.get("status") or "pending"),
    )


def _failure_context(step: Dict[str, Any], outcome) -> Dict[str, Any]:
    """组装传给重规划器的失败证据。"""
    return {
        "step_id": step.get("step_id"),
        "goal": step.get("goal"),
        "tool": step.get("tool_name"),
        "status": outcome.status,
        "error": outcome.error,
        "repair_plan": outcome.repair_plan,
        "evidence": outcome.evidence,
    }


async def _finalize(plan: Dict[str, Any], llm_without_tools) -> str:
    """计划完成后生成用户可见回复。"""
    steps = []
    for step in plan.get("steps") or []:
        steps.append({
            "step_id": step.get("step_id"),
            "goal": step.get("goal"),
            "status": step.get("status"),
            "outputs": step.get("outputs") or {},
            "evidence": step.get("evidence") or [],
        })
    payload = {
        "goal": plan.get("goal"),
        "task_type": plan.get("task_type"),
        "steps": steps,
        "completion_gate": plan.get("completion_gate") or {},
    }
    messages = [
        SystemMessage(content=FINALIZE_SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)[:12000]),
    ]
    response = await llm_without_tools.ainvoke(messages)
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        )
    return str(content or "").strip()


def _trace(event: Dict[str, Any]) -> None:
    """写执行轨迹 JSONL；失败不影响主流程。"""
    try:
        append_jsonl(Path("logs") / "agent-plan-execute.jsonl", event)
    except Exception as exc:
        logger.debug(f"[PlanExecute] trace 写入失败: {exc}")


async def run_plan_and_execute(
    *,
    conversation_id: str,
    user_id: str,
    goal: str,
    plan: Dict[str, Any],
    llm_factory,
    llm_without_tools,
    stream_llm,
    tool_runner,
    emit: Optional[EmitFunc] = None,
    save_plan: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_step_done: Optional[Callable[[Dict[str, Any]], None]] = None,
    tool_cards: Optional[List[Dict[str, Any]]] = None,
    available_tools: Optional[set] = None,
    tool_whitelist: Optional[List[str]] = None,
    context_inputs: Optional[Dict[str, Any]] = None,
    all_messages: Optional[list] = None,
    max_versions: int = 2,
    max_tool_calls: int = 20,
    max_step_turns: int = 6,
) -> PlanRunResult:
    """
    入参:
      - plan: 已构建的 plan state（至少含一个步骤）。
      - llm_factory: 按工具列表构造 LLM。
      - llm_without_tools: 用于最终回复。
      - stream_llm / tool_runner / emit: 执行期依赖。
      - save_plan / on_step_done: 持久化与快照回调。
      - 其余: 重规划所需工具清单与预算。
    方法:
      - 逐步执行 → 失败重规划 → 完成门收尾；不捕获 CancelledError。
    出参:
      - PlanRunResult。
    """
    if not plan.get("steps"):
        return PlanRunResult(status="empty", plan=plan, reason="计划没有可执行步骤")

    version = int(plan.get("plan_version") or 1)
    total_calls = 0
    status = "failed"
    reason = ""
    tool_cards = tool_cards or []

    while True:
        step = next_ready_step(plan)
        if step is None:
            break

        node = _to_plan_node(step)
        upstream = collect_completed_outputs(plan)
        logger.info(
            "[PlanExecute] conv=%s version=%s step=%s tool=%s",
            conversation_id, version, node.node_id, node.tool or "-",
        )

        outcome = await run_step(
            node,
            plan_goal=goal or str(plan.get("goal") or ""),
            upstream_outputs=upstream,
            llm_factory=llm_factory,
            stream_llm=stream_llm,
            tool_runner=tool_runner,
            emit=emit,
            conversation_id=conversation_id,
            max_turns=max_step_turns,
            all_messages=all_messages,
        )
        total_calls += outcome.tool_calls

        apply_node_outcome(
            plan,
            node.node_id,
            status=outcome.status,
            outputs=outcome.outputs,
            evidence=outcome.evidence,
            repair_plan=outcome.repair_plan,
            error=outcome.error,
        )
        if save_plan is not None:
            save_plan(plan)
        if on_step_done is not None:
            on_step_done(plan)

        _trace({
            "event": "step",
            "conversation_id": conversation_id,
            "plan_id": plan.get("plan_id"),
            "plan_version": plan.get("plan_version"),
            "step_id": node.node_id,
            "tool": node.tool,
            "status": outcome.status,
            "tool_calls": outcome.tool_calls,
            "error": outcome.error,
        })

        is_required = bool(step.get("required", True))
        if not is_required and outcome.status != "passed":
            step["status"] = "cancelled"
            step["next_action"] = "可选步骤失败，已跳过"
            if save_plan is not None:
                save_plan(plan)
        if outcome.status != "passed" and is_required:
            if version >= max_versions:
                status = "blocked"
                reason = f"重规划次数已达上限，步骤 {node.node_id} 仍未通过验证"
                break
            revision, _ = create_plan_revision(
                plan,
                trigger=str((outcome.evidence or {}).get("reason") or outcome.error or "step_failed"),
                conversation_id=conversation_id,
            )
            new_graph = await revise_plan(
                goal or str(plan.get("goal") or ""),
                plan,
                _failure_context(step, outcome),
                tool_cards,
                llm=llm_without_tools,
                available_tools=available_tools,
                tool_whitelist=tool_whitelist,
            )
            if new_graph is None:
                status = "blocked"
                reason = "重规划未产出可执行计划"
                break
            try:
                validate_plan_graph(
                    new_graph,
                    available_tools=available_tools,
                    tool_whitelist=tool_whitelist,
                )
            except PlanValidationError as exc:
                status = "blocked"
                reason = f"重规划计划校验失败: {'; '.join(exc.issues[:5])}"
                break
            plan = graph_to_plan_state(
                new_graph,
                conversation_id,
                context_inputs=context_inputs or {},
                keep_completed_from=revision,
            )
            if len(plan.get("steps") or []) <= len(revision.get("steps") or []):
                status = "blocked"
                reason = "重规划未产生新的可执行步骤"
                break
            plan["plan_version"] = int(revision.get("plan_version") or version + 1)
            plan["parent_plan_id"] = revision.get("parent_plan_id")
            plan["revision_reason"] = revision.get("revision_reason")
            version = plan["plan_version"]
            _trace({
                "event": "replan",
                "conversation_id": conversation_id,
                "plan_id": plan.get("plan_id"),
                "plan_version": version,
                "trigger": plan.get("revision_reason"),
            })
            if save_plan is not None:
                save_plan(plan)
            continue

        if total_calls >= max_tool_calls:
            status = "budget_exceeded"
            reason = f"工具调用预算耗尽 ({total_calls}/{max_tool_calls})"
            break

    allowed, gate_reason = can_finalize_plan(plan)
    if allowed:
        status = "completed"
    elif status == "failed":
        status = "blocked"
        reason = gate_reason
    elif status == "budget_exceeded":
        reason = reason or gate_reason

    final_content = ""
    if status == "completed":
        try:
            final_content = await _finalize(plan, llm_without_tools)
        except Exception as exc:
            logger.warning(f"[PlanExecute] 最终回复生成失败: {exc}")
            status = "failed"
            reason = f"最终回复生成失败: {exc}"

    return PlanRunResult(
        status=status,
        plan=plan,
        final_content=final_content,
        reason=reason,
    )
