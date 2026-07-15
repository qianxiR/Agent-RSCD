"""
动态上下文提示词片段 (Agent 层 / Prompt)

本模块只负责把每轮变化的短期记忆、工作状态和长期记忆拼成动态段。
"""


DYNAMIC_SECTION_TEMPLATE = """---

## 当前任务状态

{task_state}

---

## 会话摘要

{conversation_summary}

---

## 用户画像

{user_profile}"""


def build_dynamic_section(
    conversation_summary: str,
    user_profile: str,
    task_state: str,
) -> str:
    """
    入参:
      - conversation_summary: 本会话早期摘要。
      - user_profile: 跨会话用户画像和已学教训。
      - task_state: 当前轮工作记忆状态卡。
    方法:
      - 拼装动态段文本, 把短期记忆 / 工作记忆 / 长期记忆并列注入。
      - 空值使用明确占位语, 避免模型误以为上下文被截断。
    出参:
      - str, 可直接拼入 system prompt 的动态上下文段。
    """
    return DYNAMIC_SECTION_TEMPLATE.format(
        conversation_summary=conversation_summary or "暂无历史摘要",
        task_state=task_state or "暂无任务状态记录",
        user_profile=user_profile or "暂无已知用户偏好",
    )
