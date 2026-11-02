"""
Runtime 模块 (Agent 层) — 运行状态管理 / 上下文模块管理
- context_vars: 运行时上下文 (ContextVar), 多对话并行的会话/用户隔离
- run_state: Agent 运行时快照（消息/计划/工具/迭代），支持断点恢复
"""
from backend.agent.runtime.context_vars import (
    set_runtime_context,
    get_current_conversation_id,
    get_current_user_id,
    get_current_project_id,
)
from backend.agent.runtime.run_state import (
    RunSnapshot,
    deserialize_message,
    load_resumable,
    mark_status,
    save_snapshot,
    serialize_message,
)

__all__ = [
    "set_runtime_context",
    "get_current_conversation_id",
    "get_current_user_id",
    "get_current_project_id",
    "RunSnapshot",
    "deserialize_message",
    "load_resumable",
    "mark_status",
    "save_snapshot",
    "serialize_message",
]
