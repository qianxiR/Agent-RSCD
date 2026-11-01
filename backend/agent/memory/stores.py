# -*- coding: utf-8 -*-
"""分离的记忆存储。

三层记忆各自有独立 store，不再由 memory_context 同时承担：
- WorkingMemoryStore：message 表（工作记忆/工具链）
- SummaryMemoryStore：conversation_summary（短期摘要）
- LongTermMemoryStore：user_memory（长期画像/教训）

memory_context 仍负责编排（load → trim → inject），但存取边界已独立。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.agent.memory import agent_db


class WorkingMemoryStore:
    """工作记忆：会话消息。"""

    def load(self, conversation_id: str, limit: int = 50) -> List[Any]:
        return agent_db.load_messages(conversation_id, limit=limit)

    def last_message_id(self, conversation_id: str) -> int:
        return agent_db.get_last_message_id(conversation_id)


class SummaryMemoryStore:
    """短期记忆：会话摘要。"""

    def load(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return agent_db.load_summary(conversation_id)

    def save(
        self,
        conversation_id: str,
        summary: str,
        summarized_upto: int,
        compress_level: int = 0,
    ) -> bool:
        return agent_db.save_summary(
            conversation_id, summary, summarized_upto, compress_level=compress_level
        )


class LongTermMemoryStore:
    """长期记忆：用户画像 / 教训。"""

    def load(self, user_id: str, category: str = None) -> List[Dict[str, Any]]:
        return agent_db.load_user_memory(user_id, category=category)

    def save(self, user_id: str, key: str, value: str, category: str = "preference") -> bool:
        return agent_db.save_user_memory(user_id, key, value, category=category)

    def delete(self, user_id: str, key: str) -> bool:
        return agent_db.delete_user_memory(user_id, key)


@dataclass
class MemoryStores:
    """三层记忆 store 容器，便于依赖注入。"""

    working: WorkingMemoryStore
    summary: SummaryMemoryStore
    long_term: LongTermMemoryStore


_DEFAULT_STORES = MemoryStores(
    working=WorkingMemoryStore(),
    summary=SummaryMemoryStore(),
    long_term=LongTermMemoryStore(),
)


def get_memory_stores() -> MemoryStores:
    """返回进程默认三层记忆存储。"""
    return _DEFAULT_STORES
