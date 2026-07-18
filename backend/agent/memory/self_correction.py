"""
自纠捕捉器 (Self-Correction Detector) — Agent 反思学习
======================================================================
对接 chat_service 工具循环, 识别"犯错 → 自纠"模式, 自动沉淀为全局教训记忆,
融入三层记忆中的「长期记忆」层 (不独立成层).

★ 设计目标: 让 Agent 跨会话记住"曾经犯过的错 + 自己摸索出的修复方法",
  下次遇到同类问题主动规避 (通过 system_prompt 的长期记忆/用户画像段注入).

★ 与记忆分层的关系:
  - 教训写入 user_memory 表 (user_id='global', category='lesson')
  - 被 memory_context.build_user_profile_text() 合并到长期记忆文本中
  - 在 system prompt 的「用户画像」段显示, 属于三层记忆中"长期记忆"的组成部分
  - ★ 不是独立的第四层记忆, 而是长期记忆的自动补充机制

★ 检测模式 (轻量规则, 不调 LLM, O(n) 扫一遍消息):
  ... → AIMessage(tool_calls=[X]) → ToolMessage(error)
       → AIMessage(thinking 含"修复/重试/重新/纠正"等关键词)
       → AIMessage(tool_calls=[X' 或 Y]) → ToolMessage(success)
  ↑ 这就是一次自纠. 把"失败原因 + 修复动作"总结成教训记忆.

★ 检测 vs 沉淀 的分工:
  - 检测阶段 (detect_self_corrections): 纯规则, 零 LLM, O(n) 扫一遍, 只负责"识别发生过自纠"
  - 沉淀阶段 (save_lesson_to_memory): 调 LLM 提炼, 把原始素材 (error_msg + thinking +
    修复动作) 归纳成结构化经验 (现象/根因/正确方法/验证信号/严重度 五段)
  - 为何沉淀要调 LLM: 规则拼接只能记录"发生了什么" (现象层), 提炼不出"正确做法是什么"
    (根因+方法层). 后者需要语义归纳, 正是 LLM 该做的事.
  - 调用时机: 沉淀在对话收尾的后台异步任务里 (fire-and-forget), 不阻塞用户响应.
  - 成本: deepseek-v4-flash (走百炼免费额度), 不占主对话模型额度.

★ 无降级策略:
  LLM 提炼失败 (超时/返回缺字段/解析错误) 时跳过本次写入, 不回退到规则拼接.
  保证 user_memory 里只存在结构化格式的教训, 宁缺毋滥, 避免流水账噪声污染记忆.

依赖方向: agent.memory.self_correction → agent_db (写记忆), memory_context (LLM 提炼)
"""
import asyncio
import json
import re
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from langchain_core.messages import AIMessage, ToolMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


# 自纠关键词 (出现在 AI thinking 里, 表明它在"反思修复")
_CORRECTION_KEYWORDS = [
    # 中文
    "修复", "重试", "重新", "纠正", "改正", "重画", "修正", "调整",
    "再来", "改一下", "fix", "retry", "again", "regenerate",
    # 自纠意识
    "失败", "不对", "错了", "有问题", "方块", "乱码", "缺失",
    "我来", "重新生成", "重新执行", "重新调用",
]

# 教训记忆的 user_id (全局共享, 所有用户都能读到)
GLOBAL_LESSON_USER_ID = "global"

# 教训去重窗口: 同一工具 + 同一错误关键词 1 小时内只记一次 (避免刷库)
_DEDUP_TTL_SECONDS = 3600
_recent_lessons: Dict[str, float] = {}  # key → timestamp

# LLM 提炼超时 (秒): 超时即视为失败, 跳过本次写入 (不降级)
# 实测 deepseek-v4-flash 提炼单条约 5~8s, 设 20s 留余量; 后台任务不阻塞用户响应
_DISTILL_TIMEOUT_SECONDS = 20

# 五段结构化经验的段名 (用于校验 LLM 返回是否完整)
_REQUIRED_FIELDS = ["现象", "根因", "正确方法", "验证信号", "严重度"]

_DISTILL_LESSON_SYSTEM = """你是经验提炼助手。下面会给你一段"工具失败 → Agent 自纠 → 修复成功"的过程原始素材，\
请你把它提炼成可复用的结构化经验，供下次遇到同类问题时直接照做。

只返回一个 JSON 对象（不要 markdown 代码块包裹，不要任何解释文字）：
{"现象":"失败时观察到的表象（一两句话，客观描述）",
  "根因":"为什么会失败——必须归纳本质原因，不能照抄报错原文",
  "正确方法":"①具体可执行步骤（写清调用哪个工具、传什么参数）②...③...",
  "验证信号":"如何确认修复成功（可观测的返回值或前端状态）",
  "严重度":"high 或 medium 或 low（基于对用户核心体验的影响程度）"}

硬要求（违反任一即视为失败）：
- 「正确方法」必须是具体步骤，写清工具名和关键参数，禁止"重试""检查参数"这种空话
- 「根因」要讲清本质（为什么错），不能只是复述报错信息
- 五段缺一不可，任何一段为空字符串都视为失败
- 「严重度」只能是 high / medium / low 三选一

正例（只返回这个 JSON，前后无其他内容）：
{"现象":"publish_geojson_layer 成功发布后前端未显示图斑","根因":"GeoServer 发布图层不等于前端自动激活显示，需显式触发图层可见性","正确方法":"① 发布后立即调用 toggle_layer_visibility(layer_name, \\"show\\", workspace=\\"xxx\\")\\n② 强制 locate: true 确保缩放到图层范围\\n③ 若底图遮挡同步执行 hide_layer(\\"底图名\\")","验证信号":"前端返回 visible: true 且 wms_loaded: true 且 bbox 非空","严重度":"high"}
"""


async def _distill_correction_to_structured(correction: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """用 LLM 把一次自纠原始素材提炼成五段结构化经验文本.

    入参:
      - correction: detect_self_corrections 返回的单条 dict (7 字段)
    出参:
      - 成功: 返回结构化 dict (现象/根因/正确方法/验证信号/严重度 五段)
      - 失败 (LLM 超时/返回缺字段/解析错误): 返回 None (★ 不降级, 由调用方决定跳过)

    复用 memory_context 的 LLM 实例和 JSON 解析器, 不重复造轮子.
    """
    try:
        from backend.agent.memory.memory_context import (
            _get_long_term_memory_llm, _parse_json_items,
        )
    except ImportError as e:
        logger.warning(f"[SelfCorrection] 无法导入 LLM 工具: {e}")
        return None

    # 把 correction 的 7 字段格式化成 LLM 输入素材
    material = (
        f"【失败工具】{correction.get('tool_name', '?')}\n"
        f"【错误现象】{(correction.get('error_msg') or '')[:400]}\n"
        f"【Agent 反思】{(correction.get('correction_thinking') or '')[:400]}\n"
        f"【修复工具】{correction.get('fixed_by_tool', '?')}\n"
        f"【修复参数】{(correction.get('fixed_args_hint') or '')[:200]}\n"
        f"【初步严重度】{correction.get('severity', 'medium')}\n"
    )

    async def _call():
        llm = _get_long_term_memory_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=_DISTILL_LESSON_SYSTEM),
            HumanMessage(content=material),
        ])
        raw = resp.content if hasattr(resp, "content") else str(resp)
        return raw

    try:
        raw = await asyncio.wait_for(_call(), timeout=_DISTILL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.info(f"[SelfCorrection] LLM 提炼超时 ({_DISTILL_TIMEOUT_SECONDS}s), 跳过 "
                    f"tool={correction.get('tool_name')}")
        return None
    except Exception as e:
        logger.warning(f"[SelfCorrection] LLM 调用失败: {e}")
        return None

    # 解析 JSON (复用 memory_context 的健壮解析器)
    items = _parse_json_items(raw)
    if not items:
        logger.info(f"[SelfCorrection] LLM 返回无法解析为 JSON, 跳过 tool={correction.get('tool_name')}")
        return None
    data = items[0]

    # ★ 严格校验: 五段必须齐全且非空, 否则视为失败 (不降级)
    if not all(data.get(f) and str(data[f]).strip() for f in _REQUIRED_FIELDS):
        missing = [f for f in _REQUIRED_FIELDS if not (data.get(f) and str(data[f]).strip())]
        logger.info(f"[SelfCorrection] LLM 返回缺字段 {missing}, 跳过 tool={correction.get('tool_name')}")
        return None
    severity = str(data["严重度"]).strip().lower()
    if severity not in ("high", "medium", "low"):
        logger.info(f"[SelfCorrection] 严重度非法 '{severity}', 跳过 tool={correction.get('tool_name')}")
        return None

    data["严重度"] = severity
    return data


def detect_self_corrections(messages: List) -> List[Dict[str, Any]]:
    """
    扫描消息序列, 识别所有"犯错→自纠"事件.

    入参:
        - messages: 完整消息列表 (含 HumanMessage / AIMessage / ToolMessage)
    出参:
        - corrections: [{tool_name, error_msg, correction_thinking,
                         fixed_by_tool, fixed_args_hint, severity}, ...]
    """
    corrections = []
    n = len(messages)

    # 先建索引: tool_call_id → (tool_name, args)
    # 因为 ToolMessage 只有 tool_call_id, 要回溯到对应的 AIMessage.tool_calls
    tool_call_index: Dict[str, Dict] = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tcid = tc.get("id")
                if tcid:
                    tool_call_index[tcid] = {
                        "name": tc.get("name", "?"),
                        "args": tc.get("args", {}),
                    }

    # 扫描: 找 ToolMessage(error) → 后续是否出现修复 thinking + 成功重调
    for i in range(n):
        msg = messages[i]
        if not isinstance(msg, ToolMessage):
            continue

        # 解析 ToolMessage content (是 JSON 字符串)
        tool_result = _parse_tool_content(msg.content)
        failed_validation = tool_result.get("agent_validation") if isinstance(tool_result, dict) else None
        failed_validation = failed_validation if isinstance(failed_validation, dict) else {}
        is_failed_observation = (
            isinstance(tool_result, dict)
            and (
                tool_result.get("type") == "error"
                or failed_validation.get("status") in {"failed", "warning"}
            )
        )
        if not is_failed_observation:
            continue

        # 找到失败的工具名
        failed_tc = tool_call_index.get(msg.tool_call_id, {})
        failed_tool = failed_tc.get("name", "?")
        failed_args = failed_tc.get("args", {})
        error_msg = (tool_result.get("msg") or tool_result.get("stderr")
                     or str(tool_result))[:300]
        repair_plan = failed_validation.get("repair_plan") or {}

        # 向后看最多 6 条消息, 找"修复 thinking" + "成功重调"
        correction_thinking = ""
        fixed_by_tool = None
        fixed_args_hint = ""
        found_fix = False
        next_agent_validation = {}
        repaired_tool_call_id = ""

        for j in range(i + 1, min(i + 7, n)):
            nxt = messages[j]

            # 收集修复 thinking
            if isinstance(nxt, AIMessage):
                thinking = _extract_thinking(nxt)
                if thinking and _has_correction_keyword(thinking):
                    correction_thinking = thinking[:400]
                # 同名工具或相关工具再次调用
                if nxt.tool_calls:
                    for tc in nxt.tool_calls:
                        tname = tc.get("name", "")
                        # 同工具重试, 或明显的"修复性"工具
                        if tname == failed_tool or _is_related_fix_tool(failed_tool, tname):
                            fixed_by_tool = tname
                            fixed_args_hint = _summarize_args(tc.get("args", {}))
                            # 标记为"已发起修复", 还需确认后续是否成功
                            # 找这个 tool_call 对应的 ToolMessage
                            for k in range(j + 1, min(j + 4, n)):
                                kmsg = messages[k]
                                if isinstance(kmsg, ToolMessage) and kmsg.tool_call_id == tc.get("id"):
                                    kres = _parse_tool_content(kmsg.content)
                                    validation = kres.get("agent_validation") if isinstance(kres, dict) else None
                                    validation = validation if isinstance(validation, dict) else {}
                                    if validation.get("status") == "passed":
                                        found_fix = True
                                        next_agent_validation = validation
                                        repaired_tool_call_id = tc.get("id") or ""
                                    break
                            if found_fix:
                                break
                if found_fix:
                    break

        if found_fix:
            severity = _assess_severity(failed_tool, error_msg, correction_thinking)
            corrections.append({
                "tool_name": failed_tool,
                "error_msg": error_msg,
                "correction_thinking": correction_thinking,
                "fixed_by_tool": fixed_by_tool,
                "fixed_args_hint": fixed_args_hint,
                "severity": severity,  # high / medium / low
                "failure_type": repair_plan.get("failure_type") or "unknown_failure",
                "repair_plan": repair_plan,
                "actual_repair_action": fixed_by_tool,
                "next_agent_validation": next_agent_validation,
                "repair_success": True,
                "evidence_refs": [
                    ref for ref in [msg.tool_call_id, repaired_tool_call_id] if ref
                ],
                "detected_at": datetime.now().isoformat(timespec="seconds"),
            })

    return corrections


def _parse_tool_content(content) -> Optional[Dict]:
    """解析 ToolMessage.content (可能是 JSON 字符串或 dict)."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _extract_thinking(ai_msg: AIMessage) -> str:
    """从 AIMessage 提取 thinking 内容 (additional_kwargs.thinking_content 优先)."""
    ak = ai_msg.additional_kwargs or {}
    thinking = ak.get("thinking_content") or ""
    if thinking:
        return str(thinking)
    return str(ai_msg.content or "")


def _has_correction_keyword(text: str) -> bool:
    """检测文本是否含自纠关键词."""
    text_lower = text.lower()
    for kw in _CORRECTION_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    return False


def _is_related_fix_tool(failed: str, candidate: str) -> bool:
    """判断 candidate 是否是 failed 的相关修复工具."""
    # 沙盒代码工具族: run_python_code / run_shell_command 互为修复手段
    sandbox_tools = {"run_python_code", "run_shell_command", "render_sandbox_image"}
    if failed in sandbox_tools and candidate in sandbox_tools:
        return True
    # 上传族
    upload_tools = {"upload_raster_layer", "upload_shapefile_layer"}
    if failed in upload_tools and candidate in upload_tools:
        return True
    return False


def _assess_severity(tool_name: str, error_msg: str, thinking: str) -> str:
    """评估教训严重度 (决定是否值得长期记忆)."""
    text = (error_msg + " " + thinking).lower()
    # 高严重度: 影响功能可用性 (字体缺失/依赖缺失/配置错误)
    high_signals = ["font", "字体", "missing", "未安装", "not found", "不可用",
                    "permission", "权限", "timeout", "超时"]
    for sig in high_signals:
        if sig.lower() in text:
            return "high"
    # 中: 参数错误/格式错误
    medium_signals = ["参数", "argument", "format", "格式", "invalid", "无效"]
    for sig in medium_signals:
        if sig.lower() in text:
            return "medium"
    return "low"


def _summarize_args(args: dict, max_len: int = 150) -> str:
    """摘要工具参数 (避免长代码污染记忆)."""
    try:
        s = json.dumps(args, ensure_ascii=False)
        if len(s) > max_len:
            return s[:max_len] + "..."
        return s
    except Exception:
        return "(无法序列化)"


async def save_lesson_to_memory(correction: Dict[str, Any]) -> bool:
    """
    把一次自纠事件沉淀为全局教训记忆.
    - 写入 user_memory 表, user_id='global', category='lesson'
    - key 用工具名 + 错误特征做哈希, 同类错误去重
    - value: ★ 由 LLM 提炼成五段结构化经验 (现象/根因/正确方法/验证信号/严重度)
    - ★ 无降级: LLM 提炼失败则跳过本次写入, 保证库里只有结构化格式

    返回: 是否新写入 (True=新教训, False=去重跳过或提炼失败)
    """
    # ★ LLM 提炼: 失败返回 None → 不降级, 直接跳过
    distilled = await _distill_correction_to_structured(correction)
    if not distilled:
        logger.info(f"[SelfCorrection] 提炼失败跳过 (不降级) tool={correction['tool_name']}")
        return False

    from backend.agent.memory import agent_db
    from backend.agent.memory.lesson_policy import (
        audit_lesson_decision,
        build_lesson_contract,
        choose_strongest_lesson,
    )

    lesson = build_lesson_contract(correction, distilled)
    audit_lesson_decision(lesson)
    if lesson["status"] != "accepted":
        logger.info(
            f"[SelfCorrection] 教训未准入: status={lesson['status']} "
            f"reason={lesson['admission_reason']}"
        )
        return False

    dedup_key = lesson["dedup_key"]
    import time
    now = time.time()
    if dedup_key in _recent_lessons and now - _recent_lessons[dedup_key] < _DEDUP_TTL_SECONDS:
        logger.debug(f"[SelfCorrection] 教训去重跳过: {dedup_key}")
        return False
    existing = agent_db.load_user_memory(GLOBAL_LESSON_USER_ID, category="lesson")
    if not choose_strongest_lesson(lesson, [item.get("value") for item in existing]):
        logger.debug(f"[SelfCorrection] 已有证据强度不低于候选: {dedup_key}")
        return False
    _recent_lessons[dedup_key] = now
    value = json.dumps(lesson, ensure_ascii=False)
    ok = agent_db.save_user_memory(
        user_id=GLOBAL_LESSON_USER_ID,
        key=dedup_key,
        value=value,
        category="lesson",
    )
    if ok:
        logger.info(
            f"[SelfCorrection] 教训已沉淀 (accepted): tool={correction['tool_name']} "
            f"severity={correction['severity']} key={dedup_key}"
        )
    return ok


async def scan_and_save_corrections(messages: List, user_id: str = None) -> List[Dict]:
    """
    一站式入口: 扫描消息序列 → 识别自纠 → LLM 提炼 → 沉淀结构化教训.
    供 chat_service 在对话收尾时调用 (后台异步, 不阻塞用户).

    入参:
        - messages: 完整消息列表
        - user_id: 当前用户 (用于日志, 教训本身写全局)
    出参:
        - saved: 实际写入的教训列表 (去重 + 提炼成功后的)
    """
    try:
        corrections = detect_self_corrections(messages)
        if not corrections:
            return []

        saved = []
        for c in corrections:
            # 只记 high/medium 严重度 (low 多为参数小错, 价值低)
            if c["severity"] == "low":
                continue
            # ★ 只写 lesson (结构化), 不再双写 workflow (内容重复, 已废弃)
            lesson_written = await save_lesson_to_memory(c)
            if lesson_written:
                saved.append(c)

        if saved:
            logger.info(
                f"[SelfCorrection] 识别 {len(corrections)} 次自纠, "
                f"沉淀 {len(saved)} 条结构化教训 (user={user_id or 'N/A'})"
            )
        return saved
    except Exception as e:
        logger.warning(f"[SelfCorrection] 扫描失败 (不影响主流程): {e}")
        return []
