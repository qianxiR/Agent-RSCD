# -*- coding: utf-8 -*-
"""
PlanGraph 模板 (Agent 层 / Planner)

第一阶段先提供"已知流程"的确定性 DAG 模板，不依赖 LLM Planner：
- change_detection：校验输入 → 变化检测 → 统计图/报告（后两者可选）；
- 后续可扩展 segmentation / geoserver_publish / batch_analysis。

模板中的 inputs 支持 {{节点ID.字段名}} 占位符，由 DAG Executor 在运行时解析。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from backend.agent.planner.schema import PlanGraph, PlanNode


CHANGE_DETECTION = "change_detection"


def _plan_id(task_type: str) -> str:
    """生成可读且唯一的计划 ID。"""
    return f"plan_{task_type}_{uuid.uuid4().hex[:10]}"


def build_change_detection_graph(
    goal: str,
    t1_path: str,
    t2_path: str,
    classes: str = "",
    bbox: Optional[List[float]] = None,
    include_chart: bool = True,
    include_report: bool = True,
) -> PlanGraph:
    """
    入参:
      - goal: 用户目标，写入计划审计主线。
      - t1_path/t2_path: 两期影像本地路径。
      - classes: 地物类别（可中文/英文/同义词）。
      - bbox: 可选 AOI [minx,miny,maxx,maxy]（4326）。
      - include_chart/include_report: 是否附加统计图与报告节点。
    方法:
      - 构造"输入校验 → detect_change → 统计图/报告"的 DAG；
      - 统计图/报告依赖 detect_change 的 vector_path，标记为非必需，
        失败不阻塞主链路，但会记录证据。
    出参:
      - PlanGraph。
    """
    nodes: List[PlanNode] = [
        PlanNode(
            node_id="n1_validate",
            goal="校验两期影像、类别与 AOI 输入",
            executor="internal",
            capability="validate_inputs",
            required=True,
            inputs={
                "t1_path": t1_path,
                "t2_path": t2_path,
                "classes": classes,
                "bbox": bbox,
            },
            acceptance=[
                "两期影像路径存在且可读",
                "两期影像均带 CRS",
                "bbox 合法（如提供）",
            ],
        ),
        PlanNode(
            node_id="n2_detect_change",
            goal="对两期影像执行变化检测并生成变化矢量",
            executor="tool",
            tool="detect_change",
            capability="change_detection",
            depends_on=["n1_validate"],
            required=True,
            inputs={
                "t1_path": t1_path,
                "t2_path": t2_path,
                "classes": classes,
            },
            acceptance=[
                "返回 vector_path 且文件存在",
                "变化图斑数量 >= 0 且统计完整",
                "agent_validation.status=passed",
            ],
            fallback_tools=[],
        ),
    ]

    if include_chart:
        nodes.append(PlanNode(
            node_id="n3_change_chart",
            goal="基于变化矢量生成统计图表",
            executor="tool",
            tool="generate_change_stats_chart",
            capability="report",
            depends_on=["n2_detect_change"],
            required=False,
            inputs={
                "change_geojson_path": "{{n2_detect_change.vector_path}}",
                "chart_type": "both",
            },
            acceptance=["图表文件存在且可渲染"],
        ))

    if include_report:
        nodes.append(PlanNode(
            node_id="n4_monitor_report",
            goal="基于变化矢量生成监测报告",
            executor="tool",
            tool="generate_monitor_report",
            capability="report",
            depends_on=["n2_detect_change"],
            required=False,
            inputs={
                "change_geojson_path": "{{n2_detect_change.vector_path}}",
                "output_format": "pdf",
                "region_name": "",
            },
            acceptance=["报告文件存在且可下载"],
        ))

    return PlanGraph(
        plan_id=_plan_id(CHANGE_DETECTION),
        goal=(goal or "两期影像变化检测").strip(),
        task_type=CHANGE_DETECTION,
        nodes=nodes,
        budget={
            "max_nodes": 8,
            "max_tool_calls": 12,
            "max_seconds": 1800,
        },
        metadata={
            "source": "template",
            "has_bbox": bool(bbox),
            "classes": classes,
        },
    )


# 任务类型 → 模板构造器；后续逐步补充
_TEMPLATE_BUILDERS = {
    CHANGE_DETECTION: build_change_detection_graph,
}


def build_graph_for_task(
    task_type: str,
    goal: str,
    inputs: Optional[Dict[str, Any]] = None,
) -> Optional[PlanGraph]:
    """
    入参:
      - task_type: task_spec 推断出的任务类型。
      - goal: 用户目标。
      - inputs: 已确认输入（t1_path/t2_path/classes/bbox 等）。
    方法:
      - 查模板注册表；未命中返回 None，由调用方回退 ReAct。
    出参:
      - PlanGraph 或 None。
    """
    builder = _TEMPLATE_BUILDERS.get(task_type)
    if builder is None:
        return None
    data = inputs if isinstance(inputs, dict) else {}
    if task_type == CHANGE_DETECTION:
        t1_path = str(data.get("t1_path") or "")
        t2_path = str(data.get("t2_path") or "")
        if not t1_path or not t2_path:
            return None
        return builder(
            goal=goal,
            t1_path=t1_path,
            t2_path=t2_path,
            classes=str(data.get("classes") or ""),
            bbox=data.get("bbox") if isinstance(data.get("bbox"), list) else None,
        )
    return None
