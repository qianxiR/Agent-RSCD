"""
Agent 评估数据集固定入口。

运行方式:
python tests\\agent_evaluation_dataset.py
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.prompt import build_system_prompt, build_system_prompt_blocks
from backend.agent.runtime.observation_builder import build_observation_tool_message
from backend.agent.team.validation_stage import run_validation_stage
from backend.agent.team.verification_worker import run_verification

ARTIFACT_DIR = ROOT / "tests" / "artifacts"
METRICS_PATH = ARTIFACT_DIR / "agent_evaluation_metrics.json"


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    """
    入参:
      - actual: 实际值。
      - expected: 期望值。
      - label: 断言标签, 用于定位失败场景。
    方法:
      - 比较实际值和期望值。
      - 不一致时抛 AssertionError, 让固定评估命令直接失败。
    出参:
      - None; 失败时抛出 AssertionError。
    """
    if actual != expected:
        raise AssertionError(f"{label}: expected={expected!r}, actual={actual!r}")


def _assert_true(condition: bool, label: str) -> None:
    """
    入参:
      - condition: 需要为 True 的判断结果。
      - label: 断言标签, 用于定位失败场景。
    方法:
      - 检查 condition 是否成立。
      - 不成立时抛 AssertionError, 保证评估数据不能静默漂移。
    出参:
      - None; 失败时抛出 AssertionError。
    """
    if not condition:
        raise AssertionError(label)


def _repair_plan(validation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    入参:
      - validation: verification_agent 或 validation_stage 输出, 允许为 None。
    方法:
      - 从验证结果中安全提取 repair_plan。
      - 缺失时返回空 dict, 让调用侧可以统一处理 skip 场景。
    出参:
      - dict, repair_plan 内容或空字典。
    """
    if not isinstance(validation, dict):
        return {}
    repair = validation.get("repair_plan")
    return repair if isinstance(repair, dict) else {}


def _run_stage_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - case: 单个 validation_stage 评估样本。
    方法:
      - 调用 run_validation_stage 获得实际验证结果。
      - 检查 status、failure_type、next_action 等期望字段。
      - 记录该样本贡献的 failure_type。
    出参:
      - dict, 包含 case_id、passed、status、failure_type 等评估结果。
    """
    validation = run_validation_stage(
        tool_name=case["tool_name"],
        tool_result=case["tool_result"],
        user_goal=case.get("user_goal", ""),
    )
    expected_status = case.get("expected_status")
    if expected_status is None:
        _assert_equal(validation, None, f"{case['case_id']} validation skip")
        return {
            "case_id": case["case_id"],
            "passed": True,
            "status": "skipped",
            "failure_type": "",
        }

    _assert_true(isinstance(validation, dict), f"{case['case_id']} validation should exist")
    _assert_equal(validation.get("status"), expected_status, f"{case['case_id']} status")
    repair = _repair_plan(validation)
    expected_failure_type = case.get("expected_failure_type")
    if expected_failure_type is not None:
        _assert_equal(
            repair.get("failure_type"),
            expected_failure_type,
            f"{case['case_id']} failure_type",
        )
    expected_next_action = case.get("expected_next_action")
    if expected_next_action is not None:
        _assert_equal(
            repair.get("next_action"),
            expected_next_action,
            f"{case['case_id']} next_action",
        )

    return {
        "case_id": case["case_id"],
        "passed": True,
        "status": validation.get("status", ""),
        "failure_type": repair.get("failure_type", ""),
    }


def _run_worker_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - case: 单个 verification_worker 直连评估样本。
    方法:
      - 调用 run_verification 覆盖 validation_stage 会跳过的证据不足场景。
      - 检查 worker 输出状态、failure_type 和 next_action。
    出参:
      - dict, 包含 case_id、passed、status、failure_type 等评估结果。
    """
    validation = run_verification(case["payload"])
    _assert_equal(validation.get("status"), case["expected_status"], f"{case['case_id']} status")
    repair = _repair_plan(validation)
    _assert_equal(
        repair.get("failure_type"),
        case["expected_failure_type"],
        f"{case['case_id']} failure_type",
    )
    _assert_equal(
        repair.get("next_action"),
        case["expected_next_action"],
        f"{case['case_id']} next_action",
    )
    return {
        "case_id": case["case_id"],
        "passed": True,
        "status": validation.get("status", ""),
        "failure_type": repair.get("failure_type", ""),
    }


def _run_observation_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - case: 单个 Observation 回灌评估样本。
    方法:
      - 调用 build_observation_tool_message 构造 ToolMessage。
      - 检查 content 是否包含 agent_validation、repair_plan 和目标 failure_type。
      - 检查 summary 是否包含下一步修正字段。
    出参:
      - dict, 包含 case_id、passed 和 failure_type。
    """
    message, enhanced = build_observation_tool_message(
        tool_name=case["tool_name"],
        tool_call_id=case["tool_call_id"],
        tool_result=case["tool_result"],
        user_goal=case.get("user_goal", ""),
    )
    payload = json.loads(message.content)
    _assert_true("agent_validation" in payload, f"{case['case_id']} should include agent_validation")
    repair = payload["agent_validation"]["repair_plan"]
    _assert_equal(repair.get("failure_type"), case["expected_failure_type"], f"{case['case_id']} failure_type")
    summary = payload.get("summary", "")
    for token in ["next_action=", "recommended_tool=", "parameter_changes=", "fallback_tools="]:
        _assert_true(token in summary, f"{case['case_id']} summary should include {token}")
    _assert_true(enhanced["agent_validation"]["status"] in {"failed", "warning"}, f"{case['case_id']} status")
    return {
        "case_id": case["case_id"],
        "passed": True,
        "failure_type": repair.get("failure_type", ""),
    }


def _run_prompt_contract_checks() -> List[Dict[str, Any]]:
    """
    入参:
      - 无。
    方法:
      - 构造系统提示词文本和 block。
      - 检查与验证修正闭环有关的硬性规则是否仍存在。
      - 这些检查用于发现 prompt 重构时误删关键规则。
    出参:
      - list[dict], 每条 prompt 契约检查的结果。
    """
    prompt = build_system_prompt("会话摘要", "用户画像", "任务状态")
    blocks = build_system_prompt_blocks("会话摘要", "用户画像", "任务状态")
    checks = [
        ("P1", "agent_validation.status 为 failed 或 warning 时，禁止直接最终回复", "agent_validation.status 为 failed 或 warning 时，禁止直接最终回复"),
        ("P2", "必须优先读取 agent_validation.repair_plan.next_action", "repair_plan.next_action"),
        ("P3", "下一轮工具调用必须体现 recommended_tool", "repair_plan.recommended_tool"),
        ("P4", "下一轮工具调用必须体现 parameter_changes", "parameter_changes"),
        ("P5", "下一轮工具调用必须体现 fallback_tools", "fallback_tools"),
        ("P6", "当前工具结果优先于历史记忆", "当前轮可验证结果"),
    ]
    results = []
    for case_id, description, token in checks:
        _assert_true(token in prompt, f"{case_id} prompt token missing: {description}")
        results.append({"case_id": case_id, "passed": True, "description": description})
    _assert_true(isinstance(blocks, list) and blocks, "prompt blocks should be non-empty list")
    return results


def _build_stage_cases(existing_artifact: Path) -> List[Dict[str, Any]]:
    """
    入参:
      - existing_artifact: 临时创建的真实产物路径。
    方法:
      - 构造 validation_stage 固定样本集。
      - 每个样本包含输入工具结果和期望 status / failure_type / next_action。
    出参:
      - list[dict], validation_stage 评估样本。
    """
    return [
        {
            "case_id": "V1",
            "tool_name": "render_sandbox_image",
            "tool_result": {"type": "success", "summary": "产物已生成", "data": {"artifact_path": str(existing_artifact)}},
            "user_goal": "验证图片产物",
            "expected_status": "passed",
            "expected_failure_type": "",
            "expected_next_action": "continue",
        },
        {
            "case_id": "V2",
            "tool_name": "segment_image",
            "tool_result": {"type": "error", "msg": "模型权重不存在"},
            "user_goal": "执行分割",
            "expected_status": "failed",
            "expected_failure_type": "tool_error",
            "expected_next_action": "diagnose_error_then_retry",
        },
        {
            "case_id": "V3",
            "tool_name": "list_image_catalog",
            "tool_result": {"type": "success", "summary": "查询完成", "data": {"count": 3}},
            "user_goal": "查询影像",
            "expected_status": None,
        },
        {
            "case_id": "V4",
            "tool_name": "render_sandbox_image",
            "tool_result": {"type": "success", "summary": "产物已生成", "data": {"artifact_path": str(ROOT / "agent-files" / "missing-for-eval.png")}},
            "user_goal": "验证缺失产物修复策略",
            "expected_status": "failed",
            "expected_failure_type": "missing_artifact",
            "expected_next_action": "retry_same_tool_with_checked_output_path",
        },
        {
            "case_id": "V5",
            "tool_name": "export_mask_to_shapefile",
            "tool_result": {"type": "success", "summary": "矢量导出完成", "verification": {"ok": False, "reason": "要素数为 0 (< 1 阈值), 矢量为空", "feature_count": 0}},
            "user_goal": "提取建筑物",
            "expected_status": "failed",
            "expected_failure_type": "empty_vector",
            "expected_next_action": "return_to_segmentation_with_same_target_and_synonyms",
        },
        {
            "case_id": "V6",
            "tool_name": "import_file_to_sandbox",
            "tool_result": {"type": "error", "msg": "本地文件不存在: H:\\西藏遥感Agent\\Agent\\agent-files\\missing-input.png。请确认路径正确。"},
            "user_goal": "对本地影像做建筑物分割",
            "expected_status": "failed",
            "expected_failure_type": "missing_input_file",
            "expected_next_action": "request_valid_input_file_or_upload",
        },
        {
            "case_id": "V7",
            "tool_name": "segment_image",
            "tool_result": {"type": "success", "summary": "参数校验失败", "verification": {"ok": False, "reason": "参数 classes 无效"}},
            "user_goal": "按指定类别分割",
            "expected_status": "failed",
            "expected_failure_type": "invalid_arguments",
            "expected_next_action": "repair_arguments_before_retry",
        },
        {
            "case_id": "V8",
            "tool_name": "export_mask_to_shapefile",
            "tool_result": {"type": "success", "summary": "Shapefile 已导出", "verification": {"ok": False, "reason": "zip 缺少核心组件 .dbf"}},
            "user_goal": "导出矢量压缩包",
            "expected_status": "failed",
            "expected_failure_type": "incomplete_shapefile_zip",
            "expected_next_action": "reexport_shapefile_or_fallback_geojson",
        },
        {
            "case_id": "V9",
            "tool_name": "download_file_from_sandbox",
            "tool_result": {"type": "error", "msg": "source file not found: /workspace/missing-stats.csv"},
            "user_goal": "下载沙盒统计表",
            "expected_status": "failed",
            "expected_failure_type": "missing_sandbox_file",
            "expected_next_action": "locate_existing_sandbox_artifact_then_retry",
        },
        {
            "case_id": "V10",
            "tool_name": "publish_geojson_layer",
            "tool_result": {"type": "error", "msg": "本地文件不存在: H:\\西藏遥感Agent\\Agent\\agent-files\\missing-change.geojson。请确认路径正确。"},
            "user_goal": "发布 GeoJSON 变化图斑",
            "expected_status": "failed",
            "expected_failure_type": "missing_vector_artifact",
            "expected_next_action": "locate_existing_vector_artifact_then_retry",
        },
    ]


def _build_worker_cases() -> List[Dict[str, Any]]:
    """
    入参:
      - 无。
    方法:
      - 构造 verification_worker 直连样本。
      - 当前用于覆盖 validation_stage 主动跳过的证据不足 warning。
    出参:
      - list[dict], verification_worker 评估样本。
    """
    return [
        {
            "case_id": "W1",
            "payload": {
                "user_goal": "确认任务是否完成",
                "tool_name": "custom_tool",
                "tool_result": {"type": "success", "summary": "处理完成"},
            },
            "expected_status": "warning",
            "expected_failure_type": "insufficient_evidence",
            "expected_next_action": "collect_evidence_before_final_answer",
        }
    ]


def _build_observation_cases() -> List[Dict[str, Any]]:
    """
    入参:
      - 无。
    方法:
      - 构造 Observation 回灌固定样本。
      - 覆盖通用工具失败、缺失产物和输入文件不存在三类高频场景。
    出参:
      - list[dict], Observation 回灌评估样本。
    """
    return [
        {
            "case_id": "O1",
            "tool_call_id": "call_eval_tool_error",
            "tool_name": "segment_image",
            "tool_result": {"type": "error", "msg": "参数 classes 不能为空", "error_type": "invalid_arguments"},
            "user_goal": "提取建筑物并导出矢量",
            "expected_failure_type": "invalid_arguments",
        },
        {
            "case_id": "O2",
            "tool_call_id": "call_eval_missing_artifact",
            "tool_name": "render_sandbox_image",
            "tool_result": {"type": "success", "summary": "产物已生成", "data": {"artifact_path": str(ROOT / "agent-files" / "missing-observation.png")}},
            "user_goal": "渲染分析图",
            "expected_failure_type": "missing_artifact",
        },
        {
            "case_id": "O3",
            "tool_call_id": "call_eval_missing_input",
            "tool_name": "import_file_to_sandbox",
            "tool_result": {"type": "error", "msg": "本地文件不存在: H:\\西藏遥感Agent\\Agent\\agent-files\\missing-input.png。请确认路径正确。"},
            "user_goal": "导入本地影像",
            "expected_failure_type": "missing_input_file",
        },
        {
            "case_id": "O4",
            "tool_call_id": "call_eval_missing_sandbox",
            "tool_name": "download_file_from_sandbox",
            "tool_result": {"type": "error", "msg": "source file not found: /workspace/missing-stats.csv"},
            "user_goal": "下载沙盒统计表",
            "expected_failure_type": "missing_sandbox_file",
        },
        {
            "case_id": "O5",
            "tool_call_id": "call_eval_missing_vector",
            "tool_name": "publish_geojson_layer",
            "tool_result": {"type": "error", "msg": "本地文件不存在: H:\\西藏遥感Agent\\Agent\\agent-files\\missing-change.geojson。请确认路径正确。"},
            "user_goal": "发布 GeoJSON 变化图斑",
            "expected_failure_type": "missing_vector_artifact",
        },
    ]


def _format_rate(passed: int, total: int) -> str:
    """
    入参:
      - passed: 通过数量。
      - total: 总数量。
    方法:
      - 格式化为固定的 x / y 文本。
      - total 为 0 时返回 0 / 0, 避免除零。
    出参:
      - str, 可直接写入基线报告的比率文本。
    """
    if total == 0:
        return "0 / 0"
    return f"{passed} / {total}"


def _write_metrics(metrics: Dict[str, Any]) -> None:
    """
    入参:
      - metrics: 本次评估产出的指标字典。
    方法:
      - 确保 tests\\artifacts 目录存在。
      - 将评估结果写入 JSON 文件, 作为本地可追踪评估产物。
    出参:
      - None。
    """
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


def run_agent_evaluation_dataset() -> Dict[str, Any]:
    """
    入参:
      - 无。
    方法:
      - 创建临时真实产物, 执行验证层、worker 层、Observation 层和 prompt 契约评估。
      - 汇总固定量化指标和覆盖的 failure_type。
      - 写入 tests\\artifacts\\agent_evaluation_metrics.json。
    出参:
      - dict, 本次评估的完整指标与样本结果。
    """
    with tempfile.TemporaryDirectory() as tmp:
        existing_artifact = Path(tmp) / "result.png"
        existing_artifact.write_bytes(b"x" * 256)
        stage_results = [_run_stage_case(case) for case in _build_stage_cases(existing_artifact)]

    worker_results = [_run_worker_case(case) for case in _build_worker_cases()]
    observation_results = [_run_observation_case(case) for case in _build_observation_cases()]
    prompt_results = _run_prompt_contract_checks()

    validation_results = stage_results + worker_results
    validation_total = len(validation_results)
    validation_passed = sum(1 for item in validation_results if item["passed"])
    observation_total = len(observation_results)
    observation_passed = sum(1 for item in observation_results if item["passed"])
    prompt_total = len(prompt_results)
    prompt_passed = sum(1 for item in prompt_results if item["passed"])
    covered_failure_types = sorted(
        {
            item["failure_type"]
            for item in validation_results + observation_results
            if item.get("failure_type")
        }
    )

    report = {
        "metrics": {
            "validation_rule_pass_rate": _format_rate(validation_passed, validation_total),
            "observation_injection_pass_rate": _format_rate(observation_passed, observation_total),
            "prompt_contract_pass_rate": _format_rate(prompt_passed, prompt_total),
            "covered_failure_types": len(covered_failure_types),
            "covered_failure_type_names": covered_failure_types,
            "llm_follow_rate": "未建立",
            "same_failure_repeat_rate": "未建立",
            "false_success_rate": "未建立",
        },
        "cases": {
            "validation": validation_results,
            "observation": observation_results,
            "prompt_contract": prompt_results,
        },
        "artifact": str(METRICS_PATH),
    }
    _write_metrics(report)
    return report


if __name__ == "__main__":
    result = run_agent_evaluation_dataset()
    print("agent_evaluation_dataset: PASS")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
