# -*- coding: utf-8 -*-
"""
变化检测 Plan-first 执行器（Pilot）

把 PlanGraph 模板接到真实工具契约上：
- internal 节点做输入校验；
- tool 节点调用 invoke_tool(name, args)；
- 工具返回的 data 作为节点 outputs，供下游 {{node.field}} 解析。

当前未接入聊天主链路，供灰度开关和测试使用；
接入时只需在 chat_service 中构造 graph 并调用 run_change_detection_graph。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from backend.agent.executor.dag_executor import NodeResult, run_plan
from backend.agent.planner.schema import PlanGraph, PlanNode
from backend.agent.planner.templates import build_change_detection_graph


ToolInvoker = Callable[[str, Dict[str, Any]], Awaitable[Any] | Any]


def _validate_inputs(inputs: Dict[str, Any]) -> NodeResult:
    """校验两期影像存在、可读；CRS 由 detect_change 内部预检。"""
    t1 = str(inputs.get("t1_path") or "")
    t2 = str(inputs.get("t2_path") or "")
    for label, path in (("T1", t1), ("T2", t2)):
        if not path or not Path(path).is_file():
            return NodeResult(status="failed", error=f"{label} 影像不存在: {path}")
    return NodeResult(status="passed", outputs={"t1_path": t1, "t2_path": t2})


def _tool_result_to_node(result: Any) -> NodeResult:
    """把工具 dict 转成 NodeResult，并暴露 data 字段给下游占位符。"""
    if not isinstance(result, dict):
        return NodeResult(status="passed", outputs={"value": result})
    if result.get("type") == "error":
        return NodeResult(status="failed", error=str(result.get("msg") or "工具执行失败"))
    validation = result.get("agent_validation")
    if isinstance(validation, dict) and validation.get("status") == "failed":
        return NodeResult(status="failed", error=str(validation.get("reason") or "验证失败"))

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    outputs: Dict[str, Any] = dict(data)
    # 常用路径字段兼容：让模板可以直接写 {{n2_detect_change.vector_path}}
    for key in ("vector_path", "geojson_path", "artifact_path", "output_path"):
        if result.get(key) and key not in outputs:
            outputs[key] = result[key]
    return NodeResult(
        status="passed",
        outputs=outputs,
        evidence={
            "summary": result.get("summary") or "",
            "type": result.get("type") or "",
        },
    )


async def run_change_detection_graph(
    graph: PlanGraph,
    invoke_tool: ToolInvoker,
) -> PlanGraph:
    """
    入参:
      - graph: 变化检测 PlanGraph（通常来自 build_change_detection_graph）。
      - invoke_tool: (tool_name, args) -> 工具结果 dict，可同步/异步。
    方法:
      - internal 节点走本地校验；
      - tool 节点调用 invoke_tool 并把 data 暴露给下游。
    出参: 执行后的 PlanGraph。
    """

    async def runner(node: PlanNode, inputs: Dict[str, Any]) -> NodeResult:
        if node.executor == "internal":
            if node.capability == "validate_inputs":
                return _validate_inputs(inputs)
            return NodeResult(status="passed")
        if not node.tool:
            return NodeResult(status="failed", error="节点缺少 tool")
        value = invoke_tool(node.tool, inputs)
        if hasattr(value, "__await__"):
            value = await value  # type: ignore[misc]
        return _tool_result_to_node(value)

    return await run_plan(graph, runner)


def build_default_graph(
    goal: str,
    t1_path: str,
    t2_path: str,
    classes: str = "",
    bbox: Optional[list] = None,
) -> PlanGraph:
    """构造默认变化检测计划；便于聊天层灰度调用。"""
    return build_change_detection_graph(
        goal=goal,
        t1_path=t1_path,
        t2_path=t2_path,
        classes=classes,
        bbox=bbox,
    )
