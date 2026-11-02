# -*- coding: utf-8 -*-
"""按需工具发现工具。

search_tools: 只返回工具名/短描述，供模型判断是否需要；
load_tool: 返回完整 description + parameters schema，ChatService 据此重新绑定工具。
"""

from __future__ import annotations

from typing import Any, Dict

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from backend.agent.tools._result import build_error, build_success
from backend.agent.tools.discovery import get_tool_card, search_tool_cards
from backend.agent.tools.registry import get_tool_by_name, register_tool
from backend.agent.tools.schema import tool_schema_payload


class SearchToolsArgs(BaseModel):
    query: str = Field(..., description="用户目标或工具能力关键词")
    limit: int = Field(8, ge=1, le=20, description="最多返回多少个候选工具")


class LoadToolArgs(BaseModel):
    name: str = Field(..., description="search_tools 返回的精确工具名")


@register_tool("discovery")
@tool(args_schema=SearchToolsArgs)
def search_tools(query: str, limit: int = 8) -> Dict[str, Any]:
    """
    检索当前系统可用的工具。只返回 name、分类和短描述，不返回参数 schema。

    入参:
        - query (str): 用户目标或工具能力关键词。
        - limit (int): 最多返回多少个候选，默认 8。
    出参: success/error；data.tools 为 [{name, category, description}]。
    """
    try:
        cards = search_tool_cards(query, limit=limit)
        return build_success(
            f"检索到 {len(cards)} 个候选工具",
            data={
                "query": query,
                "tools": [card.to_dict() for card in cards],
                "hint": "确认需要某个工具后，调用 load_tool(name) 加载完整参数 schema。",
            },
        )
    except Exception as exc:
        return build_error(f"工具检索失败: {exc}")


@register_tool("discovery")
@tool(args_schema=LoadToolArgs)
def load_tool(name: str) -> Dict[str, Any]:
    """
    加载指定工具的完整契约（description + parameters schema）。

    入参:
        - name (str): search_tools 返回的工具名。
    出参: success/error；data.tool 为完整工具 schema。
    """
    if not name:
        return build_error("工具名不能为空")
    tool_obj = get_tool_by_name(name)
    if tool_obj is None:
        return build_error(f"未找到工具: {name}")
    card = get_tool_card(name)
    payload = tool_schema_payload(tool_obj)
    payload["category"] = card.category if card else ""
    return build_success(
        f"已加载工具 {name} 的完整 schema，可在下一轮直接调用",
        data={"tool": payload, "loaded": True},
        verification={"status": "passed", "evidence": "tool schema loaded"},
    )
