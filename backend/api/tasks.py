"""AI 任务查询路由 (/api/v1/tasks)。

这些端点只读 ai_task / task_log, 供前端任务监控面板或排查问题用。
"""
from fastapi import APIRouter

from backend.api.deps import agent_db

router = APIRouter()


@router.get("/api/v1/tasks")
async def list_tasks(
    conversation_id: str = None,
    status: str = None,
    user_id: str = None,
    limit: int = 50,
):
    """
    列出 AI 任务 (按创建时间倒序). 可按 conversation_id / status / user_id 过滤.
    - status: pending / running / done / failed / cancelled
    - 返回简表 (不含 input/output 详情)
    """
    if not agent_db.agent_db_available():
        return {"status": "error", "msg": "agent_db 不可用, 任务功能未启用"}
    tasks = agent_db.list_tasks(
        conversation_id=conversation_id, status=status, user_id=user_id, limit=limit
    )
    return {"status": "success", "count": len(tasks), "tasks": tasks}


@router.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: int):
    """查询单个任务详情 (含 input/output/error/时间戳)."""
    if not agent_db.agent_db_available():
        return {"status": "error", "msg": "agent_db 不可用, 任务功能未启用"}
    task = agent_db.get_task(task_id)
    if task is None:
        return {"status": "error", "msg": f"任务不存在: {task_id}"}
    return {"status": "success", "task": task}


@router.get("/api/v1/tasks/{task_id}/logs")
async def get_task_logs(task_id: int, limit: int = 200):
    """查询某任务的执行日志 (按时间正序)."""
    if not agent_db.agent_db_available():
        return {"status": "error", "msg": "agent_db 不可用, 任务功能未启用"}
    logs = agent_db.get_task_logs(task_id, limit=limit)
    return {"status": "success", "count": len(logs), "logs": logs}
