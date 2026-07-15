"""
修正成功率观测工具。

本模块记录 failed/warning 后的 repair_plan 是否在下一次真实工具执行后得到修复。
它回答的问题不是“主控是否遵循 repair_plan”, 而是“遵循后是否真的修好了”。
"""

from typing import Any, Dict, List


def build_repair_attempt(
    conversation_id: str,
    iteration: int,
    source_tool: str,
    repair_plan: Dict[str, Any],
    audit: Dict[str, Any],
) -> Dict[str, Any]:
    """
    入参:
      - conversation_id: 当前对话 ID。
      - iteration: 当前 ReAct 轮次。
      - source_tool: 触发失败验证的源工具。
      - repair_plan: 验证 Agent 给出的修正策略。
      - audit: 主控下一步决策是否遵循 repair_plan 的审计结果。
    方法:
      - 构造一次待观察的修正尝试。
      - attempt_id 使用 conversation_id、iteration、source_tool 组合, 便于日志追踪。
    出参:
      - dict, 可在下一次工具执行完成后传给 evaluate_repair_success。
    """
    safe_repair = repair_plan if isinstance(repair_plan, dict) else {}
    return {
        "repair_attempt_id": f"{conversation_id}:{iteration}:{source_tool}",
        "conversation_id": conversation_id,
        "iteration": iteration,
        "source_tool": source_tool,
        "source_failure_type": safe_repair.get("failure_type") or "",
        "repair_plan": safe_repair,
        "decision_audit": audit if isinstance(audit, dict) else {},
    }


def evaluate_repair_success(
    attempt: Dict[str, Any],
    repaired_tool_name: str,
    repaired_tool_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    入参:
      - attempt: build_repair_attempt 生成的修正尝试。
      - repaired_tool_name: repair_plan 后实际执行的工具名。
      - repaired_tool_result: 已经过 Observation 增强的工具结果。
    方法:
      - 优先读取下一次工具结果中的 agent_validation.status。
      - status=passed 计为修正成功。
      - status=failed/warning 计为修正未成功。
      - 没有 agent_validation 时标记 unverified, 不计入成功。
    出参:
      - dict, 单条 repair_success 记录。
    """
    safe_attempt = attempt if isinstance(attempt, dict) else {}
    safe_result = repaired_tool_result if isinstance(repaired_tool_result, dict) else {}
    validation = safe_result.get("agent_validation")
    validation = validation if isinstance(validation, dict) else {}
    status = validation.get("status") or ""
    repair_success = True if status == "passed" else False
    if not status:
        repair_success = False
        status = "unverified"
    return {
        "repair_attempt_id": safe_attempt.get("repair_attempt_id") or "",
        "conversation_id": safe_attempt.get("conversation_id") or "",
        "iteration": safe_attempt.get("iteration"),
        "source_tool": safe_attempt.get("source_tool") or "",
        "source_failure_type": safe_attempt.get("source_failure_type") or "",
        "repaired_tool_name": repaired_tool_name,
        "next_agent_validation_status": status,
        "repair_success": repair_success,
        "repair_plan": safe_attempt.get("repair_plan") or {},
        "decision_audit": safe_attempt.get("decision_audit") or {},
        "next_agent_validation": validation,
    }


def build_repair_success_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    入参:
      - records: repair_success 记录列表。
    方法:
      - 只统计 next_agent_validation_status 非 unverified 的记录。
      - 计算修正成功率和失败率。
    出参:
      - dict, 可写入评估产物的指标。
    """
    completed = [
        item for item in records
        if isinstance(item, dict) and item.get("next_agent_validation_status") != "unverified"
    ]
    total = len(completed)
    success = sum(1 for item in completed if item.get("repair_success") is True)
    failed = total - success
    score = round(success / total * 100, 2) if total else 0.0
    return {
        "repair_attempt_count": len(records),
        "repair_completed_count": total,
        "repair_success_rate": f"{success} / {total}",
        "repair_failed_rate": f"{failed} / {total}",
        "repair_success_score": f"{score} / 100",
    }
