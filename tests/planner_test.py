"""PlanGraph / DAG Executor 回归测试（不依赖数据库、LLM、GPU）。

运行: python tests\\planner_test.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.executor.change_detection_runner import run_change_detection_graph  # noqa: E402
from backend.agent.executor.dag_executor import NodeResult, resolve_node_inputs, run_plan  # noqa: E402
from backend.agent.memory.task_state import (  # noqa: E402
    apply_step_observation,
    can_finalize_plan,
    start_tool_step,
)
from backend.agent.planner.plan_adapter import graph_to_plan_state  # noqa: E402
from backend.agent.planner.runtime import (  # noqa: E402
    extract_change_inputs,
    extract_publish_file,
    infer_classes_from_prompt,
)
from backend.agent.planner.schema import PlanGraph, PlanNode  # noqa: E402
from backend.agent.planner.templates import (  # noqa: E402
    CHANGE_DETECTION,
    GEOSERVER_PUBLISH,
    SEMANTIC_SEGMENTATION,
    build_change_detection_graph,
    build_graph_for_task,
    build_publish_graph,
    build_segmentation_graph,
)
from backend.agent.planner.validator import (  # noqa: E402
    PlanValidationError,
    collect_plan_issues,
    validate_plan_graph,
)

TOOLS = {
    "detect_change",
    "generate_change_stats_chart",
    "generate_monitor_report",
}


def test_template_structure() -> None:
    graph = build_change_detection_graph(
        goal="对比两期建筑变化",
        t1_path="/tmp/t1.tif",
        t2_path="/tmp/t2.tif",
        classes="building",
    )
    assert graph.task_type == "change_detection"
    assert [node.node_id for node in graph.nodes] == [
        "n1_validate", "n2_detect_change", "n3_change_chart", "n4_monitor_report",
    ]
    assert graph.node("n2_detect_change").depends_on == ["n1_validate"]
    assert graph.node("n3_change_chart").depends_on == ["n2_detect_change"]
    assert graph.node("n3_change_chart").required is False
    validate_plan_graph(graph, available_tools=TOOLS, tool_whitelist=TOOLS)


def test_roundtrip() -> None:
    graph = build_change_detection_graph("g", "/tmp/t1.tif", "/tmp/t2.tif")
    restored = PlanGraph.from_dict(graph.to_dict())
    assert restored.plan_id == graph.plan_id
    assert [node.node_id for node in restored.nodes] == [node.node_id for node in graph.nodes]
    assert restored.node("n2_detect_change").tool == "detect_change"


def test_cycle_and_unknown_tool() -> None:
    graph = PlanGraph(
        plan_id="p1",
        goal="cycle",
        nodes=[
            PlanNode(node_id="a", goal="a", tool="detect_change", depends_on=["b"], acceptance=["ok"]),
            PlanNode(node_id="b", goal="b", tool="detect_change", depends_on=["a"], acceptance=["ok"]),
        ],
    )
    issues = collect_plan_issues(graph, available_tools=TOOLS, tool_whitelist=TOOLS)
    assert any("环" in issue for issue in issues)

    graph2 = PlanGraph(
        plan_id="p2",
        goal="unknown tool",
        nodes=[PlanNode(
            node_id="a", goal="a", tool="not_exists", depends_on=[], acceptance=["ok"],
        )],
    )
    try:
        validate_plan_graph(graph2, available_tools=TOOLS, tool_whitelist=TOOLS)
    except PlanValidationError as exc:
        assert any("未注册" in issue for issue in exc.issues)
    else:
        raise AssertionError("unknown tool should fail")


def test_resolve_placeholders() -> None:
    graph = build_change_detection_graph("g", "/tmp/t1.tif", "/tmp/t2.tif")
    graph.node("n2_detect_change").outputs = {"vector_path": "/tmp/change.geojson"}
    inputs = resolve_node_inputs(graph.node("n3_change_chart"), graph)
    assert inputs["change_geojson_path"] == "/tmp/change.geojson"


def test_dag_execution_order_and_optional_failure() -> None:
    graph = build_change_detection_graph("g", "/tmp/t1.tif", "/tmp/t2.tif")
    order = []

    async def runner(node, inputs):
        order.append(node.node_id)
        if node.node_id == "n2_detect_change":
            return NodeResult(outputs={"vector_path": "/tmp/change.geojson"})
        if node.node_id == "n3_change_chart":
            assert inputs["change_geojson_path"] == "/tmp/change.geojson"
            return NodeResult(status="failed", error="chart failed")
        return NodeResult()

    result = asyncio.run(run_plan(graph, runner))
    assert order[0] == "n1_validate"
    assert order[1] == "n2_detect_change"
    assert result.status == "completed"
    assert result.node("n2_detect_change").status == "completed"
    assert result.node("n3_change_chart").status == "skipped"
    assert result.node("n4_monitor_report").status == "completed"


def test_required_failure_blocks_downstream() -> None:
    graph = build_change_detection_graph("g", "/tmp/t1.tif", "/tmp/t2.tif")

    async def runner(node, inputs):
        if node.node_id == "n2_detect_change":
            return NodeResult(status="failed", error="empty vector")
        return NodeResult()

    result = asyncio.run(run_plan(graph, runner))
    assert result.status == "blocked"
    assert result.node("n2_detect_change").status == "failed"
    assert result.node("n3_change_chart").status == "blocked"


def test_extract_change_inputs() -> None:
    assert extract_change_inputs([]) is None
    assert extract_change_inputs([{"path": "/tmp/a.tif"}]) is None
    one = [{"path": "/tmp/a.tif"}, {"path": "/tmp/b.tif"}]
    assert extract_change_inputs(one) == {"t1_path": "/tmp/a.tif", "t2_path": "/tmp/b.tif"}
    marked = [
        {"path": "/tmp/old.tif", "intent": "change"},
        {"path": "/tmp/new.tif", "intent": "change"},
        {"path": "/tmp/other.tif"},
    ]
    assert extract_change_inputs(marked) == {
        "t1_path": "/tmp/old.tif",
        "t2_path": "/tmp/new.tif",
    }


def test_change_detection_plan_runner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t1 = Path(tmp) / "t1.tif"
        t2 = Path(tmp) / "t2.tif"
        t1.write_bytes(b"t1")
        t2.write_bytes(b"t2")
        graph = build_change_detection_graph("g", str(t1), str(t2), classes="building")
        calls = []

        def invoke(name, args):
            calls.append(name)
            if name == "detect_change":
                return {
                    "type": "frontend_action",
                    "data": {"vector_path": str(Path(tmp) / "change.geojson")},
                    "summary": "change done",
                }
            return {"type": "frontend_action", "data": {}}

        result = asyncio.run(run_change_detection_graph(graph, invoke))
        assert result.status == "completed"
        assert calls[0] == "detect_change"
        assert set(calls[1:]) == {"generate_change_stats_chart", "generate_monitor_report"}
        assert result.node("n2_detect_change").status == "completed"


def test_graph_to_plan_state() -> None:
    graph = build_change_detection_graph("g", "/tmp/t1.tif", "/tmp/t2.tif", classes="building")
    plan = graph_to_plan_state(graph, "conv_1", {"t1_path": "/tmp/t1.tif", "t2_path": "/tmp/t2.tif"})
    steps = {step["step_id"]: step for step in plan["steps"]}
    assert "n1_validate" not in steps
    assert steps["n2_detect_change"]["tool_name"] == "detect_change"
    assert steps["n2_detect_change"]["depends_on"] == []
    assert steps["n3_change_chart"]["depends_on"] == ["n2_detect_change"]
    assert steps["n3_change_chart"]["required"] is False

    allowed, _ = can_finalize_plan(plan)
    assert allowed is False

    active = start_tool_step(plan, "detect_change", {"t1_path": "/tmp/t1.tif"})
    assert active["step_id"] == "n2_detect_change"
    apply_step_observation(plan, "n2_detect_change", "detect_change", {
        "agent_validation": {"status": "passed", "summary": "ok"},
    })
    allowed, _ = can_finalize_plan(plan)
    assert allowed is True
    next_step = start_tool_step(plan, "generate_change_stats_chart", {})
    assert next_step["step_id"] == "n3_change_chart"


def test_infer_classes_from_prompt() -> None:
    classes = infer_classes_from_prompt("对比建筑和水体的变化")
    assert "building" in classes
    assert "water" in classes
    assert infer_classes_from_prompt("随便看看") == ""


def test_extract_publish_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.tif"
        path.write_bytes(b"x")
        assert extract_publish_file(f"把 {path} 发布到 GeoServer") == str(path)
        assert extract_publish_file(f"把 {tmp}/missing.tif 发布") is None
        assert extract_publish_file("发布这个图层") is None


def test_segmentation_and_publish_templates() -> None:
    seg_tools = {"segment_image", "visualize_vector"}
    seg = build_segmentation_graph("提取建筑", "/tmp/a.tif", classes="building")
    assert seg.task_type == SEMANTIC_SEGMENTATION
    assert seg.node("n2_segment").tool == "segment_image"
    assert seg.node("n3_visualize").required is False
    validate_plan_graph(seg, available_tools=seg_tools, tool_whitelist=seg_tools)

    pub_tools = {"upload_raster_layer", "load_geoserver_layer"}
    pub = build_publish_graph("发布影像", "/tmp/a.tif", layer_name="demo")
    assert pub.task_type == GEOSERVER_PUBLISH
    assert pub.node("n2_upload").tool == "upload_raster_layer"
    assert pub.node("n3_load").inputs["layer_name"] == "{{n2_upload.layer_name}}"
    validate_plan_graph(pub, available_tools=pub_tools, tool_whitelist=pub_tools)

    assert build_graph_for_task(
        SEMANTIC_SEGMENTATION, "提取建筑", {"image_path": "/tmp/a.tif", "classes": "building"}
    ) is not None
    assert build_graph_for_task(
        GEOSERVER_PUBLISH, "发布", {"file_path": "/tmp/a.tif"}
    ) is not None


if __name__ == "__main__":
    test_template_structure()
    test_roundtrip()
    test_cycle_and_unknown_tool()
    test_resolve_placeholders()
    test_dag_execution_order_and_optional_failure()
    test_required_failure_blocks_downstream()
    test_extract_change_inputs()
    test_change_detection_plan_runner()
    test_graph_to_plan_state()
    test_infer_classes_from_prompt()
    test_extract_publish_file()
    test_segmentation_and_publish_templates()
    print("planner: PASS")
