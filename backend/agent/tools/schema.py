# -*- coding: utf-8 -*-
"""工具 schema 工具函数。

- compact_tool: 绑定时只保留短 description，避免完整 docstring 占用上下文；
- tool_schema_payload: load_tool 返回的完整 schema；
- tool_card: 检索时只返回 name/description/分类，不返回参数 schema。
"""

from __future__ import annotations

from typing import Any, Dict


def compact_description(description: str, limit: int = 220) -> str:
    """把长 docstring 压成一句用途描述。"""
    text = " ".join((description or "").split())
    for marker in ("入参:", "入参：", "★ 触发场景", "★ 适用", "参数:"):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
            break
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text or "工具"


def compact_tool(tool: Any) -> Any:
    """返回绑定用工具副本：保留参数 schema，缩短 description。"""
    try:
        return tool.model_copy(update={"description": compact_description(getattr(tool, "description", ""))})
    except Exception:
        return tool


def tool_schema_payload(tool: Any) -> Dict[str, Any]:
    """返回完整工具契约（load_tool 使用）。"""
    try:
        parameters = tool.args_schema.model_json_schema()
    except Exception:
        parameters = {"type": "object", "properties": {}}
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", "") or "",
        "parameters": parameters,
    }
