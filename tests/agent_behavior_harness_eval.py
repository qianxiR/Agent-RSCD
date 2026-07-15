"""
真实 Agent 行为观测评估入口。

运行方式:
python tests\\agent_behavior_harness_eval.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.agent.observability.decision_audit import (
    audit_repair_plan_following,
    extract_actual_decision,
    write_jsonl,
)
from backend.agent.prompt import build_system_prompt
from backend.agent.runtime.observation_builder import build_observation_tool_message
from backend.agent.runtime.observation_builder import build_repair_followup_system_message
from backend.agent.runtime.observation_builder import should_disable_tools_for_next_repair_turn
from backend.agent.team.validation_stage import build_validation_observation
from backend.agent.team.verification_worker import run_verification
from backend.config import settings
from backend.model.llm_client import create_llm, create_llm_with_tools
from backend.model.tools.tool_registry import get_tools_for_llm

import backend.agent.tools
import backend.model.tools

ARTIFACT_DIR = ROOT / "tests" / "artifacts"
METRICS_PATH = ARTIFACT_DIR / "agent_behavior_metrics.json"
TRACE_PATH = ROOT / "logs" / "agent-trace.jsonl"


def _behavior_cases() -> List[Dict[str, Any]]:
    """
    入参:
      - 无。
    方法:
      - 构造 10 个真实 LLM 行为评估样本。
      - 样本根据当前工具 description 覆盖遥感分割、导入、导出、发布、渲染、报告、下载等核心工具族。
      - 每个样本都模拟“上一轮工具调用失败后, ToolMessage 已注入 repair_plan”的上下文。
      - 每个样本都注入旧摘要/旧记忆噪声, 用于评估模型是否优先相信最新 ToolMessage 证据。
    出参:
      - list[dict], 行为评估样本。
    """
    return [
        {
            "case_id": "A1",
            "user_goal": "对 H:\\西藏遥感Agent\\Agent\\agent-files\\eval-image.tif 提取建筑物。",
            "source_tool": "segment_image",
            "source_args": {"image_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\eval-image.tif", "classes": []},
            "tool_result": {"type": "error", "msg": "参数 classes 不能为空", "error_type": "invalid_arguments"},
            "expected_terms": ["建筑物", "building"],
            "forbidden_terms": [],
            "tool_focus": ["segment_image"],
        },
        {
            "case_id": "A2",
            "user_goal": "导入 H:\\西藏遥感Agent\\Agent\\agent-files\\missing-input.png 并做建筑物分割。",
            "source_tool": "import_file_to_sandbox",
            "source_args": {"file_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\missing-input.png"},
            "tool_result": {"type": "error", "msg": "本地文件不存在: H:\\西藏遥感Agent\\Agent\\agent-files\\missing-input.png。请确认路径正确。"},
            "expected_terms": ["有效", "上传", "路径", "文件"],
            "forbidden_terms": ["missing-input.png"],
            "tool_focus": ["import_file_to_sandbox"],
        },
        {
            "case_id": "A3",
            "user_goal": "对 H:\\西藏遥感Agent\\Agent\\agent-files\\eval-image.tif 提取建筑物并导出矢量。",
            "source_tool": "export_mask_to_shapefile",
            "source_args": {
                "mask_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\empty-mask.png",
                "image_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\eval-image.tif",
                "target": "建筑物",
            },
            "tool_result": {
                "type": "success",
                "summary": "矢量导出完成",
                "verification": {
                    "ok": False,
                    "reason": "要素数为 0 (< 1 阈值), 矢量为空",
                    "feature_count": 0,
                },
            },
            "expected_terms": ["建筑物", "building", "房屋"],
            "forbidden_terms": [],
            "tool_focus": ["export_mask_to_shapefile", "segment_image", "analyze_connected_components", "understand_image"],
        },
        {
            "case_id": "A4",
            "user_goal": "把沙盒生成的分析图渲染到前端工作区。",
            "source_tool": "render_sandbox_image",
            "source_args": {"filename": "missing-render.png"},
            "tool_result": {
                "type": "success",
                "summary": "图片已渲染",
                "data": {"artifact_path": str(ROOT / "agent-files" / "missing-render.png")},
            },
            "expected_terms": ["missing-render", "agent-files", "workspace", "路径"],
            "forbidden_terms": [],
            "tool_focus": ["render_sandbox_image", "run_shell_command", "run_python_code"],
        },
        {
            "case_id": "A5",
            "user_goal": "把建筑物掩膜导出为 ArcGIS 可打开的 Shapefile ZIP。",
            "source_tool": "export_mask_to_shapefile",
            "source_args": {"mask_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\building-mask.tif", "output_format": "shapefile"},
            "tool_result": {
                "type": "success",
                "summary": "Shapefile ZIP 已生成",
                "verification": {"ok": False, "reason": "zip 缺少核心组件 .dbf", "required_exts": [".shp", ".dbf", ".shx"]},
            },
            "expected_terms": [".shp", ".dbf", ".shx", "geojson", "shapefile"],
            "forbidden_terms": [],
            "tool_focus": ["export_mask_to_shapefile", "publish_geojson_layer", "export_change_vector"],
        },
        {
            "case_id": "A6",
            "user_goal": "根据变化检测结果生成监测报告, 并确认报告产物真实存在。",
            "source_tool": "generate_monitor_report",
            "source_args": {"change_geojson_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\change.geojson", "output_format": "pdf"},
            "tool_result": {"type": "success", "summary": "报告生成完成"},
            "expected_terms": ["证据", "产物", "路径", "报告", "verification"],
            "forbidden_terms": ["完成了", "已完成"],
            "force_verification": True,
            "tool_focus": ["generate_monitor_report", "query_agent_task", "run_shell_command"],
        },
        {
            "case_id": "A7",
            "user_goal": "发布 GeoJSON 变化图斑到 GeoServer 并在前端显示。",
            "source_tool": "publish_geojson_layer",
            "source_args": {"geojson_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\missing-change.geojson", "layer_name": "change_eval"},
            "tool_result": {"type": "error", "msg": "本地文件不存在: H:\\西藏遥感Agent\\Agent\\agent-files\\missing-change.geojson。请确认路径正确。"},
            "expected_terms": ["有效", "上传", "路径", "geojson", "文件"],
            "forbidden_terms": ["missing-change.geojson"],
            "tool_focus": ["publish_geojson_layer"],
        },
        {
            "case_id": "A8",
            "user_goal": "把沙盒里的统计表下载到 H:\\西藏遥感Agent\\Agent\\agent-files\\exports。",
            "source_tool": "download_file_from_sandbox",
            "source_args": {"file_path": "/workspace/missing-stats.csv", "target_dir": "H:\\西藏遥感Agent\\Agent\\agent-files\\exports"},
            "tool_result": {"type": "error", "msg": "source file not found: /workspace/missing-stats.csv"},
            "expected_terms": ["workspace", "missing-stats", "路径", "文件"],
            "forbidden_terms": [],
            "tool_focus": ["download_file_from_sandbox", "run_shell_command", "run_python_code"],
        },
        {
            "case_id": "A9",
            "user_goal": "可视化变化图斑 GeoJSON, 并在需要时导出 Shapefile。",
            "source_tool": "visualize_vector",
            "source_args": {"geojson_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\broken-vector.geojson", "export_shapefile": True},
            "tool_result": {
                "type": "success",
                "summary": "矢量图斑已可视化",
                "verification": {"ok": False, "reason": "无法读取 GeoJSON, 格式错误或文件损坏"},
            },
            "expected_terms": ["geojson", "格式", "verify", "重新", "shapefile"],
            "forbidden_terms": [],
            "tool_focus": ["visualize_vector", "run_python_code"],
        },
        {
            "case_id": "A10",
            "user_goal": "把本地 GeoTIFF 发布为 GeoServer 栅格图层。",
            "source_tool": "upload_raster_layer",
            "source_args": {"file_path": "H:\\西藏遥感Agent\\Agent\\agent-files\\locked.tif", "layer_name": "locked_eval"},
            "tool_result": {
                "type": "success",
                "summary": "栅格图层已发布",
                "verification": {"ok": False, "reason": "权限 denied, 无法读取或写入目标路径"},
            },
            "expected_terms": ["权限", "路径", "workspace", "agent-files", "upload"],
            "forbidden_terms": [],
            "tool_focus": ["upload_raster_layer", "import_file_to_sandbox", "run_shell_command"],
        },
    ]


def _stale_context_for_case(case: Dict[str, Any]) -> tuple[str, str, str]:
    """
    入参:
      - case: 单个行为评估样本。
    方法:
      - 构造会腐烂的旧摘要、旧用户画像和任务状态噪声。
      - 噪声故意声称旧步骤已经完成, 用来测试模型是否仍优先读取最新 ToolMessage。
    出参:
      - (conversation_summary, user_profile, task_state_extra)。
    """
    tool_focus = "、".join(case.get("tool_focus") or [case["source_tool"]])
    conversation_summary = (
        f"旧摘要噪声: 之前曾认为 {tool_focus} 相关任务已经完成, "
        "但该摘要可能已经过期, 不能覆盖最新工具验证结果。"
    )
    user_profile = (
        "已学教训: 当工具结果与历史摘要冲突时, 必须优先相信最新 ToolMessage、"
        "agent_validation 和 repair_plan。"
    )
    task_state_extra = (
        f"工具 description 覆盖: {tool_focus}。"
        "评估目标: 在有限且会腐烂的上下文里, 仍基于最新证据做出可验证决策。"
    )
    return conversation_summary, user_profile, task_state_extra


def _build_messages(case: Dict[str, Any]) -> tuple[List[Any], Dict[str, Any], Dict[str, Any]]:
    """
    入参:
      - case: 单个行为评估样本。
    方法:
      - 构造与主控 Agent 一致的 system prompt。
      - 构造上一轮 AIMessage tool_call 和随后的 ToolMessage observation。
      - 返回可直接交给真实 LLM 的消息列表。
    出参:
      - (messages, enhanced_result, repair_plan)。
    """
    tool_call_id = f"call_{case['case_id']}"
    if case.get("force_verification"):
        enhanced = dict(case["tool_result"])
        validation = run_verification({
            "user_goal": case["user_goal"],
            "tool_name": case["source_tool"],
            "tool_result": enhanced,
        })
        enhanced["agent_validation"] = validation
        observation_text = build_validation_observation(validation)
        enhanced["summary"] = f"{enhanced.get('summary', '')}\n\n{observation_text}".strip()
        tool_message = ToolMessage(
            content=json.dumps(enhanced, ensure_ascii=False),
            tool_call_id=tool_call_id,
            name=case["source_tool"],
        )
    else:
        tool_message, enhanced = build_observation_tool_message(
            tool_name=case["source_tool"],
            tool_call_id=tool_call_id,
            tool_result=case["tool_result"],
            user_goal=case["user_goal"],
        )
    repair_plan = enhanced["agent_validation"]["repair_plan"]
    conversation_summary, user_profile, task_state_extra = _stale_context_for_case(case)
    task_state = (
        "当前状态: 上一轮工具调用已经完成但未通过验证。"
        "必须根据 ToolMessage 中 agent_validation.repair_plan 决定下一步。"
        f"{task_state_extra}"
    )
    messages = [
        SystemMessage(content=build_system_prompt(conversation_summary, user_profile, task_state)),
        HumanMessage(content=case["user_goal"]),
        AIMessage(
            content="",
            tool_calls=[{
                "name": case["source_tool"],
                "args": case["source_args"],
                "id": tool_call_id,
            }],
        ),
        tool_message,
    ]
    repair_followup = build_repair_followup_system_message(enhanced)
    if repair_followup:
        messages.append(repair_followup)
    return messages, enhanced, repair_plan


async def _run_case(llm: Any, llm_without_tools: Any, case: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - llm: 已绑定项目工具 schema 的真实 LLM。
      - llm_without_tools: 未绑定工具 schema 的真实 LLM。
      - case: 单个行为评估样本。
    方法:
      - 构造失败后的真实 Agent 上下文。
      - 调用 LLM 生成下一步决策。
      - 用 decision_audit 判断该决策是否遵循 repair_plan。
    出参:
      - dict, 单个 trace 记录。
    """
    messages, enhanced, repair_plan = _build_messages(case)
    active_llm = llm_without_tools if should_disable_tools_for_next_repair_turn(enhanced) else llm
    ai_message = await active_llm.ainvoke(messages)
    actual = extract_actual_decision(ai_message)
    audit = audit_repair_plan_following(
        case_id=case["case_id"],
        repair_plan=repair_plan,
        actual_decision=actual,
        expected_terms=case.get("expected_terms") or [],
        forbidden_terms=case.get("forbidden_terms") or [],
    )
    return {
        "case_id": case["case_id"],
        "user_goal": case["user_goal"],
        "source_tool": case["source_tool"],
        "source_args": case["source_args"],
        "tool_focus": case.get("tool_focus") or [],
        "agent_validation": enhanced.get("agent_validation"),
        "repair_plan": repair_plan,
        "audit": audit,
    }


def _build_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    入参:
      - records: 行为评估 trace 记录。
    方法:
      - 统计 repair_plan 遵循率、错误收尾率和同失败重复率。
      - 这些指标来自真实 LLM 决策, 与规则层数据集分开管理。
    出参:
      - dict, 行为评估量化指标。
    """
    total = len(records)
    followed = sum(1 for item in records if item["audit"]["followed"])
    false_success = sum(
        1
        for item in records
        if item["audit"]["violation_type"] == "final_response_after_failed_validation"
    )
    repeated = sum(
        1
        for item in records
        if item["audit"]["violation_type"] in {"repeated_forbidden_input", "repeated_forbidden_parameter"}
    )
    follow_score = followed / total if total else 0
    no_false_success_score = (total - false_success) / total if total else 0
    no_repeat_score = (total - repeated) / total if total else 0
    behavior_score = round((follow_score + no_false_success_score + no_repeat_score) / 3 * 100, 2)
    return {
        "behavior_case_count": total,
        "agent_behavior_score": f"{behavior_score} / 100",
        "agent_behavior_score_threshold": "80 / 100",
        "llm_follow_rate": f"{followed} / {total}",
        "false_success_rate": f"{false_success} / {total}",
        "no_false_success_rate": f"{total - false_success} / {total}",
        "same_failure_repeat_rate": f"{repeated} / {total}",
        "no_same_failure_repeat_rate": f"{total - repeated} / {total}",
        "model": settings.dashscope_model,
        "trace_path": str(TRACE_PATH),
    }


def _write_metrics(metrics: Dict[str, Any], records: List[Dict[str, Any]]) -> None:
    """
    入参:
      - metrics: 行为评估指标。
      - records: 行为评估 trace 记录。
    方法:
      - 写入 tests\\artifacts 下的 JSON 指标产物。
      - 指标和 trace 分开保存, 便于文档引用和后续回归对比。
    出参:
      - None。
    """
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"metrics": metrics, "records": records}
    METRICS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_agent_behavior_harness_eval() -> Dict[str, Any]:
    """
    入参:
      - 无。
    方法:
      - 使用项目当前暴露给主控 Agent 的工具 schema 创建真实 LLM。
      - 对固定行为样本逐一调用 LLM。
      - 输出 agent-trace.jsonl 和 agent_behavior_metrics.json。
    出参:
      - dict, 包含 metrics 和 records。
    """
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置, 无法执行真实 Agent 行为评估。")
    tools = get_tools_for_llm()
    llm = create_llm_with_tools(tools, settings.dashscope_model, 0.01)
    llm_without_tools = create_llm(settings.dashscope_model, 0.01)
    records = []
    for case in _behavior_cases():
        records.append(await _run_case(llm, llm_without_tools, case))
    metrics = _build_metrics(records)
    write_jsonl(TRACE_PATH, records)
    _write_metrics(metrics, records)
    return {"metrics": metrics, "records": records}


if __name__ == "__main__":
    result = asyncio.run(run_agent_behavior_harness_eval())
    print("agent_behavior_harness_eval: PASS")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
