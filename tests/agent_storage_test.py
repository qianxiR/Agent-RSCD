"""Prompt/Context/Memory 拆分存储回归测试。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.context.builder import ContextBuilder  # noqa: E402
from backend.agent.memory import memory_context  # noqa: E402
from backend.agent.memory.stores import get_memory_stores  # noqa: E402
from backend.agent.prompt_store import PromptStore  # noqa: E402


def test_prompt_store() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = PromptStore(tmp)
        store.put("greeting", "你好，{name}", version="1.0")
        assert "greeting" in store.list_templates()
        assert store.render("greeting", name="解译员") == "你好，解译员"
        assert store.get("missing") is None
        from backend.agent.prompt_store import get_prompt_store
        assert get_prompt_store().get("static_template2") is not None


def test_context_builder_delegates() -> None:
    original = memory_context.build_context_messages
    captured = {}

    def fake(conversation_id, prompt, llm, user_id="", images=None):
        captured.update({"conversation_id": conversation_id, "prompt": prompt, "images": images})
        return ["msg"], 0

    memory_context.build_context_messages = fake
    try:
        messages, idx = ContextBuilder().build("c1", "你好", None, user_id="u1", images=[{"path": "a"}])
        assert messages == ["msg"] and idx == 0
        assert captured["conversation_id"] == "c1"
        assert captured["prompt"] == "你好"
    finally:
        memory_context.build_context_messages = original


def test_memory_stores() -> None:
    stores = get_memory_stores()
    assert stores.working is not None
    assert stores.summary is not None
    assert stores.long_term is not None


if __name__ == "__main__":
    test_prompt_store()
    test_context_builder_delegates()
    test_memory_stores()
    print("agent_storage: PASS")
