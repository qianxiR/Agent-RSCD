# -*- coding: utf-8 -*-
"""
Planner 运行时接入（Shadow 模式）

当前阶段不改变 ReAct 执行，只在检测到"变化检测"输入时：
- 构建 PlanGraph 模板；
- 用真实工具注册表/白名单做结构校验；
- 把计划写入 logs/agent-plan-shadow.jsonl，供后续 DAG Executor 切换前后对比。

这样做可以先验证计划质量与覆盖面，再逐步把执行切到 DAG，避免一次性替换主链路。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.agent.planner.plan_adapter import graph_to_plan_state
from backend.agent.planner.templates import CHANGE_DETECTION, build_graph_for_task
from backend.agent.planner.validator import PlanValidationError, validate_plan_graph
from backend.config import settings

logger = logging.getLogger(__name__)

# 常见类别提示：Plan-first 灰度只处理能确定性推断类别的请求，其余回退 ReAct。
_CLASS_HINTS = [
    (("建筑", "房屋", "building", "house", "roof"), "building, house, roof"),
    (("水体", "河流", "湖泊", "water", "river", "lake"), "water, river, lake"),
    (("植被", "林地", "草地", "vegetation", "tree", "grass"), "vegetation, tree, grass"),
    (("道路", "公路", "road", "street", "highway"), "road, street, highway"),
    (("耕地", "农田", "farmland", "cropland"), "farmland, cropland, field"),
    (("裸地", "bareland", "soil"), "bareland, soil"),
]


def infer_classes_from_prompt(prompt: str) -> str:
    """
    入参: prompt 用户输入。
    方法: 按中英文关键词确定性映射到英文类别 + 同义词；不调 LLM。
    出参: 分号分隔的类别字符串；无命中返回空串。
    """
    text = (prompt or "").lower()
    groups: List[str] = []
    for keywords, classes in _CLASS_HINTS:
        if any(keyword.lower() in text for keyword in keywords):
            if classes not in groups:
                groups.append(classes)
    return "; ".join(groups)


def extract_change_inputs(images: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """
    入参:
      - images: 本轮聊天附件（含 path/intent）。
    方法:
      - 优先取 intent=change 的附件；没有标记时，两张及以上附件视为双时相；
      - 按附件顺序把第一张作为 T1、最后一张作为 T2。
    出参:
      - {t1_path, t2_path}；不足两张返回 None。
    """
    valid = [
        item for item in (images or [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]
    if len(valid) < 2:
        return None
    marked = [item for item in valid if str(item.get("intent") or "") == "change"]
    chosen = marked if len(marked) >= 2 else valid
    return {
        "t1_path": str(chosen[0].get("path") or ""),
        "t2_path": str(chosen[-1].get("path") or ""),
    }


def maybe_build_plan_first_state(
    prompt: str,
    images: List[Dict[str, Any]],
    conversation_id: str,
    user_id: str = "",
    existing_plan: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    入参:
      - prompt/images/conversation_id/user_id: 本轮请求上下文。
      - existing_plan: 会话已有计划；存在时不覆盖。
    方法:
      - 仅当 AGENT_PLAN_FIRST=true 且是双时相输入时触发；
      - 用关键词确定性推断类别，无法推断则返回 None（回退 ReAct）；
      - 构建/校验 PlanGraph，再转成现有 plan state（step_id=node_id）。
    出参:
      - plan state dict；未触发/不可用返回 None。
    """
    if not getattr(settings, "agent_plan_first_enabled", False):
        return None
    if existing_plan:
        return None
    inputs = extract_change_inputs(images)
    if not inputs:
        return None
    classes = infer_classes_from_prompt(prompt)
    if not classes:
        return None
    for path in (inputs["t1_path"], inputs["t2_path"]):
        if not Path(path).is_file():
            return None

    try:
        graph = build_graph_for_task(
            CHANGE_DETECTION,
            prompt,
            {**inputs, "classes": classes},
        )
        if graph is None:
            return None
        from backend.agent.tools.registry import (  # 延迟导入，避免启动期重依赖
            get_all_tools,
            get_llm_exposed_tool_names,
        )

        validate_plan_graph(
            graph,
            available_tools=set(get_all_tools().keys()),
            tool_whitelist=get_llm_exposed_tool_names(),
        )
        return graph_to_plan_state(
            graph,
            conversation_id,
            context_inputs={**inputs, "classes": classes},
        )
    except (PlanValidationError, OSError, ValueError) as exc:
        logger.warning(f"[Planner] Plan-first 构建失败，回退 ReAct: {exc}")
        return None


def maybe_log_shadow_plan(
    prompt: str,
    images: List[Dict[str, Any]],
    conversation_id: str,
    user_id: str = "",
) -> Optional[Dict[str, Any]]:
    """
    入参:
      - prompt: 用户本轮输入。
      - images: 规范化后的附件。
      - conversation_id/user_id: 会话与用户，用于日志溯源。
    方法:
      - 识别双时相输入 → 构建变化检测 PlanGraph；
      - 用当前注册工具与 LLM 白名单校验；
      - 成功/失败都写 JSONL（失败带 issues），不抛异常、不影响主链路。
    出参:
      - 计划 dict；未触发/关闭/构建失败时返回 None。
    """
    if not getattr(settings, "agent_plan_shadow_enabled", False):
        return None
    inputs = extract_change_inputs(images)
    if not inputs:
        return None

    try:
        graph = build_graph_for_task(CHANGE_DETECTION, prompt, inputs)
        if graph is None:
            return None

        from backend.agent.tools.registry import (  # 延迟导入，避免启动期重依赖
            get_all_tools,
            get_llm_exposed_tool_names,
        )

        available = set(get_all_tools().keys())
        whitelist = get_llm_exposed_tool_names()
        validate_plan_graph(graph, available_tools=available, tool_whitelist=whitelist)
        record: Dict[str, Any] = {
            "event": "shadow_plan",
            "status": "valid",
            "conversation_id": conversation_id,
            "user_id": user_id,
            "plan": graph.to_dict(),
        }
        result = graph.to_dict()
    except PlanValidationError as exc:
        record = {
            "event": "shadow_plan",
            "status": "invalid",
            "conversation_id": conversation_id,
            "user_id": user_id,
            "issues": exc.issues,
        }
        result = None
    except Exception as exc:  # 计划影子模式绝不能影响聊天
        logger.warning(f"[Planner] shadow plan 构建失败: {exc}")
        return None

    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "agent-plan-shadow.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return result

