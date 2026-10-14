"""
专业 worker 编排层 (Agent 层)

本包承载"主控 Agent + 专业 worker"的实现:
  - verification_agent: 确定性规则验收, 同步内联在 validation_stage 中 (每次工具结果都走)。
  - report_agent: 异步后台 worker, 由 chat_service 在分割成功后代码派发 (不走 LLM 决策)。
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
