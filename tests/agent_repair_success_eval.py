"""
Agent 修正成功率评估入口。

样本集覆盖真实工具链 (遥感分割 / 变化检测 / 报告 / 发布 / 下载 / 沙盒 / 视觉理解)
的 repair_plan 修正结果, 把指标从单点样本扩展到可代表多轮真实任务的程度。

运行方式:
python tests\\agent_repair_success_eval.py
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.observability.repair_success import (
    build_repair_attempt,
    build_repair_success_metrics,
    evaluate_repair_success,
)

ARTIFACT_DIR = ROOT / "tests" / "artifacts"
METRICS_PATH = ARTIFACT_DIR / "agent_repair_success_metrics.json"


def _assert_true(condition: bool, label: str) -> None:
    """
    入参:
      - condition: 需要为 True 的条件。
      - label: 断言标签。
    方法:
      - 条件不成立时抛 AssertionError。
    出参:
      - None。
    """
    if not condition:
        raise AssertionError(label)


# 真实工具链修正样本: (case_id, failure_type, source_tool, repaired_tool, next_status)
# - next_status="passed": 遵循 repair_plan 后下一次工具通过客观验收 (修正成功)。
# - next_status="failed": 修正后仍未通过 (缺输入 / 证据不足等主控无法自愈的失败)。
# - next_status=None: 下一次工具结果无 agent_validation (下载等工具不进验证阶段, unverified 不计入分母)。
# 这样做是为了让 repair_success_rate 覆盖遥感、发布、报告、下载等真实链路, 而非单点合成。
_REPAIR_CASES: Tuple[Tuple[str, str, str, str, Optional[str]], ...] = (
    ("R1", "invalid_arguments", "segment_image", "segment_image", "passed"),
    ("R2", "empty_vector", "segment_image", "segment_image", "passed"),
    ("R3", "missing_input_file", "segment_image", "segment_image", "failed"),
    ("R4", "empty_or_tiny_artifact", "detect_change", "detect_change", "passed"),
    ("R5", "insufficient_evidence", "detect_change", "detect_change", "failed"),
    ("R6", "unreadable_artifact", "generate_monitor_report", "generate_monitor_report", "passed"),
    ("R7", "permission_or_path_error", "generate_monitor_report", "generate_monitor_report", None),
    ("R8", "missing_vector_artifact", "publish_geojson_layer", "upload_shapefile", "failed"),
    ("R9", "incomplete_shapefile_zip", "upload_shapefile", "upload_shapefile", "passed"),
    ("R10", "missing_sandbox_file", "download_file_from_sandbox", "download_file_from_sandbox", "passed"),
    ("R11", "tool_error", "run_in_sandbox", "run_in_sandbox", "passed"),
    ("R12", "tool_error", "download_file_from_sandbox", "download_file_from_sandbox", None),
    ("R13", "invalid_arguments", "understand_image", "understand_image", "passed"),
    ("R14", "missing_artifact", "visualize_vector", "visualize_vector", "passed"),
    ("R15", "unknown_failure", "segment_image", "segment_image", "passed"),
)

# 下一次工具结果按期望状态构造: passed/failed 带对应 agent_validation; None 不带验证字段。
_REPAIRED_RESULT_BY_STATUS: Dict[Optional[str], Dict[str, Any]] = {
    "passed": {"agent_validation": {"status": "passed", "summary": "修正后通过客观验收"}},
    "failed": {"agent_validation": {"status": "failed", "summary": "修正后仍未通过"}},
}


def _build_repaired_result(next_status: Optional[str]) -> Dict[str, Any]:
    """
    入参:
      - next_status: 期望的下一次工具验证状态。
    方法:
      - passed/failed 取带 agent_validation 的结果模板。
      - None 返回无验证字段的通用成功结果, 触发 evaluate_repair_success 的 unverified 分支。
    出参:
      - dict, 模拟的下一次工具结果。
    """
    return _REPAIRED_RESULT_BY_STATUS.get(next_status) or {
        "type": "success",
        "summary": "工具执行完成但未进入验证阶段",
    }


def _run_cases() -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    入参:
      - 无。
    方法:
      - 遍历 _REPAIR_CASES, 为每条样本构造 repair attempt 和下一次工具结果。
      - 调 evaluate_repair_success 得到修正记录, 并逐条断言判定符合预期。
      - 统计 passed/failed/unverified 三类计数, 供指标断言使用。
    出参:
      - (records, expected), records 为修正记录列表, expected 为三类计数。
    """
    records: List[Dict[str, Any]] = []
    expected = {"passed": 0, "failed": 0, "unverified": 0}
    for case_id, failure_type, source_tool, repaired_tool, next_status in _REPAIR_CASES:
        attempt = build_repair_attempt(
            conversation_id=f"repair_eval_{case_id}",
            iteration=1,
            source_tool=source_tool,
            repair_plan={
                "failure_type": failure_type,
                "recommended_tool": repaired_tool,
                "next_action": "retry_after_repair",
            },
            audit={"followed": True, "violation_type": ""},
        )
        record = evaluate_repair_success(
            attempt, repaired_tool, _build_repaired_result(next_status)
        )
        records.append(record)
        status = record["next_agent_validation_status"]
        if status not in ("passed", "failed", "unverified"):
            raise AssertionError(f"{case_id} 非法 status: {status}")
        if next_status == "passed":
            _assert_true(record["repair_success"] is True, f"{case_id} passed 应计成功")
            expected["passed"] += 1
        elif next_status == "failed":
            _assert_true(record["repair_success"] is False, f"{case_id} failed 应计失败")
            expected["failed"] += 1
        else:
            _assert_true(status == "unverified", f"{case_id} 应为 unverified")
            expected["unverified"] += 1
    return records, expected


def _write_report(report: Dict[str, Any]) -> None:
    """
    入参:
      - report: 评估报告。
    方法:
      - 写入 tests\\artifacts\\agent_repair_success_metrics.json。
    出参:
      - None。
    """
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_agent_repair_success_eval() -> Dict[str, Any]:
    """
    入参:
      - 无。
    方法:
      - 执行真实工具链修正样本集。
      - 断言 repair_success_rate 与 attempt_count 与样本计数一致, 锁定指标语义。
    出参:
      - dict, 包含 metrics 和 cases。
    """
    records, expected = _run_cases()
    metrics = build_repair_success_metrics(records)
    verified = expected["passed"] + expected["failed"]
    _assert_true(
        metrics["repair_success_rate"] == f"{expected['passed']} / {verified}",
        "repair_success_rate",
    )
    _assert_true(metrics["repair_attempt_count"] == len(_REPAIR_CASES), "repair_attempt_count")
    report = {
        "metrics": metrics,
        "cases": records,
        "artifact": str(METRICS_PATH),
    }
    _write_report(report)
    return report


if __name__ == "__main__":
    result = run_agent_repair_success_eval()
    print("agent_repair_success_eval: PASS")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
