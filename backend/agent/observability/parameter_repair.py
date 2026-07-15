"""
参数级修正验证。

本模块判断模型下一步工具调用是否真的修正了参数, 避免只调用了正确工具
但参数仍为空、仍旧或无效。
"""

import json
from typing import Any, Dict, List


EMPTY_VALUES = {"", "[]", "{}", "null", "none", "None"}
BUILDING_TERMS = {"建筑", "建筑物", "房屋", "楼房", "building", "buildings"}
EVIDENCE_TERMS = {
    "证据",
    "产物",
    "路径",
    "报告",
    "verification",
    "verify",
    "检查",
    "存在",
    "find",
    "ls",
    "dir",
    "get-childitem",
    "test-path",
    "os.path",
    "pdf",
    "report",
}


def _args_text(tool_calls: List[Dict[str, Any]]) -> str:
    """
    入参:
      - tool_calls: LLM 输出的工具调用列表。
    方法:
      - 将工具参数序列化为文本, 便于做关键词和空参数检查。
    出参:
      - str, 参数文本。
    """
    try:
        return json.dumps([call.get("args") or {} for call in tool_calls], ensure_ascii=False)
    except TypeError:
        return str([call.get("args") for call in tool_calls])


def _is_empty_value(value: Any) -> bool:
    """
    入参:
      - value: 单个参数值。
    方法:
      - 判断 None、空列表、空字典、空字符串和字符串形式空值。
    出参:
      - bool, True 表示参数为空。
    """
    if value is None:
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    if isinstance(value, str) and value.strip() in EMPTY_VALUES:
        return True
    return False


def _text_contains_any(text: str, terms: List[str]) -> bool:
    """
    入参:
      - text: 待检查文本。
      - terms: 候选关键词列表。
    方法:
      - 执行大小写不敏感包含判断。
      - 忽略空关键词, 避免空字符串导致误通过。
    出参:
      - bool, 任一关键词命中时为 True。
    """
    lowered = (text or "").lower()
    return any(str(term).lower() in lowered for term in terms if term)


def _semantic_terms_match(text: str, expected_terms: List[str]) -> bool:
    """
    入参:
      - text: 工具参数或回复文本。
      - expected_terms: 评估样本期望语义词。
    方法:
      - 先做直接关键词匹配。
      - 对建筑物目标做同义词归一, 允许“建筑”表达“建筑物/building”的同类语义。
    出参:
      - bool, 文本体现期望语义时为 True。
    """
    if not expected_terms:
        return True
    if _text_contains_any(text, expected_terms):
        return True
    expected_lower = {str(term).lower() for term in expected_terms if term}
    text_lower = (text or "").lower()
    if expected_lower & BUILDING_TERMS:
        return any(term in text_lower for term in BUILDING_TERMS)
    return False


def _uses_shapefile_reexport(tool_calls: List[Dict[str, Any]], text: str) -> bool:
    """
    入参:
      - tool_calls: LLM 输出的工具调用列表。
      - text: 已序列化的工具参数文本。
    方法:
      - 判断 Shapefile 缺组件后的下一步是否是重新导出或切换矢量格式。
      - 不强制 .shp/.dbf/.shx 出现在参数里, 因为重导工具本身可能隐式生成组件。
    出参:
      - bool, 动作符合重导或格式切换策略时为 True。
    """
    names = {str(call.get("name") or "").lower() for call in tool_calls}
    lowered = (text or "").lower()
    if "export_mask_to_shapefile" in names:
        return True
    return any(term in lowered for term in ["shapefile", "geojson", "zip", ".shp", ".dbf", ".shx"])


def _uses_evidence_check(text: str, final_response: str) -> bool:
    """
    入参:
      - text: 工具参数文本。
      - final_response: LLM 回复文本。
    方法:
      - 判断 insufficient_evidence 后是否在补证据或检查报告产物。
      - 允许 shell / python 查询文件、路径、PDF、报告等客观证据。
    出参:
      - bool, 能看出证据检查动作时为 True。
    """
    return _text_contains_any(f"{text}\n{final_response}", list(EVIDENCE_TERMS))


def _uses_sandbox_lookup(text: str, final_response: str) -> bool:
    """
    入参:
      - text: 工具参数文本。
      - final_response: LLM 回复文本。
    方法:
      - 判断沙盒文件缺失后是否在定位真实沙盒产物路径。
      - 允许列目录、find、按扩展名搜索或请求用户提供正确沙盒路径。
    出参:
      - bool, 动作体现沙盒路径定位时为 True。
    """
    combined = f"{text}\n{final_response}".lower()
    return "/workspace" in combined and any(
        term in combined for term in ["find", "ls", "dir", "csv", "xlsx", "xls", "路径", "文件"]
    )


def _uses_vector_lookup(text: str, final_response: str) -> bool:
    """
    入参:
      - text: 工具参数文本。
      - final_response: LLM 回复文本。
    方法:
      - 判断矢量产物路径缺失后是否在定位 GeoJSON/JSON 候选产物。
      - 允许搜索工作区、沙盒或文件目录中的矢量交付物。
    出参:
      - bool, 动作体现矢量产物定位时为 True。
    """
    combined = f"{text}\n{final_response}".lower()
    has_vector_term = any(term in combined for term in ["geojson", ".json", ".geojson", "矢量", "图斑"])
    has_lookup_term = any(term in combined for term in ["find", "ls", "dir", "get-childitem", "搜索", "查找", "路径", "文件"])
    return has_vector_term and has_lookup_term


def validate_parameter_repair(
    repair_plan: Dict[str, Any],
    actual_decision: Dict[str, Any],
    expected_terms: List[str] = None,
) -> Dict[str, Any]:
    """
    入参:
      - repair_plan: agent_validation.repair_plan。
      - actual_decision: decision_audit.extract_actual_decision 输出。
      - expected_terms: 期望在参数中出现的语义关键词。
    方法:
      - 检查工具参数是否仍为空。
      - 对 invalid_arguments / tool_error 场景, 要求参数文本体现 expected_terms 中至少一个。
      - 对 missing_input_file 场景, 要求不调用工具而请求用户补输入。
    出参:
      - dict, 包含 passed、reason、details。
    """
    expected_terms = expected_terms or []
    failure_type = repair_plan.get("failure_type") or ""
    tool_calls = actual_decision.get("actual_tool_calls") or []
    final_response = actual_decision.get("final_response") or ""

    if failure_type == "missing_input_file":
        if tool_calls:
            return {"passed": False, "reason": "缺失输入文件时仍调用了工具。", "details": tool_calls}
        ok = any(term in final_response for term in ["有效", "上传", "路径", "文件"])
        return {"passed": ok, "reason": "已请求有效输入。" if ok else "未请求有效输入。", "details": final_response}

    if failure_type == "missing_sandbox_file":
        if not tool_calls:
            ok = any(term in final_response for term in ["workspace", "沙盒", "路径", "文件"])
            return {"passed": ok, "reason": "已请求正确沙盒路径。" if ok else "未定位或请求沙盒路径。", "details": final_response}
        text = _args_text(tool_calls)
        ok = _uses_sandbox_lookup(text, final_response)
        return {"passed": ok, "reason": "沙盒路径定位策略通过。" if ok else "未体现沙盒路径定位。", "details": text}

    if failure_type == "missing_vector_artifact":
        if not tool_calls:
            ok = any(term in final_response for term in ["geojson", "上传", "路径", "文件"])
            return {"passed": ok, "reason": "已请求有效矢量路径。" if ok else "未定位或请求矢量路径。", "details": final_response}
        text = _args_text(tool_calls)
        ok = _uses_vector_lookup(text, final_response)
        return {"passed": ok, "reason": "矢量产物定位策略通过。" if ok else "未体现矢量产物定位。", "details": text}

    if not tool_calls:
        return {"passed": False, "reason": "需要修正参数时没有工具调用。", "details": final_response}

    for call in tool_calls:
        args = call.get("args") or {}
        if any(_is_empty_value(value) for value in args.values()):
            return {"passed": False, "reason": "工具调用仍包含空参数。", "details": args}

    text = _args_text(tool_calls)

    if failure_type == "incomplete_shapefile_zip":
        ok = _uses_shapefile_reexport(tool_calls, text)
        return {"passed": ok, "reason": "Shapefile 重导策略通过。" if ok else "未体现 Shapefile 重导或格式切换。", "details": text}

    if failure_type == "insufficient_evidence":
        ok = _uses_evidence_check(text, final_response)
        return {"passed": ok, "reason": "证据检查策略通过。" if ok else "未体现产物证据检查。", "details": text}

    if expected_terms and not _semantic_terms_match(text, expected_terms):
        return {"passed": False, "reason": "参数未体现期望语义修正。", "details": text}

    return {"passed": True, "reason": "参数修正通过。", "details": text}
