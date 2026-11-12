# -*- coding: utf-8 -*-
"""LLM 规划器提示词 (Agent 层 / Planner)"""
from __future__ import annotations

PLANNER_SYSTEM_PROMPT = """你是遥感解译任务的规划器。你的唯一输出是一个 JSON 计划，不要输出解释、Markdown 代码块或多余文本。

计划结构：
{
  "goal": "本次任务目标（一句话）",
  "task_type": "change_detection | semantic_segmentation | geoserver_publish | image_understanding | monitoring_report_generation | data_management | unknown",
  "nodes": [
    {
      "node_id": "n1",
      "goal": "该步骤要达成的结果",
      "executor": "tool",
      "tool": "工具名（必须来自给定工具清单）",
      "depends_on": ["前置 node_id"],
      "inputs": {"参数名": "值或 {{上游node_id.字段名}}"},
      "acceptance": ["可客观检查的完成条件"],
      "required": true
    }
  ]
}

硬性约束：
1. 节点数 2 到 8 个；依赖必须引用已存在的 node_id，禁止成环。
2. tool 必须是给定工具清单里的名字，禁止虚构工具。
3. 每个 required=true 的 tool 节点必须有 acceptance，且验收条件可检查（产物路径存在、统计完整、验证通过）。
4. 不得编造本地文件路径。输入缺失时，把该步骤写成"先向用户请求输入"的 internal 节点（executor=internal），并在 goal 中写明缺什么。
5. 上游产物用 {{node_id.字段名}} 引用，字段名使用工具返回 data 中的字段（如 vector_path）。
6. 报告、图表等非关键产物节点设 required=false。
7. 只规划完成用户目标必需的步骤，不要添加与目标无关的步骤。"""

PLANNER_REPAIR_PROMPT = """上一版计划未通过结构校验。请修正后重新输出完整 JSON，不要解释。

校验问题：
{issues}

原始需求：
{goal}

可用工具：
{tools}"""

PLANNER_REVISE_PROMPT = """任务执行失败，需要重新规划剩余步骤。已完成步骤不得重做。

用户目标：
{goal}

已完成步骤（保留，不要重复）：
{completed}

失败步骤与证据：
{failure}

可用工具：
{tools}

请只输出剩余工作的完整 JSON 计划。"""
