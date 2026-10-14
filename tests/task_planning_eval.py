# -*- coding: utf-8 -*-
"""
第①层评估: 任务规划与工具选择。

运行方式:
    python tests\\task_planning_eval.py

评估目标: 给定多步任务目标, Agent 首步能否选对工具族、参数是否完整。
判定方式: 首步 tool_call 的工具名是否落在期望工具集, 关键参数是否非空且语义正确。

设计取舍: 测"规划决策" (首步工具选择) 而非"完整执行" (多轮真实推理)。
  原因: 完整执行需 SamSeg 推理 + GeoServer, 3 任务耗时 15+ 分钟;
        首步决策能精准测"规划能力"且单轮可完成, 适合定期回归。
  完整执行由 e2e_segment_auto_report_eval.py 覆盖。
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage

from backend.agent.observability.decision_audit import extract_actual_decision
from backend.agent.prompt import build_system_prompt
from backend.agent.prompt.tool_decision_rules import get_forbidden_tools
from backend.config import settings
from backend.model.llm_client import create_responses_llm_with_tools
from backend.model.tools.tool_registry import get_tools_for_llm

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

ARTIFACT_DIR = ROOT / "tests" / "artifacts"
METRICS_PATH = ARTIFACT_DIR / "task_planning_metrics.json"
TRACE_PATH = ROOT / "logs" / "task-planning-trace.jsonl"

SUBJECT_MODEL = settings.chat_model

IMG1 = "agent-files/geoserver/generate/_default/7ddbc1a4/r000_c006_t1.tif"
IMG2 = "agent-files/geoserver/generate/_default/b1c61702/r000_c006_t1.tif"

# 门槛
PLANNING_THRESHOLD = 0.67  # ≥ 2/3 任务规划正确
TOOL_SELECTION_THRESHOLD = 0.80
PARAM_THRESHOLD = 0.90


# ==================== 任务规划样本 ====================

def _planning_cases() -> List[Dict[str, Any]]:
    """
    3 个真实多步任务, 对应 capability-four-layers.md 的 P1/P2/P3。
    每个标注: expected_tools (期望首步工具族), forbidden_tools (不容许的工具),
              required_params (关键参数名), param_semantics (参数应含语义词)。
    """
    return [
        {
            "case_id": "P1",
            "user_goal": f"对这张遥感影像 {IMG1} 做建筑物分割，在前端展示结果",
            "expected_tools": ["segment_image", "understand_image", "lookup_skill"],
            "forbidden_tools": ["detect_change", "generate_monitor_report", "export_mask_to_shapefile"],
            "required_params": ["image_path"],
            "param_semantics": {"image_path": [IMG1], "classes": ["建筑", "building", "房屋"]},
            "task_type": "semantic_segmentation",
            "desc": "单图分割, 首步应选 segment_image",
        },
        {
            "case_id": "P2",
            "user_goal": f"分割这张影像 {IMG1} 并发布到 GeoServer，然后生成监测报告",
            "expected_tools": ["segment_image", "understand_image", "lookup_skill"],
            "forbidden_tools": ["detect_change", "generate_monitor_report"],
            "required_params": ["image_path"],
            "param_semantics": {"image_path": [IMG1], "classes": ["建筑", "building", "房屋", "植被", "道路", "水体", "农田", "裸地"]},
            "task_type": "semantic_segmentation",
            "desc": "分割→发布→报告链, 首步仍是 segment_image (后续步骤依赖分割产物)",
        },
        {
            "case_id": "P3",
            "user_goal": f"对比这两期影像 {IMG1} 和 {IMG2} 的变化，导出变化图斑矢量，生成变化监测报告",
            "expected_tools": ["detect_change", "lookup_skill", "list_image_catalog"],
            "forbidden_tools": ["segment_image"],
            "required_params": ["t1_path", "t2_path"],
            "param_semantics": {"t1_path": [IMG1], "t2_path": [IMG2]},
            "task_type": "change_detection",
            "desc": "双时相变化检测, 首步应选 detect_change (非 segment_image); understand_image 作前置探索可接受",
        },
    ]


def _judge_planning(case: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参: case (任务样本), actual (LLM 首步决策)。
    方法:
      - 三维度判定 — 工具正确性 / 参数完整性 / 无禁用工具。
      - forbidden 集合合并 case 自身配置与决策表 (tool_decision_rules) 对应 task_type 的禁用工具,
        使评估基准随决策表自动同步。
    出参: {tool_correct, param_complete, no_forbidden, passed, reason}。
    约束:
      - 决策表 forbidden 与 case expected_tools 的不交叉性由 test_decision_rules 守护,
        合并不会破坏 3/3 基线。
    """
    expected = set(case.get("expected_tools", []))
    forbidden = set(case.get("forbidden_tools", []))
    # 合并决策表对应 task_type 的禁用主工具 (评估基准与 prompt 决策表保持单一可信源)
    forbidden.update(get_forbidden_tools(case.get("task_type", "")))
    required_params = case.get("required_params", [])
    semantics = case.get("param_semantics", {})

    tool_calls = actual.get("actual_tool_calls") or []
    actual_names = [str(c.get("name", "")) for c in tool_calls]
    first_tool = actual_names[0] if actual_names else ""

    # 维度1: 工具正确性 (首步落在期望工具集)
    tool_correct = first_tool in expected if first_tool else False

    # 维度2: 无禁用工具 (只检查首步工具; 并行调用的合理辅助工具不影响规划判定)
    no_forbidden = first_tool not in forbidden

    # 维度3: 参数完整性 (必需参数非空 + 语义正确)
    param_complete = False
    param_reason = ""
    if tool_calls:
        args = tool_calls[0].get("args") or {}
        # 必需参数非空
        missing = [p for p in required_params if not args.get(p)]
        if missing:
            param_reason = f"缺少必需参数: {missing}"
        else:
            # 语义检查: 参数值应含期望语义词
            sem_ok = True
            for param_name, expected_vals in semantics.items():
                actual_val = str(args.get(param_name, "")).lower()
                if actual_val and not any(v.lower() in actual_val for v in expected_vals):
                    # 对 classes 参数, 只要非空即算完整 (允许模型自选类别)
                    if param_name == "classes":
                        continue
                    sem_ok = False
                    param_reason = f"参数 {param_name} 未含期望值 {expected_vals}"
                    break
            if sem_ok:
                param_complete = True
    else:
        param_reason = "无工具调用"

    passed = tool_correct and param_complete and no_forbidden
    reason_parts = []
    if not tool_correct:
        reason_parts.append(f"工具错误: 首步={first_tool}, 期望={sorted(expected)}")
    if not no_forbidden:
        bad = set(actual_names) & forbidden
        reason_parts.append(f"调用了禁用工具: {bad}")
    if not param_complete:
        reason_parts.append(param_reason or "参数不完整")
    return {
        "tool_correct": tool_correct,
        "param_complete": param_complete,
        "no_forbidden": no_forbidden,
        "passed": passed,
        "reason": "; ".join(reason_parts) if reason_parts else "规划正确",
        "actual_tools": actual_names,
    }


async def _run_case(llm: Any, case: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参: llm (带工具), case (任务样本)。
    方法: 构造系统 prompt + 任务目标, 调 LLM 取首步决策, 判定规划质量。
    出参: 单任务 trace。
    """
    system_prompt = build_system_prompt("", "", "")
    messages = [
        SystemMessage_safe(system_prompt),
        HumanMessage(content=case["user_goal"]),
    ]
    ai_message = await llm.ainvoke(messages)
    actual = extract_actual_decision(ai_message)
    judgment = _judge_planning(case, actual)
    return {
        "case_id": case["case_id"],
        "user_goal": case["user_goal"],
        "task_type": case["task_type"],
        "expected_tools": case["expected_tools"],
        "judgment": judgment,
    }


def SystemMessage_safe(content: str):
    """构造 SystemMessage (惰性 import 避免循环)。"""
    from langchain_core.messages import SystemMessage
    return SystemMessage(content=content)


def _build_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合任务规划准确率 + 工具选择准确率 + 参数完整率。"""
    total = len(records)
    if not total:
        return {"total_cases": 0}
    planning_ok = sum(1 for r in records if r["judgment"]["passed"])
    tool_ok = sum(1 for r in records if r["judgment"]["tool_correct"])
    param_ok = sum(1 for r in records if r["judgment"]["param_complete"])
    return {
        "total_cases": total,
        "model_subject": SUBJECT_MODEL,
        "task_planning_accuracy": f"{planning_ok} / {total} ({planning_ok/total:.0%})",
        "task_planning_threshold": f"{PLANNING_THRESHOLD:.0%}",
        "tool_selection_accuracy": f"{tool_ok} / {total} ({tool_ok/total:.0%})",
        "tool_selection_threshold": f"{TOOL_SELECTION_THRESHOLD:.0%}",
        "param_completeness_rate": f"{param_ok} / {total} ({param_ok/total:.0%})",
        "param_completeness_threshold": f"{PARAM_THRESHOLD:.0%}",
        "pass": planning_ok / total >= PLANNING_THRESHOLD,
    }


async def run_task_planning_eval() -> Dict[str, Any]:
    """运行任务规划评估: 3 任务 × 首步决策判定。"""
    import backend.agent.tools
    import backend.model.tools

    cases = _planning_cases()
    logger.info(f"[TaskPlanning] 加载 {len(cases)} 个任务, 主模型={SUBJECT_MODEL}")
    llm = create_responses_llm_with_tools(get_tools_for_llm(), model=SUBJECT_MODEL)

    records = []
    for case in cases:
        cid = case["case_id"]
        logger.info(f"[TaskPlanning] 任务 {cid} ({case['task_type']})...")
        try:
            record = await _run_case(llm, case)
        except Exception as e:
            logger.warning(f"[TaskPlanning] 任务 {cid} 失败: {e}")
            record = {
                "case_id": cid, "task_type": case["task_type"],
                "judgment": {"tool_correct": False, "param_complete": False, "no_forbidden": False, "passed": False, "reason": f"异常: {e}", "actual_tools": []},
            }
        j = record["judgment"]
        logger.info(f"[TaskPlanning] 任务 {cid}: 工具={'对' if j['tool_correct'] else '错'} 参数={'完整' if j['param_complete'] else '缺'} 通过={'是' if j['passed'] else '否'}")
        records.append(record)

    metrics = _build_metrics(records)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    TRACE_PATH.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    return metrics


if __name__ == "__main__":
    result = asyncio.run(run_task_planning_eval())
    print("task_planning_eval:", "PASS" if result.get("pass") else "CHECK")
    print(json.dumps(result, ensure_ascii=False, indent=2))
