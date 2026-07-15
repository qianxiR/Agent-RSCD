"""
Prompt 模块 (Agent 层)
- system_prompt: 智能体系统提示词 (工具目录 + 内嵌规则 + ReAct 工作流 + 动态记忆段)

★ 从原 model/llm_client.py 拆出。prompt 是智能体设计的核心组成, 归入 agent 层。
- build_system_prompt():        纯字符串形式 (摘要/轻量模型调用用)
- build_system_prompt_blocks(): 显式缓存块形式 (主推理用, 命中按 10% 计费)
"""
from backend.agent.prompt.system_prompt import (
    build_system_prompt,
    build_system_prompt_blocks,
    SYSTEM_PROMPT_TEMPLATE,
    DYNAMIC_SECTION_TEMPLATE,
)

__all__ = [
    "build_system_prompt",
    "build_system_prompt_blocks",
    "SYSTEM_PROMPT_TEMPLATE",
    "DYNAMIC_SECTION_TEMPLATE",
]
