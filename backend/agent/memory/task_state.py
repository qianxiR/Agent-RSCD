"""
任务状态提炼器 (Agent 层 / Working Memory)
- 入参: conversation_id / current_prompt / recent_history / conversation_summary
- 方法: 从近期消息、工具结果、ai_task 状态中提炼一张轻量任务状态卡
- 出参: 可直接注入 system prompt 的多行文本

设计目标:
  1. 把“工作记忆”从隐式历史中抽出来, 让模型每轮都能看到当前做到哪一步
  2. 复用现有 message 历史和 ai_task 表, 不新增数据库结构
  3. 只保留任务推进真正需要的状态, 避免把整段历史原样重复注入
"""
import json
from typing import List

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage

from . import agent_db


def _clip_text(text: str, limit: int = 120) -> str:
    """
    入参:
      - text: 待截断文本
      - limit: 最大保留长度
    方法:
      - 去除首尾空白, 长文本截断后追加省略标记
      - 这样做是为了在 prompt 中保留关键信号, 同时控制 token 膨胀
    出参:
      - 适合放入任务状态卡的短文本
    """
    clean = (text or "").strip()
    return clean if len(clean) <= limit else clean[:limit] + "...(截断)"


def _find_latest_user_goal(history: List[BaseMessage]) -> str:
    """
    入参:
      - history: 当前会话近期历史消息
    方法:
      - 从后往前找最近一条 HumanMessage
      - 这样做是为了拿到“当前输入之前”的上一个用户目标, 便于恢复连续任务
    出参:
      - 最近一条用户目标摘要, 不存在时返回空串
    """
    for message in reversed(history):
        if isinstance(message, HumanMessage):
            return _clip_text(str(message.content))
    return ""


def _find_latest_tool_action(history: List[BaseMessage]) -> str:
    """
    入参:
      - history: 当前会话近期历史消息
    方法:
      - 从后往前找最近一条带 tool_calls 的 AIMessage
      - 提取工具名列表, 因为工具选择本身就是“上一动作”最稳定的事实
    出参:
      - 最近一次工具动作摘要, 不存在时返回空串
    """
    for message in reversed(history):
        if isinstance(message, AIMessage) and message.tool_calls:
            tool_names = [item.get("name", "") for item in message.tool_calls if item.get("name")]
            if tool_names:
                return " -> ".join(tool_names[:3])
    return ""


def _summarize_tool_result(raw_content: str) -> str:
    """
    入参:
      - raw_content: ToolMessage.content 原始文本
    方法:
      - 优先解析 JSON 结构并提取 summary / msg / description / type
      - 这样做是为了把工具观察结果压成一句话, 而不是把大 JSON 整段塞回 prompt
    出参:
      - 工具结果摘要, 无法解析时返回截断后的原文
    """
    content = (raw_content or "").strip()
    if not content:
        return ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return _clip_text(content)

    if not isinstance(data, dict):
        return _clip_text(str(data))

    summary = data.get("summary") or data.get("msg") or data.get("description")
    if isinstance(summary, str) and summary.strip():
        return _clip_text(summary)

    result_type = data.get("type")
    if result_type == "frontend_action":
        return "前端已接管展示或交互"
    if result_type == "error":
        return _clip_text(f"工具报错: {data.get('msg', '未知错误')}")
    return _clip_text(str(data))


def _find_latest_observation(history: List[BaseMessage]) -> str:
    """
    入参:
      - history: 当前会话近期历史消息
    方法:
      - 从后往前找最近一条 ToolMessage, 并压缩成一句观察结果
      - 这样做是为了让模型看到“行动之后得到了什么反馈”
    出参:
      - 最近一次观察摘要, 不存在时返回空串
    """
    for message in reversed(history):
        if isinstance(message, ToolMessage):
            return _summarize_tool_result(str(message.content))
    return ""


def _format_recent_tasks(conversation_id: str) -> str:
    """
    入参:
      - conversation_id: 当前会话 ID
    方法:
      - 读取 ai_task 最近记录, 按状态压缩成短句
      - 这样做是为了把“持久化任务状态”注入给模型, 帮助续跑和错误恢复
    出参:
      - 任务状态短句, 无记录时返回空串
    """
    if not agent_db.agent_db_available():
        return ""

    tasks = agent_db.list_tasks(conversation_id=conversation_id, limit=5)
    if not tasks:
        return ""

    items = []
    for task in tasks[:3]:
        tool_name = task.get("tool_name") or task.get("task_type") or "unknown"
        status = task.get("status") or "pending"
        progress = task.get("progress")
        error = task.get("error")
        if status in ("running", "pending"):
            suffix = f"({progress}%)" if progress is not None else ""
            items.append(f"{tool_name}:{status}{suffix}")
        elif status == "failed":
            items.append(f"{tool_name}:failed({_clip_text(error or '无错误详情', 40)})")
        else:
            items.append(f"{tool_name}:{status}")
    return "；".join(items)


def build_task_state_text(
    conversation_id: str,
    current_prompt: str,
    history: List[BaseMessage],
    conversation_summary: str = "",
) -> str:
    """
    入参:
      - conversation_id: 当前会话 ID
      - current_prompt: 当前轮用户输入
      - history: 最近历史消息 (不含当前 HumanMessage)
      - conversation_summary: 会话摘要文本
    方法:
      - 提炼当前目标、上一轮目标、最近动作、最近观察、持久化任务状态
      - 这样做是为了把隐式工作记忆转成显式状态卡, 降低多步任务中“失忆”和“误收尾”的概率
    出参:
      - 多行任务状态文本, 可直接注入 system prompt
    """
    current_goal = _clip_text(current_prompt, 160) or "暂无当前输入"
    latest_goal = _find_latest_user_goal(history)
    latest_action = _find_latest_tool_action(history)
    latest_observation = _find_latest_observation(history)
    recent_tasks = _format_recent_tasks(conversation_id)

    lines = [f"- 当前目标: {current_goal}"]
    if latest_goal:
        lines.append(f"- 前序目标: {latest_goal}")
    if latest_action:
        lines.append(f"- 最近动作: {latest_action}")
    if latest_observation:
        lines.append(f"- 最近观察: {latest_observation}")
    if recent_tasks:
        lines.append(f"- 持久化任务状态: {recent_tasks}")
    if conversation_summary:
        lines.append("- 早期上下文: 更早轮次已压缩进会话摘要, 恢复长链任务前应先参考摘要")
    return "\n".join(lines)
