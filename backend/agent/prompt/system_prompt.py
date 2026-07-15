"""
智能体系统提示词构建器 (Agent 层 / Prompt)

本模块只负责编排静态模板、工具目录和动态上下文。
"""

from backend.agent.prompt.dynamic_context import (
    DYNAMIC_SECTION_TEMPLATE,
    build_dynamic_section,
)
from backend.agent.prompt.static_template import SYSTEM_PROMPT_TEMPLATE
from backend.agent.prompt.tool_catalog import build_tools_catalog


def build_system_prompt(
    conversation_summary: str = None,
    user_profile: str = None,
    task_state: str = None,
) -> str:
    """
    入参:
      - conversation_summary: 短期记忆, 本会话过去轮次的摘要。
      - user_profile: 长期记忆, 跨会话用户画像与已学教训。
      - task_state: 工作记忆, 当前轮任务状态卡。
    方法:
      - 从工具注册表生成工具目录。
      - 从动态上下文模块生成会话摘要 / 当前任务状态 / 用户画像。
      - 将两者填入静态模板, 产出完整 system prompt。
    出参:
      - str, 完整 system prompt 字符串。
    """
    return SYSTEM_PROMPT_TEMPLATE.format(
        tools_catalog=build_tools_catalog(),
        dynamic_section=build_dynamic_section(conversation_summary, user_profile, task_state),
    )


def build_system_prompt_blocks(
    conversation_summary: str = None,
    user_profile: str = None,
    task_state: str = None,
) -> list:
    """
    入参:
      - conversation_summary: 短期记忆, 本会话过去轮次的摘要。
      - user_profile: 长期记忆, 跨会话用户画像与已学教训。
      - task_state: 工作记忆, 当前轮任务状态卡。
    方法:
      - 构建显式缓存 content-blocks。
      - 块 A 放静态规则和工具目录, 用于跨轮缓存命中。
      - 块 B 放动态上下文, 让摘要 / 状态 / 用户画像变化时只刷新动态块。
    出参:
      - list[dict], OpenAI content-blocks 格式的 system prompt 内容。
    """
    static_text = SYSTEM_PROMPT_TEMPLATE.format(
        tools_catalog=build_tools_catalog(),
        dynamic_section="",
    )
    dynamic_text = build_dynamic_section(conversation_summary, user_profile, task_state)

    return [
        {
            "type": "text",
            "text": static_text,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_text,
            "cache_control": {"type": "ephemeral"},
        },
    ]
