"""
worker 汇报契约测试。

验证所有专业 worker (verification / report) 的输出都包含统一 12 字段,
缺字段即破坏契约。运行方式:

python tests\\worker_report_contract_test.py
"""

import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.team.result_contract import WORKER_RESULT_FIELDS
from backend.agent.team.report_worker import run_report_worker
from backend.agent.team.verification_worker import run_verification


def _assert_fields(result: Dict[str, Any], label: str) -> None:
    """
    入参:
      - result: worker 输出 dict。
      - label: 断言标签, 标识是哪个 worker 的哪种状态。
    方法:
      - 逐字段检查 WORKER_RESULT_FIELDS 是否都存在于 result。
      - 任一缺失抛 AssertionError, 暴露破坏契约的 worker。
    出参:
      - None。
    """
    missing = [field for field in WORKER_RESULT_FIELDS if field not in result]
    if missing:
        raise AssertionError(f"{label} 缺失契约字段: {missing}")


def _verify_case(payload: Dict[str, Any], expected_status: str, label: str) -> Dict[str, Any]:
    """
    入参:
      - payload: verification_agent 输入。
      - expected_status: 期望的 status (passed/failed/warning)。
      - label: 断言标签。
    方法:
      - 跑 run_verification, 校验状态正确、12 字段齐全、交接字段非空。
    出参:
      - dict, verification 输出。
    """
    result = run_verification(payload)
    _assert_fields(result, label)
    if result["status"] != expected_status:
        raise AssertionError(f"{label} 期望 status={expected_status}, 实际 {result['status']}")
    if not result["next_action"] or not result["handoff_notes"]:
        raise AssertionError(f"{label} next_action/handoff_notes 不应为空")
    return result


def _report_case(payload: Dict[str, Any], expected_status: str, label: str) -> Dict[str, Any]:
    """
    入参:
      - payload: report_agent 输入。
      - expected_status: 期望的 status。
      - label: 断言标签。
    方法:
      - 跑 run_report_worker, 校验状态正确、12 字段齐全。
      - 选用不依赖 GeoServer 的输入, 保证纯单测可复现。
    出参:
      - dict, report 输出。
    """
    result = run_report_worker(payload)
    _assert_fields(result, label)
    if result["status"] != expected_status:
        raise AssertionError(f"{label} 期望 status={expected_status}, 实际 {result['status']}")
    return result


def run_worker_report_contract_test() -> Dict[str, Any]:
    """
    入参:
      - 无。
    方法:
      - verification_agent 三态 (failed/passed/warning) 各跑一例, 校验 12 字段。
      - report_agent 不依赖 GeoServer 的两态 (warning/failed) 各跑一例, 校验 12 字段。
      - 这样做是为了在 rs/publish worker 拆分前, 先锁死现有 worker 的汇报契约。
    出参:
      - dict, 测试摘要。
    """
    # verification failed: 工具直接返回 error
    _verify_case(
        {
            "user_goal": "分割影像",
            "tool_name": "segment_image",
            "tool_result": {"type": "error", "msg": "模型加载失败"},
        },
        "failed",
        "verification_failed",
    )
    # verification passed: 工具自带 verification ok=true
    _verify_case(
        {
            "user_goal": "分割影像",
            "tool_name": "segment_image",
            "tool_result": {"type": "success", "verification": {"ok": True}},
        },
        "passed",
        "verification_passed",
    )
    # verification warning: 无 error、无 verification、无产物路径
    _verify_case(
        {
            "user_goal": "查询目录",
            "tool_name": "run_shell_command",
            "tool_result": {"type": "success", "summary": "执行完成"},
        },
        "warning",
        "verification_warning",
    )

    # report warning: 缺 GeoServer 图层参数, 在调 GeoServer 前即返回
    _report_case(
        {
            "task_type": "segmentation_auto_report",
            "image_path": "x.tif",
            "classes": "建筑物",
            "polygon_layer": {},
            "base_layer": {},
        },
        "warning",
        "report_missing_layers",
    )
    # report failed: 不支持的任务类型
    _report_case(
        {"task_type": "unknown_report_type"},
        "failed",
        "report_unsupported_type",
    )

    return {
        "worker_count": 2,
        "cases": 5,
        "fields": list(WORKER_RESULT_FIELDS),
    }


if __name__ == "__main__":
    summary = run_worker_report_contract_test()
    print("worker_report_contract_test: PASS")
    print(f"校验 worker={summary['worker_count']}, cases={summary['cases']}, fields={len(summary['fields'])}")
