"""
工具执行后的验证阶段 (Agent 层)

主控 Agent 每次工具执行完成后, 在 ToolMessage 回灌 LLM 之前调用本模块。
第一阶段使用 verification_agent 的确定性验收逻辑, 把执行结果与客观证据分离。
"""

from typing import Any, Dict, Optional

from backend.agent.team.verification_worker import run_verification
from backend.model.tools._result import extract_artifact_path


def should_run_validation_stage(tool_result: Dict[str, Any]) -> bool:
    """
    入参:
      - tool_result: 工具返回 dict。
    方法:
      - 仅对错误结果、已有 verification 的结果、或 data 中存在产物路径的结果运行验证。
      - 普通查询/记忆/技能成功结果不强制验证, 避免无证据 warning 污染 ReAct。
    出参:
      - bool, True 表示应进入 verification_agent 阶段。
    """
    if not isinstance(tool_result, dict):
        return False
    if tool_result.get("type") == "error":
        return True
    if isinstance(tool_result.get("verification"), dict):
        return True
    data = tool_result.get("data")
    return bool(isinstance(data, dict) and extract_artifact_path(data))


def run_validation_stage(
    tool_name: str,
    tool_result: Dict[str, Any],
    user_goal: str = "",
) -> Optional[Dict[str, Any]]:
    """
    入参:
      - tool_name: 刚执行完成的工具名。
      - tool_result: 工具返回 dict。
      - user_goal: 当前用户目标, 用于验证证据中保留任务语境。
    方法:
      - 先判断该结果是否需要验证。
      - 需要验证时调用 verification_agent 的确定性 worker。
      - 返回值由调用方写入 tool_result.agent_validation。
    出参:
      - dict: 验证 Agent 结构化结论。
      - None: 当前结果不需要进入验证阶段。
    """
    if not should_run_validation_stage(tool_result):
        return None
    tool_result_snapshot = dict(tool_result)
    payload = {
        "user_goal": user_goal or "",
        "tool_name": tool_name,
        "tool_result": tool_result_snapshot,
    }
    return run_verification(payload)


def build_validation_observation(validation: Dict[str, Any]) -> str:
    """
    入参:
      - validation: verification_agent 输出。
    方法:
      - 把验证状态和 repair_plan 压成短文本。
      - 该文本会进入 ToolMessage, 强化主控 Agent 对下一步修正动作的感知。
    出参:
      - str, 可拼入工具结果 summary。
    """
    if not validation:
        return ""
    status = validation.get("status", "")
    summary = validation.get("summary", "")
    repair = validation.get("repair_plan") or {}
    next_action = repair.get("next_action", "")
    recommended_tool = repair.get("recommended_tool", "")
    parameter_changes = repair.get("parameter_changes") or {}
    fallback_tools = repair.get("fallback_tools") or []
    stop_condition = repair.get("stop_condition", "")
    if status == "passed":
        return f"验证Agent: passed。{summary}"
    return (
        f"验证Agent: {status}。{summary} "
        f"修正策略: next_action={next_action}; recommended_tool={recommended_tool}; "
        f"parameter_changes={parameter_changes}; fallback_tools={fallback_tools}; "
        f"stop_condition={stop_condition}"
    )


def build_repair_execution_card(validation: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - validation: verification_agent 输出, 可能包含 repair_plan。
    方法:
      - 将 repair_plan 转成更短、更硬的下一步执行卡片。
      - 该卡片直接写入 ToolMessage, 让主控 Agent 在腐烂上下文中更容易抓住最新证据。
      - 不改变 repair_policy 的事实判断, 只改变回灌给模型的上下文表达。
    出参:
      - dict, 空字典表示无需额外执行卡片。
    """
    if not validation or validation.get("status") == "passed":
        return {}
    repair = validation.get("repair_plan") or {}
    failure_type = repair.get("failure_type") or ""
    recommended_tool = repair.get("recommended_tool") or ""
    fallback_tools = repair.get("fallback_tools") or []
    allowed_tools = [item for item in [recommended_tool, *fallback_tools] if item]
    user_goal = repair.get("user_goal") or ""
    goal_lowered = user_goal.lower()
    card = {
        "priority": "highest_current_context",
        "latest_evidence_overrides": ["conversation_summary", "user_profile", "old_task_state"],
        "failure_type": failure_type,
        "required_next_action": repair.get("next_action") or "",
        "allowed_tools": allowed_tools,
        "argument_requirements": repair.get("parameter_changes") or {},
        "stop_condition": repair.get("stop_condition") or "",
    }
    if failure_type == "missing_input_file":
        card.update({
            "must_not_call_tool": True,
            "required_response": "请求用户提供新的有效本地路径或上传文件; 没有新路径前不要继续调用工具。",
            "prohibited_actions": [
                "不要用 run_shell_command 查目录来替代有效输入",
                "不要重试同一个缺失路径",
                "不要调用依赖该缺失文件的工具",
            ],
        })
    elif failure_type == "empty_vector":
        card.update({
            "must_call_tool": "segment_image",
            "required_arguments": "classes 必须保留用户原目标并增加同义词/英文名/常见遥感地物表述。",
            "prohibited_actions": ["不要查看旧摘要来判断完成", "不要改变用户原目标类别"],
        })
    elif failure_type in {"tool_error", "invalid_arguments"}:
        class_requirement = (
            "classes 必须使用 建筑物, building, 房屋; 不得传 []、\"[]\" 或空字符串。"
            if "建筑" in goal_lowered or "building" in goal_lowered
            else "从用户目标和工具 schema 修正参数; 参数为空时必须补齐, 不得传 [] 或 \"[]\"。"
        )
        card.update({
            "must_call_tool": recommended_tool,
            "required_arguments": class_requirement,
        })
    elif failure_type == "insufficient_evidence":
        card.update({
            "required_evidence": "补充产物路径、数量统计、图层状态或 verification 后再判断完成。",
        })
    else:
        card.update({
            "tool_policy": "下一步只能调用 recommended_tool 或 fallback_tools 中列出的工具; 不能把 run_shell_command 当默认诊断工具。",
        })
    return card


def apply_validation_to_tool_result(
    tool_name: str,
    tool_result: Dict[str, Any],
    user_goal: str = "",
) -> Dict[str, Any]:
    """
    入参:
      - tool_name: 刚执行完成的工具名。
      - tool_result: 工具返回 dict。
      - user_goal: 当前用户目标, 用于修正策略保留任务语境。
    方法:
      - 调用 verification_agent 验证阶段。
      - 若需要验证, 将 agent_validation 写回工具结果。
      - 同步把修正观察文本追加到 summary, 让主控 Agent 下一轮 ReAct 能直接看到修正约束。
    出参:
      - dict, 返回已增强的工具结果; 当前结果不需要验证时原样返回。
    """
    if not isinstance(tool_result, dict):
        return tool_result
    validation = run_validation_stage(
        tool_name=tool_name,
        tool_result=tool_result,
        user_goal=user_goal,
    )
    if not validation:
        return tool_result
    tool_result["agent_validation"] = validation
    execution_card = build_repair_execution_card(validation)
    if execution_card:
        tool_result["repair_execution_card"] = execution_card
    observation_text = build_validation_observation(validation)
    if observation_text:
        current_summary = tool_result.get("summary") or tool_result.get("msg") or ""
        tool_result["summary"] = (
            f"{current_summary}\n\n{observation_text}"
            if current_summary else observation_text
        )
    return tool_result
