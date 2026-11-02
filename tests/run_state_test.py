"""运行时快照与 checkpoint 回滚回归测试。"""
import sys
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.memory import agent_db
from backend.agent.runtime.run_state import (
    RunSnapshot,
    load_resumable,
    mark_status,
    save_snapshot,
    serialize_message,
)


def test_snapshot_roundtrip() -> None:
    if not agent_db.agent_db_available():
        print("run_state: SKIP (db unavailable)")
        return
    cid = "run_probe_" + uuid.uuid4().hex[:8]
    assert agent_db.create_conversation(cid, "probe")
    try:
        agent_db.append_message(cid, HumanMessage(content="恢复测试"))
        checkpoint = agent_db.get_last_message_id(cid)
        snapshot = RunSnapshot.new(cid, "s1")
        snapshot.checkpoint_id = checkpoint
        snapshot.persist_start_idx = 2
        snapshot.active_tool_names = ["search_tools", "load_tool"]
        snapshot.messages = [serialize_message(HumanMessage(content="恢复测试"))]
        snapshot.tasks = [{"id": 1, "status": "running"}]
        snapshot.stream_state = {"content": "partial", "chunks": ["par", "tial"], "tool_calls": []}
        assert save_snapshot(snapshot)
        loaded = load_resumable(cid)
        assert loaded is not None
        assert loaded.run_id == snapshot.run_id
        assert loaded.messages[0]["content"] == "恢复测试"
        assert loaded.tasks[0]["status"] == "running"
        assert loaded.stream_state["content"] == "partial"
        assert loaded.stream_state["chunks"] == ["par", "tial"]
        assert mark_status(snapshot.run_id, "completed")
        assert load_resumable(cid) is None
    finally:
        agent_db.delete_conversation(cid)


def test_rollback_restores_active_leaf() -> None:
    if not agent_db.agent_db_available():
        return
    cid = "rollback_probe_" + uuid.uuid4().hex[:8]
    assert agent_db.create_conversation(cid, "probe")
    try:
        agent_db.append_message(cid, HumanMessage(content="第一轮"))
        checkpoint = agent_db.get_last_message_id(cid)
        node = agent_db.get_active_leaf_node(cid)
        agent_db.append_message(cid, AIMessage(content="已完成"))
        agent_db.append_message(cid, AIMessage(content="应回滚"))
        assert agent_db.rollback_to_checkpoint(cid, checkpoint) == 2
        assert agent_db.get_active_leaf_node(cid) == node
        assert len(agent_db.load_messages(cid)) == 1
    finally:
        agent_db.delete_conversation(cid)


if __name__ == "__main__":
    test_snapshot_roundtrip()
    test_rollback_restores_active_leaf()
    print("run_state: PASS")
