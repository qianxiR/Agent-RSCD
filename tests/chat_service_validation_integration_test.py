"""
chat_service 工具结果验证回灌集成测试。

运行方式:
python tests\\chat_service_validation_integration_test.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.runtime.observation_builder import build_observation_tool_message
from backend.agent.chat_service import (
    _extract_report_download_from_payload,
    _find_latest_report_download,
    _find_latest_report_download_from_worker_tasks,
    _is_report_download_request,
)
from langchain_core.messages import ToolMessage


def _assert_true(condition, label):
    """
    入参:
      - condition: 需要成立的布尔表达式。
      - label: 断言标签。
    方法:
      - 条件不成立时抛 AssertionError。
      - 这样做是为了保持 tests/ 独立脚本风格, 不引入 pytest 依赖。
    出参:
      - 无返回; 失败时抛异常。
    """
    if not condition:
        raise AssertionError(label)


def test_tool_message_contains_agent_validation_and_repair_plan():
    """
    入参:
      - 无。
    方法:
      - 构造工具失败结果, 模拟 chat_service 在 ToolMessage 回灌前执行验证阶段。
      - 断言 ToolMessage JSON content 包含 agent_validation。
      - 断言 summary 中包含 next_action / recommended_tool / parameter_changes / fallback_tools。
    出参:
      - 无返回; 失败时抛异常。
    """
    tool_name = "segment_image"
    tool_result = {
        "type": "error",
        "msg": "推理服务超时，请稍后重试",
    }
    message, enhanced = build_observation_tool_message(
        tool_name=tool_name,
        tool_call_id="call_validation_test",
        tool_result=tool_result,
        user_goal="提取建筑物并导出矢量",
    )
    payload = json.loads(message.content)

    _assert_true("agent_validation" in payload, "ToolMessage content should include agent_validation")
    _assert_true(enhanced["agent_validation"]["status"] == "failed", "enhanced result should include validation")
    _assert_true(payload["agent_validation"]["status"] == "failed", "validation status should be failed")
    repair_plan = payload["agent_validation"]["repair_plan"]
    _assert_true(repair_plan["next_action"] == "diagnose_error_then_retry", "repair next_action should guide retry")
    _assert_true(repair_plan["recommended_tool"] == tool_name, "repair recommended_tool should keep source tool")
    _assert_true(bool(repair_plan["parameter_changes"]), "repair parameter_changes should not be empty")
    _assert_true(bool(repair_plan["fallback_tools"]), "repair fallback_tools should not be empty")

    summary = payload.get("summary", "")
    _assert_true("next_action=diagnose_error_then_retry" in summary, "summary should include next_action")
    _assert_true("recommended_tool=segment_image" in summary, "summary should include recommended_tool")
    _assert_true("parameter_changes=" in summary, "summary should include parameter_changes")
    _assert_true("fallback_tools=" in summary, "summary should include fallback_tools")


def test_report_download_reused_from_worker_task():
    """
    入参:
      - 无。
    方法:
      - 解耦后 segment ToolMessage 不再含 auto_report, 报告 URL 改存 report_agent worker 的 ai_task.output。
      - mock agent_db.list_tasks/get_task 返回一个 done+passed 的 report_agent 任务。
      - 断言 _find_latest_report_download_from_worker_tasks 能取回 url/local_path/report_task_id。
    出参:
      - 无返回; 失败时抛异常。
    """
    from backend.agent.memory import agent_db

    report_url = "/api/v1/download/report/_default/16375e29/16375e29_report_q4ih.pdf?t=q4ih"
    report_path = "H:\\西藏遥感Agent\\Agent\\agent-files\\report\\_default\\16375e29\\16375e29_report_q4ih.pdf"
    fake_task = {
        "id": 200,
        "status": "done",
        "output": {
            "status": "passed",
            "report_url": report_url,
            "report_path": report_path,
            "artifacts": {"report_url": report_url, "report_path": report_path},
        },
    }
    orig_list = agent_db.list_tasks
    orig_get = agent_db.get_task
    try:
        agent_db.list_tasks = lambda **kw: [{"id": 200, "status": "done"}]
        agent_db.get_task = lambda tid: fake_task if tid == 200 else None
        found = _find_latest_report_download_from_worker_tasks("conv_worker_test")
    finally:
        agent_db.list_tasks = orig_list
        agent_db.get_task = orig_get

    _assert_true(_is_report_download_request("下载这个报告"), "prompt should be report download request")
    _assert_true(found is not None, "should find report from worker task")
    _assert_true(found["url"] == report_url, "worker task url mismatch")
    _assert_true(found["local_path"] == report_path, "worker task local_path mismatch")
    _assert_true(found["report_task_id"] == 200, "worker task id mismatch")


def test_extract_report_download_from_download_action():
    """
    入参:
      - 无。
    方法:
      - _extract_report_download_from_payload 对非 segment 报告工具 (如 generate_monitor_report)
        直接产出的 download action 仍应生效, 证明该函数未被解耦误伤。
    出参:
      - 无返回; 失败时抛异常。
    """
    report_url = "/api/v1/download/report/_default/abc/abc_report.pdf"
    payload = {
        "type": "frontend_action",
        "instruction": {
            "type": "download",
            "action": "download",
            "params": {"url": report_url, "filename": "abc_report.pdf"},
        },
    }
    direct = _extract_report_download_from_payload(payload)
    _assert_true(direct is not None, "download action should be extracted")
    _assert_true(direct["url"] == report_url, "extracted url mismatch")

    from_history = _find_latest_report_download([
        ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id="call_report_tool",
            name="generate_monitor_report",
        )
    ])
    _assert_true(from_history is not None, "history extraction should still work for download action")


if __name__ == "__main__":
    test_tool_message_contains_agent_validation_and_repair_plan()
    test_report_download_reused_from_worker_task()
    test_extract_report_download_from_download_action()
    print("chat_service_validation_integration_test: PASS")
