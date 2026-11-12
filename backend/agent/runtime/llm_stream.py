# -*- coding: utf-8 -*-
"""LLM 流式调用 (Agent 层 / Runtime)

把 chat_service 里的流式实现抽成可复用函数，供自由 ReAct 与 Plan-and-Execute
两条链路共用：
- 逐 chunk 推送 thinking 事件；
- 合并 chunk 为完整 AIMessage；
- 可选 on_partial 回调用于运行快照。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, List, Optional

from langchain_core.messages import AIMessage, AIMessageChunk

from backend.config import settings

logger = logging.getLogger(__name__)

EmitFunc = Callable[[dict], Awaitable[None]]


def _json_safe_tool_chunks(chunks: List[Any]) -> List[dict]:
    """把流式 tool_call_chunks 转成可 JSON 序列化的 dict 列表。"""
    safe = []
    for chunk in chunks:
        item = chunk if isinstance(chunk, dict) else getattr(chunk, "__dict__", {})
        safe.append({
            "index": item.get("index"),
            "name": item.get("name"),
            "args": item.get("args"),
            "id": item.get("id"),
        })
    return safe


async def stream_llm(
    llm,
    messages: List,
    emit: Optional[EmitFunc] = None,
    on_partial: Optional[Callable[[str, list, list], None]] = None,
    conversation_id: str = "",
) -> AIMessage:
    """
    入参:
      - llm: 已绑定工具或未绑定的 LLM 实例。
      - messages: 完整消息列表。
      - emit: 异步事件回调, 接收 {"type": "thinking", "content": ...}。
      - on_partial: 流式快照回调 (每 8 个 chunk 一次)。
      - conversation_id: 仅用于缓存命中日志。
    方法:
      - astream 逐 chunk 推送 content; 合并为完整 AIMessage。
      - 每个 chunk 之间显式让出事件循环, 保证取消可响应。
    出参:
      - AIMessage (含 content、tool_calls、thinking_content)。
    """
    collected_chunks: List[AIMessageChunk] = []
    partial_content = ""
    partial_chunks: List[str] = []
    partial_tool_calls: List[Any] = []

    async for chunk in llm.astream(messages):
        collected_chunks.append(chunk)

        if chunk.content:
            partial_content += chunk.content
            partial_chunks.append(chunk.content)
            if emit is not None:
                await emit({
                    "type": "thinking",
                    "content": chunk.content,
                    "conversation_id": conversation_id,
                })

        tool_chunks = getattr(chunk, "tool_call_chunks", None) or []
        if tool_chunks:
            partial_tool_calls.extend(_json_safe_tool_chunks(tool_chunks))

        if on_partial is not None and len(collected_chunks) % 8 == 0:
            on_partial(partial_content, partial_tool_calls, partial_chunks)

        await asyncio.sleep(0)

    if not collected_chunks:
        return AIMessage(content="")

    full_message = collected_chunks[0]
    for chunk in collected_chunks[1:]:
        full_message = full_message + chunk

    if settings.enable_context_cache and settings.log_cache_hit_rate:
        usage = getattr(full_message, "usage_metadata", None)
        if usage:
            cached = (usage.get("input_token_details") or {}).get("cache_read", 0)
            total_in = usage.get("input_tokens", 0)
            rate = cached / total_in * 100 if total_in else 0
            logger.info(
                f"[Cache] conv={conversation_id} hit={cached}/{total_in} ({rate:.0f}%) "
                f"output={usage.get('output_tokens', 0)}"
            )

    return AIMessage(
        content=full_message.content,
        tool_calls=full_message.tool_calls,
        id=full_message.id,
        additional_kwargs={
            **(getattr(full_message, "additional_kwargs", {}) or {}),
            "thinking_content": full_message.content or "",
        },
    )
