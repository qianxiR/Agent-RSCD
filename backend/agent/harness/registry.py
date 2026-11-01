# -*- coding: utf-8 -*-
"""Harness 注册表。"""

from __future__ import annotations

from typing import Dict, List, Optional

from backend.agent.harness.base import AgentHarness


_HARNESSES: Dict[str, AgentHarness] = {}


def register_harness(harness: AgentHarness) -> str:
    """注册/覆盖一个 Harness 实例。"""
    name = getattr(harness, "name", None) or type(harness).__name__
    _HARNESSES[name] = harness
    return name


def get_harness(name: str) -> Optional[AgentHarness]:
    """按名称获取 Harness。"""
    return _HARNESSES.get(name)


def list_harnesses() -> List[str]:
    """列出已注册 Harness 名称。"""
    return sorted(_HARNESSES)
