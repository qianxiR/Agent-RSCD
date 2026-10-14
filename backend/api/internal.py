"""内部接口路由 (/internal/*) — 仅供 Java 业务服务代理调用。

★ 双服务架构 (Java 管业务 + Python 管 AI): 会话/消息/任务数据归 Python,
  Java 侧 ConversationProxyController / TaskProxyController 将前端请求转发到此处。
  本路由与对外路由 (conversations.py / tasks.py) 的差异仅在:
  1. 认证方式: X-Internal-Token 请求头 (静态令牌, 不走用户 token)
  2. 复用实现: 端点函数直接调用对外路由的同名实现, 保证两端行为完全一致

  /internal 前缀只允许本机回环/内网访问, 生产环境 nginx 不转发该前缀。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from backend.api import conversations, tasks as tasks_router
from backend.api.schemas import (
    ForkRequest,
    RegenerateRequest,
    UpdateConversationRequest,
    UpdateConversationTraceRequest,
)
from backend.agent.memory import agent_db
from backend.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_internal_token(x_internal_token: Optional[str] = Header(default=None)) -> None:
    """内部令牌校验: 不匹配返回 401 (FailFast, 不落入业务逻辑)。"""
    if x_internal_token != settings.internal_api_token:
        raise HTTPException(status_code=401, detail="internal token 无效或缺失")


class DetachProjectRequest(BaseModel):
    """项目删除时的会话解绑请求 (原 conversation.project_id 外键的 SET NULL 替代)。"""
    project_id: str


# ==================== 会话 CRUD (转发到 conversations.py 实现) ====================

@router.get("/internal/conversations", dependencies=[Depends(_require_internal_token)])
async def internal_list_conversations(user_id: str = "study_user", project_id: str = None):
    return await conversations.list_conversations(user_id=user_id, project_id=project_id)


@router.get("/internal/conversations/{conversation_id}/messages",
            dependencies=[Depends(_require_internal_token)])
async def internal_conversation_messages(conversation_id: str):
    return await conversations.get_conversation_messages(conversation_id)


@router.patch("/internal/conversations/{conversation_id}",
              dependencies=[Depends(_require_internal_token)])
async def internal_update_conversation(conversation_id: str, req: UpdateConversationRequest):
    return await conversations.update_conversation(conversation_id, req)


@router.put("/internal/conversations/{conversation_id}/trace",
            dependencies=[Depends(_require_internal_token)])
async def internal_update_trace(conversation_id: str, req: UpdateConversationTraceRequest):
    return await conversations.update_conversation_trace(conversation_id, req)


@router.delete("/internal/conversations/{conversation_id}",
               dependencies=[Depends(_require_internal_token)])
async def internal_delete_conversation(conversation_id: str):
    """删除会话 (含沙盒/磁盘回收编排, 编排逻辑整体留在 Python)。"""
    return await conversations.delete_conversation(conversation_id)


@router.post("/internal/conversations/{conversation_id}/fork",
             dependencies=[Depends(_require_internal_token)])
async def internal_fork(conversation_id: str, req: ForkRequest):
    return await conversations.fork_conversation(conversation_id, req)


@router.post("/internal/conversations/{conversation_id}/regenerate",
             dependencies=[Depends(_require_internal_token)])
async def internal_regenerate(conversation_id: str, req: RegenerateRequest):
    return await conversations.regenerate_from_node(conversation_id, req)


@router.get("/internal/conversations/{conversation_id}/branches",
            dependencies=[Depends(_require_internal_token)])
async def internal_branches(conversation_id: str):
    return await conversations.get_branch_info(conversation_id)


@router.post("/internal/conversations/detach-project",
             dependencies=[Depends(_require_internal_token)])
async def internal_detach_project(req: DetachProjectRequest):
    """
    项目删除前的会话解绑: 把该项目的全部会话 project_id 置 NULL。
    替代原 conversation→project 外键的 ON DELETE SET NULL (外键已随项目表迁移移除)。
    """
    count = agent_db.detach_project_conversations(req.project_id)
    logger.info(f"[Internal] 项目 {req.project_id[:8]}... 会话解绑: {count} 条")
    return {"status": "success", "detached": count}


# ==================== 任务查询 (转发到 tasks.py 实现) ====================

@router.get("/internal/tasks", dependencies=[Depends(_require_internal_token)])
async def internal_list_tasks(conversation_id: str = None, status: str = None,
                              user_id: str = None, limit: int = 50):
    return await tasks_router.list_tasks(conversation_id=conversation_id, status=status,
                                         user_id=user_id, limit=limit)


@router.get("/internal/tasks/{task_id}", dependencies=[Depends(_require_internal_token)])
async def internal_get_task(task_id: int):
    return await tasks_router.get_task(task_id)


@router.get("/internal/tasks/{task_id}/logs", dependencies=[Depends(_require_internal_token)])
async def internal_task_logs(task_id: int, limit: int = 200):
    return await tasks_router.get_task_logs(task_id, limit=limit)
