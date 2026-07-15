"""
记忆管理工具集 (Agent 层 / 工具) — 跟随记忆, 留在 agent 层
- 入参: 由 AI 决定 (查看/清空等动作)
- 方法: 包装 agent_db 的记忆读写函数为 LangChain @tool
- 出参: 标准工具结果 dict (查看类返回 data, 清空类返回 summary)

设计说明:
  记忆工具与其它工具不同 —— 它操作的是 agent 自身的记忆表
  (user_memory / conversation_summary), 而非业务数据。
  因此它留在 agent 层 (跟随记忆), 不随其它工具迁往 model 层,
  避免出现 model → agent 的反向依赖。

  ★ conversation_id 的获取:
    LangChain @tool 的 invoke 只接收 LLM 给的 args, 拿不到运行时上下文。
    运行时上下文由 chat_service 通过 agent.runtime.context_vars 注入,
    工具内部通过 get_current_conversation_id() / get_current_user_id() 读取。
"""
import logging
from typing import Dict, Any

from langchain_core.tools import tool
from backend.model.tools.tool_registry import register_tool
from backend.agent.runtime.context_vars import (
    get_current_conversation_id,
    get_current_user_id,
)

logger = logging.getLogger(__name__)


# ==================== 工具实现 ====================

@register_tool("memory")
@tool
def view_user_memory() -> Dict[str, Any]:
    """
    查看长期记忆 (跨会话保留, 当前用户私有 + 全局共享的教训/流程/环境事实)。
    这些记忆主要来自工具失败→自纠成功的自动沉淀 (lesson/workflow/envfact),
    会注入后续对话的 system prompt, 让 Agent 规避同类错误。

    入参: 无
    出参: 记忆条目列表 (key/value/category), 用于向用户展示 AI 学到了什么
    触发场景: 用户说"你学到了什么"、"查看长期记忆"、"我的教训记忆"

    ★ 记忆策略 (v17): 默认只存"错误案例+解决方案" (lesson/workflow) +
      环境事实 (envfact); preference/entity/fact 提取已默认关闭。
    ★ 全局共享: user_id='global' 的记忆 (教训/流程) 所有用户可见,
      这里一并返回, 不需要单独查询。
    """
    try:
        from backend.agent.memory import agent_db
        user_id = get_current_user_id()
        # 合并: 当前用户私有 + 全局共享 (与 build_user_profile_text 注入逻辑一致)
        own = agent_db.load_user_memory(user_id) or []
        shared = agent_db.load_user_memory("global") or []
        memories = own + shared

        if not memories:
            return {
                "type": "success",
                "summary": (
                    f"当前用户 ({user_id}) 暂无长期记忆。\n"
                    f"记忆只在工具失败→自纠成功时自动沉淀 (lesson/workflow), "
                    f"正常对话不会产生记忆。"
                ),
                "data": {"user_id": user_id, "memories": [], "count": 0},
            }

        # 分类标签 (与 memory.build_user_profile_text 保持一致)
        cat_labels = {
            "lesson": "已学教训",
            "workflow": "已学流程",
            "envfact": "环境事实",
            "preference": "偏好",
            "entity": "常用实体",
            "fact": "已知事实",
        }
        # 显示顺序: 教训/流程/环境事实优先 (高价值)
        cat_order = ["workflow", "lesson", "envfact", "preference", "entity", "fact"]
        lines = [f"📋 长期记忆 (共 {len(memories)} 条, 含全局共享)\n"]
        by_cat: dict = {}
        for m in memories:
            by_cat.setdefault(m["category"], []).append(m)
        for cat in cat_order:
            items = by_cat.pop(cat, None)
            if not items:
                continue
            label = cat_labels.get(cat, cat)
            lines.append(f"【{label}】")
            for it in items:
                lines.append(f"  • {it['key']}: {it['value']}")
            lines.append("")
        # 兜底: 未归类的 category
        for cat, items in by_cat.items():
            label = cat_labels.get(cat, cat)
            lines.append(f"【{label}】")
            for it in items:
                lines.append(f"  • {it['key']}: {it['value']}")
            lines.append("")

        return {
            "type": "success",
            "summary": "\n".join(lines),
            "data": {"user_id": user_id, "memories": memories, "count": len(memories)},
        }
    except Exception as e:
        return {"type": "error", "msg": f"加载长期记忆失败: {e}"}


@register_tool("memory")
@tool
def view_conversation_summary() -> Dict[str, Any]:
    """
    查看当前会话的短期摘要 (本对话早期消息被 AI 自动压缩成的要点)。
    摘要保留了过去轮次的关键进展, 用于在长对话中节省上下文。

    入参: 无
    出参: 摘要文本 + 覆盖到的消息 id, 或提示"暂无摘要"
    触发场景: 用户说"查看会话摘要"、"我们之前聊了什么"、"当前对话的总结"
    """
    try:
        from backend.agent.memory import agent_db
        conv_id = get_current_conversation_id()
        if not conv_id:
            return {"type": "error", "msg": "当前没有活跃会话, 无法查看摘要"}

        summary_data = agent_db.load_summary(conv_id)
        if not summary_data or not summary_data.get("summary"):
            return {
                "type": "success",
                "summary": f"当前会话 ({conv_id[:8]}...) 还没有摘要。\n"
                           f"当对话历史累计超过阈值时, 我会自动把早期消息压缩成摘要。",
                "data": {"conversation_id": conv_id, "has_summary": False},
            }

        summary_text = summary_data["summary"]
        summarized_upto = summary_data.get("summarized_upto", 0)
        preview = summary_text if len(summary_text) <= 500 else summary_text[:500] + "...(截断)"

        return {
            "type": "success",
            "summary": f"📝 会话摘要 ({conv_id[:8]}...)\n覆盖到消息 id: {summarized_upto}\n\n{preview}",
            "data": {
                "conversation_id": conv_id,
                "summary": summary_text,
                "summarized_upto": summarized_upto,
                "has_summary": True,
            },
        }
    except Exception as e:
        return {"type": "error", "msg": f"加载会话摘要失败: {e}"}


@register_tool("memory")
@tool
def clear_user_memory(confirm: bool = False) -> Dict[str, Any]:
    """
    清空当前用户的全部长期记忆 (跨会话的偏好/实体/事实)。
    ⚠️ 不可恢复操作。清空后, AI 将忘记所有从历史对话中积累的用户画像。

    入参:
        - confirm (bool): 必须为 True 才会真正执行, 防止误触发
    出参: 删除条数
    触发场景: 用户说"清空我的记忆"、"忘记我的偏好"、"重置长期记忆"
    """
    try:
        if not confirm:
            return {
                "type": "error",
                "msg": "清空长期记忆是不可逆操作, 请明确确认 (调用时 confirm=true)。"
                       "请先向用户确认后再执行。",
            }

        from backend.agent.memory import agent_db
        user_id = get_current_user_id()

        # 先查条数 (用于反馈), 再批量删除
        memories = agent_db.load_user_memory(user_id)
        count = len(memories)
        if count == 0:
            return {
                "type": "success",
                "summary": f"用户 ({user_id}) 本来就没有长期记忆, 无需清空。",
                "data": {"user_id": user_id, "deleted_count": 0},
            }

        # 逐条删除 (复用现有 delete_user_memory, 避免新增 SQL)
        deleted = 0
        for m in memories:
            if agent_db.delete_user_memory(user_id, m["key"]):
                deleted += 1

        logger.info(f"[Memory] 清空长期记忆: user={user_id}, deleted={deleted}/{count}")
        return {
            "type": "success",
            "summary": f"✅ 已清空 {deleted} 条长期记忆。\n"
                       f"从现在起, 我将以「无记忆」状态与您对话, 重新积累您的偏好。",
            "data": {"user_id": user_id, "deleted_count": deleted},
        }
    except Exception as e:
        return {"type": "error", "msg": f"清空长期记忆失败: {e}"}


@register_tool("memory")
@tool
def clear_conversation_summary(confirm: bool = False) -> Dict[str, Any]:
    """
    清空当前会话的短期摘要。
    清空后, 下次构建上下文时会重新加载全部历史消息 (不再依赖摘要压缩)。

    入参:
        - confirm (bool): 必须为 True 才会真正执行, 防止误触发
    出参: 是否成功
    触发场景: 用户说"清空会话摘要"、"重置当前对话的摘要"
    """
    try:
        if not confirm:
            return {
                "type": "error",
                "msg": "清空会话摘要请明确确认 (调用时 confirm=true)。",
            }

        from backend.agent.memory import agent_db
        conv_id = get_current_conversation_id()
        if not conv_id:
            return {"type": "error", "msg": "当前没有活跃会话"}

        # 用空摘要覆盖, 等价于清空 (summarized_upto=0 让下次重新加载全部历史)
        ok = agent_db.save_summary(conv_id, "", 0)
        if ok:
            return {
                "type": "success",
                "summary": f"✅ 已清空会话 ({conv_id[:8]}...) 的摘要。下次对话将基于完整历史。",
                "data": {"conversation_id": conv_id, "cleared": True},
            }
        return {"type": "error", "msg": "清空摘要失败, 数据库写入异常"}
    except Exception as e:
        return {"type": "error", "msg": f"清空会话摘要失败: {e}"}
