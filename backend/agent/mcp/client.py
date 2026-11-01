# -*- coding: utf-8 -*-
"""MCP 客户端封装（SSE / stdio）。

对每个已启用 MCP 服务器建立短连接：list_tools / call_tool；
连接生命周期由调用方控制，避免服务启动时被不可达服务器阻塞。
"""

from __future__ import annotations

import asyncio
import os
import shlex
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from backend.config import settings


@asynccontextmanager
async def open_session(server: Dict[str, Any]):
    """按 transport 打开 MCP ClientSession。"""
    from mcp import ClientSession

    transport = str(server.get("transport") or "sse").lower()
    if transport == "sse":
        from mcp.client.sse import sse_client

        url = str(server.get("url") or "").strip()
        if not url:
            raise ValueError("MCP sse 服务器缺少 url")
        async with sse_client(url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
    elif transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        parts = shlex.split(str(server.get("command") or ""), posix=os.name != "nt")
        if not parts:
            raise ValueError("MCP stdio 服务器缺少 command")
        # Windows 下 posix=False 会保留包裹引号，去掉首尾引号
        parts = [part.strip('"') for part in parts]
        params = StdioServerParameters(command=parts[0], args=parts[1:])
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
    else:
        raise ValueError(f"不支持的 MCP transport: {transport}")


async def list_server_tools(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """列出 server 的工具定义；超时/连接失败抛异常。"""
    timeout = float(settings.mcp_timeout_seconds or 8)
    async with asyncio.timeout(timeout):
        async with open_session(server) as session:
            result = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": getattr(tool, "inputSchema", None) or {},
                }
                for tool in (result.tools or [])
            ]


async def call_server_tool(
    server: Dict[str, Any],
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """调用 server 工具，把 MCP content 转成可回灌 LLM 的结构。"""
    timeout = float(settings.mcp_timeout_seconds or 8)
    async with asyncio.timeout(timeout):
        async with open_session(server) as session:
            result = await session.call_tool(tool_name, arguments or {})
            content = []
            for item in getattr(result, "content", []) or []:
                text = getattr(item, "text", None)
                if text is not None:
                    content.append({"type": "text", "text": text})
                else:
                    content.append({"type": type(item).__name__, "data": str(item)})
            return {
                "status": "error" if getattr(result, "isError", False) else "success",
                "content": content,
            }
