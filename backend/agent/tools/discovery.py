# -*- coding: utf-8 -*-
"""工具发现与按需选择。

不引入向量库：用工具名、分类和短描述做轻量关键词检索。
- 首轮只选少量工具，降低 bind_tools 的 schema 体积；
- 模型需要其他工具时调用 search_tools / load_tool；
- load_tool 返回完整参数 schema，由 ChatService 重新绑定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from backend.agent.tools.schema import compact_description
from backend.config import settings

# 无论什么任务都保留的基础工具
CORE_TOOL_NAMES = (
    "segment_image",
    "detect_change",
    "understand_image",
    "lookup_skill",
)
META_TOOL_NAMES = ("search_tools", "load_tool")
IMAGE_TOOL_NAMES = ("segment_image", "detect_change", "understand_image")


@dataclass
class ToolCard:
    """检索结果卡片，只含元信息，不含参数 schema。"""

    name: str
    category: str
    description: str

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "category": self.category, "description": self.description}


def _all_tools() -> Dict[str, Any]:
    from backend.agent.tools.registry import get_tools_for_llm

    return {tool.name: tool for tool in get_tools_for_llm()}


def _all_cards() -> List[ToolCard]:
    from backend.agent.tools.registry import get_tool_category_map

    cat_map = get_tool_category_map()
    cards = []
    for name, tool in _all_tools().items():
        cards.append(ToolCard(
            name=name,
            category=cat_map.get(name, "other"),
            description=compact_description(getattr(tool, "description", ""), 180),
        ))
    return cards


def _query_terms(query: str) -> List[str]:
    text = (query or "").lower()
    terms = re.findall(r"[a-z][a-z0-9_]{2,}", text)
    terms += re.findall(r"[\u4e00-\u9fa5]{2,}", text)
    # 2~4 字中文片段，提升“提取建筑”“变化检测”这类短词命中
    for chunk in re.findall(r"[\u4e00-\u9fa5]{2,}", text):
        for size in (2, 3, 4):
            terms.extend(chunk[i:i + size] for i in range(0, max(0, len(chunk) - size + 1)))
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))[:80]


def _score(card: ToolCard, terms: Iterable[str]) -> int:
    name = card.name.lower()
    summary = card.description.lower()
    category = card.category.lower()
    score = 0
    for term in terms:
        if term in name:
            score += 5
        if term in summary:
            score += 2
        if term in category:
            score += 1
    return score


def search_tool_cards(query: str, limit: int = 8) -> List[ToolCard]:
    """按关键词检索工具卡片（name + 短描述）。"""
    terms = _query_terms(query)
    cards = _all_cards()
    ranked = sorted(
        ((_score(card, terms), card) for card in cards),
        key=lambda item: (-item[0], item[1].name),
    )
    matched = [card for score, card in ranked if score > 0]
    return matched[: max(1, min(int(limit or 8), 20))]


def get_tool_card(name: str) -> Optional[ToolCard]:
    """按名称取单张卡片。"""
    for card in _all_cards():
        if card.name == name:
            return card
    return None


def select_initial_tool_names(
    prompt: str,
    images: Optional[List[Dict[str, Any]]] = None,
    limit: Optional[int] = None,
) -> List[str]:
    """首轮选择工具名；动态注入关闭时返回完整白名单。"""
    tools = _all_tools()
    if not settings.agent_dynamic_tools_enabled:
        return list(tools)

    selected: List[str] = []

    def add(name: str) -> None:
        if name in tools and name not in selected:
            selected.append(name)

    for name in META_TOOL_NAMES + CORE_TOOL_NAMES:
        add(name)

    # 影像附件决定核心解译工具：两张默认变化检测，一张默认分割/理解
    image_count = len(images or [])
    if image_count >= 2:
        add("detect_change")
    elif image_count == 1:
        add("segment_image")
        add("understand_image")

    for card in search_tool_cards(prompt, limit=20):
        add(card.name)

    max_tools = max(4, int(limit or settings.agent_dynamic_tools_limit))
    return selected[:max_tools]


def tool_catalog_names(names: Optional[Iterable[str]] = None) -> Optional[set]:
    """返回提示词工具目录的过滤集合；None 表示不过滤。"""
    if names is None:
        return None
    return {str(name) for name in names if name}
