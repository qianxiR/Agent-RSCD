"""
chat_service report_agent 派发集成测试。

验证解耦后链路: segment_image 成功 → chat_service 自动派发 report_agent worker →
worker 完成后推送 download 事件。通过 mock task_manager / agent_db / ws_manager
隔离真实 GeoServer 与数据库, 保证纯单测可复现。

运行方式:
python tests\\chat_service_report_dispatch_test.py
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.chat_service import (
    _await_and_push_report_download,
    _dispatch_segment_report_worker,
)


class _FakeWS:
    """
    入参:
      - 无。
    方法:
      - 记录 send_to_session 调用, 模拟 ws_manager, 不触达真实 WebSocket。
    出参:
      - events 属性累积收到的事件。
    """

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    async def send_to_session(self, sid: str, event: Dict[str, Any]) -> None:
        self.events.append(event)


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


def test_await_pushes_download_when_worker_passed() -> None:
    """
    入参:
      - 无。
    方法:
      - mock get_worker_task 返回 None (已完成) + get_task 返回 done+passed+report_url。
      - 断言 _await_and_push_report_download 推送一个 download 事件, 含 report_url 与 report_task_id。
    出参:
      - None。
    """
    import backend.agent.team.task_manager as task_manager
    from backend.agent.memory import agent_db

    fake_task = {
        "status": "done",
        "output": {
            "status": "passed",
            "report_url": "/api/v1/download/report/_default/c/c_report.pdf",
            "report_path": str(ROOT / "tests" / "artifacts" / "c_report.pdf"),
            "artifacts": {},
        },
    }
    orig_get_worker = task_manager.get_worker_task
    orig_get_task = agent_db.get_task
    try:
        task_manager.get_worker_task = lambda tid: None
        agent_db.get_task = lambda tid: fake_task
        ws = _FakeWS()
        asyncio.run(_await_and_push_report_download(999, "sid", "conv", ws))
    finally:
        task_manager.get_worker_task = orig_get_worker
        agent_db.get_task = orig_get_task

    _assert_true(len(ws.events) == 1, "应推送一个 download 事件")
    evt = ws.events[0]
    _assert_true(evt["event_type"] == "download", "事件类型应为 download")
    _assert_true("/download/report/" in evt["event_data"]["url"], "url 应为报告下载地址")
    _assert_true(evt["report_task_id"] == 999, "应携带 report_task_id")


def test_await_silent_when_worker_failed() -> None:
    """
    入参:
      - 无。
    方法:
      - mock get_task 返回 done 但 output.status=failed。
      - 断言不推送任何 download 事件 (worker 失败静默, 仅日志)。
    出参:
      - None。
    """
    import backend.agent.team.task_manager as task_manager
    from backend.agent.memory import agent_db

    fake_task = {"status": "done", "output": {"status": "failed", "issues": ["回读失败"]}}
    orig_get_worker = task_manager.get_worker_task
    orig_get_task = agent_db.get_task
    try:
        task_manager.get_worker_task = lambda tid: None
        agent_db.get_task = lambda tid: fake_task
        ws = _FakeWS()
        asyncio.run(_await_and_push_report_download(998, "sid", "conv", ws))
    finally:
        task_manager.get_worker_task = orig_get_worker
        agent_db.get_task = orig_get_task

    _assert_true(len(ws.events) == 0, "worker 失败不应推送 download")


def test_dispatch_calls_assign_with_segment_payload() -> None:
    """
    入参:
      - 无。
    方法:
      - mock task_manager.assign_agent_task 记录入参并返回 None (模拟 agent_db 不可用, 提前返回)。
      - 断言 _dispatch_segment_report_worker 用 tool_result.data 组装 payload, 以 report_agent 角色派发,
        并关联 parent_task_id。
    出参:
      - None。
    """
    import backend.agent.team.task_manager as task_manager

    called: Dict[str, Any] = {}

    async def fake_assign(**kw):
        called.update(kw)
        return None

    orig_assign = task_manager.assign_agent_task
    try:
        task_manager.assign_agent_task = fake_assign
        ws = _FakeWS()
        tool_result = {
            "type": "frontend_action",
            "data": {
                "image_path": "/x.tif",
                "classes": "建筑物",
                "polygon_layer": {"workspace": "w", "layer_name": "l"},
                "base_layer": {"workspace": "w", "layer_name": "b"},
                "vector_stats": {"total_features": 1},
            },
        }
        asyncio.run(_dispatch_segment_report_worker(
            tool_result=tool_result,
            parent_task_id=123,
            conversation_id="conv",
            user_id="u",
            session_id="sid",
            ws_manager=ws,
        ))
    finally:
        task_manager.assign_agent_task = orig_assign

    _assert_true(called.get("agent_role") == "report_agent", "应派发 report_agent")
    _assert_true(called.get("parent_task_id") == 123, "应关联 parent_task_id")
    payload = called.get("payload") or {}
    _assert_true(payload.get("task_type") == "segmentation_auto_report", "payload task_type 应为 segmentation_auto_report")
    _assert_true(payload.get("polygon_layer", {}).get("layer_name") == "l", "payload 应含 polygon_layer")
    _assert_true(payload.get("base_layer", {}).get("layer_name") == "b", "payload 应含 base_layer")


if __name__ == "__main__":
    test_await_pushes_download_when_worker_passed()
    test_await_silent_when_worker_failed()
    test_dispatch_calls_assign_with_segment_payload()
    print("chat_service_report_dispatch_test: PASS")
