# -*- coding: utf-8 -*-
"""LLM 规划器 (Agent 层 / Planner)

外层规划器：把用户目标、结构化 task_spec、工具卡片和失败证据交给 LLM，
产出可校验的 PlanGraph。校验失败先让 LLM 修一次，仍失败返回 None，
由调用方回退模板或自由 ReAct。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.agent.planner.prompts import (
    PLANNER_REPAIR_PROMPT,
    PLANNER_REVISE_PROMPT,
    PLANNER_SYSTEM_PROMPT,
)
from backend.agent.planner.schema import PlanGraph, PlanNode
from backend.agent.planner.validator import PlanValidationError, validate_plan_graph

logger = logging.getLogger(__name__)


def _content_text(message: Any) -> str:
    """把 LLM 返回内容统一成文本（兼容字符串与 Responses 文本块）。"""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    入参: LLM 原始输出文本。
    方法: 去掉 Markdown 代码块，从第一个 '{' 开始用 raw_decode 解析。
    出参: dict 或 None。
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start = cleaned.find("{")
    if start < 0:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _payload_to_graph(payload: Dict[str, Any], goal: str) -> Optional[PlanGraph]:
    """把 LLM JSON 规范化为 PlanGraph；结构不合法返回 None。"""
    nodes_payload = payload.get("nodes")
    if not isinstance(nodes_payload, list) or not nodes_payload:
        return None
    nodes: List[PlanNode] = []
    for index, item in enumerate(nodes_payload):
        if not isinstance(item, dict):
            return None
        node_data = dict(item)
        node_data.setdefault("node_id", f"n{index + 1}")
        if not node_data.get("node_id") or not node_data.get("goal"):
            return None
        node_data.setdefault("executor", "tool")
        try:
            nodes.append(PlanNode.from_dict(node_data))
        except (TypeError, ValueError):
            return None
    return PlanGraph(
        plan_id=str(payload.get("plan_id") or f"plan_llm_{abs(hash(goal)) % 10 ** 10}"),
        goal=str(payload.get("goal") or goal or "").strip(),
        task_type=str(payload.get("task_type") or "unknown"),
        nodes=nodes,
        metadata={"source": "llm_planner"},
    )


def _tool_list_text(tool_cards: Iterable[Dict[str, Any]]) -> str:
    """工具卡片压成单行清单，控制提示词长度。"""
    lines = []
    for card in tool_cards:
        name = card.get("name") or ""
        if not name:
            continue
        lines.append(f"- {name} [{card.get('category') or 'other'}]: {card.get('description') or ''}")
    return "\n".join(lines) or "-（无可用工具）"


def _validate(
    graph: PlanGraph,
    available_tools: Optional[Iterable[str]],
    tool_whitelist: Optional[Iterable[str]],
) -> List[str]:
    """返回校验问题列表；空列表表示通过。"""
    try:
        validate_plan_graph(
            graph,
            available_tools=available_tools,
            tool_whitelist=tool_whitelist,
        )
    except PlanValidationError as exc:
        return list(exc.issues)
    return []


async def build_plan(
    goal: str,
    tool_cards: List[Dict[str, Any]],
    *,
    llm,
    task_spec: Optional[Dict[str, Any]] = None,
    context_text: str = "",
    available_tools: Optional[Iterable[str]] = None,
    tool_whitelist: Optional[Iterable[str]] = None,
    max_repairs: int = 1,
) -> Optional[PlanGraph]:
    """
    入参:
      - goal: 用户目标。
      - tool_cards: 工具卡片 (name/category/description)。
      - llm: 未绑定工具的 LLM 实例。
      - task_spec: 结构化任务规格 (可选)。
      - context_text: 上下文摘要 (会话摘要/已确认输入)。
      - available_tools / tool_whitelist: 校验用工具集合。
      - max_repairs: 校验失败后的修复次数。
    方法:
      - 一次规划调用 + 最多 max_repairs 次修复调用; 失败返回 None。
    出参:
      - PlanGraph 或 None。
    """
    tools_text = _tool_list_text(tool_cards)
    payload = {
        "goal": goal,
        "task_spec": task_spec or {},
        "context": context_text,
        "tools": tools_text,
    }
    messages = [
        SystemMessage(content=PLANNER_SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]

    for attempt in range(max_repairs + 1):
        try:
            response = await llm.ainvoke(messages)
        except Exception as exc:
            logger.warning(f"[LLMPlanner] 规划调用失败 (attempt={attempt}): {exc}")
            return None

        raw = _content_text(response)
        parsed = extract_json_object(raw)
        if parsed is None:
            issues = ["输出不是合法 JSON"]
            graph = None
        else:
            graph = _payload_to_graph(parsed, goal)
            issues = ["计划结构不合法"] if graph is None else _validate(
                graph, available_tools, tool_whitelist
            )

        if graph is not None and not issues:
            return graph

        logger.info(f"[LLMPlanner] 计划未通过校验 (attempt={attempt}): {issues[:5]}")
        if attempt >= max_repairs:
            break
        messages.append(AIMessage(content=raw))
        messages.append(SystemMessage(content=PLANNER_REPAIR_PROMPT.format(
            issues="\n".join(f"- {item}" for item in issues[:12]),
            goal=goal,
            tools=tools_text,
        )))

    return None


async def revise_plan(
    goal: str,
    old_plan: Dict[str, Any],
    failure: Dict[str, Any],
    tool_cards: List[Dict[str, Any]],
    *,
    llm,
    available_tools: Optional[Iterable[str]] = None,
    tool_whitelist: Optional[Iterable[str]] = None,
    max_repairs: int = 1,
) -> Optional[PlanGraph]:
    """
    入参:
      - goal: 用户目标。
      - old_plan: 当前 plan state (含已完成的 steps)。
      - failure: 失败上下文 (step_id/tool/agent_validation/repair_plan)。
      - 其余同 build_plan。
    方法:
      - 把已完成步骤与失败证据交给 LLM，只规划剩余工作；同样过校验与修复。
    出参:
      - 新 PlanGraph 或 None。
    """
    tools_text = _tool_list_text(tool_cards)
    steps = old_plan.get("steps") if isinstance(old_plan.get("steps"), list) else []
    completed = [
        {"step_id": step.get("step_id"), "goal": step.get("goal"), "tool": step.get("tool_name")}
        for step in steps if step.get("status") == "completed"
    ]
    messages = [
        SystemMessage(content=PLANNER_SYSTEM_PROMPT),
        HumanMessage(content=PLANNER_REVISE_PROMPT.format(
            goal=goal,
            completed=json.dumps(completed, ensure_ascii=False),
            failure=json.dumps(failure or {}, ensure_ascii=False)[:4000],
            tools=tools_text,
        )),
    ]

    for attempt in range(max_repairs + 1):
        try:
            response = await llm.ainvoke(messages)
        except Exception as exc:
            logger.warning(f"[LLMPlanner] 重规划调用失败 (attempt={attempt}): {exc}")
            return None

        raw = _content_text(response)
        parsed = extract_json_object(raw)
        graph = _payload_to_graph(parsed, goal) if parsed else None
        issues = ["输出不是合法 JSON"] if parsed is None else (
            ["计划结构不合法"] if graph is None else _validate(graph, available_tools, tool_whitelist)
        )
        if graph is not None and not issues:
            graph.metadata["source"] = "llm_replanner"
            return graph
        logger.info(f"[LLMPlanner] 重规划未通过校验 (attempt={attempt}): {issues[:5]}")
        if attempt >= max_repairs:
            break
        messages.append(AIMessage(content=raw))
        messages.append(SystemMessage(content=PLANNER_REPAIR_PROMPT.format(
            issues="\n".join(f"- {item}" for item in issues[:12]),
            goal=goal,
            tools=tools_text,
        )))
    return None
