"""
专业 worker 任务管理 (Agent 层)

复用 agent_db.ai_task 作为唯一任务状态源。当前已支持 verification_agent 和
report_agent, 后续可在同一入口扩展 rs_agent / publish_agent。
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from backend.agent.memory import agent_db
from backend.agent.team.agent_roles import REPORT_AGENT, VERIFICATION_AGENT, is_supported_agent_role
from backend.agent.team.report_worker import run_report_worker
from backend.agent.team.verification_worker import run_verification

logger = logging.getLogger(__name__)

_running_tasks: Dict[int, asyncio.Task] = {}


async def _run_worker_task(task_id: int, agent_role: str, payload: Dict[str, Any]):
    """
    入参:
      - task_id: ai_task 主键。
      - agent_role: worker 角色。
      - payload: worker 输入载荷。
    方法:
      - 更新任务为 running。
      - 根据 agent_role 路由到具体 worker。
      - 写入 output / verification / status / task_log。
      - CancelledError 单独标记 cancelled, 不伪装失败。
    出参:
      - 无返回值, 状态全部落到 ai_task。
    """
    try:
        agent_db.update_task(task_id, status="running", progress=20)
        agent_db.append_task_log(task_id, f"开始执行 worker: {agent_role}")

        if agent_role == VERIFICATION_AGENT:
            result = run_verification(payload)
        elif agent_role == REPORT_AGENT:
            result = run_report_worker(payload)
        else:
            result = {
                "agent_role": agent_role,
                "status": "failed",
                "summary": f"未支持的 worker 角色: {agent_role}",
                "issues": [f"unsupported role: {agent_role}"],
                "recommendation": "report_error",
            }

        # 出口统一给 worker 汇报打上 task_id, 让主控 query_agent_task 时能直接读到关联主键。
        result["task_id"] = task_id
        status = result.get("status")
        db_status = "done" if status in ("passed", "warning") else "failed"
        error = None if db_status == "done" else "; ".join(result.get("issues") or [])
        agent_db.update_task(
            task_id,
            status=db_status,
            progress=100,
            output=result,
            error=error,
            verification=result,
        )
        level = "warning" if status == "warning" else ("error" if db_status == "failed" else "info")
        agent_db.append_task_log(task_id, result.get("summary", "worker 执行完成"), level=level)
    except asyncio.CancelledError:
        agent_db.update_task(task_id, status="cancelled", progress=100, error="worker 任务被取消")
        agent_db.append_task_log(task_id, "worker 任务被取消", level="warning")
        raise
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        logger.error(f"[Team] worker 执行失败 task={task_id}: {message}", exc_info=True)
        agent_db.update_task(task_id, status="failed", progress=100, error=message)
        agent_db.append_task_log(task_id, f"worker 执行异常: {message}", level="error")
    finally:
        _running_tasks.pop(task_id, None)


async def assign_agent_task(
    conversation_id: str,
    user_id: str,
    agent_role: str,
    goal: str,
    payload: Dict[str, Any],
    parent_task_id: Optional[int] = None,
) -> Optional[int]:
    """
    入参:
      - conversation_id: 当前对话 ID。
      - user_id: 当前用户 ID。
      - agent_role: worker 角色, 当前支持 verification_agent 和 report_agent。
      - goal: 子任务目标。
      - payload: worker 输入。
      - parent_task_id: 可选父任务 ID。
    方法:
      - 校验角色白名单。
      - 在 ai_task 创建 pending 记录。
      - 创建后台 asyncio task 执行 worker。
    出参:
      - task_id; 创建失败返回 None。
    """
    if not is_supported_agent_role(agent_role):
        logger.warning(f"[Team] 拒绝未支持 worker 角色: {agent_role}")
        return None

    input_data = {
        "goal": goal,
        "payload": payload or {},
    }
    task_id = agent_db.create_task(
        conversation_id=conversation_id,
        task_type="agent_worker",
        tool_name="assign_agent_task",
        user_id=user_id,
        input_data=input_data,
        agent_role=agent_role,
        parent_task_id=parent_task_id,
        goal=goal,
    )
    if task_id is None:
        return None

    task = asyncio.create_task(
        _run_worker_task(task_id, agent_role, payload or {}),
        name=f"{agent_role}-{task_id}",
    )
    _running_tasks[task_id] = task
    return task_id


def get_worker_task(task_id: int) -> Optional["asyncio.Task"]:
    """
    入参:
      - task_id: ai_task 主键。
    方法:
      - 返回 worker 的 asyncio.Task 句柄; 已完成或不存在返回 None。
      - 供 chat_service 等待 report_agent worker 跑完后推送下载事件, 不暴露内部 dict。
    出参:
      - asyncio.Task 或 None。
    """
    worker = _running_tasks.get(task_id)
    if worker is not None and not worker.done():
        return worker
    return None


def query_agent_task(task_id: int, include_logs: bool = True) -> Dict[str, Any]:
    """
    入参:
      - task_id: ai_task 主键。
      - include_logs: 是否附带 task_log。
    方法:
      - 从 agent_db 读取任务详情。
      - 可选附带日志, 让主控 Agent 理解 worker 执行过程。
    出参:
      - dict, 包含 type/status/data; 不存在时返回 error。
    """
    task = agent_db.get_task(task_id)
    if not task:
        return {"type": "error", "msg": f"未找到 worker 任务: {task_id}"}
    if include_logs:
        task["logs"] = agent_db.get_task_logs(task_id)
    return {
        "type": "success",
        "summary": f"worker 任务 {task_id} 状态: {task.get('status')}",
        "data": task,
    }


async def cancel_agent_task(task_id: int, reason: str = "") -> Dict[str, Any]:
    """
    入参:
      - task_id: ai_task 主键。
      - reason: 取消原因。
    方法:
      - 若后台 task 仍在运行, 调用 cancel。
      - 若任务已结束, 返回当前状态, 不做破坏性回滚。
      - 数据库状态统一写 cancelled。
    出参:
      - dict, 描述取消结果。
    """
    task = agent_db.get_task(task_id)
    if not task:
        return {"type": "error", "msg": f"未找到 worker 任务: {task_id}"}
    if task.get("status") in ("done", "failed", "cancelled"):
        return {
            "type": "success",
            "summary": f"worker 任务 {task_id} 已结束, 当前状态: {task.get('status')}",
            "data": task,
        }

    running = _running_tasks.get(task_id)
    if running and not running.done():
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
    message = reason or "用户或主控 Agent 请求取消"
    agent_db.update_task(task_id, status="cancelled", progress=100, error=message)
    agent_db.append_task_log(task_id, f"取消 worker 任务: {message}", level="warning")
    return {
        "type": "success",
        "summary": f"已取消 worker 任务 {task_id}",
        "data": agent_db.get_task(task_id),
    }
