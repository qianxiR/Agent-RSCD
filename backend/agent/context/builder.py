# -*- coding: utf-8 -*-
"""Context Builder：把上下文组装从记忆实现中拆出。

调用方只依赖 ContextBuilder.build()，不直接依赖 memory_context 的内部结构；
后续可替换为分层上下文（summary/profile/task_state/plan）的独立装配器。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.agent.memory import memory_context


class ContextBuilder:
    """上下文构建器适配层。"""

    def build(
        self,
        conversation_id: str,
        prompt: str,
        llm: Any,
        user_id: str = "",
        images: Optional[List[Dict[str, Any]]] = None,
        tool_names=None,
    ) -> Tuple[List[Any], int]:
        """构建 [System]+history+Human 消息列表，返回 (messages, 本轮起点)。"""
        return memory_context.build_context_messages(
            conversation_id, prompt, llm, user_id=user_id, images=images, tool_names=tool_names
        )

    def persist_turn(self, conversation_id: str, messages: List[Any], start_idx: int) -> None:
        """持久化本轮新增消息。"""
        memory_context.persist_turn(conversation_id, messages, start_idx)


_DEFAULT_BUILDER = ContextBuilder()


def get_context_builder() -> ContextBuilder:
    """返回进程默认 ContextBuilder。"""
    return _DEFAULT_BUILDER
