# -*- coding: utf-8 -*-
"""
决策表规则源守护测试。

校验 tool_decision_rules.DECISION_RULES 中所有工具名都在 LLM 暴露白名单内,
防止规则源笔误导致 prompt 引用不存在的工具。

运行方式:
    python -m pytest tests/test_decision_rules.py -v
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.prompt.tool_decision_rules import (
    DECISION_RULES,
    get_forbidden_tools,
    get_rules_by_task_type,
    validate_rules,
)
from backend.model.tools.tool_registry import get_llm_exposed_tool_names


def test_rules_non_empty():
    """规则源至少覆盖核心任务类型。"""
    assert len(DECISION_RULES) >= 5, "决策规则应至少覆盖 5 类核心任务"


def test_rule_fields_complete():
    """每条规则字段完整。"""
    required_fields = {"task_type", "task_pattern", "trigger_keywords",
                       "required_tools", "forbidden_tools", "key_constraint"}
    for rule in DECISION_RULES:
        missing = required_fields - set(rule)
        assert not missing, f"规则 {rule.get('task_type')} 缺字段: {missing}"


def test_all_tools_in_exposed_whitelist():
    """规则源所有工具名 ⊆ LLM 暴露白名单 (核心守护)。"""
    result = validate_rules(get_llm_exposed_tool_names())
    assert result["valid"], (
        "决策规则引用了未暴露工具, prompt 会误导 LLM:\n  "
        + "\n  ".join(result["issues"])
    )


def test_task_type_unique():
    """task_type 不可重复 (评估对照基准依赖唯一查找)。"""
    task_types = [rule["task_type"] for rule in DECISION_RULES]
    duplicates = {t for t in task_types if task_types.count(t) > 1}
    assert not duplicates, f"task_type 重复: {duplicates}"


def test_get_forbidden_tools_returns_list():
    """get_forbidden_tools 对已知/未知 task_type 都返回列表。"""
    known = get_forbidden_tools("change_detection")
    assert isinstance(known, list)
    assert "segment_image" in known, "变化检测应禁用 segment_image"

    unknown = get_forbidden_tools("nonexistent_task_type")
    assert unknown == [], "未知 task_type 应返回空列表"


def test_get_rules_by_task_type():
    """按 task_type 精确查找。"""
    rules = get_rules_by_task_type("semantic_segmentation")
    assert len(rules) == 1
    assert rules[0]["task_pattern"]


def test_forbidden_not_intersect_with_planning_expected():
    """
    决策表 forbidden 不得与 task_planning_eval 的 case expected_tools 交叉,
    否则合并判定会破坏 3/3 基线。
    这是 P0 落地的安全前提, 写成断言守护。
    """
    # task_planning_eval._planning_cases() 的 expected_tools 与对应 task_type
    expected_by_type = {
        "semantic_segmentation": {"segment_image", "understand_image", "lookup_skill"},
        "change_detection": {"detect_change", "lookup_skill", "list_image_catalog"},
    }
    for task_type, expected_set in expected_by_type.items():
        forbidden = set(get_forbidden_tools(task_type))
        intersect = expected_set & forbidden
        assert not intersect, (
            f"task_type={task_type} 决策表 forbidden {forbidden} "
            f"与评估 expected {expected_set} 交叉: {intersect}"
        )
