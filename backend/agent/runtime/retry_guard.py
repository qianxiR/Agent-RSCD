"""
Agent 重复失败防护。

本模块把工具名、关键参数和 failure_type 压成 retry signature, 用于阻止
同一工具、同一参数、同一失败原因的机械重复。
"""

import json
from hashlib import sha256
from typing import Any, Dict, List


def _stable_json(value: Any) -> str:
    """
    入参:
      - value: 任意可 JSON 序列化或可字符串化的值。
    方法:
      - 优先使用 JSON 排序序列化, 保证相同参数得到稳定文本。
      - 无法 JSON 序列化时退回 str。
    出参:
      - str, 稳定文本表示。
    """
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def build_retry_signature(
    tool_name: str,
    tool_args: Dict[str, Any],
    failure_type: str,
    error_message: str = "",
) -> str:
    """
    入参:
      - tool_name: 工具名。
      - tool_args: 工具参数。
      - failure_type: repair_plan.failure_type。
      - error_message: 错误文本, 可为空。
    方法:
      - 将工具名、参数、failure_type 和错误文本组成稳定 payload。
      - 使用 sha256 截断作为签名, 避免日志中大参数膨胀。
    出参:
      - str, retry signature。
    """
    payload = {
        "tool_name": tool_name or "",
        "tool_args": tool_args if isinstance(tool_args, dict) else {"raw": str(tool_args)},
        "failure_type": failure_type or "",
        "error_message": error_message or "",
    }
    return sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:16]


def should_block_retry(
    history: List[Dict[str, Any]],
    tool_name: str,
    tool_args: Dict[str, Any],
    failure_type: str,
    error_message: str = "",
) -> Dict[str, Any]:
    """
    入参:
      - history: 历史失败记录列表, 每项应包含 retry_signature。
      - tool_name: 本次准备调用的工具名。
      - tool_args: 本次准备调用的参数。
      - failure_type: 当前失败类型。
      - error_message: 当前错误文本。
    方法:
      - 计算当前 retry signature。
      - 若历史中已存在同签名失败, 返回 block=True。
      - 若参数或 failure_type 有变化, 返回 block=False。
    出参:
      - dict, 包含 block、retry_signature、reason。
    """
    signature = build_retry_signature(tool_name, tool_args, failure_type, error_message)
    for item in history or []:
        if item.get("retry_signature") == signature:
            return {
                "block": True,
                "retry_signature": signature,
                "reason": "同一工具、同一参数、同一失败原因已经失败过, 禁止机械重复。",
            }
    return {
        "block": False,
        "retry_signature": signature,
        "reason": "未发现同签名失败, 允许继续。",
    }
