"""MCP 服务器管理路由 (/api/v1/mcp/servers)。

MCP (Model Context Protocol) 服务器配置的增删改查。
★ 定位说明 (2026-10 首期): 本路由提供配置管理与面板数据源;
  MCP 协议客户端接入推理链路 (工具动态挂载) 是后续工程,
  当前面板状态列展示「已配置」, 不做连接探测。
"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from backend.agent.memory import agent_db

router = APIRouter()
logger = logging.getLogger(__name__)

_VALID_TRANSPORTS = ("sse", "stdio")


class CreateMcpServerRequest(BaseModel):
    """新增 MCP 服务器。transport 与连接参数的匹配关系由校验器保证。"""
    name: str
    transport: str = "sse"
    url: str = None
    command: str = None
    description: str = ""

    @field_validator("transport")
    @classmethod
    def _check_transport(cls, v: str) -> str:
        if v not in _VALID_TRANSPORTS:
            raise ValueError(f"transport 必须是 {'/'.join(_VALID_TRANSPORTS)}")
        return v


class UpdateMcpServerRequest(BaseModel):
    """更新 MCP 服务器 (本期仅启用开关)。"""
    enabled: bool


@router.get("/api/v1/mcp/servers")
async def list_servers():
    """列出全部 MCP 服务器配置 (按创建时间倒序)。"""
    servers = agent_db.list_mcp_servers()
    return {"status": "success", "count": len(servers), "servers": servers}


@router.post("/api/v1/mcp/servers")
async def create_server(req: CreateMcpServerRequest):
    """新增 MCP 服务器。sse 必填 url; stdio 必填 command。"""
    if req.transport == "sse" and not (req.url or "").strip():
        return {"status": "error", "msg": "sse 传输必须填写 URL"}
    if req.transport == "stdio" and not (req.command or "").strip():
        return {"status": "error", "msg": "stdio 传输必须填写启动命令"}
    server_id = agent_db.create_mcp_server(
        name=req.name.strip(), transport=req.transport,
        url=(req.url or "").strip() or None,
        command=(req.command or "").strip() or None,
        description=req.description or "",
    )
    if server_id:
        return {"status": "success", "server_id": server_id}
    return {"status": "error", "msg": "数据库不可用"}


@router.patch("/api/v1/mcp/servers/{server_id}")
async def update_server(server_id: str, req: UpdateMcpServerRequest):
    """切换 MCP 服务器启用状态。"""
    if agent_db.update_mcp_server_enabled(server_id, req.enabled):
        return {"status": "success"}
    return {"status": "error", "msg": "服务器不存在或数据库不可用"}


@router.delete("/api/v1/mcp/servers/{server_id}")
async def delete_server(server_id: str):
    """删除 MCP 服务器配置。"""
    if agent_db.delete_mcp_server(server_id):
        return {"status": "success"}
    return {"status": "error", "msg": "服务器不存在或数据库不可用"}
