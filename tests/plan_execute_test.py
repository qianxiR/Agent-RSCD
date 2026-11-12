"""Plan-and-Execute 外层循环回归测试（不依赖数据库、网络、GPU）。

运行: python tests\\plan_execute_test.py
"""
import asyncio
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage  # noqa: E402

from backend.agent.executor import plan_executor  # noqa: E402
from backend.agent.executor.step_executor import StepOutcome  # noqa: E402
from backend.agent.memory.task_state import append_plan_step, create_plan_state  # noqa: E402
from backend.agent.planner.plan_adapter import graph_to_plan_state  # noqa: E402
from backend.agent.planner.schema import PlanGraph, PlanNode  # noqa: E402
from backend.agent.planner.templates import build_change_detection_graph  # noqa: E402


class FakeLLM:
    async def ainvoke(self, messages):
        return AIMessage(content="任务已完成，产物见计划步骤。")


@contextmanager
def patch_attr(obj, name, value):
    """临时替换模块属性，退出时恢复。"""
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


def _multi_step_plan():
    plan = create_plan_state("conv_demo", "提取建筑并发布")
    append_plan_step(plan, "执行分割", "tool", tool_name="segment_image", step_id="s1")
    append_plan_step(plan, "检测变化", "tool", tool_name="detect_change", step_id="s2")
    return plan


def _run(plan, saved=None, **overrides):
    params = dict(
        conversation_id="conv_demo",
        user_id="u1",
        goal="提取建筑并发布",
        plan=plan,
        llm_factory=lambda tools: FakeLLM(),
        llm_without_tools=FakeLLM(),
        stream_llm=None,
        tool_runner=None,
        save_plan=(lambda updated: saved.append(updated.get("status"))) if saved is not None else None,
        max_versions=2,
        max_tool_calls=10,
        max_step_turns=3,
    )
    params.update(overrides)
    return asyncio.run(plan_executor.run_plan_and_execute(**params))


def test_success_path() -> None:
    graph = build_change_detection_graph(
        "对比变化", "/tmp/t1.tif", "/tmp/t2.tif", classes="building",
        include_chart=False, include_report=False,
    )
    plan = graph_to_plan_state(graph, "conv_success", {"t1_path": "/tmp/t1.tif", "t2_path": "/tmp/t2.tif"})
    saved = []

    async def fake_step(node, **kwargs):
        return StepOutcome(
            status="passed",
            outputs={"vector_path": "/tmp/change.geojson"},
            evidence={"status": "passed"},
            tool_calls=1,
        )

    with patch_attr(plan_executor, "run_step", fake_step):
        result = _run(plan, saved=saved, conversation_id="conv_success")

    assert result.status == "completed"
    assert result.final_content
    assert result.plan["steps"][0]["status"] == "completed"
    assert result.plan["steps"][0]["outputs"]["vector_path"].endswith("change.geojson")
    assert saved


def test_replan_keeps_completed_steps() -> None:
    plan = _multi_step_plan()
    calls = {"count": 0}

    async def fake_step(node, **kwargs):
        calls["count"] += 1
        if node.node_id == "s2":
            return StepOutcome(
                status="failed",
                evidence={"status": "failed", "reason": "empty vector"},
                repair_plan={"next_action": "retry_with_checked_output_path"},
                error="empty vector",
                tool_calls=1,
            )
        return StepOutcome(
            status="passed",
            outputs={"vector_path": f"/tmp/{node.node_id}.geojson"},
            tool_calls=1,
        )

    async def fake_revise(goal, old_plan, failure, cards, **kwargs):
        return PlanGraph(
            plan_id="plan_revision",
            goal=goal,
            task_type="semantic_segmentation",
            nodes=[PlanNode(
                node_id="n1_retry",
                goal="重新执行变化检测",
                tool="detect_change",
                acceptance=["agent_validation.status=passed"],
            )],
        )

    with patch_attr(plan_executor, "run_step", fake_step), \
            patch_attr(plan_executor, "revise_plan", fake_revise):
        result = _run(plan, conversation_id="conv_replan", tool_cards=[])

    assert result.status == "completed"
    assert result.plan["plan_version"] == 2
    ids = {step["step_id"]: step for step in result.plan["steps"]}
    assert "n1_retry" in ids
    assert ids["s1"]["status"] == "completed"
    assert ids["n1_retry"]["status"] == "completed"
    assert calls["count"] == 3


def test_replan_gives_up_after_budget() -> None:
    plan = _multi_step_plan()

    async def fake_step(node, **kwargs):
        return StepOutcome(
            status="failed",
            evidence={"status": "failed", "reason": "tool error"},
            error="tool error",
            tool_calls=1,
        )

    with patch_attr(plan_executor, "run_step", fake_step):
        result = _run(plan, conversation_id="conv_blocked", tool_cards=[], max_versions=1)

    assert result.status == "blocked"
    assert "重规划次数" in result.reason


def test_tool_call_budget() -> None:
    plan = _multi_step_plan()

    async def fake_step(node, **kwargs):
        return StepOutcome(status="passed", outputs={}, tool_calls=5)

    with patch_attr(plan_executor, "run_step", fake_step):
        result = _run(plan, conversation_id="conv_budget", max_tool_calls=3)

    assert result.status == "budget_exceeded"
    assert "预算" in result.reason


if __name__ == "__main__":
    test_success_path()
    test_replan_keeps_completed_steps()
    test_replan_gives_up_after_budget()
    test_tool_call_budget()
    print("plan_execute: PASS")
