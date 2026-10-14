"""
LLM 客户端封装 (Model 层)
- 入参: 模型名、温度、工具列表
- 方法: 构造 Responses API LLM 实例、绑定工具
- 出参: LLM 实例 (可直接 astream / ainvoke)

核心流程:
  ChatOpenAI → Responses API provider → 流式/非流式响应
  llm.bind_tools(tools) 将工具集注入 LLM 上下文, 使其能在推理中决定调用工具

★ 系统提示词 (System Prompt) 已拆分到 agent/prompt/system_prompt.py —— prompt 是
  智能体设计的核心组成, 归入 agent 层; 本文件只负责 LLM 实例的构造与工具绑定。
"""
import logging
from typing import Any, List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.tools import BaseTool

from backend.config import settings

logger = logging.getLogger(__name__)


def response_text(response: Any) -> str:
    """
    入参: LangChain LLM 响应或其 content 值；方法: 兼容 Chat API 字符串和 Responses API 内容块，提取所有文本块并拼接；出参: 去除首尾空白的纯文本，非文本内容返回空字符串。
    约束: 不解析工具调用，工具调用继续由 AIMessage.tool_calls 处理。
    """
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
        ]
        return "".join(text_parts).strip()
    return ""


def _llm_endpoint() -> dict:
    """
    入参: 无
    方法: LLM 端点凭据路由 — Responses API 配置齐全时优先使用;
      缺失时回退智谱 GLM OpenAI 兼容端点 (2026-10 主线, GLM-5.3-Flash);
      GLM key 也缺失时最后回退 DashScope。全部缺失时返回 Responses
      原始空凭据, 由底层客户端在真实请求时报告配置错误。
    出参: ChatOpenAI 构造参数 dict (api_key/base_url/use_responses_api/
      output_version/default_model)
    """
    if settings.responses_api_key and settings.responses_base_url:
        return {
            "api_key": settings.responses_api_key,
            "base_url": settings.responses_base_url,
            "use_responses_api": True,
            "output_version": "responses/v1",
            "default_model": settings.responses_model,
        }
    if settings.glm_api_key:
        return {
            "api_key": settings.glm_api_key,
            "base_url": settings.glm_base_url,
            "use_responses_api": False,
            "default_model": settings.chat_model,
        }
    if settings.dashscope_api_key:
        return {
            "api_key": settings.dashscope_api_key,
            "base_url": settings.dashscope_base_url,
            "use_responses_api": False,
            "default_model": settings.dashscope_model,
        }
    # 无任何可用凭据: 保持 Responses 原始路径, 真实调用时报配置错误
    return {
        "api_key": settings.responses_api_key,
        "base_url": settings.responses_base_url or None,
        "use_responses_api": True,
        "output_version": "responses/v1",
        "default_model": settings.responses_model,
    }


def create_llm(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> ChatOpenAI:
    """
    创建 LLM 实例
    - 端点凭据经 _llm_endpoint 路由 (Responses API 优先, DashScope 回退)
    - 支持工具绑定和流式输出
    - stream_usage: 开启时流式返回 usage_metadata (含 cached_tokens 命中数),
      显式缓存开启时必须为 True, 否则无法统计命中率
    """
    endpoint = _llm_endpoint()
    kwargs = {
        "api_key": endpoint["api_key"],
        "base_url": endpoint["base_url"],
        "model": model or endpoint["default_model"],
        "temperature": temperature if temperature is not None else settings.default_temperature,
        "max_tokens": settings.max_tokens,
        "stream_usage": settings.enable_context_cache,
        "use_responses_api": endpoint["use_responses_api"],
    }
    if endpoint.get("output_version"):
        kwargs["output_version"] = endpoint["output_version"]
    return ChatOpenAI(**kwargs)


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


def create_responses_llm(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> ChatOpenAI:
    """
    入参: 可选模型名和温度；方法: 使用 Responses API 创建主聊天 LLM，并输出兼容 LangChain 的 responses/v1 消息块；出参: 可直接 astream/ainvoke 的 LLM 实例。
    约束: 凭据和端点来自 settings.responses_api_*；缺少凭据时由底层客户端在真实请求时返回配置错误。
    """
    return create_llm(model, temperature)


def create_responses_llm_with_tools(
    tools: List[BaseTool],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> ChatOpenAI:
    """
    入参: 后端工具列表、可选模型名和温度；方法: 创建 Responses API 主聊天 LLM 并以 auto 策略绑定工具；出参: 可执行工具调用的 LLM 实例。
    约束: 工具 schema 与现有 tool_registry 保持不变，模型仅改变 API 传输协议和默认模型。
    """
    llm = create_responses_llm(model, temperature)
    return llm.bind_tools(tools, tool_choice="auto")
