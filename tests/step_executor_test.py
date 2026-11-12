"""单步执行器回归测试（不依赖数据库、网络、GPU）。

运行: python tests\\step_executor_test.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.agent.tools  # noqa: E402,F401  触发工具注册
from langchain_core.messages import AIMessage  # noqa: E402

from backend.agent.executor.step_executor import run_step  # noqa: E402
from backend.agent.planner.schema import PlanNode  # noqa: E402


class FakeRunner:
    """返回固定工具结果。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run(self, tool_name, tool_args, tool_call_id="", announce=False):
        self.calls.append(tool_name)
        return self.result


def _tool_call_message(tool="segment_image"):
    return AIMessage(
        content="",
        tool_calls=[{"name": tool, "args": {"image_path": "/tmp/a.tif"}, "id": "call_1"}],
    )


def _node(tool="segment_image"):
    return PlanNode(
        node_id="n1",
        goal="对影像执行分割",
        tool=tool,
        inputs={"image_path": "/tmp/a.tif"},
        acceptance=["agent_validation.status=passed"],
    )


def test_step_passed() -> None:
    bound = {}

    def llm_factory(tools):
        bound["names"] = [tool.name for tool in tools]
        return object()

    async def fake_stream(llm, messages, conversation_id=""):
        return _tool_call_message()

    runner = FakeRunner({
        "type": "success",
        "data": {"vector_path": "/tmp/out.geojson"},
        "agent_validation": {"status": "passed", "summary": "ok"},
    })
    outcome = asyncio.run(run_step(
        _node(),
        plan_goal="提取建筑",
        upstream_outputs={},
        llm_factory=llm_factory,
        stream_llm=fake_stream,
        tool_runner=runner,
        max_turns=3,
    ))
    assert outcome.status == "passed"
    assert outcome.outputs["vector_path"] == "/tmp/out.geojson"
    assert outcome.tool_calls == 1
    assert "segment_image" in bound["names"]
    assert "detect_change" not in bound["names"]


def test_step_failed_with_turn_budget() -> None:
    def llm_factory(tools):
        return object()

    async def fake_stream(llm, messages, conversation_id=""):
        return _tool_call_message()

    runner = FakeRunner({
        "type": "error",
        "msg": "文件不存在",
        "agent_validation": {
            "status": "failed",
            "reason": "文件不存在",
            "repair_plan": {"next_action": "request_valid_input_file_or_upload"},
        },
    })
    outcome = asyncio.run(run_step(
        _node(),
        plan_goal="提取建筑",
        upstream_outputs={},
        llm_factory=llm_factory,
        stream_llm=fake_stream,
        tool_runner=runner,
        max_turns=2,
    ))
    assert outcome.status == "failed"
    assert outcome.tool_calls == 2
    assert outcome.repair_plan.get("next_action")


def test_step_no_tool_calls_is_failed() -> None:
    def llm_factory(tools):
        return object()

    async def fake_stream(llm, messages, conversation_id=""):
        return AIMessage(content="我认为已完成")

    outcome = asyncio.run(run_step(
        _node(),
        plan_goal="提取建筑",
        upstream_outputs={},
        llm_factory=llm_factory,
        stream_llm=fake_stream,
        tool_runner=FakeRunner({}),
        max_turns=2,
    ))
    assert outcome.status == "failed"
    assert outcome.tool_calls == 0


if __name__ == "__main__":
    test_step_passed()
    test_step_failed_with_turn_budget()
    test_step_no_tool_calls_is_failed()
    print("step_executor: PASS")
