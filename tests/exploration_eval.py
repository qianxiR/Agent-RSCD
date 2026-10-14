# -*- coding: utf-8 -*-
"""
第③层评估: 失败探索路径合理性。

运行方式:
    python tests\\exploration_eval.py

评估目标: 任务失败后, Agent 能否基于"错误消息 + 上下文 + 任务状态 + 目标"找到正确方向。
判定方式: 首步动作是否落在 acceptable_paths (合理探索路径) 内, 而非盲目重试。

复用第②③层行为评估的消息构造机制 (注入失败 ToolMessage + 腐烂上下文),
但样本设计和判定逻辑不同: 行为评估测"是否遵循 repair_plan", 本评估测"探索方向是否合理"。
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agent.observability.decision_audit import extract_actual_decision, write_jsonl
from backend.agent.prompt import build_system_prompt
from backend.agent.runtime.observation_builder import (
    build_observation_tool_message,
    build_repair_followup_system_message,
    should_disable_tools_for_next_repair_turn,
)
from backend.config import settings
from backend.model.llm_client import create_responses_llm, create_responses_llm_with_tools
from backend.model.tools.tool_registry import get_tools_for_llm

import importlib.util as _ilu

# 复用行为评估的 _build_messages (构造失败后上下文) 和 _stale_context_for_case
_bh_spec = _ilu.spec_from_file_location("_bh", str(ROOT / "tests" / "agent_behavior_harness_eval.py"))
_bh = _ilu.module_from_spec(_bh_spec)
_bh_spec.loader.exec_module(_bh)
_build_messages = _bh._build_messages
_stale_context_for_case = _bh._stale_context_for_case

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

ARTIFACT_DIR = ROOT / "tests" / "artifacts"
METRICS_PATH = ARTIFACT_DIR / "exploration_metrics.json"
TRACE_PATH = ROOT / "logs" / "exploration-trace.jsonl"

SUBJECT_MODEL = settings.chat_model

# 门槛
DIRECTION_THRESHOLD = 0.80  # 探索方向准确率 ≥ 80%
BLIND_RETRY_THRESHOLD = 0.20  # 盲目重试率 ≤ 20%

# 通用诊断工具: 用于证据检查/参数修复/产物验证, 属于合理探索动作。
# 这些工具本身不解决业务问题, 但作为"先诊断再行动"的探索步骤是合理的。
DIAGNOSTIC_TOOLS = {"run_python_code", "run_shell_command"}


# ==================== 探索场景定义 ====================
# 每场景定义 acceptable_paths: LLM 首步动作落在此集合内即判方向正确。
# 每项: {"tools": [允许的工具名], "param_hints": [参数应含的语义词], "or_no_tool_reason": "允许不调工具时的回复关键词"}
# blind_retry_tools: 用相同/等价失败参数重试这些工具 = 盲目重试

def _exploration_cases() -> List[Dict[str, Any]]:
    """
    6 个失败探索场景, 对应 capability-four-layers.md 的 E1-E6。
    每场景复用行为评估的 case 结构 (含 tool_result 触发对应 failure_type),
    额外标注 acceptable_paths 和 blind_retry_tools。
    """
    return [
        {
            "case_id": "E1",
            "user_goal": "对这张影像做建筑物分割",
            "source_tool": "segment_image",
            "source_args": {"image_path": "agent-files/missing.tif", "classes": "building"},
            "tool_result": {"type": "error", "msg": "文件不存在: agent-files/missing.tif", "error_type": "FileNotFoundError"},
            "failure_type": "missing_input_file",
            "acceptable_paths": {
                "tools": ["list_image_catalog", "list_image_metadata", "search_images_by_region", "search_images_by_time"],
                "or_no_tool_reason": "上传|路径|文件|提供|有效",
            },
            "blind_retry_tools": ["segment_image"],
            "blind_retry_args": ["missing.tif"],
        },
        {
            "case_id": "E2",
            "user_goal": "提取建筑物并导出矢量",
            "source_tool": "export_mask_to_shapefile",
            "source_args": {"mask_path": "agent-files/empty.png", "target": "建筑物"},
            "tool_result": {"type": "success", "summary": "导出完成", "verification": {"ok": False, "reason": "要素数为0, 矢量为空", "feature_count": 0}},
            "failure_type": "empty_vector",
            "acceptable_paths": {
                "tools": ["segment_image", "understand_image", "analyze_connected_components", "query_agent_task"],
                "param_hints": ["建筑", "building", "房屋"],
            },
            "blind_retry_tools": ["export_mask_to_shapefile"],
        },
        {
            "case_id": "E3",
            "user_goal": "生成监测报告并确认报告存在",
            "source_tool": "generate_monitor_report",
            "source_args": {"change_geojson_path": "agent-files/change.geojson"},
            "tool_result": {"type": "warning", "summary": "报告已生成", "verification": {"ok": False, "reason": "未提供可校验产物路径, 证据不足"}},
            "failure_type": "insufficient_evidence",
            "acceptable_paths": {
                "tools": ["query_agent_task", "list_geoserver_services", "locate_to_layer_bounds", "generate_monitor_report"],
                "param_hints": ["report", "pdf", "geojson", "产物", "路径"],
            },
            "blind_retry_tools": [],
        },
        {
            "case_id": "E4",
            "user_goal": "提取建筑物分割",
            "source_tool": "segment_image",
            "source_args": {"image_path": "agent-files/eval.tif", "classes": ""},
            "tool_result": {"type": "error", "msg": "参数 classes 不能为空", "error_type": "invalid_arguments"},
            "failure_type": "invalid_arguments",
            "acceptable_paths": {
                "tools": ["segment_image", "understand_image"],
                "param_hints": ["建筑", "building", "房屋"],
            },
            "blind_retry_tools": ["segment_image"],
            "blind_retry_args": ["classes"],  # 重试时 classes 仍空 = 盲目
        },
        {
            "case_id": "E5",
            "user_goal": "导出 ArcGIS 可打开的 Shapefile ZIP",
            "source_tool": "export_mask_to_shapefile",
            "source_args": {"mask_path": "agent-files/mask.tif", "output_format": "shapefile"},
            "tool_result": {"type": "error", "msg": "ZIP 缺少核心组件 .shp/.dbf/.shx", "error_type": "incomplete_shapefile_zip"},
            "failure_type": "incomplete_shapefile_zip",
            "acceptable_paths": {
                "tools": ["export_mask_to_shapefile", "publish_geojson_layer", "export_change_vector", "query_agent_task"],
            },
            "blind_retry_tools": [],
        },
        {
            "case_id": "E6",
            "user_goal": "发布 GeoJSON 变化图斑到 GeoServer 并显示",
            "source_tool": "publish_geojson_layer",
            "source_args": {"geojson_path": "agent-files/change.geojson"},
            "tool_result": {"type": "error", "msg": "GeoServer 连接失败: Connection refused", "error_type": "ConnectionError"},
            "failure_type": "tool_error",
            "acceptable_paths": {
                "tools": ["list_geoserver_services", "visualize_vector", "query_agent_task"],
                "or_no_tool_reason": "GeoServer|服务|启动|可用",
            },
            "blind_retry_tools": ["publish_geojson_layer"],
        },
    ]


def _judge_exploration(case: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - case: 探索场景 (含 acceptable_paths, blind_retry_tools)。
      - actual: extract_actual_decision 输出 (actual_tool_calls + final_response)。
    方法:
      - 方向正确: 首步动作落在 acceptable_paths (工具名匹配 或 无工具但回复含关键词)。
      - 盲目重试: 调用了 blind_retry_tools 且参数仍是失败参数 (如 classes 仍空)。
    出参:
      - {direction_correct, blind_retry, reason, actual_tools, actual_response_excerpt}。
    """
    ap = case.get("acceptable_paths", {})
    allowed_tools = set(ap.get("tools", []))
    no_tool_reason = ap.get("or_no_tool_reason", "")
    param_hints = ap.get("param_hints", [])
    blind_tools = set(case.get("blind_retry_tools", []))
    blind_args = case.get("blind_retry_args", [])

    tool_calls = actual.get("actual_tool_calls") or []
    response = actual.get("final_response") or ""
    actual_names = [str(c.get("name", "")) for c in tool_calls]
    actual_args_text = json.dumps([c.get("args") for c in tool_calls], ensure_ascii=False)

    # 判定盲目重试: 调用了 blind_tools 且仍含失败参数
    blind_retry = False
    blind_reason = ""
    if blind_tools and actual_names:
        for name in actual_names:
            if name in blind_tools:
                # 检查参数是否仍是失败参数 (如 classes 空 / 仍用 missing.tif)
                if blind_args:
                    still_bad = any(ba in actual_args_text for ba in blind_args) or \
                                any(_is_arg_still_bad(c.get("args"), blind_args) for c in tool_calls if c.get("name") == name)
                    if still_bad:
                        blind_retry = True
                        blind_reason = f"盲目重试 {name}, 参数仍是失败参数"
                        break
                else:
                    blind_retry = True
                    blind_reason = f"盲目重试 {name} (同因失败)"
                    break

    # 判定方向正确
    direction_correct = False
    dir_reason = ""
    if not tool_calls:
        # 无工具调用: 检查回复是否含请求输入/说明阻塞的关键词
        if no_tool_reason:
            keywords = [k.strip() for k in no_tool_reason.split("|") if k.strip()]
            if any(k.lower() in response.lower() for k in keywords):
                direction_correct = True
                dir_reason = "无工具调用但回复请求了有效输入/说明阻塞"
            else:
                dir_reason = "无工具调用且未请求有效输入"
        else:
            dir_reason = "应有工具调用但未调用任何工具"
    else:
        # 有工具调用: 检查是否落在 allowed_tools
        matched = [n for n in actual_names if n in allowed_tools]
        # 通用诊断工具 (run_python_code/run_shell_command) 做证据检查也算合理探索
        diagnostic_matched = [n for n in actual_names if n in DIAGNOSTIC_TOOLS]
        if matched:
            # 若有 param_hints, 检查参数是否含语义词
            if param_hints:
                if any(h.lower() in actual_args_text.lower() for h in param_hints):
                    direction_correct = True
                    dir_reason = f"调用了合理工具 {matched} 且参数含期望语义"
                else:
                    dir_reason = f"调用了 {matched} 但参数未含期望语义 {param_hints}"
            else:
                direction_correct = True
                dir_reason = f"调用了合理工具 {matched}"
        elif diagnostic_matched and not blind_retry:
            # 调用诊断工具做证据检查/参数修复 (针对证据不足/产物不全类场景)
            direction_correct = True
            dir_reason = f"调用诊断工具 {diagnostic_matched} 做证据检查/参数修复"
        else:
            dir_reason = f"调用了 {actual_names}, 不在合理路径 {sorted(allowed_tools)} 内"

    passed = direction_correct and not blind_retry
    return {
        "direction_correct": direction_correct,
        "blind_retry": blind_retry,
        "passed": passed,
        "reason": (blind_reason + "; " if blind_retry else "") + dir_reason,
        "actual_tools": actual_names,
        "actual_response_excerpt": response[:150],
    }


def _is_arg_still_bad(args: Any, blind_args: List[str]) -> bool:
    """
    检查重试参数是否仍是失败参数 (如 classes 为空)。
    只有参数确实未修正 (仍是失败值) 才算盲目; 若已补非空值则不算。
    """
    if not isinstance(args, dict):
        return False
    for key, val in args.items():
        if key == "classes":
            # classes 已补非空值 (含目标语义) = 已修正, 不算盲目
            if val and val != "[]" and str(val).strip():
                return False
            # classes 仍空 = 盲目
            return True
    return False


async def _run_case(llm: Any, llm_without_tools: Any, case: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参: llm (带工具), llm_without_tools, case (探索场景)。
    方法: 构造失败上下文, 调 LLM 取首步动作, 判定探索方向。
    出参: 单场景 trace 记录。
    """
    # 复用行为评估的消息构造 (它注入失败 ToolMessage + repair_plan + 腐烂上下文)
    # 但行为评估的 case 结构与本评估略有不同, 需适配
    bh_case = {
        "case_id": case["case_id"],
        "user_goal": case["user_goal"],
        "source_tool": case["source_tool"],
        "source_args": case["source_args"],
        "tool_result": case["tool_result"],
        "expected_terms": case.get("acceptable_paths", {}).get("param_hints", []),
        "forbidden_terms": [],
        "tool_focus": [case["source_tool"]],
    }
    messages, enhanced, repair_plan = _build_messages(bh_case)
    active_llm = llm_without_tools if should_disable_tools_for_next_repair_turn(enhanced) else llm
    ai_message = await active_llm.ainvoke(messages)
    actual = extract_actual_decision(ai_message)
    judgment = _judge_exploration(case, actual)
    return {
        "case_id": case["case_id"],
        "user_goal": case["user_goal"],
        "failure_type": case["failure_type"],
        "acceptable_tools": case["acceptable_paths"].get("tools", []),
        "repair_plan_failure_type": repair_plan.get("failure_type"),
        "judgment": judgment,
    }


def _build_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合探索方向准确率 + 盲目重试率。"""
    total = len(records)
    if not total:
        return {"total_cases": 0, "exploration_direction_accuracy": "未建立"}
    direction_ok = sum(1 for r in records if r["judgment"]["direction_correct"])
    blind = sum(1 for r in records if r["judgment"]["blind_retry"])
    passed = sum(1 for r in records if r["judgment"]["passed"])
    dir_rate = direction_ok / total
    blind_rate = blind / total
    return {
        "total_cases": total,
        "model_subject": SUBJECT_MODEL,
        "exploration_direction_accuracy": f"{direction_ok} / {total} ({dir_rate:.0%})",
        "exploration_direction_threshold": f"{DIRECTION_THRESHOLD:.0%}",
        "blind_retry_rate": f"{blind} / {total} ({blind_rate:.0%})",
        "blind_retry_threshold": f"{BLIND_RETRY_THRESHOLD:.0%}",
        "passed_cases": f"{passed} / {total}",
        "pass": dir_rate >= DIRECTION_THRESHOLD and blind_rate <= BLIND_RETRY_THRESHOLD,
    }


async def run_exploration_eval() -> Dict[str, Any]:
    """运行失败探索评估: 6 场景 × 单轮首步方向判定。"""
    import backend.agent.tools
    import backend.model.tools

    cases = _exploration_cases()
    logger.info(f"[Exploration] 加载 {len(cases)} 个探索场景, 主模型={SUBJECT_MODEL}")
    llm = create_responses_llm_with_tools(get_tools_for_llm(), model=SUBJECT_MODEL)
    llm_without_tools = create_responses_llm(model=SUBJECT_MODEL)

    records = []
    for case in cases:
        cid = case["case_id"]
        logger.info(f"[Exploration] 场景 {cid} ({case['failure_type']})...")
        try:
            record = await _run_case(llm, llm_without_tools, case)
        except Exception as e:
            logger.warning(f"[Exploration] 场景 {cid} 失败: {e}")
            record = {
                "case_id": cid, "failure_type": case["failure_type"],
                "judgment": {"direction_correct": False, "blind_retry": False, "passed": False, "reason": f"执行异常: {e}"},
            }
        j = record["judgment"]
        logger.info(f"[Exploration] 场景 {cid}: 方向={'正确' if j['direction_correct'] else '错误'} 盲目重试={'是' if j['blind_retry'] else '否'}")
        records.append(record)

    metrics = _build_metrics(records)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    write_jsonl(TRACE_PATH, records)
    return metrics


if __name__ == "__main__":
    result = asyncio.run(run_exploration_eval())
    print("exploration_eval:", "PASS" if result.get("pass") else "CHECK")
    print(json.dumps(result, ensure_ascii=False, indent=2))
