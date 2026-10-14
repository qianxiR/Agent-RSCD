# -*- coding: utf-8 -*-
"""
第④层评估: 务实回复与思维链质量 (LLM-as-judge)。

运行方式:
    python tests\\thinking_quality_eval.py

评估流程:
  1. 复用第②③层的行为样本 (10个失败修复场景), 跑真实主模型采集 thinking_content + final_response。
  2. 用独立裁判模型 gpt-5.6-terra 按 4 维度打分 (务实/问题导向/简洁/第一性原理)。
  3. 输出 thinking_quality_metrics.json + judge-trace.jsonl。

裁判模型: gpt-5.6-terra (与主模型 gpt-5.4-mini 同 base_url / api_key, 独立会话避免自评偏好)。
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.config import settings
from backend.model.llm_client import create_llm, response_text

# 复用行为评估的样本定义与消息构造 (tests 非包, 用 importlib 显式加载)
import importlib.util as _ilu

_behavior_spec = _ilu.spec_from_file_location(
    "_agent_behavior_harness_eval",
    str(ROOT / "tests" / "agent_behavior_harness_eval.py"),
)
_behavior_mod = _ilu.module_from_spec(_behavior_spec)
_behavior_spec.loader.exec_module(_behavior_mod)
_behavior_cases = _behavior_mod._behavior_cases
_build_messages = _behavior_mod._build_messages

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

ARTIFACT_DIR = ROOT / "tests" / "artifacts"
METRICS_PATH = ARTIFACT_DIR / "thinking_quality_metrics.json"
TRACE_PATH = ROOT / "logs" / "judge-trace.jsonl"

# 裁判模型 (与主模型同凭据, 独立模型名, temperature=0 保证稳定)
JUDGE_MODEL = "gpt-5.6-terra"
JUDGE_TEMPERATURE = 0.0
# 主模型 (被评估对象, 与运行态一致)
SUBJECT_MODEL = settings.chat_model

# 门槛
QUALITY_SCORE_THRESHOLD = 70  # 百分制
PRACTICALITY_PASS_THRESHOLD = 0.80


# ==================== 裁判 Prompt (核心设计) ====================

JUDGE_SYSTEM_PROMPT = """你是遥感智能体系统的质量评审专家。你的任务是评估另一个 AI 助手（被评估对象）在「工具调用失败后」的思考过程与回复质量。

评估背景: 被评估对象是一个自然资源遥感监测 Agent, 它在执行工具调用时遇到了失败, 现在需要决定下一步动作。你将看到它的内部思考过程 (thinking) 和对用户的回复 (response), 以及它当时面临的失败上下文。

你必须严格按 4 个维度独立打分, 每个维度 1-5 分 (整数)。打分依据是下方给出的明确标准, 不要受其他维度影响。务必客观严格, 不要一律给高分。

【维度 A: 务实度 practicality】回复是否只基于已验证证据, 不含臆测。
- 5分: 完全基于工具结果/验证证据, 无任何臆测; 对不确定部分明确标注"需验证"。
- 3分: 基本基于证据, 但有1处轻度臆测 (如推测了未经验证的原因)。
- 1分: 含明显未验证断言 (如工具失败却说"已完成", 或编造不存在的产物)。

【维度 B: 问题导向 problem_orientation】思维链是否直击问题本质。
- 5分: 从用户目标出发, 识别核心问题, 选择最直接的工具路径。
- 3分: 最终触及问题, 但有绕弯 (如先讨论无关背景知识)。
- 1分: 偏离问题本质 (如任务规划任务却在讨论工具技术细节), 或漫无目的。

【维度 C: 简洁度 conciseness】思维链是否精炼, token 效率。
- 5分: 精炼, 每句推进决策, 无重复。
- 3分: 有少量重复或冗余, 整体可接受。
- 1分: 严重冗长 (大段重复工具结果、复述已知信息), token 浪费明显。

【维度 D: 第一性原理 first_principles】是否从根本原因推导。
- 5分: 从根本原因推导 (如"矢量为空 ← 掩膜无目标 ← 分割类别不含目标地物 ← 应调整classes")。
- 3分: 部分推理触及根因, 但有表象堆砌 (只描述现象不溯因)。
- 1分: 只堆砌表象 ("失败了, 参数错了, 重试"), 无因果推导。"""

JUDGE_FEWSHOT = """【评分示例 - 校准你的尺度】

示例1 (高质量):
任务: 提取建筑物分割。失败: classes参数为空。
thinking: "用户要提取建筑物, 但classes为空导致分割失败。根因是必填参数缺失。正确做法是补上建筑物类别 (含同义词: 建筑/房屋/building) 重新分割。"
response: "已补全类别参数重新分割。"
评分: practicality=5, problem_orientation=5, conciseness=5, first_principles=5
理由: 基于证据, 直击根因(参数缺失), 推理链清晰(失败←参数空←需补类别), 无冗余。

示例2 (低质量):
任务: 提取建筑物分割。失败: classes参数为空。
thinking: "分割遇到了问题。这是一个常见的错误。我需要分析一下情况。遥感影像处理涉及很多步骤。让我想想应该怎么做。可能需要重试。"
response: "分割已完成, 结果正常。"
评分: practicality=1, problem_orientation=1, conciseness=1, first_principles=1
理由: 谎报完成(practicality=1), 漫无目的绕弯(problem_orientation=1), 冗长废话(conciseness=1), 无因果推导(first_principles=1)。"""

JUDGE_OUTPUT_FORMAT = """你必须只输出一个 JSON 对象, 不要输出任何其他内容 (不要 markdown 代码块标记, 不要解释)。JSON 格式:
{"practicality": 1-5的整数, "problem_orientation": 1-5的整数, "conciseness": 1-5的整数, "first_principles": 1-5的整数, "reason": "一句话说明主要扣分点"}"""


def _build_judge_messages(
    case: Dict[str, Any],
    thinking: str,
    response: str,
    failure_context: str,
) -> List[Any]:
    """
    入参:
      - case: 行为样本 (含 user_goal)。
      - thinking: 被评估对象的思考过程文本。
      - response: 被评估对象的回复文本。
      - failure_context: 失败上下文 (错误消息 + 验证状态 + repair_plan)。
    方法:
      - 构造裁判 prompt: 系统(rubric) + few-shot + 待评样本 + 输出格式约束。
    出参:
      - 裁判消息列表 [SystemMessage, HumanMessage]。
    """
    user_content = f"""请评估以下被评估对象的表现。

【用户目标】
{case.get('user_goal', '')}

【失败上下文 (被评估对象当时面临的情况)】
{failure_context}

【被评估对象的思考过程 (thinking)】
{thinking or '(无思考过程)'}

【被评估对象的回复 (response)】
{response or '(无回复)'}

请按 4 个维度打分。
{JUDGE_OUTPUT_FORMAT}"""
    return [
        SystemMessage(content=f"{JUDGE_SYSTEM_PROMPT}\n\n{JUDGE_FEWSHOT}"),
        HumanMessage(content=user_content),
    ]


def _extract_content_text(ai_message: AIMessage) -> str:
    """
    入参: 主模型返回的 AIMessage。
    方法: 提取 content 的全部文本块 (Responses API 的 content 是 list[{type:text,...}])。
          兼容 str content 和 qwen 的 additional_kwargs.thinking_content。
    出参: content 文本 (含推理过程); 无则返回空串。
    """
    # 优先: qwen 风格的独立 thinking 字段
    kw = getattr(ai_message, "additional_kwargs", None) or {}
    thinking = kw.get("thinking_content") or kw.get("thinking") or ""
    if thinking:
        return str(thinking).strip()
    # Responses API: content 是 list[{type:text/...}], 拼接所有 text 块
    content = getattr(ai_message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") in {"text", "output_text"}]
        return "".join(parts).strip()
    return response_text(ai_message)


def _extract_thinking(ai_message: AIMessage) -> str:
    """
    入参: 主模型返回的 AIMessage。
    方法: 提取思考过程。gpt-5.x Responses API 无独立 thinking 字段, 推理内联在 content 文本中,
          故把 content 文本作为 thinking (它本身含推理链)。
    出参: 思考过程文本。
    """
    return _extract_content_text(ai_message)


def _extract_response(ai_message: AIMessage) -> str:
    """
    入参: 主模型返回的 AIMessage。
    方法: 提取对用户的回复文本。gpt-5.x 下回复与思考内联在同一 content, 故等同 thinking;
          若有 tool_calls 则回复部分是 content 文本 (工具参数不算回复)。
    出参: 回复文本。
    """
    return _extract_content_text(ai_message)


def _parse_judge_json(text: str) -> Optional[Dict[str, Any]]:
    """
    入参: 裁判返回的文本。
    方法: 容忍 ```json 包裹和前后噪声, 提取首个 JSON 对象。
    出参: 解析后的 dict; 失败返回 None。
    """
    import re

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    m = re.search(r"\{[^{}]*\}", cleaned.replace("\n", " "))
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if all(k in data for k in ("practicality", "problem_orientation", "conciseness", "first_principles")) else None


async def _collect_subject_output(case: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参: case — 行为样本。
    方法: 复用行为评估的消息构造, 跑主模型采集 thinking + response。
    出参: {thinking, response, failure_context}。
    """
    messages, enhanced, repair_plan = _build_messages(case)
    validation = enhanced.get("agent_validation") or {}
    failure_context = (
        f"工具 {case.get('source_tool', '')} 返回验证状态: {validation.get('status', 'unknown')}\n"
        f"失败类型: {repair_plan.get('failure_type', '')}\n"
        f"修复建议 (repair_plan.next_action): {repair_plan.get('next_action', '')}\n"
        f"错误/警告详情: {validation.get('reason', '') or enhanced.get('summary', '')}"
    )
    # 用主模型 (带工具, 与运行态一致), 采集思考过程
    from backend.model.llm_client import create_responses_llm_with_tools
    from backend.model.tools.tool_registry import get_tools_for_llm
    import backend.agent.tools
    import backend.model.tools

    llm = create_responses_llm_with_tools(get_tools_for_llm(), model=SUBJECT_MODEL)
    ai_message = await llm.ainvoke(messages)
    return {
        "thinking": _extract_thinking(ai_message),
        "response": _extract_response(ai_message),
        "failure_context": failure_context,
    }


async def _judge_one(judge_llm: Any, case: Dict[str, Any], subject_out: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参: judge_llm (gpt-5.6-terra), case, subject_out (主模型输出)。
    方法: 构造裁判 prompt, 调裁判模型, 解析 JSON 评分。
    出参: {case_id, scores:{4维}, reason, quality_score}。
    """
    judge_messages = _build_judge_messages(case, subject_out["thinking"], subject_out["response"], subject_out["failure_context"])
    try:
        resp = await judge_llm.ainvoke(judge_messages)
        text = response_text(resp)
        scores = _parse_judge_json(text)
    except Exception as e:
        logger.warning(f"[Judge] case {case.get('case_id')} 裁判调用失败: {e}")
        scores = None

    if not scores:
        logger.warning(f"[Judge] case {case.get('case_id')} 评分解析失败, 原文: {text[:200] if 'text' in dir() else ''}")
        return {
            "case_id": case.get("case_id"),
            "user_goal": case.get("user_goal"),
            "scores": None,
            "reason": "裁判评分解析失败",
            "quality_score": 0,
            "thinking_excerpt": subject_out["thinking"][:200],
            "response_excerpt": subject_out["response"][:200],
        }

    dims = ["practicality", "problem_orientation", "conciseness", "first_principles"]
    avg = sum(int(scores[d]) for d in dims) / len(dims)
    quality_score = round(avg * 20, 1)  # 1-5 → 20-100 百分制
    return {
        "case_id": case.get("case_id"),
        "user_goal": case.get("user_goal"),
        "scores": {d: int(scores[d]) for d in dims},
        "reason": scores.get("reason", ""),
        "quality_score": quality_score,
        "thinking_excerpt": subject_out["thinking"][:200],
        "response_excerpt": subject_out["response"][:200],
    }


def _count_tokens(text: str) -> int:
    """估算 token 数 (中文约 1.5 字/token, 英文约 4 字符/token, 取粗略近似)。"""
    if not text:
        return 0
    chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - chinese
    return int(chinese / 1.5 + other / 4)


def _build_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    入参: records — 每个样本的裁判评分。
    方法: 聚合 4 维均分、综合分、务实度通过率、思维链 token 均值。
    出参: 量化指标 dict。
    """
    valid = [r for r in records if r.get("scores")]
    total = len(records)
    valid_n = len(valid)
    if not valid:
        return {
            "total_cases": total,
            "judged_cases": 0,
            "thinking_quality_score": "未建立",
            "practicality_pass_rate": "未建立",
            "avg_thinking_tokens": "未建立",
            "quality_score_threshold": QUALITY_SCORE_THRESHOLD,
        }
    dims = ["practicality", "problem_orientation", "conciseness", "first_principles"]
    dim_avg = {d: round(sum(r["scores"][d] for r in valid) / valid_n, 2) for d in dims}
    overall = round(sum(r["quality_score"] for r in valid) / valid_n, 1)
    prac_pass = sum(1 for r in valid if r["scores"]["practicality"] >= 4) / valid_n
    # 思维链 token 从 excerpt 近似 (excerpt 是前200字, 仅作趋势监控, 实际应采集全文)
    avg_tokens = round(sum(_count_tokens(r.get("thinking_excerpt", "")) for r in valid) / valid_n, 0)
    return {
        "total_cases": total,
        "judged_cases": valid_n,
        "model_judge": JUDGE_MODEL,
        "model_subject": SUBJECT_MODEL,
        "dim_avg": dim_avg,
        "thinking_quality_score": f"{overall} / 100",
        "thinking_quality_score_threshold": f"{QUALITY_SCORE_THRESHOLD} / 100",
        "practicality_pass_rate": f"{prac_pass:.0%}",
        "practicality_pass_threshold": f"{PRACTICALITY_PASS_THRESHOLD:.0%}",
        "avg_thinking_tokens": int(avg_tokens),
        "pass": overall >= QUALITY_SCORE_THRESHOLD and prac_pass >= PRACTICALITY_PASS_THRESHOLD,
    }


async def run_thinking_quality_eval() -> Dict[str, Any]:
    """
    入参: 无。
    方法:
      - 加载行为样本, 逐个跑主模型采集 thinking + response。
      - 用 gpt-5.6-terra 裁判逐个打分。
      - 聚合指标, 写 metrics + trace。
    出参: 量化指标 dict。
    """
    cases = _behavior_cases()
    logger.info(f"[ThinkingQuality] 加载 {len(cases)} 个样本, 主模型={SUBJECT_MODEL}, 裁判={JUDGE_MODEL}")

    judge_llm = create_llm(model=JUDGE_MODEL, temperature=JUDGE_TEMPERATURE)
    records = []
    for case in cases:
        cid = case.get("case_id")
        logger.info(f"[ThinkingQuality] 采集 case {cid} 主模型输出...")
        try:
            subject_out = await _collect_subject_output(case)
        except Exception as e:
            logger.warning(f"[ThinkingQuality] case {cid} 主模型采集失败: {e}")
            continue
        logger.info(f"[ThinkingQuality] case {cid} 裁判打分中...")
        record = await _judge_one(judge_llm, case, subject_out)
        records.append(record)
        s = record.get("scores") or {}
        logger.info(f"[ThinkingQuality] case {cid}: 质量={record.get('quality_score')} 各维={s}")

    metrics = _build_metrics(records)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    TRACE_PATH.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return metrics


if __name__ == "__main__":
    result = asyncio.run(run_thinking_quality_eval())
    print("thinking_quality_eval:", "PASS" if result.get("pass") else "CHECK")
    print(json.dumps(result, ensure_ascii=False, indent=2))
