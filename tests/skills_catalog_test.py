"""标准技能目录回归测试。

运行: python tests\\skills_catalog_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.agent.tools  # noqa: E402,F401
from backend.agent.skills.catalog import (  # noqa: E402
    get_standard_skill,
    list_standard_skills,
    match_standard_skill,
)
from backend.agent.skills.spec import validate_skill_spec  # noqa: E402
from backend.agent.tools.registry import get_all_tools  # noqa: E402


def test_standard_skills() -> None:
    tools = set(get_all_tools())
    skills = list_standard_skills()
    assert len(skills) >= 5
    for skill in skills:
        issues = validate_skill_spec(skill, available_tools=tools)
        assert not issues, (skill.skill_id, issues)

    assert get_standard_skill("segment_extract") is not None
    assert match_standard_skill("帮我提取建筑物").skill_id == "segment_extract"
    assert match_standard_skill("对比两期影像变化").skill_id == "change_detect"
    assert match_standard_skill("随便聊聊") is None


if __name__ == "__main__":
    test_standard_skills()
    print("skills_catalog: PASS")
