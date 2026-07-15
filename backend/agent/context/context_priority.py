"""
Agent 动态上下文优先级裁决。

本模块用于在旧摘要、长期记忆、任务状态和最新工具证据冲突时, 给出
确定性 winner, 防止腐烂上下文覆盖最新证据。
"""

from typing import Any, Dict, List


PRIORITY_ORDER = {
    "current_user_request": 100,
    "latest_agent_validation": 95,
    "latest_tool_result": 90,
    "repair_execution_card": 88,
    "worker_report": 80,
    "task_state": 70,
    "recent_message": 60,
    "conversation_summary": 40,
    "long_term_memory": 30,
    "skill": 20,
}


def source_priority(source_type: str) -> int:
    """
    入参:
      - source_type: 上下文来源类型。
    方法:
      - 从 PRIORITY_ORDER 读取优先级。
      - 未知来源返回 0, 避免意外压过已知证据。
    出参:
      - int, 优先级数值, 越大越可信。
    """
    return PRIORITY_ORDER.get(source_type or "", 0)


def resolve_context_conflict(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    入参:
      - items: 冲突上下文候选列表, 每项包含 source_type、claim、value。
    方法:
      - 根据 source_type 优先级排序。
      - 选择优先级最高的项作为 winner。
      - 返回 losers 和裁决 reason。
    出参:
      - dict, 包含 winner、losers、reason。
    """
    normalized = []
    for item in items or []:
        current = dict(item)
        current["priority"] = source_priority(str(current.get("source_type") or ""))
        normalized.append(current)
    ordered = sorted(normalized, key=lambda item: item["priority"], reverse=True)
    winner = ordered[0] if ordered else {}
    losers = ordered[1:] if len(ordered) > 1 else []
    reason = (
        f"选择 {winner.get('source_type')} 作为最新有效依据。"
        if winner else "没有可裁决的上下文。"
    )
    return {
        "winner": winner,
        "losers": losers,
        "reason": reason,
    }
