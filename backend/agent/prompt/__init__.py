"""
Prompt 模块 (Agent 层)
- system_prompt: 智能体系统提示词 (唯一静态模板 + 工具目录 + ReAct 规则 + 动态上下文)

★ 静态模板唯一来源: prompt_store/templates/static_template2.md
- build_system_prompt():        纯字符串形式 (摘要/轻量模型调用用)
- build_system_prompt_blocks(): 显式缓存块形式 (主推理用, 命中按 10% 计费)
"""
from backend.agent.prompt.dynamic_context import DYNAMIC_SECTION_TEMPLATE
from backend.agent.prompt.system_prompt import (
    STATIC_TEMPLATE_NAME,
    build_system_prompt,
    build_system_prompt_blocks,
)

__all__ = [
    "build_system_prompt",
    "build_system_prompt_blocks",
    "DYNAMIC_SECTION_TEMPLATE",
    "STATIC_TEMPLATE_NAME",
]
