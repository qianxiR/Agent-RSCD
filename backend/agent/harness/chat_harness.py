# -*- coding: utf-8 -*-
"""ChatHarness：把现有 AgentChatService 适配成标准 Harness。"""

from __future__ import annotations

from typing import Optional

from backend.agent.harness.base import AgentHarness, HarnessContext, HarnessEmitter, HarnessEvent
from backend.agent.mcp.registry import ensure_mcp_tools_loaded
from backend.config import settings


class ChatHarness(AgentHarness):
    """当前主链路的 Harness 适配器。

    保持 ChatService 的 ReAct/Plan-first 行为不变；
    上层只需依赖 HarnessContext + 生命周期，不再直接拼 ToolChatRequest。
    """

    name = "chat"

    def __init__(self, service, ws_manager=None) -> None:
        self._service = service
        self._ws_manager = ws_manager

    async def run(self, context: HarnessContext, emit: HarnessEmitter) -> None:
        """执行一次聊天运行；事件仍由 ChatService 通过 ws_manager 推送。"""
        await ensure_mcp_tools_loaded()
        if context.metadata.get("emit_started"):
            await emit(HarnessEvent(
                "session_started",
                {"conversation_id": context.conversation_id},
                conversation_id=context.conversation_id,
            ))
        from backend.agent.chat_service import ToolChatRequest

        req = ToolChatRequest(
            prompt=context.prompt,
            model=context.metadata.get("model") or settings.chat_model,
            temperature=context.metadata.get("temperature", settings.default_temperature),
            conversation_id=context.conversation_id,
            user_id=context.user_id,
            project_id=context.project_id or None,
            images=context.metadata.get("images") or [],
            selected_layers=context.metadata.get("selected_layers") or [],
            multi_round=context.metadata.get("multi_round", True),
            resume=bool(context.metadata.get("resume", False)),
        )
        await self._service.tool_chat_ws(
            req,
            context.session_id,
            self._ws_manager,
            checkpoint_id=context.metadata.get("checkpoint_id"),
        )

    async def stop(self, conversation_id: str, session_id: str = "") -> int:
        """停止指定会话；未给 session 时遍历活跃 session 精确匹配对话。"""
        if self._ws_manager is None:
            return 0
        if session_id:
            return self._ws_manager.cancel_task(session_id, conversation_id)
        cancelled = 0
        for sid, tasks in list(getattr(self._ws_manager, "active_tasks", {}).items()):
            if conversation_id in tasks:
                cancelled += self._ws_manager.cancel_task(sid, conversation_id)
        return cancelled
