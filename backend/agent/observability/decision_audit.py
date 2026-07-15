"""
Agent 决策审计工具。

本模块把 repair_plan 与真实 LLM 下一步输出进行结构化对比, 用于评估
主控 Agent 是否真的遵循验证 Agent 给出的修正策略。
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from backend.agent.observability.parameter_repair import validate_parameter_repair


def extract_actual_decision(ai_message: Any) -> Dict[str, Any]:
    """
    入参:
      - ai_message: LLM 返回的 AIMessage 或兼容对象。
    方法:
      - 提取 tool_calls 和 final_response。
      - tool_calls 只保留 name / args / id, 避免 trace 写入不可序列化对象。
    出参:
      - dict, 包含 actual_tool_calls、final_response、has_tool_call。
    """
    tool_calls = []
    for call in getattr(ai_message, "tool_calls", None) or []:
        if not isinstance(call, dict):
            continue
        tool_calls.append({
            "name": call.get("name") or "",
            "args": call.get("args") or {},
            "id": call.get("id") or "",
        })
    content = getattr(ai_message, "content", "") or ""
    return {
        "actual_tool_calls": tool_calls,
        "final_response": content if isinstance(content, str) else str(content),
        "has_tool_call": bool(tool_calls),
    }


def _text_contains_any(text: str, terms: List[str]) -> bool:
    """
    入参:
      - text: 待检查文本。
      - terms: 候选关键词列表。
    方法:
      - 对文本和关键词做小写包含判断。
      - 空关键词自动忽略, 避免误判。
    出参:
      - bool, 任一关键词命中时为 True。
    """
    lowered = (text or "").lower()
    return any(str(term).lower() in lowered for term in terms if term)


def _args_text(tool_calls: List[Dict[str, Any]]) -> str:
    """
    入参:
      - tool_calls: 真实 LLM 输出的工具调用列表。
    方法:
      - 将工具参数序列化为中文安全 JSON 文本。
      - 序列化失败时退回 str, 保证审计不因参数形态中断。
    出参:
      - str, 用于规则匹配的参数文本。
    """
    try:
        return json.dumps([call.get("args") or {} for call in tool_calls], ensure_ascii=False)
    except TypeError:
        return str([call.get("args") for call in tool_calls])


def _tool_names(tool_calls: List[Dict[str, Any]]) -> List[str]:
    """
    入参:
      - tool_calls: 真实 LLM 输出的工具调用列表。
    方法:
      - 抽取非空工具名。
      - 保留原始顺序, 用于判断 recommended_tool 和 fallback_tools 是否被采用。
    出参:
      - list[str], 工具名列表。
    """
    return [str(call.get("name") or "") for call in tool_calls if call.get("name")]


def _final_response_requests_input(final_response: str) -> bool:
    """
    入参:
      - final_response: LLM 无工具调用时的最终回复文本。
    方法:
      - 判断回复是否是在请求有效路径或上传文件。
      - 该规则服务 missing_input_file 场景, 因为没有新路径前不能继续调用工具。
    出参:
      - bool, 回复包含最小用户输入请求时为 True。
    """
    return _text_contains_any(final_response, ["有效", "真实", "上传", "路径", "文件"])


def audit_repair_plan_following(
    case_id: str,
    repair_plan: Dict[str, Any],
    actual_decision: Dict[str, Any],
    expected_terms: List[str] = None,
    forbidden_terms: List[str] = None,
) -> Dict[str, Any]:
    """
    入参:
      - case_id: 评估样本编号。
      - repair_plan: agent_validation.repair_plan。
      - actual_decision: extract_actual_decision 的输出。
      - expected_terms: 期望在下一步工具参数或回复中出现的任务关键词。
      - forbidden_terms: 禁止在下一步工具参数中重复出现的旧错误关键词。
    方法:
      - 根据 failure_type、recommended_tool、fallback_tools 和参数文本判断是否遵循 repair_plan。
      - missing_input_file 允许无工具调用, 但必须请求用户提供最小有效输入。
      - 其他失败类型优先要求工具调用体现 recommended_tool 或 fallback_tools。
    出参:
      - dict, 包含 followed、violation_type、reason、expected、actual。
    """
    expected_terms = expected_terms or []
    forbidden_terms = forbidden_terms or []
    tool_calls = actual_decision.get("actual_tool_calls") or []
    final_response = actual_decision.get("final_response") or ""
    names = _tool_names(tool_calls)
    args_text = _args_text(tool_calls)
    failure_type = repair_plan.get("failure_type") or ""
    recommended_tool = repair_plan.get("recommended_tool") or ""
    fallback_tools = repair_plan.get("fallback_tools") or []
    allowed_tools = [item for item in [recommended_tool, *fallback_tools] if item]

    forbidden_hit = _text_contains_any(args_text, forbidden_terms)
    expected_hit = not expected_terms or _text_contains_any(args_text + final_response, expected_terms)
    parameter_check = validate_parameter_repair(repair_plan, actual_decision, expected_terms)
    semantic_expected_hit = expected_hit or bool(parameter_check.get("passed"))

    if failure_type == "missing_input_file":
        if forbidden_hit:
            followed = False
            violation_type = "repeated_forbidden_input"
            reason = "模型继续使用已确认不存在的输入路径。"
        elif tool_calls:
            followed = any(name in allowed_tools for name in names) and expected_hit
            violation_type = "" if followed else "unexpected_tool_after_missing_input"
            reason = "缺少新输入时仍调用工具, 只有在工具动作符合修正约束时才算通过。"
        else:
            followed = _final_response_requests_input(final_response)
            violation_type = "" if followed else "final_response_without_minimum_input_request"
            reason = "没有新文件路径时, 正确行为是请求用户提供有效路径或上传文件。"
    elif not tool_calls:
        followed = False
        violation_type = "final_response_after_failed_validation"
        reason = "failed/warning 后未调用修正工具, 直接给出最终回复。"
    else:
        tool_allowed = any(name in allowed_tools for name in names) if allowed_tools else True
        followed = tool_allowed and semantic_expected_hit and not forbidden_hit and parameter_check.get("passed")
        if not tool_allowed:
            violation_type = "ignored_recommended_tool"
            reason = "下一步工具调用没有体现 recommended_tool 或 fallback_tools。"
        elif not semantic_expected_hit:
            violation_type = "missing_expected_parameter_change"
            reason = "下一步工具调用没有体现期望的参数修正或任务关键词。"
        elif forbidden_hit:
            violation_type = "repeated_forbidden_parameter"
            reason = "下一步工具调用重复了禁止参数。"
        elif not parameter_check.get("passed"):
            violation_type = "parameter_repair_failed"
            reason = parameter_check.get("reason") or "参数级修正未通过。"
        else:
            violation_type = ""
            reason = "下一步动作符合 repair_plan。"

    return {
        "case_id": case_id,
        "followed": followed,
        "violation_type": violation_type,
        "reason": reason,
        "expected": {
            "failure_type": failure_type,
            "recommended_tool": recommended_tool,
            "fallback_tools": fallback_tools,
            "expected_terms": expected_terms,
            "forbidden_terms": forbidden_terms,
        },
        "actual": actual_decision,
        "parameter_check": parameter_check,
    }


def write_jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    """
    入参:
      - path: JSONL 输出路径。
      - records: 待写入记录列表。
    方法:
      - 创建父目录。
      - 每条记录写成一行 JSON, 供后续按行追加和分析。
    出参:
      - None。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """
    入参:
      - path: JSONL 输出路径。
      - record: 单条审计记录。
    方法:
      - 创建父目录。
      - 将单条记录追加到 JSONL 文件尾部。
    出参:
      - None。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
