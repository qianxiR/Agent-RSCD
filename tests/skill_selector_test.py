"""阶段 8 技能选择与执行协议回归测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.model.tools
from backend.agent.memory.task_state import (
    apply_step_observation,
    can_finalize_plan,
    create_plan_state,
    start_tool_step,
)
from backend.agent.runtime.skill_execution import apply_skill_selection_to_plan
from backend.model.skills import loader
from backend.model.skills.selector import select_skill, validate_skill_contract
from backend.model.tools.skill_tools import lookup_skill
from backend.model.tools.tool_registry import get_llm_exposed_tool_names


def _passed_result() -> dict:
    """
    入参:
      - 无。
    方法:
      - 构造技能步骤的通过验证 Observation。
    出参:
      - agent_validation.status=passed 的工具结果。
    """
    return {"type": "success", "agent_validation": {"status": "passed", "evidence": ["artifact"]}}


def test_all_skill_contracts_pass_quality_gate() -> None:
    """
    入参:
      - 无。
    方法:
      - 重新扫描全部 Markdown 技能并执行契约质量门。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    skills = loader.reload()
    assert len(skills) == 3
    public_skills = loader.list_skills()
    assert all("executable" not in item for item in public_skills)
    assert all(item["contract_valid"] is True for item in public_skills)
    assert all(item["required_inputs"] for item in public_skills)
    exposed = get_llm_exposed_tool_names()
    for skill in skills:
        quality = validate_skill_contract(skill)
        assert quality["valid"], f"{skill['name']}: {quality['issues']}"
        assert set(skill["contract"]["tool_allowlist"]) <= exposed


def test_selector_requires_inputs_and_returns_one_skill() -> None:
    """
    入参:
      - 无。
    方法:
      - 使用相同分割意图比较输入完整与缺 classes 两种选择结果。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    query = "分割遥感影像中的建筑并生成监测报告"
    selected = select_skill(query, {"image_path": "scene.tif", "classes": "建筑"})
    missing = select_skill(query, {"image_path": "scene.tif"})

    assert selected["selected_skill"]["name"] == "wf1-segment-visualize-report"
    assert selected["match_reasons"] == [
        "keyword_score=" + str(selected["selected_skill"]["score"]),
        "required_inputs=satisfied",
        "contract=valid",
    ]
    assert missing["selected_skill"] is None
    wf1_candidate = next(item for item in missing["candidates"] if item["name"].startswith("wf1-"))
    assert wf1_candidate["missing_inputs"] == ["classes"]


def test_skill_steps_enforce_verification_and_repair() -> None:
    """
    入参:
      - 无。
    方法:
      - 把 WF1 写入 plan, 依次模拟分割通过、报告失败、报告修复通过。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    inputs = {"image_path": "scene.tif", "classes": "建筑"}
    selection = select_skill("分割遥感影像建筑并生成报告", inputs)
    plan = create_plan_state("conv-skill", "分割建筑并生成报告")
    added = apply_skill_selection_to_plan(plan, selection, inputs)

    assert [item["tool_name"] for item in added] == ["segment_image", "generate_monitor_report"]
    assert can_finalize_plan(plan)[0] is False

    segment = start_tool_step(plan, "segment_image", inputs)
    apply_step_observation(plan, segment["step_id"], "segment_image", _passed_result())
    report = start_tool_step(plan, "generate_monitor_report", {"change_geojson_path": "result.geojson"})
    failed = {
        "type": "error",
        "agent_validation": {
            "status": "failed",
            "repair_plan": {
                "failure_type": "missing_artifact",
                "next_action": "retry_same_tool_with_checked_output_path",
            },
        },
    }
    apply_step_observation(plan, report["step_id"], "generate_monitor_report", failed)
    assert report["status"] == "blocked"
    assert can_finalize_plan(plan)[0] is False

    repaired_report = start_tool_step(plan, "generate_monitor_report", {"change_geojson_path": "result.geojson"})
    assert repaired_report["step_id"] == report["step_id"]
    apply_step_observation(plan, report["step_id"], "generate_monitor_report", _passed_result())
    assert can_finalize_plan(plan)[0] is True


def test_selector_uses_accepted_lesson_and_current_plan() -> None:
    """
    入参:
      - 无。
    方法:
      - 为 WF1 提供 accepted lesson 与已选计划, 检查两个稳定加分均可解释。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    result = select_skill(
        "分割遥感影像建筑并生成报告",
        {"image_path": "scene.tif", "classes": "建筑"},
        accepted_lessons=[{"status": "accepted", "future_rule": "segment_image 后验证矢量产物"}],
        current_plan={"selected_skill": "wf1-segment-visualize-report@1.0"},
    )
    assert "accepted_lesson_bonus=1" in result["match_reasons"]
    assert "current_plan_tiebreak=1" in result["match_reasons"]


def test_lookup_skill_returns_executable_contract() -> None:
    """
    入参:
      - 无。
    方法:
      - 调用真实 lookup_skill 工具, 检查选择依据和执行契约被结构化返回。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    result = lookup_skill.invoke({
        "query": "分割遥感影像建筑并生成报告",
        "context_inputs": {"image_path": "scene.tif", "classes": "建筑"},
    })
    selection = result["data"]["selection"]
    assert result["type"] == "success"
    assert selection["selected_skill"]["name"] == "wf1-segment-visualize-report"
    assert selection["selected_skill"]["contract"]["fallback_rule"]


def main() -> None:
    """
    入参:
      - 无。
    方法:
      - 顺序运行阶段 8 的契约、选择、计划执行和回退测试。
    出参:
      - 全部通过时打印固定成功标记。
    """
    test_all_skill_contracts_pass_quality_gate()
    test_selector_requires_inputs_and_returns_one_skill()
    test_skill_steps_enforce_verification_and_repair()
    test_selector_uses_accepted_lesson_and_current_plan()
    test_lookup_skill_returns_executable_contract()
    print("skill_selector_test: PASS")


if __name__ == "__main__":
    main()
