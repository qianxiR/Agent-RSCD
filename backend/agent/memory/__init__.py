"""
记忆子包 (Agent 层 / Memory) — 记忆管理 + 记忆数据库

记忆系统是智能体层的核心组成, 包含「编排逻辑」与「数据库」两部分:
  - memory_context.py : 上下文构建 / 持久化编排 / 摘要 / 长期记忆提取
  - agent_db.py       : 记忆数据库客户端 (连接池 + 会话/消息/记忆/项目 CRUD)

★ 记忆数据库归入 agent 层 (而非 data 层):
  记忆是智能体自身的状态, 与业务数据 (data/business_db) 职责完全不同。
  data 层只承载外部业务库与 GeoServer, 不承载智能体内部状态。

三层记忆 (对应工业级 harness 的标准分层):
  1. 长期记忆 (long-term): user_memory 表, 跨会话偏好
  2. 短期记忆 (summary):   conversation_summary 表, 本会话旧消息摘要
  3. 工作记忆 (buffer):    message 表近期消息 (含 tool_calls)

本 __init__ re-export 编排函数与 agent_db, 保持以下老调用兼容:
  from backend.agent.memory import build_context_messages, persist_turn, ...
  from backend.agent.memory import agent_db
"""
from backend.agent.memory import agent_db
from backend.agent.memory.memory_context import (
    build_context_messages,
    persist_user_message,
    persist_turn,
    maybe_summarize,
    extract_and_save_long_term_memory,
    build_user_profile_text,
    debug_dump_history,
)

__all__ = [
    "agent_db",
    "build_context_messages",
    "persist_user_message",
    "persist_turn",
    "maybe_summarize",
    "extract_and_save_long_term_memory",
    "build_user_profile_text",
    "debug_dump_history",
]
