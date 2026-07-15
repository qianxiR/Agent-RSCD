"""
专业 worker 角色定义 (Agent 层)

当前阶段启用 verification_agent 和 report_agent。后续 rs_agent / publish_agent
按同一角色表扩展, 避免把 worker 权限散落在多个模块。
"""

from typing import Dict, List


VERIFICATION_AGENT = "verification_agent"
REPORT_AGENT = "report_agent"


AGENT_ROLE_TOOL_WHITELIST: Dict[str, List[str]] = {
    VERIFICATION_AGENT: [],
    REPORT_AGENT: [
        "generate_monitor_report",
        "visualize_vector",
    ],
}


def is_supported_agent_role(agent_role: str) -> bool:
    """
    入参:
      - agent_role: worker 角色名。
    方法:
      - 在集中角色表中判断该角色是否已开放。
      - 当前只开放已实现的 verification_agent 和 report_agent, 防止主控 Agent 调用未实现角色。
    出参:
      - bool, True 表示可派发。
    """
    return agent_role in AGENT_ROLE_TOOL_WHITELIST


def get_role_tool_names(agent_role: str) -> List[str]:
    """
    入参:
      - agent_role: worker 角色名。
    方法:
      - 返回角色绑定的工具名白名单副本。
      - verification_agent 当前是确定性 worker, 不绑定 LLM 工具。
    出参:
      - list[str], 调用方可安全修改返回值。
    """
    return list(AGENT_ROLE_TOOL_WHITELIST.get(agent_role, []))
