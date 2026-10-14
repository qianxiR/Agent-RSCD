"""
HTTP/WebSocket 路由层 (按业务域拆分)。

每个子模块导出一个 ``router: APIRouter``, 由 ``backend.main`` 统一挂载。
路由行为与拆分前完全一致, 仅做结构重组。
"""

from backend.api import (  # noqa: F401
    conversations,
    files,
    geoserver,
    internal,
    mcp,
    sandbox,
    skills,
    system,
    tasks,
    workspace,
    ws,
)

__all__ = [
    "conversations",
    "files",
    "geoserver",
    "internal",
    "mcp",
    "sandbox",
    "skills",
    "system",
    "tasks",
    "workspace",
    "ws",
]
