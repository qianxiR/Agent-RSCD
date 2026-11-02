"""
智能体系统提示词构建器 (Agent 层 / Prompt)

本模块只负责编排唯一静态模板、工具目录和动态上下文。
"""
from backend.agent.prompt.dynamic_context import build_dynamic_section
from backend.agent.prompt.tool_catalog import build_decision_table, build_tools_catalog

STATIC_TEMPLATE_NAME = "static_template2"


def _static_template_text() -> str:
    """读取唯一静态模板；不存在时直接报错，不做代码兜底。"""
    from backend.agent.prompt_store import get_prompt_store

    template = get_prompt_store().get(STATIC_TEMPLATE_NAME)
    if template is None:
        raise RuntimeError(
            f"静态提示词模板缺失: prompt_store/templates/{STATIC_TEMPLATE_NAME}.md"
        )
    return template.text


def build_system_prompt(
    conversation_summary: str = None,
    user_profile: str = None,
    task_state: str = None,
    plan_state: str = None,
    tool_names=None,
) -> str:
    """
    入参:
      - conversation_summary: 短期记忆, 本会话过去轮次的摘要。
      - user_profile: 长期记忆, 跨会话用户画像与已学教训。
      - task_state: 工作记忆, 当前轮任务状态卡。
    方法:
      - 从工具注册表生成工具目录 + 从决策规则源生成任务→工具决策表。
      - 从动态上下文模块生成会话摘要 / 当前任务状态 / 用户画像。
      - 将三者填入静态模板, 产出完整 system prompt。
    出参:
      - str, 完整 system prompt 字符串。
    """
    return _static_template_text().format(
        tools_catalog=build_tools_catalog(tool_names),
        decision_table=build_decision_table(),
        dynamic_section=build_dynamic_section(conversation_summary, user_profile, task_state, plan_state),
    )


def build_system_prompt_blocks(
    conversation_summary: str = None,
    user_profile: str = None,
    task_state: str = None,
    plan_state: str = None,
    tool_names=None,
) -> list:
    """
    入参:
      - conversation_summary: 短期记忆, 本会话过去轮次的摘要。
      - user_profile: 长期记忆, 跨会话用户画像与已学教训。
      - task_state: 工作记忆, 当前轮任务状态卡。
    方法:
      - 构建显式缓存 content-blocks。
      - 块 A 放静态规则、决策表和工具目录, 用于跨轮缓存命中。
      - 块 B 放动态上下文, 让摘要 / 状态 / 用户画像变化时只刷新动态块。
    出参:
      - list[dict], OpenAI content-blocks 格式的 system prompt 内容。
    """
    static_text = _static_template_text().format(
        tools_catalog=build_tools_catalog(tool_names),
        decision_table=build_decision_table(),
        dynamic_section="",
    )
    dynamic_text = build_dynamic_section(conversation_summary, user_profile, task_state, plan_state)

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


