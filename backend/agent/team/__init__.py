"""
专业 worker 编排层 (Agent 层)

本包承载“主控 Agent + 专业 worker”的第一阶段实现。
当前仅开放 verification_agent, 用于把工具执行与客观验收分离。
"""

from backend.agent.team.agent_roles import VERIFICATION_AGENT, is_supported_agent_role
from backend.agent.team.task_manager import assign_agent_task, query_agent_task, cancel_agent_task
from backend.agent.team.validation_stage import run_validation_stage

__all__ = [
    "VERIFICATION_AGENT",
    "is_supported_agent_role",
    "assign_agent_task",
    "query_agent_task",
    "cancel_agent_task",
    "run_validation_stage",
]
