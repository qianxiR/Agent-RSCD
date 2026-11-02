# -*- coding: utf-8 -*-
"""Agent Run 运行时快照。

目标：不只保存聊天消息，还保存一次 Agent 运行的可恢复状态：
- 当前迭代轮次；
- 计划 / 任务状态；
- 当前绑定的工具；
- 本轮已产生的 System/Human/AI/Tool 消息；
- 持久化起点（避免恢复后重复写消息）。

快照在每个工具批次结束后落库；进程重启或重连后可通过 resume 继续 LLM 循环。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from backend.agent.memory import agent_db


def _json_safe(value: Any) -> Any:
    """把消息附加字段压成可 JSON 序列化的结构。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def serialize_message(message: BaseMessage) -> Dict[str, Any]:
    """LangChain 消息 → 可存 JSONB 的 dict。"""
    data: Dict[str, Any] = {
        "type": getattr(message, "type", "human"),
        "content": getattr(message, "content", ""),
        "additional_kwargs": _json_safe(getattr(message, "additional_kwargs", {}) or {}),
    }
    if isinstance(message, AIMessage):
        data["tool_calls"] = _json_safe(getattr(message, "tool_calls", []) or [])
        data["id"] = getattr(message, "id", None)
    if isinstance(message, ToolMessage):
        data["tool_call_id"] = getattr(message, "tool_call_id", "")
        data["name"] = getattr(message, "name", "")
    return data


def deserialize_message(data: Dict[str, Any]) -> BaseMessage:
    """JSONB dict → LangChain 消息。"""
    msg_type = data.get("type", "human")
    content = data.get("content", "")
    kwargs = {"additional_kwargs": data.get("additional_kwargs") or {}}
    if msg_type == "system":
        return SystemMessage(content=content, **kwargs)
    if msg_type == "ai":
        return AIMessage(
            content=content,
            tool_calls=data.get("tool_calls") or [],
            id=data.get("id"),
            **kwargs,
        )
    if msg_type == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=data.get("tool_call_id", ""),
            name=data.get("name", ""),
            **kwargs,
        )
    return HumanMessage(content=content, **kwargs)


@dataclass
class RunSnapshot:
    """一次 Agent 运行的可恢复状态。"""

    run_id: str
    conversation_id: str
    session_id: str = ""
    status: str = "running"
    iteration: int = 0
    checkpoint_id: Optional[int] = None
    persist_start_idx: int = 0
    active_tool_names: List[str] = field(default_factory=list)
    plan: Optional[Dict[str, Any]] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    last_tool: Optional[Dict[str, Any]] = None
    error: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def new(cls, conversation_id: str, session_id: str = "") -> "RunSnapshot":
        return cls(run_id=f"run_{uuid.uuid4().hex}", conversation_id=conversation_id, session_id=session_id)


_TABLE_READY = False


def _ensure_table() -> bool:
    """懒创建 agent_run 表（不依赖全量 schema 初始化）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return True
    with agent_db.get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS agent_run (
                        run_id            TEXT PRIMARY KEY,
                        conversation_id   TEXT NOT NULL,
                        session_id        TEXT,
                        status            TEXT NOT NULL DEFAULT 'running',
                        iteration         INT DEFAULT 0,
                        checkpoint_id     BIGINT,
                        persist_start_idx INT DEFAULT 0,
                        active_tool_names JSONB,
                        plan              JSONB,
                        messages          JSONB,
                        last_tool         JSONB,
                        error             TEXT,
                        updated_at        TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_agent_run_conv
                    ON agent_run(conversation_id, updated_at DESC)
                """)
            conn.commit()
            _TABLE_READY = True
            return True
        except Exception:
            conn.rollback()
            return False


def save_snapshot(snapshot: RunSnapshot) -> bool:
    """UPSERT 运行时快照。"""
    if not _ensure_table():
        return False
    _, _, Json = agent_db._get_psycopg2()
    if Json is None:
        return False
    with agent_db.get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_run (
                           run_id, conversation_id, session_id, status, iteration,
                           checkpoint_id, persist_start_idx, active_tool_names,
                           plan, messages, last_tool, error, updated_at
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                       ON CONFLICT (run_id) DO UPDATE SET
                           status = EXCLUDED.status,
                           iteration = EXCLUDED.iteration,
                           checkpoint_id = EXCLUDED.checkpoint_id,
                           persist_start_idx = EXCLUDED.persist_start_idx,
                           active_tool_names = EXCLUDED.active_tool_names,
                           plan = EXCLUDED.plan,
                           messages = EXCLUDED.messages,
                           last_tool = EXCLUDED.last_tool,
                           error = EXCLUDED.error,
                           updated_at = now()""",
                    (
                        snapshot.run_id, snapshot.conversation_id, snapshot.session_id,
                        snapshot.status, snapshot.iteration, snapshot.checkpoint_id,
                        snapshot.persist_start_idx, Json(snapshot.active_tool_names),
                        Json(snapshot.plan or {}), Json(snapshot.messages),
                        Json(snapshot.last_tool or {}), snapshot.error,
                    ),
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False


def load_resumable(conversation_id: str) -> Optional[RunSnapshot]:
    """读取最近一个可恢复快照（running / paused）。"""
    if not _ensure_table():
        return None
    with agent_db.get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT run_id, conversation_id, session_id, status, iteration,
                              checkpoint_id, persist_start_idx, active_tool_names,
                              plan, messages, last_tool, error, updated_at
                       FROM agent_run
                       WHERE conversation_id = %s AND status IN ('running', 'paused')
                       ORDER BY updated_at DESC LIMIT 1""",
                    (conversation_id,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return RunSnapshot(
                run_id=row[0], conversation_id=row[1], session_id=row[2] or "",
                status=row[3], iteration=row[4] or 0, checkpoint_id=row[5],
                persist_start_idx=row[6] or 0,
                active_tool_names=list(row[7] or []),
                plan=row[8] or None,
                messages=list(row[9] or []),
                last_tool=row[10] or None,
                error=row[11] or "",
                updated_at=row[12].isoformat() if row[12] else "",
            )
        except Exception:
            return None


def mark_status(run_id: str, status: str, error: str = "") -> bool:
    """更新快照状态：running / paused / completed / stopped / failed。"""
    if not _ensure_table():
        return False
    with agent_db.get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_run SET status = %s, error = %s, updated_at = now() WHERE run_id = %s",
                    (status, error[:500], run_id),
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
