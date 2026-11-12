"""LLM 规划器回归测试（不依赖数据库、网络、GPU）。

运行: python tests\\llm_planner_test.py
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, SystemMessage  # noqa: E402

from backend.agent.planner.llm_planner import (  # noqa: E402
    build_plan,
    extract_json_object,
    revise_plan,
)

TOOLS = {"segment_image", "detect_change"}
CARDS = [
    {"name": "segment_image", "category": "samseg", "description": "语义分割"},
    {"name": "detect_change", "category": "samseg", "description": "变化检测"},
]


class FakeLLM:
    """按队列返回预设内容，并记录调用次数。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        content = self.responses.pop(0) if self.responses else "{}"
        return AIMessage(content=content)


def _valid_payload(tool="segment_image"):
    return {
        "goal": "提取建筑",
        "task_type": "semantic_segmentation",
        "nodes": [{
            "node_id": "n1",
            "goal": "对影像执行分割",
            "executor": "tool",
            "tool": tool,
            "depends_on": [],
            "inputs": {"image_path": "/tmp/a.tif"},
            "acceptance": ["agent_validation.status=passed"],
            "required": True,
        }],
    }


def test_extract_json_object() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json_object("说明文字 {\"a\": 3} 结束") == {"a": 3}
    assert extract_json_object("不是 JSON") is None


def test_build_plan_valid() -> None:
    llm = FakeLLM([json.dumps(_valid_payload(), ensure_ascii=False)])
    graph = asyncio.run(build_plan(
        "提取建筑", CARDS, llm=llm,
        available_tools=TOOLS, tool_whitelist=TOOLS,
    ))
    assert graph is not None
    assert graph.nodes[0].tool == "segment_image"
    assert graph.nodes[0].acceptance
    assert llm.calls == 1


def test_build_plan_repair_once() -> None:
    bad = json.dumps(_valid_payload(tool="not_exists"), ensure_ascii=False)
    good = json.dumps(_valid_payload(), ensure_ascii=False)
    llm = FakeLLM([bad, good])
    graph = asyncio.run(build_plan(
        "提取建筑", CARDS, llm=llm,
        available_tools=TOOLS, tool_whitelist=TOOLS,
    ))
    assert graph is not None
    assert llm.calls == 2


def test_build_plan_gives_up() -> None:
    bad = json.dumps(_valid_payload(tool="not_exists"), ensure_ascii=False)
    llm = FakeLLM([bad, bad])
    graph = asyncio.run(build_plan(
        "提取建筑", CARDS, llm=llm,
        available_tools=TOOLS, tool_whitelist=TOOLS,
    ))
    assert graph is None
    assert llm.calls == 2


def test_revise_plan() -> None:
    llm = FakeLLM([json.dumps(_valid_payload("detect_change"), ensure_ascii=False)])
    old_plan = {
        "goal": "提取建筑",
        "steps": [
            {"step_id": "n1", "goal": "已完成步骤", "status": "completed", "tool_name": "segment_image"},
            {"step_id": "n2", "goal": "失败步骤", "status": "failed", "tool_name": "detect_change"},
        ],
    }
    graph = asyncio.run(revise_plan(
        "提取建筑", old_plan,
        {"step_id": "n2", "error": "empty vector"},
        CARDS, llm=llm,
        available_tools=TOOLS, tool_whitelist=TOOLS,
    ))
    assert graph is not None
    assert graph.metadata.get("source") == "llm_replanner"


if __name__ == "__main__":
    test_extract_json_object()
    test_build_plan_valid()
    test_build_plan_repair_once()
    test_build_plan_gives_up()
    test_revise_plan()
    print("llm_planner: PASS")
