# -*- coding: utf-8 -*-
"""Agent 技能层（Backend V2）。

loader/selector 已迁入本目录；技能 Markdown 文档仍留在 backend/model/skills，
loader 会自动回退扫描旧目录；model/skills 保留兼容转发。
"""

from backend.agent.skills import loader, selector  # noqa: F401

__all__ = ["loader", "selector"]
