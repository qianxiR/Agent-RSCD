"""
工具观察结果构建器 (Agent 层 / Runtime)

本模块负责把工具原始返回值转成可回灌给 LLM 的 ToolMessage。
"""

import json
import logging
from typing import Any, Dict, Tuple

from langchain_core.messages import SystemMessage, ToolMessage

from backend.agent.team.validation_stage import apply_validation_to_tool_result

logger = logging.getLogger(__name__)


def _attach_path_verification(tool_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - tool_result: 工具返回 dict。
    方法:
      - 工具若声称产出文件但未写 verification, 自动按路径补客观校验。
      - 路径字段抽取交给 model.tools._result.extract_artifact_path, 兼容新旧字段名。
      - 兜底校验失败只记录 debug, 不阻断工具结果回灌。
    出参:
      - dict, 已尽量补齐 verification 的工具结果。
    """
    if not isinstance(tool_result, dict) or "verification" in tool_result:
        return tool_result

    output_path = None
    data = tool_result.get("data")
    if isinstance(data, dict):
        try:
            from backend.model.tools._result import extract_artifact_path
            output_path = extract_artifact_path(data)
        except ImportError:
            output_path = data.get("output_path") or data.get("geojson_path")

    if not output_path:
        return tool_result

    try:
        from backend.model.tools._verification import verify_by_path
        tool_result["verification"] = verify_by_path(output_path)
    except Exception as err:
        logger.debug(f"[Verify] 兜底校验跳过 {output_path}: {err}")
    return tool_result


def build_observation_tool_message(
    tool_name: str,
    tool_call_id: str,
    tool_result: Any,
    user_goal: str = "",
) -> Tuple[ToolMessage, Any]:
    """
    入参:
      - tool_name: 刚执行完成的工具名。
      - tool_call_id: LLM 工具调用 ID, 用于 ToolMessage 配对。
      - tool_result: 工具原始返回值。
      - user_goal: 当前用户目标, 用于 verification_agent 生成 repair_plan。
    方法:
      - 对 dict 结果补路径 verification。
      - 调用 verification_agent 写入 agent_validation 与修正策略 summary。
      - 将增强后的工具结果序列化为 ToolMessage.content。
      - 工具 type=error 时设置 LangChain ToolMessage.status=error。
    出参:
      - (ToolMessage, enhanced_tool_result)。
    """
    enhanced_result = tool_result
    if isinstance(enhanced_result, dict):
        enhanced_result = _attach_path_verification(enhanced_result)
        enhanced_result = apply_validation_to_tool_result(
            tool_name=tool_name,
            tool_result=enhanced_result,
            user_goal=user_goal,
        )

    result_str = json.dumps(enhanced_result, ensure_ascii=False)
    message_kwargs = {
        "content": result_str,
        "tool_call_id": tool_call_id,
        "name": tool_name,
    }
    if isinstance(enhanced_result, dict) and enhanced_result.get("type") == "error":
        message_kwargs["status"] = "error"
    return ToolMessage(**message_kwargs), enhanced_result


def build_repair_followup_system_message(tool_result: Any) -> SystemMessage | None:
    """
    入参:
      - tool_result: 已经过 observation_builder 增强后的工具结果。
    方法:
      - 当 agent_validation 为 failed/warning 时, 生成一条紧跟 ToolMessage 的短 SystemMessage。
      - 该消息只描述下一轮必须遵守的即时执行约束, 用于压过旧摘要和旧记忆噪声。
      - passed 或无验证结果时不生成消息, 避免污染正常成功路径。
    出参:
      - SystemMessage: 需要追加到消息列表的即时约束。
      - None: 当前结果不需要追加约束。
    """
    if not isinstance(tool_result, dict):
        return None
    validation = tool_result.get("agent_validation")
    if not isinstance(validation, dict) or validation.get("status") not in {"failed", "warning"}:
        return None
    repair = validation.get("repair_plan") or {}
    card = tool_result.get("repair_execution_card") or {}
    failure_type = repair.get("failure_type") or card.get("failure_type") or ""
    allowed_tools = card.get("allowed_tools") or [
        item for item in [repair.get("recommended_tool"), *(repair.get("fallback_tools") or [])] if item
    ]
    if card.get("must_not_call_tool"):
        content = (
            "即时修正约束: 最新 ToolMessage 验证失败, 且 repair_execution_card.must_not_call_tool=true。"
            "下一步禁止调用任何工具; 直接向用户请求新的有效路径、上传文件或最小缺失输入。"
            "不要用 run_shell_command、目录查询或旧摘要替代缺失输入。"
        )
        return SystemMessage(content=content)
    content = (
        "即时修正约束: 最新 ToolMessage 的 agent_validation 是当前最高优先级证据。"
        f"failure_type={failure_type}; next_action={repair.get('next_action') or ''}; "
        f"allowed_tools={allowed_tools}; argument_requirements={card.get('argument_requirements') or repair.get('parameter_changes') or {}}。"
        "下一步必须调用 allowed_tools 中的工具并体现 argument_requirements; "
        "禁止调用 view_conversation_summary/view_user_memory 来确认完成; "
        "如果 run_shell_command 不在 allowed_tools 中, 不得调用 run_shell_command。"
    )
    return SystemMessage(content=content)


def should_disable_tools_for_next_repair_turn(tool_result: Any) -> bool:
    """
    入参:
      - tool_result: 已经过 observation_builder 增强后的工具结果。
    方法:
      - 检查 repair_execution_card.must_not_call_tool。
      - 该标记用于 missing_input_file 等必须请求用户输入的场景。
      - 返回 True 时, 下一轮 LLM 不应绑定工具 schema, 防止模型继续调用目录查询或旧路径工具。
    出参:
      - bool, True 表示下一轮应移除工具 schema。
    """
    if not isinstance(tool_result, dict):
        return False
    card = tool_result.get("repair_execution_card")
    return bool(isinstance(card, dict) and card.get("must_not_call_tool") is True)
