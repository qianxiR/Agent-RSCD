"""
Agent 工程护栏评估集。

运行方式:
python tests\\agent_guardrail_eval.py
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.context.context_priority import resolve_context_conflict
from backend.agent.observability.decision_audit import append_jsonl, audit_repair_plan_following
from backend.agent.observability.parameter_repair import validate_parameter_repair
from backend.agent.runtime.retry_guard import build_retry_signature, should_block_retry

ARTIFACT_DIR = ROOT / "tests" / "artifacts"
METRICS_PATH = ARTIFACT_DIR / "agent_guardrail_metrics.json"


def _assert_true(condition: bool, label: str) -> None:
    """
    入参:
      - condition: 需要为 True 的条件。
      - label: 断言标签。
    方法:
      - 条件不成立时抛 AssertionError。
      - 这样做是为了让固定评估命令直接暴露护栏退化。
    出参:
      - None。
    """
    if not condition:
        raise AssertionError(label)


def _base_repair_plan(failure_type: str, recommended_tool: str = "segment_image") -> Dict[str, Any]:
    """
    入参:
      - failure_type: 失败类型。
      - recommended_tool: 推荐工具名。
    方法:
      - 构造最小 repair_plan, 供护栏评估复用。
    出参:
      - dict, repair_plan。
    """
    return {
        "failure_type": failure_type,
        "recommended_tool": recommended_tool,
        "fallback_tools": ["run_python_code"],
        "next_action": "repair_arguments_before_retry",
        "parameter_changes": {"classes": "建筑物, building, 房屋"},
    }


def _decision(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - tool_name: 工具名。
      - args: 工具参数。
    方法:
      - 构造 decision_audit 所需的 actual_decision。
    出参:
      - dict, actual_decision。
    """
    return {
        "actual_tool_calls": [{"name": tool_name, "args": args, "id": "call_guardrail"}],
        "final_response": "",
        "has_tool_call": True,
    }


def _run_case(case_id: str, fn) -> Dict[str, Any]:
    """
    入参:
      - case_id: 评估样本编号。
      - fn: 无参评估函数。
    方法:
      - 执行单个护栏样本。
      - 返回统一 case 结果。
    出参:
      - dict, 包含 case_id 和 passed。
    """
    fn()
    return {"case_id": case_id, "passed": True}


def _cases() -> List[Dict[str, Any]]:
    """
    入参:
      - 无。
    方法:
      - 构造 10 条工程护栏评估样本。
      - 覆盖 context_priority、retry_guard、parameter_repair、decision_audit。
    出参:
      - list[dict], 每项包含 case_id 和 fn。
    """
    def c1_latest_validation_wins() -> None:
        result = resolve_context_conflict([
            {"source_type": "conversation_summary", "claim": "任务完成", "value": "旧摘要说完成"},
            {"source_type": "latest_agent_validation", "claim": "任务失败", "value": "最新验证失败"},
        ])
        _assert_true(result["winner"]["source_type"] == "latest_agent_validation", "C1 winner")

    def c2_user_request_wins_memory() -> None:
        result = resolve_context_conflict([
            {"source_type": "long_term_memory", "claim": "输出 PDF", "value": "旧偏好"},
            {"source_type": "current_user_request", "claim": "输出 GeoJSON", "value": "当前要求"},
        ])
        _assert_true(result["winner"]["source_type"] == "current_user_request", "C2 winner")

    def c3_retry_blocks_same_signature() -> None:
        signature = build_retry_signature("import_file_to_sandbox", {"file_path": "missing.png"}, "missing_input_file", "not found")
        result = should_block_retry(
            [{"retry_signature": signature}],
            "import_file_to_sandbox",
            {"file_path": "missing.png"},
            "missing_input_file",
            "not found",
        )
        _assert_true(result["block"] is True, "C3 retry should block")

    def c4_retry_allows_changed_args() -> None:
        signature = build_retry_signature("segment_image", {"classes": []}, "tool_error", "classes empty")
        result = should_block_retry(
            [{"retry_signature": signature}],
            "segment_image",
            {"classes": ["建筑物"]},
            "tool_error",
            "classes empty",
        )
        _assert_true(result["block"] is False, "C4 retry should allow changed args")

    def c5_parameter_repair_passes_non_empty_class() -> None:
        result = validate_parameter_repair(
            _base_repair_plan("tool_error"),
            _decision("segment_image", {"classes": "建筑物, building", "image_path": "a.tif"}),
            ["建筑物", "building"],
        )
        _assert_true(result["passed"] is True, "C5 parameter repair should pass")

    def c6_parameter_repair_fails_empty_class() -> None:
        result = validate_parameter_repair(
            _base_repair_plan("tool_error"),
            _decision("segment_image", {"classes": "[]", "image_path": "a.tif"}),
            ["建筑物", "building"],
        )
        _assert_true(result["passed"] is False, "C6 parameter repair should fail")

    def c7_missing_input_final_response_passes() -> None:
        result = validate_parameter_repair(
            _base_repair_plan("missing_input_file", "import_file_to_sandbox"),
            {"actual_tool_calls": [], "final_response": "请提供有效路径或上传文件", "has_tool_call": False},
            ["有效", "路径", "文件"],
        )
        _assert_true(result["passed"] is True, "C7 missing input final response should pass")

    def c8_missing_input_tool_call_fails() -> None:
        result = validate_parameter_repair(
            _base_repair_plan("missing_input_file", "import_file_to_sandbox"),
            _decision("run_shell_command", {"command": "ls missing.png"}),
            ["有效", "路径", "文件"],
        )
        _assert_true(result["passed"] is False, "C8 missing input tool call should fail")

    def c9_decision_audit_uses_parameter_check() -> None:
        audit = audit_repair_plan_following(
            "C9",
            _base_repair_plan("tool_error"),
            _decision("segment_image", {"classes": "[]", "image_path": "a.tif"}),
            ["建筑物"],
            [],
        )
        _assert_true(audit["followed"] is False, "C9 audit should fail empty args")
        _assert_true(audit["violation_type"] == "missing_expected_parameter_change", "C9 violation")

    def c10_audit_trace_appends_jsonl() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            append_jsonl(path, {"case_id": "C10", "ok": True})
            append_jsonl(path, {"case_id": "C10b", "ok": True})
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            _assert_true(len(lines) == 2, "C10 jsonl line count")

    return [
        {"case_id": "G1", "fn": c1_latest_validation_wins},
        {"case_id": "G2", "fn": c2_user_request_wins_memory},
        {"case_id": "G3", "fn": c3_retry_blocks_same_signature},
        {"case_id": "G4", "fn": c4_retry_allows_changed_args},
        {"case_id": "G5", "fn": c5_parameter_repair_passes_non_empty_class},
        {"case_id": "G6", "fn": c6_parameter_repair_fails_empty_class},
        {"case_id": "G7", "fn": c7_missing_input_final_response_passes},
        {"case_id": "G8", "fn": c8_missing_input_tool_call_fails},
        {"case_id": "G9", "fn": c9_decision_audit_uses_parameter_check},
        {"case_id": "G10", "fn": c10_audit_trace_appends_jsonl},
    ]


def _write_metrics(report: Dict[str, Any]) -> None:
    """
    入参:
      - report: 护栏评估报告。
    方法:
      - 写入 tests\\artifacts\\agent_guardrail_metrics.json。
    出参:
      - None。
    """
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_agent_guardrail_eval() -> Dict[str, Any]:
    """
    入参:
      - 无。
    方法:
      - 执行 10 条工程护栏评估样本。
      - 汇总 guardrail_score 和分项通过率。
    出参:
      - dict, 评估报告。
    """
    results = [_run_case(case["case_id"], case["fn"]) for case in _cases()]
    total = len(results)
    passed = sum(1 for item in results if item["passed"])
    report = {
        "metrics": {
            "guardrail_case_count": total,
            "guardrail_pass_rate": f"{passed} / {total}",
            "guardrail_score": f"{round(passed / total * 100, 2)} / 100",
            "guardrail_score_threshold": "80 / 100",
        },
        "cases": results,
        "artifact": str(METRICS_PATH),
    }
    _write_metrics(report)
    return report


if __name__ == "__main__":
    output = run_agent_guardrail_eval()
    print("agent_guardrail_eval: PASS")
    print(json.dumps(output["metrics"], ensure_ascii=False, indent=2))
