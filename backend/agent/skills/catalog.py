# -*- coding: utf-8 -*-
"""标准业务技能目录。

当前目录为空：原有 5 个标准技能已按需求全部移除，待用户自行重建。
重建时在 STANDARD_SKILLS 中追加 SkillSpec 即可，结构见 spec.py。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from backend.agent.skills.spec import SkillSpec


STANDARD_SKILLS: List[SkillSpec] = []


_BY_ID: Dict[str, SkillSpec] = {skill.skill_id: skill for skill in STANDARD_SKILLS}


def list_standard_skills() -> List[SkillSpec]:
    """返回全部标准技能。"""
    return list(STANDARD_SKILLS)


def get_standard_skill(skill_id: str) -> Optional[SkillSpec]:
    """按 ID 取标准技能。"""
    return _BY_ID.get(skill_id)


def match_standard_skill(text: str) -> Optional[SkillSpec]:
    """按触发词做确定性匹配；无命中返回 None。"""
    lowered = (text or "").lower()
    for skill in STANDARD_SKILLS:
        if any(trigger.lower() in lowered for trigger in skill.triggers):
            return skill
    return None
