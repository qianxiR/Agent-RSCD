"""
多 Agent 调度工具 (Agent 层 / 工具)

这些工具由主控 Agent 调用, 用于派发、查询和取消专业 worker 任务。
第一阶段只开放 verification_agent, 不允许 worker 递归派发子任务。
"""

from typing import Any, Dict, Optional

from langchain_core.tools import tool

from backend.agent.runtime.context_vars import (
    get_current_conversation_id,
    get_current_user_id,
)
import backend.agent.team.task_manager as task_manager
from backend.agent.team.agent_roles import VERIFICATION_AGENT, is_supported_agent_role
from backend.model.tools.tool_registry import register_tool


@register_tool("agent_team")
@tool
async def assign_agent_task(
    agent_role: str,
    goal: str,
    payload: Dict[str, Any],
    parent_task_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    派发一个专业 worker 任务。主控 Agent 可派发 verification_agent 做客观验收;
    report_agent 由 chat_service 在 segment_image 成功后自动派发, 主控无须手动派发,
    如需查阅报告产物应使用 query_agent_task。

    入参:
        - agent_role: worker 角色。主控可派发 verification_agent; report_agent 由系统自动派发。
        - goal: 子任务目标, 应明确验收对象和成功标准。
        - payload: worker 输入。verification_agent 推荐包含 user_goal、tool_name、
          tool_result、artifact_path 或 artifact_paths。
        - parent_task_id: 可选父任务 ID, 用于关联重工具任务。
    方法:
        - 从运行时上下文读取 conversation_id / user_id。
        - 创建 ai_task 记录并启动后台 worker。
        - 只返回 task_id, 主控 Agent 应调用 query_agent_task 获取结果。
    出参:
        - success: {task_id, agent_role, status=pending}
        - error: 参数错误、角色未开放或数据库不可用。
    """
    conv_id = get_current_conversation_id()
    user_id = get_current_user_id()
    if not conv_id:
        return {"type": "error", "msg": "当前没有 conversation_id, 无法派发 worker 任务"}
    if not is_supported_agent_role(agent_role):
        return {
            "type": "error",
            "msg": f"未支持的 worker 角色: {agent_role}。当前可选: {VERIFICATION_AGENT}",
        }
    if not goal or not goal.strip():
        return {"type": "error", "msg": "worker 任务 goal 不能为空"}
    if not isinstance(payload, dict):
        return {"type": "error", "msg": "payload 必须是对象"}

    task_id = await task_manager.assign_agent_task(
        conversation_id=conv_id,
        user_id=user_id,
        agent_role=agent_role,
        goal=goal.strip(),
        payload=payload,
        parent_task_id=parent_task_id,
    )
    if task_id is None:
        return {"type": "error", "msg": "创建 worker 任务失败, agent_db 可能不可用"}
    return {
        "type": "success",
        "summary": f"已派发 {agent_role} 任务, task_id={task_id}",
        "data": {
            "task_id": task_id,
            "agent_role": agent_role,
            "status": "pending",
        },
    }


@register_tool("agent_team")
@tool
def query_agent_task(task_id: int, include_logs: bool = True) -> Dict[str, Any]:
    """
    查询专业 worker 任务状态和输出。

    入参:
        - task_id: assign_agent_task 返回的任务 ID。
        - include_logs: 是否返回 task_log, 默认 True。
    方法:
        - 读取 ai_task 详情和可选日志。
        - 主控 Agent 应依据 output.status 与 verification 判断是否继续。
    出参:
        - success: 返回任务详情、输出、verification、日志。
        - error: 任务不存在或数据库不可用。
    """
    return task_manager.query_agent_task(task_id, include_logs=include_logs)


@register_tool("agent_team")
@tool
async def cancel_agent_task(task_id: int, reason: str = "") -> Dict[str, Any]:
    """
    取消专业 worker 任务。

    入参:
        - task_id: worker 任务 ID。
        - reason: 取消原因, 可为空。
    方法:
        - 若任务仍在后台运行, 请求取消 asyncio task。
        - 若任务已结束, 不做回滚, 返回当前状态。
    出参:
        - success: 返回取消结果或已结束状态。
        - error: 任务不存在。
    """
    return await task_manager.cancel_agent_task(task_id, reason=reason)
