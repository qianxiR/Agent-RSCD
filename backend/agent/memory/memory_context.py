"""
Agent 记忆与上下文管理 (Agent 层)
- 入参: conversation_id / 当前用户输入 / LLM 实例
- 方法: 构建发送给 LLM 的上下文 (含 trim 裁剪) / 持久化本轮新消息 / 摘要 / 长期记忆提取
- 出参: (messages, new_start_idx) / None

三层职责 (这是记忆分层落地的核心):
  - agent/memory/agent_db.py : 记忆数据库 (source of truth, 连接池+CRUD)
  - agent/memory/memory_context.py : 上下文构建 + 持久化编排 (本文件)
  - agent/chat_service.py : Agent 推理循环

三层记忆 (对应工业级 harness 的标准分层):
  1. 长期记忆 (long-term): user_memory 表, 跨会话偏好 + 自纠学习教训 (category=lesson 的记忆融入此层), 注入 system prompt
  2. 短期记忆 (summary): conversation_summary 表, 本会话旧消息摘要, 注入 system prompt
  3. 工作记忆 (buffer): message 表近期消息 (含 tool_calls), 以消息列表形式喂给 LLM

核心函数:

build_context_messages(conv_id, user_prompt, llm, user_id)
  返回 (all_messages, new_start_idx)
  ┌───────────────────────────────────────────────────────┐
  │ 1. 加载三层记忆:                                         │
  │    - user_profile (长期) → system prompt                │
  │    - summary (短期)     → system prompt                │
  │    - recent history (工作) → 消息列表 (只取 summarized_  │
  │      upto 之后, 避免与摘要重复)                          │
  │ 2. 拼装: [System] + history + [Human(user_prompt)]    │
  │ 3. trim_messages 裁剪到 context_max_tokens:           │
  │      strategy="last"     保留最近                       │
  │      include_system=True system prompt 永不裁剪         │
  │      start_on="human"    从 human 轮切, 不切断           │
  │                          (human→ai→tool→ai) 的完整配对  │
  │ 4. new_start_idx 指向本轮新增消息的起点 (用于收尾持久化) │
  └───────────────────────────────────────────────────────┘

persist_turn(conv_id, all_messages, new_start_idx)
  持久化 all_messages[new_start_idx:]
  = 当前 Human + 本轮所有 AIMessage(含 tool_calls) + 所有 ToolMessage
  这样下一轮 load_messages 能完整还原工具调用链

降级: agent_db 不可用 → 使用模块级 _mem_fallback dict, 行为同原始实现
"""
import logging
from typing import List, Tuple, Optional, Dict, Any

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
    trim_messages,
)

from backend.config import settings
from backend.agent.memory import agent_db
from backend.agent.prompt import build_system_prompt, build_system_prompt_blocks
from backend.agent.memory.task_state import (
    build_plan_context_text,
    build_task_state_text,
    load_plan_state,
)
from backend.agent.memory.lesson_policy import format_accepted_lesson_for_prompt

logger = logging.getLogger(__name__)


def _get_summary_llm():
    """
    获取用于摘要/长期记忆提取的轻量 LLM (deepseek-v4-flash, 走百炼免费额度)。
    延迟导入避免循环依赖 (memory ↔ llm_client ↔ memory)。
    """
    from backend.model.llm_client import create_llm
    return create_llm(model=settings.summary_model, temperature=0.0)


def _get_long_term_memory_llm():
    """
    获取用于长期记忆提取的轻量 LLM。
    - 入参: 无
    - 方法: 延迟导入 create_llm, 使用 long_term_memory_model 独立配置
    - 出参: 已配置好的 LangChain LLM 实例
    """
    from backend.model.llm_client import create_llm
    return create_llm(model=settings.long_term_memory_model, temperature=0.0)


# ==================== Token 计数 (兼容 Qwen/DashScope) ====================
# trim_messages 官方推荐传 token_counter=llm, 但 ChatOpenAI 对 qwen-plus 这类
# 非 OpenAI 官方模型不支持 get_num_tokens_from_messages() (NotImplementedError)。
# 解法: 自定义一个基于 tiktoken cl100k_base 的近似计数函数。
# 中文场景下 cl100k_base 对汉字约 1.5~2 token, 量级足够 trim 决策用。
_tiktoken_enc = None


def _get_encoder():
    global _tiktoken_enc
    if _tiktoken_enc is None:
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _tiktoken_enc = False  # 标记不可用, 回退字符计数
    return _tiktoken_enc


def _count_tokens(text: str) -> int:
    enc = _get_encoder()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # 回退: 粗略估算 (中文 1 字 ≈ 2 token, 英文 4 字符 ≈ 1 token)
    return max(1, len(text) // 2)


def _count_message_tokens(messages) -> int:
    """对 LangChain 消息列表做 token 计数 (含 tool_calls 结构)"""
    total = 0
    for m in messages:
        # content: 可能是 str 或 content-blocks 数组 (显式缓存模式)
        content = m.content
        if isinstance(content, str):
            total += _count_tokens(content)
        elif isinstance(content, list):
            # OpenAI content-blocks 格式: [{"type":"text","text":"...","cache_control":{}}]
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    total += _count_tokens(block["text"])
                else:
                    total += _count_tokens(str(block))
        else:
            total += _count_tokens(str(content))
        # tool_calls (AIMessage)
        tcs = getattr(m, "tool_calls", None) or []
        for tc in tcs:
            total += _count_tokens(tc.get("name", ""))
            total += _count_tokens(str(tc.get("args", {})))
        # tool_call_id / name (ToolMessage)
        if hasattr(m, "tool_call_id") and m.tool_call_id:
            total += _count_tokens(str(m.tool_call_id))
        # 每条消息固定开销 (角色标记等)
        total += 4
    return total


# ==================== 降级: 内存兜底 (agent_db 不可用时) ====================
# 保留 _get_history 语义与原 chat_service.py 完全一致, 仅作学习零阻塞
_mem_fallback: dict = {}


def _is_db_mode() -> bool:
    """是否使用 DB 模式 (agent_db 可用即用)"""
    return agent_db.agent_db_available()


# ==================== 上下文构建 ====================

def fold_tool_messages(messages: List[BaseMessage], keep_tail: int = 3) -> List[BaseMessage]:
    """
    ★ v2.5 可逆压缩层 (Level 0.5): 在 trim 之前折叠已完成的工具消息内容。

    痛点: 工具 (segment_image/detect_change/report 等) 返回的 stats/legend/URL
    占用大量 token, 但这些细节在后续多轮对话里已不重要 (LLM 只需记住"做过分割,
    结果是建筑占 30%")。trim_messages 是粗暴的尾部截断, 会从最近的消息开始丢,
    反而丢失当前任务的上下文。

    方法 (只改喂给 LLM 的形式, DB 原消息不动):
      - 找出所有"已完成的工具调用" (该 tool_call_id 已无后续待处理)
      - 除最近的 keep_tail 条工具消息保留原文外, 其余折叠为短摘要
      - 折叠形式: content → "[已完成·工具名·结果摘要前80字]"
      - 保留 tool_call_id 和 name (不破坏配对结构)

    入参:
        - messages: 含 SystemMessage + history + 当前 HumanMessage 的完整列表
        - keep_tail: 保留最近 N 条工具消息不折叠 (近期上下文完整)
    返回: 折叠后的新列表 (不修改原列表)
    """
    if not messages or keep_tail < 0:
        return messages

    # 找出所有 ToolMessage 的 index (按出现顺序)
    tool_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    if len(tool_indices) <= keep_tail:
        return messages  # 工具消息不多, 无需折叠

    # 需要折叠的: 除最后 keep_tail 条外的所有 ToolMessage
    fold_indices = set(tool_indices[:-keep_tail]) if keep_tail > 0 else set(tool_indices)

    result = []
    for i, m in enumerate(messages):
        if i in fold_indices and isinstance(m, ToolMessage):
            # 折叠: content → 短摘要, 保留 tool_call_id + name
            content_str = m.content if isinstance(m.content, str) else str(m.content)
            # 抽取工具结果的关键信号 (type/summary), 避免 LLM 完全丢失上下文
            folded_text = _fold_tool_content(content_str, m.name or "tool")
            result.append(ToolMessage(
                content=folded_text,
                tool_call_id=m.tool_call_id,
                name=m.name,
            ))
        else:
            result.append(m)
    return result


def _fold_tool_content(content_str: str, tool_name: str, max_chars: int = 80) -> str:
    """
    把工具结果 JSON 折叠成短摘要, 保留关键语义信号。
    - 尝试解析 JSON 抽 type/summary/msg 字段
    - 解析失败则截断原文前 max_chars 字
    """
    import json as _json
    try:
        data = _json.loads(content_str)
        if isinstance(data, dict):
            # 优先保留 summary (给 LLM 的文本摘要)
            summary = data.get("summary") or data.get("msg") or ""
            if summary:
                summary_short = summary[:max_chars]
                rtype = data.get("type", "")
                return f"[已完成·{tool_name}·{rtype}] {summary_short}"
            # 无 summary, 只记 type
            return f"[已完成·{tool_name}·{data.get('type', 'result')}]"
    except Exception:
        pass
    # 非 JSON, 截断原文
    return f"[已完成·{tool_name}] {content_str[:max_chars]}"


def build_context_messages(
    conversation_id: str,
    user_prompt: str,
    llm,
    user_id: str = "study_user",
    images: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[BaseMessage], int]:
    """
    构建发送给 LLM 的完整消息列表, 并返回本轮新增起点 index。

    返回:
        (all_messages, new_start_idx)
        - all_messages: 已 trim 的消息列表 (可直接喂给 llm.astream)
        - new_start_idx: 本轮新增消息 (Human/Tool/AI) 在 all_messages 中的起点
          → 收尾时只需持久化 all_messages[new_start_idx:]

    注入的三层记忆:
        1. 长期记忆 (user_profile): 跨会话偏好, 拼到 system prompt
        2. 短期记忆 (conversation_summary): 本会话过去轮次摘要, 拼到 system prompt
        3. 近期消息 (buffer): summarized_upto 之后的完整历史 (含 tool_calls)
    """
    # 加载三层记忆
    user_profile_text = build_user_profile_text(user_id)
    summary_data = None
    if _is_db_mode():
        summary_data = agent_db.load_summary(conversation_id)

    summary_text = (summary_data or {}).get("summary") if summary_data else None
    summarized_upto = (summary_data or {}).get("summarized_upto") if summary_data else 0

    if _is_db_mode():
        # 先加载近期历史, 再基于“摘要 + 历史 + 当前输入”提炼工作记忆状态卡。
        # 这样做是为了把隐式上下文压成显式状态, 减少多步任务中途跑偏。
        history = agent_db.load_messages_since(conversation_id, summarized_upto or 0, settings.history_load_limit)
    else:
        history = list(_mem_fallback.get(conversation_id, []))

    task_state_text = build_task_state_text(
        conversation_id=conversation_id,
        current_prompt=user_prompt,
        history=history,
        conversation_summary=summary_text or "",
    )
    persisted_plan, _ = load_plan_state(conversation_id)
    plan_state_text = build_plan_context_text(persisted_plan)

    system_prompt = build_system_prompt(
        conversation_summary=summary_text,
        user_profile=user_profile_text,
        task_state=task_state_text,
        plan_state=plan_state_text,
    ) if not settings.enable_context_cache else build_system_prompt_blocks(
        conversation_summary=summary_text,
        user_profile=user_profile_text,
        task_state=task_state_text,
        plan_state=plan_state_text,
    )

    # 拼装: [System] + history + [Human]
    input_images = _normalize_context_images(images or [])
    agent_prompt = _append_image_context_to_prompt(user_prompt, input_images)
    current_human = HumanMessage(
        content=agent_prompt,
        additional_kwargs={
            "display_content": user_prompt,
            "images": input_images,
        },
    )
    raw_messages = [SystemMessage(content=system_prompt)] + history + [current_human]
    raw_total_tokens = _count_message_tokens(raw_messages)

    # ★ v2.5 Level 0.5 可逆压缩: trim 之前先折叠旧的已完成工具消息内容
    # 工具结果 (stats/legend/URL) 占大量 token, 折叠后 trim 更不容易丢近期上下文
    if settings.tool_message_keep_tail > 0:
        raw_messages = fold_tool_messages(raw_messages, keep_tail=settings.tool_message_keep_tail)

    # trim_messages 裁剪到 token 上限内
    # 注意: 不能用 token_counter=llm, 因为 ChatOpenAI 对 qwen-plus 不支持
    # get_num_tokens_from_messages (NotImplementedError), 改用自定义 tiktoken 计数
    trimmed = trim_messages(
        raw_messages,
        max_tokens=settings.context_max_tokens,
        strategy="last",          # 保留最近的
        token_counter=_count_message_tokens,
        include_system=True,      # system prompt 永远保留
        start_on="human",         # 从 human 轮切, 不切断工具调用配对
    )

    # ★ 关键防御: 确保"本轮 Human 消息"一定存活在 trimmed 里
    # trim_messages 在 system prompt 本身就接近 max_tokens 的极端情况下,
    # 可能为了凑齐 "以 human 开头" 的切片而把唯一的当前 HumanMessage 丢弃,
    # 导致 LLM 收不到用户输入, 只能反问"请提供需求"。这里强制兜底。
    if current_human not in trimmed:
        logger.warning(
            f"[Memory] ⚠️ trim_messages 丢弃了当前 HumanMessage, 强制补回. "
            f"conv={conversation_id} raw_tokens={raw_total_tokens} "
            f"trimmed={len(trimmed)}"
        )
        # 补回: 直接 append 到最后 (LLM 看到的就是 System + (可能残缺的历史) + 当前 Human)
        trimmed = list(trimmed) + [current_human]

    # 计算 new_start_idx (本轮新增 Human 在 trimmed 中的位置)
    # 用对象身份定位, 避免依赖 "最后一条就是 Human" 的隐式假设
    new_start_idx = _locate_message(trimmed, current_human)

    # ★ 边界兜底: 若定位失败 (理论上不会), 从 system 之后算起
    if new_start_idx < 0:
        new_start_idx = 1 if (trimmed and isinstance(trimmed[0], SystemMessage)) else 0
        logger.error(
            f"[Memory] ⚠️ 无法定位当前 HumanMessage, new_start_idx 兜底为 {new_start_idx}. "
            f"conv={conversation_id} trimmed_count={len(trimmed)}"
        )

    logger.info(
        f"[Memory] conv={conversation_id} user={user_id} history={len(history)} "
        f"trimmed={len(trimmed)} new_start={new_start_idx} "
        f"raw_tokens={raw_total_tokens} has_summary={bool(summary_text)} db_mode={_is_db_mode()} "
        f"last_msg_type={type(trimmed[-1]).__name__ if trimmed else 'None'}"
    )
    return trimmed, new_start_idx


def _normalize_context_images(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    入参:
      - images: 当前轮上传影像和 GeoServer 下载影像附件。
    方法:
      - 过滤非 dict 或无 path 的项。
      - 保留前端展示与 Agent 路径上下文所需字段。
    出参:
      - list[dict], 规范化后的影像附件。
    """
    result = []
    for item in images or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        result.append({
            "name": str(item.get("name") or ""),
            "path": path,
            "preview_url": str(item.get("preview_url") or item.get("url") or ""),
            "source": str(item.get("source") or "upload"),
            "workspace": str(item.get("workspace") or ""),
            "layer_name": str(item.get("layer_name") or ""),
            "full_name": str(item.get("full_name") or ""),
        })
    return result


def _append_image_context_to_prompt(prompt: str, images: List[Dict[str, Any]]) -> str:
    """
    入参:
      - prompt: 用户原始可见文本。
      - images: 本轮可供 Agent 使用的本地影像附件。
    方法:
      - 无附件时返回原文。
      - 有附件时追加结构化影像输入上下文, 明确本地路径和来源。
    出参:
      - str, 发送给 LLM 的增强 HumanMessage 内容。
    """
    if not images:
        return prompt
    lines = ["", "[本轮影像输入]"]
    for idx, item in enumerate(images, 1):
        source = item.get("source") or "upload"
        title = item.get("full_name") or item.get("name") or f"image_{idx}"
        lines.append(f"- image_{idx}: {title}")
        if item.get("name"):
            lines.append(f"  文件名: {item['name']}")
        if item.get("full_name"):
            lines.append(f"  图层: {item['full_name']}")
        lines.append(f"  本地路径: {item.get('path')}")
        lines.append(f"  来源: {'GeoServer 图层下载' if source == 'geoserver' else '用户手动上传'}")
    return f"{prompt.rstrip()}\n" + "\n".join(lines)


def _locate_message(messages: List[BaseMessage], target: BaseMessage) -> int:
    """用对象身份定位 (target 是同一个 Python 对象)"""
    for i, m in enumerate(messages):
        if m is target:
            return i
    return -1


# ==================== 本轮持久化 ====================

def persist_user_message(
    conversation_id: str,
    all_messages: List[BaseMessage],
    new_start_idx: int,
) -> bool:
    """
    ★ v2.3: 在 LLM 循环前立即持久化用户消息 (HumanMessage), 防止停止时丢失。

    设计目的:
      原架构中 HumanMessage 与 AI/Tool 消息在 persist_turn 统一落库,
      但若用户中途停止 → CancelledError → 回滚删除 → HumanMessage 未入库 → 丢失。
      现在将 HumanMessage 提前落库 (checkpoint 之前), 停止回滚只删 AI/Tool 消息。

    - 入参: 会话 id / 完整消息列表 / 本轮起点 index
    - 方法: 取 all_messages[new_start_idx] (第一条新消息即 HumanMessage), 单独 insert
    - 出参: True 表示成功持久化
    """
    if new_start_idx >= len(all_messages):
        return False
    msg = all_messages[new_start_idx]
    if not isinstance(msg, HumanMessage):
        logger.warning(
            f"[Memory] persist_user_message: new_start_idx 位置不是 HumanMessage "
            f"(实际: {type(msg).__name__}), 跳过"
        )
        return False
    if _is_db_mode():
        ok = agent_db.append_message(conversation_id, msg)
        if ok:
            logger.info(
                f"[Memory] persist_user_message conv={conversation_id} "
                f"content={str(msg.content)[:50]}"
            )
        return ok
    else:
        bucket = _mem_fallback.setdefault(conversation_id, [])
        bucket.append(msg)
        logger.info(f"[Memory] persist_user_message conv={conversation_id} (MEM fallback)")
        return True


def persist_turn(
    conversation_id: str,
    all_messages: List[BaseMessage],
    new_start_idx: int,
) -> int:
    """
    持久化本轮新消息 (含工具调用链)。
    - 入参: 会话 id / 完整消息列表 / 本轮起点 index
    - 方法: 逐条 append_message 到 agent_db (或降级到内存)
    - 出参: 成功持久化的消息条数

    持久化范围 = all_messages[new_start_idx:]
      通常包含: [HumanMessage, AIMessage(tool_calls=...), ToolMessage, ..., AIMessage(最终回复)]
      完整还原工具调用链, 下轮 LLM 能据此决定是否继续操作
    """
    new_msgs = all_messages[new_start_idx:]
    if not new_msgs:
        return 0

    if _is_db_mode():
        count = 0
        for msg in new_msgs:
            ok = agent_db.append_message(conversation_id, msg)
            if ok:
                count += 1
        logger.info(f"[Memory] persist_turn conv={conversation_id} saved={count}/{len(new_msgs)} (DB)")
        return count
    else:
        # 内存兜底: 只保留 Human + AI(最终回复), 与原实现语义一致
        # (内存模式下不存 ToolMessage, 因为原代码就这么做, 学习时可对比差异)
        bucket = _mem_fallback.setdefault(conversation_id, [])
        human = next((m for m in new_msgs if isinstance(m, HumanMessage)), None)
        ai_final = next((m for m in reversed(new_msgs) if isinstance(m, AIMessage)), None)
        if human:
            bucket.append(human)
        if ai_final:
            bucket.append(ai_final)
        saved = (1 if human else 0) + (1 if ai_final else 0)
        logger.info(f"[Memory] persist_turn conv={conversation_id} saved={saved} (MEM fallback)")
        return saved


# ==================== 短期记忆: 摘要 (Summary Memory) ====================

# 摘要提示词: 让 LLM 把旧消息压成要点, 保留工具调用结果的关键信息
_SUMMARY_SYSTEM = (
    "你是对话摘要助手。请把以下对话历史压缩成不超过 {max_tokens} token 的要点。"
    "必须保留: 用户操作了哪些工具、查询/修改了哪些图层或数据库表、关键参数和最终结论。"
    "只输出摘要正文, 不要加'摘要:'之类的标题。"
)


async def maybe_summarize(
    conversation_id: str,
    all_messages: List[BaseMessage],
    new_start_idx: int,
) -> bool:
    """
    ★ v2.5 多级压缩: 检查历史 token, 超阈值时按级别渐进压缩。

    压缩级别 (沿用现有时机, 即对话结束异步触发):
      - Level 1 (drop 工具消息, 可逆): total_tokens >= summarize_threshold
        把旧工具结果 content 在 DB 里标记为 [outdated], 保留结构和 tool_call_id。
        下次加载时识别标记, 折叠显示。不删消息, 可逆。
      - Level 2 (LLM 摘要, 不可逆): Level 1 后 token 仍超 threshold × multiplier
        取旧的一半消息调 LLM 生成结构化摘要, 落库后删除原消息。

    - 入参: 会话 id / 本轮完整消息 / 本轮起点 index
    - 出参: 是否触发了压缩 (任意级别)

    Level 升级是单调的: 已到 Level 2 的会话, 下次仍从 Level 1 检查
    (因为 Level 2 已删旧消息, Level 1 可能不再触发)。
    """
    if settings.summarize_threshold <= 0:
        return False  # 摘要被禁用
    if not _is_db_mode():
        return False  # 内存模式不做摘要

    # 查当前压缩级别 (决定从哪级开始检查)
    summary_data = agent_db.load_summary(conversation_id) or {}
    current_level = summary_data.get("compress_level", 0)

    # 估算当前历史 + 本轮新消息的总 token
    full_history = agent_db.load_messages(conversation_id, limit=10000)
    new_msgs = all_messages[new_start_idx:]
    combined = full_history + [m for m in new_msgs if not isinstance(m, SystemMessage)]
    total_tokens = _count_message_tokens(combined)

    if total_tokens < settings.summarize_threshold:
        return False

    # 消息太少不压缩
    if len(combined) < 6:
        return False

    logger.info(
        f"[Memory] v2.5 多级压缩检查: conv={conversation_id} total_tokens={total_tokens} "
        f"threshold={settings.summarize_threshold} current_level={current_level} "
        f"msgs={len(combined)}"
    )

    # ==================== Level 1: drop 工具消息 (可逆) ====================
    # Level 1 的核心是 fold_tool_messages (在 build_context_messages 每次都执行),
    # 这里只需记录 compress_level=1 状态, 并评估是否需要升级到 Level 2。
    level2_threshold = int(settings.summarize_threshold * settings.summary_level2_multiplier)

    if current_level < 1:
        # 评估 Level 1 (fold) 后的 token
        folded = fold_tool_messages(combined, keep_tail=settings.tool_message_keep_tail)
        folded_tokens = _count_message_tokens(folded)
        # 记录 Level 1 状态 (不改 content, fold 在 build_context_messages 实时做)
        level1_summary = summary_data.get("summary", "")
        summarized_upto = summary_data.get("summarized_upto") or 0
        agent_db.save_summary(conversation_id, level1_summary, summarized_upto, compress_level=1)
        logger.info(
            f"[Memory] Level 1 记录 conv={conversation_id}: "
            f"fold 后 token={folded_tokens} (原始={total_tokens})"
        )
        if folded_tokens < level2_threshold:
            return True  # Level 1 够了, 不升级
        logger.info(
            f"[Memory] Level 1 后 token={folded_tokens} 仍超 Level 2 门槛 {level2_threshold}, 升级"
        )

    # ==================== Level 2: LLM 摘要 (不可逆) ====================
    if total_tokens < level2_threshold and current_level < 1:
        return True  # Level 1 够了 (上面已处理), 不进 Level 2

    # 取旧的一半去摘要 (保留近期一半消息不动)
    half = len(combined) // 2
    half = _align_to_safe_boundary(combined, half)
    old_messages = combined[:half]

    logger.info(
        f"[Memory] Level 2 摘要: conv={conversation_id} old_msgs={len(old_messages)} "
        f"half={half}"
    )

    # 调用轻量 LLM 生成摘要 (含已有的 Level 1/旧摘要作为上下文, 支持递归压缩)
    try:
        llm = _get_long_term_memory_llm()
        existing_summary = summary_data.get("summary", "")
        summary_input = _format_messages_for_summary(old_messages)
        if existing_summary:
            summary_input = f"[已有历史摘要]\n{existing_summary}\n\n[新增对话]\n{summary_input}"
        summary_resp = await llm.ainvoke([
            SystemMessage(content=_SUMMARY_SYSTEM.format(max_tokens=settings.summary_max_tokens)),
            HumanMessage(content=summary_input),
        ])
        summary_text = summary_resp.content if hasattr(summary_resp, "content") else str(summary_resp)
    except Exception as e:
        logger.error(f"[Memory] Level 2 摘要生成失败 conv={conversation_id}: {e}")
        return current_level >= 1  # Level 1 已成功, 返回 True

    if not summary_text:
        return current_level >= 1

    # 计算 summarized_upto
    remaining_count = len(combined) - len(old_messages)
    summarized_upto = _compute_summarized_upto(conversation_id, remaining_count)
    if summarized_upto is None:
        logger.warning(f"[Memory] 无法计算 summarized_upto, 跳过 Level 2 conv={conversation_id}")
        return current_level >= 1

    # 保存摘要 + 删除旧消息 (Level 2 不可逆)
    ok = agent_db.save_summary(conversation_id, summary_text, summarized_upto, compress_level=2)
    if ok:
        deleted = agent_db.delete_messages_upto(conversation_id, summarized_upto)
        logger.info(
            f"[Memory] Level 2 完成 conv={conversation_id} summary_len={len(summary_text)} "
            f"summarized_upto={summarized_upto} deleted_old={deleted}"
        )
        return True
    return current_level >= 1


def _align_to_safe_boundary(messages: List[BaseMessage], idx: int) -> int:
    """
    把切分点 idx 向后移动, 直到落在 ToolMessage 序列之后 (避免切断 tool 配对)。
    切分点必须满足: messages[idx-1] 不是 ToolMessage (即不切断工具调用链)。
    """
    while idx < len(messages) and isinstance(messages[idx - 1], ToolMessage):
        idx += 1
    return idx


def _compute_summarized_upto(conversation_id: str, remaining_count: int):
    """
    推算被摘要覆盖的最大 message.id。
    方法: 查询会话的总消息数, 用 倒数 remaining_count 条的最小 id - 1 作为 summarized_upto。
    返回 None 表示无法计算。
    """
    from backend.agent.memory.agent_db import get_conn
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                # 取剩余部分的第一条 id (按时间正序的第 remaining_count+1 条之前的边界)
                cur.execute(
                    """SELECT id FROM message
                       WHERE conversation_id = %s
                       ORDER BY id DESC
                       LIMIT 1 OFFSET %s""",
                    (conversation_id, remaining_count),
                )
                row = cur.fetchone()
                # 该 id 是"被摘要的最后一条", summarized_upto = 这个 id
                return row[0] if row else None
        except Exception as e:
            logger.error(f"计算 summarized_upto 失败 conv={conversation_id}: {e}")
            return None


def _format_messages_for_summary(messages: List[BaseMessage]) -> str:
    """把消息列表格式化为 LLM 易读的纯文本 (用于摘要输入)"""
    lines = []
    for m in messages:
        if isinstance(m, HumanMessage):
            lines.append(f"用户: {m.content}")
        elif isinstance(m, AIMessage):
            tc = m.tool_calls or []
            tc_str = f" [调用工具: {', '.join(t.get('name','') for t in tc)}]" if tc else ""
            content = m.content or ""
            if content or tc_str:
                lines.append(f"助手:{tc_str} {content}")
        elif isinstance(m, ToolMessage):
            lines.append(f"工具结果({m.name}): {m.content}")
    return "\n".join(lines)


# ==================== 长期记忆: 用户画像 (Long-term Memory) ====================

# 提取提示词: 让 LLM 从对话中识别抽象的用户行为模式 (不存储具体参数值)
_EXTRACT_SYSTEM = (
    "你是对话行为分析助手。请分析以下对话, 提取用户的操作行为模式和工作习惯。\n"
    "要求: 只记录抽象的行为类型, 不要存储具体的参数值 (如图层名、表名、文件路径等)。\n"
    "正例: {{\"key\": \"操作偏好\", \"value\": \"偏好以表格形式查看数据\", \"category\": \"preference\"}}\n"
    "正例: {{\"key\": \"操作偏好\", \"value\": \"经常进行多图层叠加分析\", \"category\": \"preference\"}}\n"
    "正例: {{\"key\": \"工作习惯\", \"value\": \"倾向于先查看数据结构再执行查询\", \"category\": \"fact\"}}\n"
    "反例: {{\"key\": \"常用图层\", \"value\": \"建筑用地\", \"category\": \"entity\"}}  ← 含具体参数值, 禁止!\n"
    "反例: {{\"key\": \"常用数据表\", \"value\": \"land_use\", \"category\": \"entity\"}}  ← 含具体参数值, 禁止!\n"
    # ★ JSON 示例里的花括号已用 {{ }} 转义, 确保 str.format 不会误解析为占位符
    '仅返回 JSON 数组, 每项格式: {{"key": "简短标识", "value": "抽象行为描述", "category": "preference|fact"}}\n'
    "如果没有值得记住的行为模式, 返回 []。最多提取 {max_items} 条。不要输出任何解释。"
)


async def extract_and_save_long_term_memory(
    conversation_id: str,
    user_id: str,
    all_messages: List[BaseMessage],
    new_start_idx: int,
) -> int:
    """
    从对话中提取抽象的用户行为模式并存入 user_memory。

    抽象化设计:
      - 不存储具体参数值 (图层名/表名/文件路径等), 只记录抽象行为模式
      - 例如: 不记"常用图层: 建筑用地", 而记"操作偏好: 偏好图层叠加分析"
      - category 只使用 preference/fact, 不再使用 entity (不含具体实体)

    - 入参: 会话 id / 用户 id / 消息列表 / 新消息起始 index
    - 方法: 用 deepseek-v4-flash 分析对话, 返回 JSON 数组, 批量 UPSERT
    - 出参: 成功写入的记忆条数

    设计权衡:
      - 每轮多一次便宜 LLM 调用 (deepseek-v4-flash, 走百炼免费额度), 不占主对话模型额度
      - 用户无感, 记忆自动积累
      - 同 user_id+key 覆盖, 不会无限增长

    ★ 开关 enable_preference_extraction (默认 true):
      与自纠捕捉器 (self_correction.py) 并行 — 两者在对话收尾时由同一触发点并行启动,
      本函数提取"偏好/事实"抽象行为, 自纠捕捉器沉淀"错误案例+解决方案" (lesson/workflow).
      若需只保留错误案例学习, 设 ENABLE_PREFERENCE_EXTRACTION=false 单独关闭本路.
    """
    if not _is_db_mode():
        return 0
    if not settings.enable_preference_extraction:
        logger.debug("[Memory] 偏好/事实提取已关闭 (enable_preference_extraction=False), 跳过")
        return 0

    new_msgs = all_messages[new_start_idx:]
    if len(new_msgs) < 2:
        return 0  # 太短不值得提取

    try:
        llm = _get_summary_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=_EXTRACT_SYSTEM.format(max_items=settings.long_term_memory_max_items)),
            HumanMessage(content=_format_messages_for_summary(new_msgs)),
        ])
        raw = resp.content if hasattr(resp, "content") else str(resp)
    except Exception as e:
        logger.error(f"[Memory] 长期记忆提取失败 (LLM 调用) conv={conversation_id}: {e}", exc_info=True)
        return 0

    logger.debug(f"[Memory] 长期记忆 LLM 原始返回 (前 200 字): {raw[:200]!r}")

    # 解析 JSON (LLM 可能包裹 ```json 代码块)
    items = _parse_json_items(raw)
    if not items:
        logger.info(f"[Memory] 长期记忆提取: 本轮无可提取内容 conv={conversation_id}")
        return 0

    saved = 0
    for item in items:
        # 防御性取值: LLM 可能返回不规范结构, 用 .get 避免 KeyError
        if not isinstance(item, dict):
            continue
        key = (item.get("key") or "").strip() if isinstance(item.get("key"), str) else ""
        value = (item.get("value") or "").strip() if isinstance(item.get("value"), str) else ""
        category_raw = item.get("category", "preference")
        category = category_raw.strip() if isinstance(category_raw, str) else "preference"
        category = category or "preference"
        if not key or not value:
            continue
        if agent_db.save_user_memory(user_id, key, value, category):
            saved += 1

    if saved:
        logger.info(f"[Memory] 长期记忆提取 conv={conversation_id} user={user_id} saved={saved}")
    return saved


def _parse_json_items(raw: str) -> List[dict]:
    """从 LLM 输出中解析 JSON 数组, 容错处理代码块包裹"""
    import json
    import re
    if not raw:
        return []
    # 去掉可能的 ```json ... ``` 包裹
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    except Exception:
        pass
    # 兜底: 尝试提取第一个 [ ... ] 片段
    m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return []


def build_user_profile_text(user_id: str) -> str:
    """
    加载用户的长期记忆, 格式化为可注入 system prompt 的文本。
    - 入参: 用户 id
    - 出参: 多行文本 (如 "- 常用图层: <图层名>"), 无记忆时返回空串

    注入位置: system prompt 的「用户画像」section (见 model/llm_client.py)

    ★ 含全局教训/过程记忆: user_id='global' 的 category='lesson' / 'workflow'
      会被合并注入到长期记忆, 让 Agent 跨会话记住"曾经犯过的错 + 自己摸索的修复方法
      + 从失败到成功的执行过程"。(仍属于长期记忆的一部分)

    ★ 记忆策略 (enable_preference_extraction):
      - 开启 (默认): 全量注入. 偏好/事实提取 与 错误案例学习 并行: 对话收尾时
        _async_memory_finale (本路偏好/事实) 与 _async_self_correction_scan (lesson/workflow)
        在同一触发点并行启动.
      - 关闭 (ENABLE_PREFERENCE_EXTRACTION=false): 只注入"错误案例+解决方案"类记忆
        (lesson/workflow) + 环境事实 (envfact, 如沙盒字体知识), 屏蔽 preference/entity/fact 噪声.
    """
    if not _is_db_mode():
        return ""
    # 1. 当前用户的偏好/实体/事实记忆
    memories = agent_db.load_user_memory(user_id)
    # 2. 全局教训记忆 (跨用户共享, 自纠捕捉器写入, 融入长期记忆)
    global_memories = agent_db.load_user_memory("global")
    memories = (memories or []) + (global_memories or [])
    if not memories:
        return ""

    # ★ 记忆策略过滤: 默认关闭偏好提取时, 只保留高价值类别
    #   lesson   = 工具错误+自纠方案 (核心价值)
    #   workflow = 失败→探索→修复的过程记忆
    #   envfact  = 环境事实 (沙盒字体/依赖等, 本质是"已知答案的问题")
    if not settings.enable_preference_extraction:
        keep_cats = {"lesson", "workflow", "envfact"}
        memories = [m for m in memories if m["category"] in keep_cats]
        if not memories:
            return ""

    # 按 category 分组, 每组列条目
    by_cat: dict = {}
    for m in memories:
        by_cat.setdefault(m["category"], []).append(m)
    cat_labels = {
        "preference": "偏好",
        "entity": "常用实体",
        "fact": "已知事实",
        "lesson": "已学教训",
        "workflow": "已学流程",
        "envfact": "环境事实",
    }
    lines = []
    # 教训优先显示在最前面 (最重要的反思学习, 融入长期记忆)
    cat_order = ["workflow", "lesson", "envfact", "preference", "entity", "fact"]
    for cat in cat_order:
        items = by_cat.get(cat, [])
        if not items:
            continue
        label = cat_labels.get(cat, cat)
        for it in items:
            value = it["value"]
            if cat == "lesson":
                value = format_accepted_lesson_for_prompt(value)
                if not value:
                    continue
            # value 可能含换行 (结构化教训五段格式), 换行后补 4 空格缩进保持结构
            indented = value.replace("\n", "\n    ")
            lines.append(f"- [{label}] {it['key']}:\n    {indented}")
    # 兜底: 处理未归类的 category
    for cat, items in by_cat.items():
        if cat in cat_order:
            continue
        label = cat_labels.get(cat, cat)
        for it in items:
            indented = it['value'].replace("\n", "\n    ")
            lines.append(f"- [{label}] {it['key']}:\n    {indented}")
    return "\n".join(lines)


# ==================== 调试辅助 ====================

def debug_dump_history(conversation_id: str) -> List[str]:
    """把会话历史格式化为可读字符串列表 (调试/日志用)"""
    if _is_db_mode():
        msgs = agent_db.load_messages(conversation_id)
    else:
        msgs = _mem_fallback.get(conversation_id, [])
    result = []
    for m in msgs:
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
        if isinstance(m, HumanMessage):
            result.append(f"[Human] {m.content[:60]}")
        elif isinstance(m, AIMessage):
            tc = m.tool_calls or []
            tc_names = [t.get("name", "") for t in tc]
            tail = f" tools={tc_names}" if tc_names else ""
            result.append(f"[AI] {(m.content or '')[:60]}{tail}")
        elif isinstance(m, ToolMessage):
            result.append(f"[Tool name={m.name} id={m.tool_call_id}] {(m.content or '')[:60]}")
        elif isinstance(m, SystemMessage):
            result.append(f"[System] {m.content[:60]}")
        else:
            result.append(f"[Unknown] {str(m)[:60]}")
    return result
