# -*- coding: utf-8 -*-
"""标准 Agent Harness 协议。

目标：把"一次 Agent 运行"的输入、生命周期、事件输出与具体实现解耦：
- HarnessContext：一次运行的不可变上下文；
- HarnessEvent：统一事件（thinking/tool_call/tool_result/content/done/error）；
- AgentHarness：prepare/run/stop 生命周期。

现有 ChatService 通过 ChatHarness 适配，后续 Planner/Executor 可替换而不改调用方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol

HarnessEmitter = Callable[["HarnessEvent"], Awaitable[None]]


@dataclass
class HarnessContext:
    """一次 Harness 运行上下文。"""

    session_id: str
    conversation_id: str
    user_id: str
    prompt: str
    project_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HarnessEvent:
    """统一 Harness 事件。"""

    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    conversation_id: str = ""
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class AgentHarness(Protocol):
    """Harness 生命周期协议。"""

    name: str

    async def run(self, context: HarnessContext, emit: HarnessEmitter) -> None:
        """执行一次完整运行，通过 emit 输出事件。"""
        ...

    async def stop(self, conversation_id: str, session_id: str = "") -> int:
        """停止指定会话的运行，返回取消数量。"""
        ...
