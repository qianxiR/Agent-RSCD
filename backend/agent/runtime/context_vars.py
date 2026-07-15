"""
运行时上下文 (Agent 层 / Runtime) — 上下文模块管理

用 contextvars 在 asyncio 任务间隔离运行时上下文, 多对话并行不会串号。
chat_service 进入每次对话时注入当前 conv_id / user_id, 工具内部通过
get_current_conversation_id() / get_current_user_id() 读取。

★ 为什么独立模块:
  LangChain @tool 的 invoke 只接收 LLM 给的 args, 拿不到运行时上下文。
  把 ContextVar 抽到 runtime 子包, 让 memory_tools 等工具与 chat_service
  共享同一份运行时上下文定义, 解耦具体工具实现。

依赖方向: agent.runtime 仅依赖标准库 (contextvars), 无跨层依赖。
"""
from contextvars import ContextVar

# ==================== 运行时上下文 (由 chat_service 注入) ====================
# ContextVar 在 asyncio 任务间天然隔离, 多对话并行不会串号
_current_conversation_id: ContextVar[str] = ContextVar("current_conversation_id", default="")
_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="study_user")
# ★ 沙盒工作区来源: project_id (分组), 同一分组的对话共享同一 work_dir
_current_project_id: ContextVar[str] = ContextVar("current_project_id", default="")


def set_runtime_context(conversation_id: str, user_id: str = "study_user",
                        project_id: str = ""):
    """由 chat_service 在每次 tool_chat_ws 开始时调用, 注入当前对话上下文
    - conversation_id: 对话 ID (= 沙盒 client_id)
    - user_id: 用户 ID
    - project_id: 所属分组 ID (= 沙盒 work_dir 的来源; 空表示未分组)
    """
    _current_conversation_id.set(conversation_id or "")
    _current_user_id.set(user_id or "study_user")
    _current_project_id.set(project_id or "")


def get_current_conversation_id() -> str:
    return _current_conversation_id.get()


def get_current_user_id() -> str:
    return _current_user_id.get()


def get_current_project_id() -> str:
    return _current_project_id.get()
