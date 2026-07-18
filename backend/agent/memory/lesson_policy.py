"""基于修复证据的结构化 lesson 准入策略。"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from backend.agent.observability.decision_audit import append_jsonl


LESSON_AUDIT_PATH = Path("logs") / "lesson-policy.jsonl"
LESSON_STATUSES = {"accepted", "review", "rejected"}


def _sanitize_text(value: Any, limit: int = 600) -> str:
    """
    入参:
      - value: 待写入长期记忆或审计的内容。
      - limit: 最大字符数。
    方法:
      - 移除 Windows/Unix 绝对路径并压缩空白, 防止隐私路径进入 prompt。
    出参:
      - 已清洗且截断的文本。
    """
    text = str(value or "")
    text = re.sub(r"[A-Za-z]:[\\/][^\s,;，；]+", "<path>", text)
    text = re.sub(r"(?<!:)\/(?:[^\s,;，；]+\/)*[^\s,;，；]+", "<path>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def build_failure_signature(correction: Dict[str, Any]) -> str:
    """
    入参:
      - correction: 自纠检测器关联出的失败与修复证据。
    方法:
      - 组合源工具、failure_type 与去路径后的错误类型, 形成可泛化签名。
    出参:
      - 稳定失败签名。
    """
    failure_type = correction.get("failure_type") or "unknown_failure"
    error = _sanitize_text(correction.get("error_msg"), 220).lower()
    error = re.sub(r"\d+", "N", error)
    return f"{correction.get('tool_name') or 'unknown'}|{failure_type}|{error}"


def build_dedup_key(failure_signature: str, root_cause: str, successful_repair: str) -> str:
    """
    入参:
      - failure_signature/root_cause/successful_repair: 教训的稳定语义字段。
    方法:
      - 对三字段标准化后计算 SHA-256 短摘要, 避免错误原文成为数据库 key。
    出参:
      - lesson_<16位摘要> 格式的去重键。
    """
    material = "|".join(
        _sanitize_text(item, 500).lower()
        for item in (failure_signature, root_cause, successful_repair)
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"lesson_{digest}"


def determine_admission_status(correction: Dict[str, Any], distilled: Dict[str, Any]) -> Dict[str, str]:
    """
    入参:
      - correction: 含 repair_success 与 next_agent_validation 的修复事件。
      - distilled: LLM 提炼出的根因、正确方法、验证信号等字段。
    方法:
      - repair_success=true 且 verification=passed 才 accepted。
      - 高严重度但未成功修复进入 review；其余证据不足或空内容 rejected。
    出参:
      - status/reason 准入结论。
    """
    required = ["现象", "根因", "正确方法", "验证信号", "严重度"]
    missing = [name for name in required if not _sanitize_text(distilled.get(name))]
    if missing:
        return {"status": "rejected", "reason": f"提炼字段缺失: {','.join(missing)}"}
    validation = correction.get("next_agent_validation")
    validation = validation if isinstance(validation, dict) else {}
    verified = validation.get("status") == "passed"
    repair_success = correction.get("repair_success") is True
    if repair_success and verified:
        return {"status": "accepted", "reason": "修复动作已由 passed verification 客观确认"}
    if str(correction.get("severity") or "").lower() == "high":
        return {"status": "review", "reason": "高严重度失败尚无成功修复证据"}
    return {"status": "rejected", "reason": "修复失败或缺少 passed verification"}


def build_lesson_contract(correction: Dict[str, Any], distilled: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - correction: 已关联工具、修复动作和验证证据的事件。
      - distilled: 经严格字段校验的语义提炼结果。
    方法:
      - 构造固定 lesson contract, 生成去重键与未来可执行规则。
      - 所有进入 prompt 的字段均先移除路径与长错误原文。
    出参:
      - 含 accepted/review/rejected 状态的结构化教训。
    """
    signature = build_failure_signature(correction)
    root_cause = _sanitize_text(distilled.get("根因"))
    successful_repair = _sanitize_text(distilled.get("正确方法"))
    verification_signal = _sanitize_text(distilled.get("验证信号"))
    dedup_key = build_dedup_key(signature, root_cause, successful_repair)
    admission = determine_admission_status(correction, distilled)
    lesson = {
        "lesson_id": dedup_key,
        "failure_signature": signature,
        "root_cause": root_cause,
        "wrong_action": _sanitize_text(distilled.get("现象")),
        "successful_repair": successful_repair,
        "verification_signal": verification_signal,
        "future_rule": (
            f"遇到同类失败时，执行：{successful_repair}；"
            f"仅在观察到以下信号后确认完成：{verification_signal}"
        ),
        "severity": _sanitize_text(distilled.get("严重度"), 20).lower(),
        "evidence_refs": list(correction.get("evidence_refs") or []),
        "dedup_key": dedup_key,
        "status": admission["status"],
        "admission_reason": admission["reason"],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return lesson


def choose_strongest_lesson(candidate: Dict[str, Any], existing_values: Iterable[str]) -> bool:
    """
    入参:
      - candidate: 待写入的 accepted lesson。
      - existing_values: 数据库中同类别的 JSON value 集合。
    方法:
      - dedup_key 相同且已有 accepted 证据数量不少于候选时拒绝覆盖。
      - 候选证据更多时允许 UPSERT, 保留最新且证据最强版本。
    出参:
      - True 表示候选应写入或覆盖。
    """
    candidate_refs = candidate.get("evidence_refs") or []
    for raw in existing_values:
        try:
            existing = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if existing.get("dedup_key") != candidate.get("dedup_key"):
            continue
        existing_refs = existing.get("evidence_refs") or []
        return len(candidate_refs) > len(existing_refs)
    return True


def audit_lesson_decision(lesson: Dict[str, Any], path: Path = LESSON_AUDIT_PATH) -> None:
    """
    入参:
      - lesson: lesson policy 的完整准入结论。
      - path: JSONL 审计路径。
    方法:
      - 追加决策记录, 让 review/rejected 候选可追溯但不污染长期记忆。
    出参:
      - None。
    """
    append_jsonl(path, lesson)


def format_accepted_lesson_for_prompt(raw_value: str) -> str:
    """
    入参:
      - raw_value: user_memory 中保存的 lesson JSON。
    方法:
      - 只解析 status=accepted 的记录, 仅输出 future_rule 与 verification_signal。
    出参:
      - 精简 prompt 文本; 非 accepted 或非法 JSON 返回空串。
    """
    try:
        lesson = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return ""
    if lesson.get("status") != "accepted":
        return ""
    future_rule = _sanitize_text(lesson.get("future_rule"), 700)
    verification_signal = _sanitize_text(lesson.get("verification_signal"), 300)
    if not future_rule or not verification_signal:
        return ""
    return f"规则：{future_rule}\n验证：{verification_signal}"


def parse_accepted_lesson_contracts(memory_items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    入参:
      - memory_items: agent_db.load_user_memory 返回的记忆条目。
    方法:
      - 解析 category=lesson 且 status=accepted 的结构化记录。
      - 非法 JSON 与历史纯文本自动忽略。
    出参:
      - 可供 skill_selector 使用的 accepted lesson 列表。
    """
    accepted = []
    for item in memory_items:
        if not isinstance(item, dict) or item.get("category") != "lesson":
            continue
        try:
            lesson = json.loads(item.get("value") or "")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(lesson, dict) and lesson.get("status") == "accepted":
            accepted.append(lesson)
    return accepted
