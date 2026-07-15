"""
验证 Agent 修复策略层 (Agent 层)

本模块把 verification_agent 的失败/警告结论转成可执行 repair_plan。
核心目标是让主控 Agent 不只知道“错了”, 还知道“下一步怎么改”。
"""

from typing import Any, Dict, List

from backend.agent.team.result_contract import (
    RECOMMEND_CONTINUE,
    RECOMMEND_REPORT_ERROR,
    RECOMMEND_RETRY,
    RECOMMEND_SWITCH_TOOL,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_WARNING,
)


def _issue_text(issues: List[str]) -> str:
    """
    入参:
      - issues: 验证阶段发现的问题列表。
    方法:
      - 合并为小写文本, 便于规则匹配。
      - 中文和英文信号都保留, 不做翻译。
    出参:
      - str, 规则匹配用文本。
    """
    return " ".join(str(item) for item in issues if item).lower()


def _class_argument_hint(user_goal: str) -> str:
    """
    入参:
      - user_goal: 当前用户目标。
    方法:
      - 从用户目标中抽取常见遥感类别的最小可执行参数提示。
      - 当前优先覆盖建筑物, 因为该类是评估和业务中高频目标。
    出参:
      - str, 可直接写入 repair_plan.parameter_changes 的类别参数提示。
    """
    lowered = (user_goal or "").lower()
    if "建筑" in lowered or "building" in lowered:
        return "classes 必须为非空, 使用 建筑物, building, 房屋; 不得传 []、\"[]\" 或空字符串"
    return "classes 必须从用户目标提取非空类别; 不得传 []、\"[]\" 或空字符串"


def _infer_failure_type(validation: Dict[str, Any], tool_result: Dict[str, Any], tool_name: str = "") -> str:
    """
    入参:
      - validation: verification_agent 初步验收结果。
      - tool_result: 原始工具结果。
      - tool_name: 当前工具名, 用于区分同类错误在不同工具中的修正策略。
    方法:
      - 优先识别可确定根因的工具 error, 如输入文件不存在。
      - 再根据普通工具 error 判断 tool_error。
      - 再根据 issues / evidence 中的客观信号分类。
      - 未命中时返回 unknown_failure 或 insufficient_evidence。
    出参:
      - failure_type 字符串, 供 repair_plan 使用。
    """
    status = validation.get("status")
    issues = validation.get("issues") or []
    text = _issue_text(issues)
    msg = str(tool_result.get("msg") or tool_result.get("summary") or "").lower()
    error_type = str(tool_result.get("error_type") or "").lower()
    error_text = f"{text} {msg}"

    if tool_name in {"publish_geojson_layer", "visualize_vector"} and "geojson" in error_text and (
        "本地文件不存在" in error_text or "not found" in error_text or "不存在" in error_text
    ):
        return "missing_vector_artifact"
    if "source file not found" in error_text and "/workspace" in error_text:
        return "missing_sandbox_file"
    if (
        "本地文件不存在" in error_text
        or "输入文件不存在" in error_text
        or "影像文件不存在" in error_text
        or "input file not found" in error_text
        or "source file not found" in error_text
    ):
        return "missing_input_file"
    if (
        error_type == "invalid_arguments"
        or "参数" in error_text
        or "argument" in error_text
        or "invalid" in error_text
        or "不能为空" in error_text
    ):
        return "invalid_arguments"
    if tool_result.get("type") == "error":
        return "tool_error"

    if status == STATUS_WARNING:
        return "insufficient_evidence"
    if "不存在" in text or "未生成" in text or "not exist" in text or "not found" in text:
        return "missing_artifact"
    if "异常小" in text or "空文件" in text or "0b" in text or "too small" in text:
        return "empty_or_tiny_artifact"
    if "要素数为 0" in text or "矢量为空" in text or "无有效要素" in text:
        return "empty_vector"
    if "zip 缺少核心组件" in text or ".shp" in text or ".dbf" in text or ".shx" in text:
        return "incomplete_shapefile_zip"
    if "无法读取" in text or "损坏" in text or "格式错误" in text:
        return "unreadable_artifact"
    if "权限" in text or "permission" in text or "denied" in text:
        return "permission_or_path_error"
    if "参数" in text or "argument" in text or "invalid" in text or "无效" in text:
        return "invalid_arguments"
    return "unknown_failure"


def build_repair_plan(
    validation: Dict[str, Any],
    tool_name: str,
    tool_result: Dict[str, Any],
    user_goal: str = "",
) -> Dict[str, Any]:
    """
    入参:
      - validation: verification_agent 初步验收结果。
      - tool_name: 刚执行的工具名。
      - tool_result: 原始工具结果。
      - user_goal: 当前用户目标。
    方法:
      - passed 时返回 continue 计划。
      - failed/warning 时根据 failure_type 生成下一步修正动作。
      - 每个计划都包含 recommended_tool / parameter_changes / fallback_tools /
        stop_condition, 让主控 Agent 有明确执行约束。
    出参:
      - repair_plan dict, 可直接写入 ToolMessage。
    """
    status = validation.get("status")
    if status == STATUS_PASSED:
        return {
            "failure_type": "",
            "root_cause_hint": "客观验证已通过",
            "next_action": "continue",
            "recommended_tool": "",
            "parameter_changes": {},
            "fallback_tools": [],
            "stop_condition": "无",
        }

    failure_type = _infer_failure_type(validation, tool_result, tool_name)
    issues = validation.get("issues") or []
    base = {
        "failure_type": failure_type,
        "source_tool": tool_name,
        "user_goal": user_goal or "",
        "observed_issues": issues,
    }

    plans = {
        "tool_error": {
            "root_cause_hint": "工具执行本身返回 error, 需要先根据 msg/error_type 修正参数或切换工具。",
            "next_action": "diagnose_error_then_retry",
            "recommended_tool": tool_name,
            "parameter_changes": {
                "from_error_message": "读取 tool_result.msg / error_type, 修正缺失或非法参数后再调用",
                "classes": _class_argument_hint(user_goal),
            },
            "fallback_tools": ["run_python_code", "run_shell_command"],
            "stop_condition": "同一 error_type 连续出现两次且无新参数可改时, 向用户报告阻塞点。",
            "recommendation": RECOMMEND_RETRY,
        },
        "missing_input_file": {
            "root_cause_hint": "工具读取的用户输入文件在宿主机上不存在, 继续重跑同一路径无法成功。",
            "next_action": "request_valid_input_file_or_upload",
            "recommended_tool": tool_name,
            "parameter_changes": {
                "file_path": "替换为真实存在的本地影像绝对路径, 或使用用户上传后系统返回的文件路径",
                "do_not_retry_same_missing_path": True,
                "ask_user_for_minimum_input": "仅请求用户提供有效影像路径或上传影像文件",
            },
            "fallback_tools": [],
            "stop_condition": "没有新的有效文件路径或上传文件前, 不再调用依赖该输入文件的工具。",
            "recommendation": RECOMMEND_REPORT_ERROR,
        },
        "missing_sandbox_file": {
            "root_cause_hint": "工具读取的沙盒路径不存在, 继续重跑同一路径无法成功, 但可以先列出沙盒目录定位真实产物。",
            "next_action": "locate_existing_sandbox_artifact_then_retry",
            "recommended_tool": "run_shell_command",
            "parameter_changes": {
                "command": "列出 /workspace 中同类型候选文件, 找到真实路径后再调用原下载或渲染工具",
                "do_not_retry_same_missing_path": True,
            },
            "fallback_tools": ["run_python_code", tool_name],
            "stop_condition": "沙盒中找不到同类型候选文件时, 请求用户提供正确沙盒路径或重新生成上游产物。",
            "recommendation": RECOMMEND_RETRY,
        },
        "missing_vector_artifact": {
            "root_cause_hint": "矢量发布或可视化所需 GeoJSON 路径不存在, 继续使用同一路径无法成功, 但可以先定位当前工作区或沙盒中的同类矢量产物。",
            "next_action": "locate_existing_vector_artifact_then_retry",
            "recommended_tool": "run_shell_command",
            "parameter_changes": {
                "command": "搜索 agent-files、/workspace 或 /files 中的 .geojson/.json 候选产物, 找到真实路径后再发布或可视化",
                "do_not_retry_same_missing_path": True,
            },
            "fallback_tools": ["run_python_code", "import_file_to_sandbox", tool_name],
            "stop_condition": "找不到同类矢量候选产物时, 请求用户提供有效 GeoJSON 路径或重新生成上游矢量。",
            "recommendation": RECOMMEND_RETRY,
        },
        "missing_artifact": {
            "root_cause_hint": "工具声称生成产物, 但产物路径不存在或未落地。",
            "next_action": "retry_same_tool_with_checked_output_path",
            "recommended_tool": tool_name,
            "parameter_changes": {
                "output_path": "改用当前 conversation/project 的 agent-files 子目录",
                "verify_path_before_final": True,
            },
            "fallback_tools": ["run_shell_command", "run_python_code"],
            "stop_condition": "重跑后路径仍不存在时, 检查输入路径和写入权限; 仍失败则报告。",
            "recommendation": RECOMMEND_RETRY,
        },
        "empty_or_tiny_artifact": {
            "root_cause_hint": "产物文件存在但过小, 可能是空文件、写入失败或上游数据为空。",
            "next_action": "rerun_and_check_upstream_data",
            "recommended_tool": tool_name,
            "parameter_changes": {
                "check_input_non_empty": True,
                "use_alternative_export_format": True,
            },
            "fallback_tools": ["run_python_code"],
            "stop_condition": "确认上游数据为空时, 回到上游分析/分割步骤而不是继续导出。",
            "recommendation": RECOMMEND_RETRY,
        },
        "empty_vector": {
            "root_cause_hint": "矢量产物可读但无要素, 常见原因是掩膜为空或类别表达过窄, 不应改变原目标类别。",
            "next_action": "return_to_segmentation_with_same_target_and_synonyms",
            "recommended_tool": "segment_image",
            "parameter_changes": {
                "classes": "保持用户原目标类别, 增加同义词、别名、英文名或常见遥感地物表述",
                "preserve_user_target": True,
                "check_mask_stats": True,
            },
            "fallback_tools": ["analyze_connected_components", "understand_image"],
            "stop_condition": "同一目标类别扩展同义词后仍无要素时, 明确说明当前影像中未检测到该目标地物。",
            "recommendation": RECOMMEND_SWITCH_TOOL,
        },
        "incomplete_shapefile_zip": {
            "root_cause_hint": "Shapefile ZIP 缺少 .shp/.dbf/.shx 核心组件, GIS 无法打开。",
            "next_action": "reexport_shapefile_or_fallback_geojson",
            "recommended_tool": "export_mask_to_shapefile",
            "parameter_changes": {
                "ensure_required_exts": [".shp", ".dbf", ".shx"],
            },
            "fallback_tools": ["publish_geojson_layer", "export_change_vector"],
            "stop_condition": "Shapefile 连续失败时, 改用 GeoJSON 作为可用交付物。",
            "recommendation": RECOMMEND_SWITCH_TOOL,
        },
        "unreadable_artifact": {
            "root_cause_hint": "产物存在但无法被对应解析器读取, 可能格式损坏或扩展名与内容不匹配。",
            "next_action": "regenerate_with_format_check",
            "recommended_tool": tool_name,
            "parameter_changes": {
                "explicit_format": "指定用户需要的输出格式",
                "verify_after_write": True,
            },
            "fallback_tools": ["run_python_code", "import_file_to_sandbox"],
            "stop_condition": "重新生成后仍不可读, 切换格式或报告依赖/数据问题。",
            "recommendation": RECOMMEND_RETRY,
        },
        "permission_or_path_error": {
            "root_cause_hint": "路径或权限导致无法读取/写入产物。",
            "next_action": "move_to_allowed_workspace",
            "recommended_tool": tool_name,
            "parameter_changes": {
                "output_dir": "使用当前会话 agent-files 或 sandbox /workspace",
            },
            "fallback_tools": ["import_file_to_sandbox", "download_file_from_sandbox"],
            "stop_condition": "目标目录必须由用户系统外授权时, 请求用户介入。",
            "recommendation": RECOMMEND_RETRY,
        },
        "invalid_arguments": {
            "root_cause_hint": "工具参数不合法或缺失。",
            "next_action": "repair_arguments_before_retry",
            "recommended_tool": tool_name,
            "parameter_changes": {
                "from_schema": "按工具 schema 补齐必填参数",
                "from_context": "优先复用当前上下文中已确认的路径、图层名、类别名",
                "classes": _class_argument_hint(user_goal),
            },
            "fallback_tools": [],
            "stop_condition": "必要参数无法从上下文恢复时, 只询问用户最小缺失信息。",
            "recommendation": RECOMMEND_RETRY,
        },
        "insufficient_evidence": {
            "root_cause_hint": "工具没有提供 verification 或可校验产物路径, 证据不足。",
            "next_action": "collect_evidence_before_final_answer",
            "recommended_tool": "query_agent_task",
            "parameter_changes": {
                "request": "补充产物路径、图层状态、数量统计或重新调用可返回 verification 的工具",
            },
            "fallback_tools": ["list_geoserver_services", "locate_to_layer_bounds", "run_shell_command"],
            "stop_condition": "无法获得客观证据时, 不宣告成功, 明确说明证据不足。",
            "recommendation": RECOMMEND_REPORT_ERROR,
        },
        "unknown_failure": {
            "root_cause_hint": "验证失败但规则层无法确定根因。",
            "next_action": "inspect_evidence_then_choose_strategy",
            "recommended_tool": tool_name,
            "parameter_changes": {
                "inspect": "读取 issues/evidence, 至少改变一个参数、工具或策略后再行动",
            },
            "fallback_tools": ["run_python_code", "run_shell_command"],
            "stop_condition": "无法形成新策略时, 报告当前阻塞点。",
            "recommendation": RECOMMEND_REPORT_ERROR,
        },
    }

    selected = plans.get(failure_type, plans["unknown_failure"])
    recommendation = selected.pop("recommendation")
    plan = {**base, **selected, "recommendation": recommendation}
    return plan
