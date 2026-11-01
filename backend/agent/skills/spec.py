# -*- coding: utf-8 -*-
"""标准业务技能规格 (Agent 层 / Skills)。

把现有业务流程从散落的 Prompt/Markdown 说明收敛成结构化 SkillSpec：
- 触发条件、必填输入、工具序列、验收标准、失败回退；
- 供 selector / PlanGraph / 前端技能面板共用一个事实源。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class SkillSpec:
    """一个标准业务流程。"""

    skill_id: str
    name: str
    version: str = "1.0"
    description: str = ""
    triggers: List[str] = field(default_factory=list)
    required_inputs: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)
    fallback: str = "回退 ReAct，由主控 Agent 逐步探索"

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        return asdict(self)


def validate_skill_spec(skill: SkillSpec, available_tools: set[str] | None = None) -> List[str]:
    """校验技能字段与工具依赖，返回 issues。"""
    issues: List[str] = []
    if not skill.skill_id or not skill.name:
        issues.append("skill_id/name 不能为空")
    if not skill.steps:
        issues.append("steps 不能为空")
    if not skill.acceptance:
        issues.append("acceptance 不能为空")
    if available_tools is not None:
        missing = [tool for tool in skill.tools if tool not in available_tools]
        if missing:
            issues.append(f"tools 未注册: {missing}")
    return issues
