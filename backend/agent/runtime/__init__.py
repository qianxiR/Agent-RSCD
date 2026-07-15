"""
Runtime 模块 (Agent 层) — 运行状态管理 / 上下文模块管理
- context_vars: 运行时上下文 (ContextVar), 多对话并行的会话/用户隔离
"""
from backend.agent.runtime.context_vars import (
    set_runtime_context,
    get_current_conversation_id,
    get_current_user_id,
    get_current_project_id,
)

__all__ = [
    "set_runtime_context",
    "get_current_conversation_id",
    "get_current_user_id",
    "get_current_project_id",
]
