# -*- coding: utf-8 -*-
"""单步执行器 (Agent 层 / Executor)

内层 ReAct：一次只执行一个计划步骤。
- 工具绑定收窄到本步骤声明的工具（无声明时按步骤目标检索）；
- 有独立轮次上限，达到验收即停；
- 工具结果统一过 observation_builder，得到 agent_validation；
- 不捕获 asyncio.CancelledError，交给外层停止语义处理。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.agent.planner.schema import PlanNode
from backend.agent.runtime.observation_builder import build_observation_tool_message
from backend.agent.tools.registry import get_tools_for_llm

logger = logging.getLogger(__name__)

META_TOOL_NAMES = ("search_tools",)

StreamFunc = Callable[..., Awaitable[AIMessage]]
LlmFactory = Callable[[List[str]], Any]
EmitFunc = Callable[[dict], Awaitable[None]]


@dataclass
class StepOutcome:
    """单步执行结果。"""

    status: str = "failed"                 # passed / failed
    outputs: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    repair_plan: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    tool_calls: int = 0


def _step_tool_names(node: PlanNode) -> List[str]:
    """本步骤允许绑定的工具名。"""
    names: List[str] = []
    if node.tool:
        names.append(node.tool)
    if node.fallback_tools:
        names.extend(node.fallback_tools)
    for meta in META_TOOL_NAMES:
        if meta not in names:
            names.append(meta)
    if not node.tool:
        from backend.agent.tools.discovery import select_initial_tool_names
        for name in select_initial_tool_names(node.goal):
            if name not in names:
                names.append(name)
    return names


def _step_contract_text(node: PlanNode, plan_goal: str, upstream_outputs: Dict[str, Any]) -> str:
    """生成步骤契约文本（作为 SystemMessage 注入）。"""
    lines = [
        "你正在执行显式计划中的一个步骤。只完成本步骤，不要处理其他步骤。",
        f"- 计划目标: {plan_goal or '未提供'}",
        f"- 当前步骤: {node.node_id} | {node.goal}",
    ]
    if node.tool:
        lines.append(f"- 本步骤应使用的工具: {node.tool}")
    if node.inputs:
        lines.append(f"- 已确认输入: {json.dumps(node.inputs, ensure_ascii=False)[:1500]}")
    if upstream_outputs:
        lines.append(f"- 上游产物: {json.dumps(upstream_outputs, ensure_ascii=False)[:1500]}")
    if node.acceptance:
        lines.append("- 验收标准: " + "；".join(node.acceptance))
    lines.append("- 工具返回 agent_validation.status=passed 后立即停止本步骤；失败时按 repair_plan 调整参数重试。")
    return "\n".join(lines)


def _extract_outputs(tool_result: Dict[str, Any]) -> Dict[str, Any]:
    """从工具结果提取可被下游占位符引用的字段。"""
    if not isinstance(tool_result, dict):
        return {}
    data = tool_result.get("data")
    outputs = dict(data) if isinstance(data, dict) else {}
    for key in ("type", "summary", "msg"):
        if key in tool_result and key not in outputs:
            outputs[key] = tool_result[key]
    return outputs


async def run_step(
    node: PlanNode,
    *,
    plan_goal: str,
    upstream_outputs: Optional[Dict[str, Any]],
    llm_factory: LlmFactory,
    stream_llm: StreamFunc,
    tool_runner,
    emit: Optional[EmitFunc] = None,
    conversation_id: str = "",
    max_turns: int = 6,
    all_messages: Optional[list] = None,
) -> StepOutcome:
    """
    入参:
      - node: 当前计划节点。
      - plan_goal: 整张计划的用户目标。
      - upstream_outputs: 上游已完成步骤的产物汇总。
      - llm_factory: 按工具名列表构造 LLM 实例。
      - stream_llm: 流式调用函数（与自由 ReAct 共用）。
      - tool_runner: ToolCallRunner 实例。
      - emit: 事件发送回调。
      - max_turns: 本步骤最多 LLM 轮次。
      - all_messages: 会话消息列表；传入时把本步骤消息追加进去，保持持久化结构。
    方法:
      - 逐步执行工具，直到 agent_validation passed / 无 tool_calls / 轮次上限。
    出参:
      - StepOutcome。
    """
    tool_names = _step_tool_names(node)
    tools = get_tools_for_llm(tool_names, compact=True)
    llm = llm_factory(tools)

    messages: List[Any] = [
        SystemMessage(content=_step_contract_text(node, plan_goal, upstream_outputs or {})),
        HumanMessage(content=json.dumps({
            "step_id": node.node_id,
            "goal": node.goal,
            "upstream_outputs": upstream_outputs or {},
        }, ensure_ascii=False)),
    ]
    if all_messages is not None:
        all_messages.append(messages[0])

    tool_calls = 0
    last_repair: Dict[str, Any] = {}
    last_error = ""

    for turn in range(max(1, int(max_turns))):
        ai_message = await stream_llm(llm, messages, conversation_id=conversation_id)
        messages.append(ai_message)
        if all_messages is not None:
            all_messages.append(ai_message)

        if not ai_message.tool_calls:
            last_error = last_error or "步骤未返回通过验证的工具结果"
            break

        for call in ai_message.tool_calls:
            tool_name = call.get("name") or ""
            tool_args = call.get("args") if isinstance(call.get("args"), dict) else {}
            tool_call_id = call.get("id") or f"{node.node_id}_{turn}"

            if emit is not None:
                await emit({
                    "type": "tool_call",
                    "tool_calls": [{
                        "id": tool_call_id,
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "args": tool_args,
                    }],
                    "conversation_id": conversation_id,
                })

            tool_result = await tool_runner.run(
                tool_name, tool_args, tool_call_id, announce=False
            )
            tool_calls += 1

            tool_message, enhanced = build_observation_tool_message(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_result=tool_result,
                user_goal=plan_goal or node.goal,
            )
            messages.append(tool_message)
            if all_messages is not None:
                all_messages.append(tool_message)

            if emit is not None:
                await emit({
                    "type": "tool_result",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": tool_message.content or "",
                    "status": "error" if isinstance(enhanced, dict) and enhanced.get("type") == "error" else "done",
                    "conversation_id": conversation_id,
                })

            validation = enhanced.get("agent_validation") if isinstance(enhanced, dict) else None
            validation = validation if isinstance(validation, dict) else {}
            if validation.get("status") == "passed":
                return StepOutcome(
                    status="passed",
                    outputs=_extract_outputs(enhanced),
                    evidence=validation,
                    tool_calls=tool_calls,
                )
            if validation.get("status") in {"failed", "warning"}:
                last_repair = validation.get("repair_plan") if isinstance(validation.get("repair_plan"), dict) else {}
                last_error = validation.get("reason") or validation.get("summary") or last_error
            elif isinstance(enhanced, dict) and enhanced.get("type") == "error":
                last_error = enhanced.get("msg") or last_error

    return StepOutcome(
        status="failed",
        evidence={"status": "failed", "reason": last_error},
        repair_plan=last_repair,
        error=last_error or "步骤未完成",
        tool_calls=tool_calls,
    )
