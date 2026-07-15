"""
verification_agent worker (Agent 层)

第一阶段的验证 worker 采用确定性规则, 不额外调用 LLM。它读取工具结果、
verification 字段和产物路径, 输出 passed / failed / warning 结构化结论。
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from backend.agent.team.agent_roles import VERIFICATION_AGENT
from backend.agent.team.result_contract import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_WARNING,
    RECOMMEND_CONTINUE,
    RECOMMEND_REPORT_ERROR,
    RECOMMEND_RETRY,
    build_worker_result,
)
from backend.agent.team.repair_policy import build_repair_plan
from backend.model.tools._result import extract_artifact_path
from backend.model.tools._verification import verify_by_path


def _as_dict(value: Any) -> Dict[str, Any]:
    """
    入参:
      - value: 任意输入, 可能是 dict 或 JSON 字符串。
    方法:
      - dict 原样返回; 字符串尝试 JSON 解析; 其他类型返回空 dict。
      - 这样做是为了兼容 LLM 工具入参中把 tool_result 序列化成字符串的情况。
    出参:
      - dict, 解析失败时为空字典。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _collect_artifact_paths(payload: Dict[str, Any], tool_result: Dict[str, Any]) -> List[str]:
    """
    入参:
      - payload: worker 输入载荷。
      - tool_result: 工具返回 dict。
    方法:
      - 按 artifact_path / artifact_paths / tool_result.data 中的标准路径字段收集产物路径。
      - 去重并过滤空值, 避免重复验证同一产物。
    出参:
      - list[str], 待校验产物路径。
    """
    paths: List[str] = []
    single = payload.get("artifact_path")
    if single:
        paths.append(str(single))

    many = payload.get("artifact_paths")
    if isinstance(many, list):
        paths.extend(str(item) for item in many if item)

    data = tool_result.get("data")
    if isinstance(data, dict):
        extracted = extract_artifact_path(data)
        if extracted:
            paths.append(str(extracted))

    deduped = []
    seen = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def _check_existing_verification(tool_result: Dict[str, Any]) -> tuple[List[str], Dict[str, Any]]:
    """
    入参:
      - tool_result: 工具返回 dict。
    方法:
      - 读取工具自带 verification, 识别 ok=false 的客观失败。
      - verification 缺失时不直接判失败, 留给路径校验或 warning 处理。
    出参:
      - (issues, evidence), issues 为空表示未发现失败。
    """
    verification = tool_result.get("verification")
    if not isinstance(verification, dict):
        return [], {}
    if verification.get("ok") is False:
        reason = verification.get("reason") or "工具 verification 标记失败"
        return [str(reason)], {"tool_verification": verification}
    return [], {"tool_verification": verification}


def run_verification(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - payload: verification_agent 输入, 包含 user_goal / tool_name / tool_result /
        artifact_path / artifact_paths 等字段。
    方法:
      - 先检查工具是否直接返回 error。
      - 再读取工具自带 verification。
      - 最后对产物路径调用 verify_by_path 做客观校验。
      - 无 error、无失败、但也无任何证据时返回 warning。
    出参:
      - 符合 worker 输出契约的 dict。
    """
    safe_payload = payload if isinstance(payload, dict) else {}
    tool_result = _as_dict(safe_payload.get("tool_result"))
    tool_name = str(safe_payload.get("tool_name") or tool_result.get("tool") or "unknown")

    issues: List[str] = []
    evidence: Dict[str, Any] = {
        "tool_name": tool_name,
        "user_goal": safe_payload.get("user_goal") or "",
    }

    if tool_result.get("type") == "error":
        message = tool_result.get("msg") or tool_result.get("summary") or "工具返回 error"
        validation = build_worker_result(
            agent_role=VERIFICATION_AGENT,
            status=STATUS_FAILED,
            summary=f"{tool_name} 未通过验收: 工具执行失败。",
            evidence={**evidence, "tool_result": tool_result},
            issues=[str(message)],
            recommendation=RECOMMEND_RETRY,
        )
        validation["repair_plan"] = build_repair_plan(validation, tool_name, tool_result, safe_payload.get("user_goal") or "")
        return validation

    verification_issues, verification_evidence = _check_existing_verification(tool_result)
    issues.extend(verification_issues)
    evidence.update(verification_evidence)

    path_results = []
    for artifact_path in _collect_artifact_paths(safe_payload, tool_result):
        result = verify_by_path(Path(artifact_path))
        path_results.append({"path": artifact_path, "verification": result})
        if result.get("ok") is False:
            issues.append(str(result.get("reason") or f"产物校验失败: {artifact_path}"))

    if path_results:
        evidence["path_results"] = path_results

    if issues:
        validation = build_worker_result(
            agent_role=VERIFICATION_AGENT,
            status=STATUS_FAILED,
            summary=f"{tool_name} 未通过验收, 发现 {len(issues)} 个问题。",
            evidence=evidence,
            issues=issues,
            recommendation=RECOMMEND_RETRY,
        )
        validation["repair_plan"] = build_repair_plan(validation, tool_name, tool_result, safe_payload.get("user_goal") or "")
        return validation

    if evidence.get("tool_verification") or path_results:
        validation = build_worker_result(
            agent_role=VERIFICATION_AGENT,
            status=STATUS_PASSED,
            summary=f"{tool_name} 已通过客观验收。",
            evidence=evidence,
            issues=[],
            recommendation=RECOMMEND_CONTINUE,
        )
        validation["repair_plan"] = build_repair_plan(validation, tool_name, tool_result, safe_payload.get("user_goal") or "")
        return validation

    validation = build_worker_result(
        agent_role=VERIFICATION_AGENT,
        status=STATUS_WARNING,
        summary=f"{tool_name} 缺少可验证证据, 不能仅凭 summary 判定完成。",
        evidence=evidence,
        issues=["未提供 verification 或可校验产物路径"],
        recommendation=RECOMMEND_REPORT_ERROR,
    )
    validation["repair_plan"] = build_repair_plan(validation, tool_name, tool_result, safe_payload.get("user_goal") or "")
    return validation
