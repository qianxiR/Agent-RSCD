"""Harness 生命周期回归测试。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.harness import ChatHarness, HarnessContext, HarnessEvent  # noqa: E402


class FakeTask:
    def __init__(self):
        self.cancelled = False

    def done(self):
        return self.cancelled

    def cancel(self):
        self.cancelled = True


class FakeService:
    def __init__(self):
        self.calls = []

    async def tool_chat_ws(self, req, session_id, ws_manager):
        self.calls.append((req.prompt, session_id))


class FakeWS:
    def __init__(self):
        self.active_tasks = {"s1": {"c1": FakeTask()}}

    def cancel_task(self, session_id, conversation_id=None):
        task = self.active_tasks.get(session_id, {}).pop(conversation_id, None)
        if task and not task.done():
            task.cancel()
            return 1
        return 0


def test_chat_harness() -> None:
    service = FakeService()
    ws = FakeWS()
    harness = ChatHarness(service, ws)
    events = []

    async def emit(event: HarnessEvent):
        events.append(event.type)

    async def run():
        await harness.run(HarnessContext(
            session_id="s1", conversation_id="c1", user_id="u1",
            prompt="提取建筑", metadata={"images": [{"path": "/tmp/a.tif"}]},
        ), emit)
        return await harness.stop("c1")

    cancelled = asyncio.run(run())
    assert service.calls and service.calls[0][0] == "提取建筑"
    assert events[0] == "session_started"
    assert cancelled == 1


if __name__ == "__main__":
    test_chat_harness()
    print("harness: PASS")
