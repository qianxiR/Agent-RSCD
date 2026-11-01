# -*- coding: utf-8 -*-
"""MCP 工具动态注册。

流程：
1. 从 agent_db 读取 enabled MCP 服务器；
2. 逐个 list_tools；
3. 把每个远程工具包装成 LangChain StructuredTool，注册到 agent.tools 注册表；
4. 刷新时先移除旧的 mcp__* 工具，避免重名。

远程工具统一接收一个 arguments 对象（MCP inputSchema 在 description 中展示），
由 LLM 按 schema 传参。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from backend.agent.mcp.client import call_server_tool, list_server_tools
from backend.agent.tools.registry import (
    get_all_tools,
    register_dynamic_tool,
    unregister_tool,
)
from backend.config import settings

logger = logging.getLogger(__name__)

_MCP_TOOL_NAMES: set[str] = set()
_LAST_LOAD_AT = 0.0
_LOAD_LOCK = asyncio.Lock()
_LOAD_TTL = 300.0


def _safe_name(server_name: str, tool_name: str) -> str:
    """生成 LLM 函数名安全、可读的 mcp__server__tool。"""
    raw = f"mcp__{server_name}__{tool_name}"
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw)[:64]


def _server_configs() -> List[Dict[str, Any]]:
    """读取并过滤已启用 MCP 服务器。"""
    from backend.agent.memory import agent_db

    servers = agent_db.list_mcp_servers() or []
    return [item for item in servers if item.get("enabled")]


def _build_tool(server: Dict[str, Any], tool_def: Dict[str, Any]):
    """把 MCP tool 定义包装成 async StructuredTool。"""
    from langchain_core.tools import StructuredTool

    server_name = str(server.get("name") or server.get("id") or "server")
    remote_name = str(tool_def.get("name") or "")
    safe = _safe_name(server_name, remote_name)
    schema = tool_def.get("input_schema") or {}
    description = (
        f"MCP 工具 {remote_name}（服务器 {server_name}）。"
        f"参数 schema: {json.dumps(schema, ensure_ascii=False)[:1200]}"
    )

    async def _invoke(arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await call_server_tool(server, remote_name, arguments or {})

    return StructuredTool.from_function(
        coroutine=_invoke,
        name=safe,
        description=description,
    )


async def refresh_mcp_tools(force: bool = False) -> Dict[str, Any]:
    """刷新所有 MCP 工具；返回每个服务器的状态与工具数。"""
    global _LAST_LOAD_AT
    if not settings.mcp_enabled:
        return {"status": "disabled", "servers": []}
    async with _LOAD_LOCK:
        if not force and time.time() - _LAST_LOAD_AT < _LOAD_TTL:
            return {"status": "cached", "tools": sorted(_MCP_TOOL_NAMES)}

        for name in list(_MCP_TOOL_NAMES):
            unregister_tool(name)
        _MCP_TOOL_NAMES.clear()

        results = []
        for server in _server_configs():
            server_name = str(server.get("name") or server.get("id") or "server")
            try:
                tools = await list_server_tools(server)
                registered = 0
                for tool_def in tools:
                    if not tool_def.get("name"):
                        continue
                    wrapped = _build_tool(server, tool_def)
                    tool_name = register_dynamic_tool(wrapped, category="mcp", expose=True)
                    _MCP_TOOL_NAMES.add(tool_name)
                    registered += 1
                results.append({
                    "server": server_name,
                    "status": "connected",
                    "tools": registered,
                })
            except Exception as exc:
                logger.warning(f"[MCP] 服务器 {server_name} 刷新失败: {exc}")
                results.append({
                    "server": server_name,
                    "status": "error",
                    "error": str(exc),
                    "tools": 0,
                })

        _LAST_LOAD_AT = time.time()
        return {"status": "refreshed", "servers": results, "tools": sorted(_MCP_TOOL_NAMES)}


async def ensure_mcp_tools_loaded() -> None:
    """聊天前按 TTL 懒加载；失败不阻断聊天。"""
    if not settings.mcp_enabled:
        return
    if time.time() - _LAST_LOAD_AT < _LOAD_TTL:
        return
    try:
        await refresh_mcp_tools()
    except Exception as exc:
        logger.warning(f"[MCP] 懒加载失败: {exc}")


def list_mcp_tools() -> List[Dict[str, Any]]:
    """返回当前注册的 MCP 工具列表。"""
    names = set(_MCP_TOOL_NAMES)
    return [
        {"name": name, "description": getattr(tool, "description", "")}
        for name, tool in get_all_tools().items()
        if name in names
    ]
