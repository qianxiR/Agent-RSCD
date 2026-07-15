"""
LLM 客户端封装 (Model 层)
- 入参: 模型名、温度、工具列表
- 方法: 构造 LLM 实例 (ChatOpenAI 兼容 DashScope/Qwen)、绑定工具
- 出参: LLM 实例 (可直接 astream / ainvoke)

核心流程:
  ChatOpenAI (兼容 OpenAI 协议) → DashScope (Qwen) → 流式/非流式响应
  llm.bind_tools(tools) 将工具集注入 LLM 上下文, 使其能在推理中决定调用工具

★ 系统提示词 (System Prompt) 已拆分到 agent/prompt/system_prompt.py —— prompt 是
  智能体设计的核心组成, 归入 agent 层; 本文件只负责 LLM 实例的构造与工具绑定。
"""
import logging
from typing import List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.tools import BaseTool

from backend.config import settings

logger = logging.getLogger(__name__)


def create_llm(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> ChatOpenAI:
    """
    创建 LLM 实例
    - 使用 DashScope 兼容 OpenAI 的接口
    - 支持工具绑定和流式输出
    - stream_usage: 开启时流式返回 usage_metadata (含 cached_tokens 命中数),
      显式缓存开启时必须为 True, 否则无法统计命中率
    """
    return ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=model or settings.dashscope_model,
        temperature=temperature or settings.default_temperature,
        max_tokens=settings.max_tokens,
        stream_usage=settings.enable_context_cache,
    )


def create_llm_with_tools(
    tools: List[BaseTool],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> ChatOpenAI:
    """
    创建带工具绑定的 LLM 实例
    - tool_choice="auto": LLM 自行决定是否调用工具
    """
    llm = create_llm(model, temperature)
    return llm.bind_tools(tools, tool_choice="auto")
