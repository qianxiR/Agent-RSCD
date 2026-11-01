# -*- coding: utf-8 -*-
"""Agent 技能层（Backend V2）。

- loader/selector：Markdown 技能文档的加载与选择（文档仍在 model/skills）；
- catalog/spec：标准业务技能的结构化规格与确定性匹配。
"""

from backend.agent.skills import catalog, loader, selector, spec  # noqa: F401

__all__ = ["catalog", "loader", "selector", "spec"]
