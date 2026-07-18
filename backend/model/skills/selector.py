"""确定性的技能质量门与单主技能选择器。"""

from typing import Any, Dict, Iterable, List, Optional

from backend.model.skills import loader
from backend.model.tools.tool_registry import get_llm_exposed_tool_names


SKILL_CONTRACT_FIELDS = {
    "version",
    "trigger_conditions",
    "required_inputs",
    "steps",
    "tool_allowlist",
    "expected_outputs",
    "verification_rules",
    "fallback_rule",
    "promotion_evidence",
}


def validate_skill_contract(skill: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - skill: loader 返回的技能对象。
    方法:
      - 校验顶层字段、首步输入检查、末步产物验证和工具白名单。
      - 技能建议工具必须属于当前 LLM 暴露工具集合。
    出参:
      - valid/issues 质量门结论。
    """
    contract = skill.get("contract") if isinstance(skill, dict) else None
    contract = contract if isinstance(contract, dict) else {}
    issues = []
    missing = sorted(SKILL_CONTRACT_FIELDS - set(contract))
    if missing:
        issues.append(f"missing_contract_fields={missing}")
    steps = contract.get("steps") if isinstance(contract.get("steps"), list) else []
    if not steps:
        issues.append("steps_empty")
    else:
        first_inputs = steps[0].get("required_inputs") or []
        if not first_inputs:
            issues.append("first_step_missing_input_check")
        last_rules = steps[-1].get("verification_rules") or []
        if not last_rules:
            issues.append("last_step_missing_verification")
    allowlist = set(contract.get("tool_allowlist") or [])
    step_tools = {item.get("tool_name") for item in steps if item.get("tool_name")}
    unknown_step_tools = sorted(step_tools - allowlist)
    if unknown_step_tools:
        issues.append(f"step_tools_outside_allowlist={unknown_step_tools}")
    unavailable = sorted(allowlist - get_llm_exposed_tool_names())
    if unavailable:
        issues.append(f"tools_not_exposed={unavailable}")
    return {"valid": not issues, "issues": issues}


def _missing_required_inputs(contract: Dict[str, Any], context_inputs: Dict[str, Any]) -> List[str]:
    """
    入参:
      - contract: 技能契约。
      - context_inputs: 用户或调用方已确认的输入映射。
    方法:
      - 逐项检查顶层 required_inputs 是否存在非空值。
    出参:
      - 缺失输入名列表。
    """
    return [
        name for name in contract.get("required_inputs") or []
        if context_inputs.get(name) in (None, "", [], {})
    ]


def _lesson_bonus(skill: Dict[str, Any], accepted_lessons: Iterable[Dict[str, Any]]) -> int:
    """
    入参:
      - skill: 候选技能。
      - accepted_lessons: 已通过 lesson_policy 的教训。
    方法:
      - accepted lesson 的 future_rule 命中技能名、标题或允许工具时增加稳定加分。
    出参:
      - 0 或 1, 避免长期记忆压过任务与输入事实。
    """
    contract = skill.get("contract") or {}
    terms = [skill.get("name") or "", skill.get("title") or "", *(contract.get("tool_allowlist") or [])]
    lesson_text = " ".join(
        str(item.get("future_rule") or "")
        for item in accepted_lessons
        if isinstance(item, dict) and item.get("status") == "accepted"
    ).lower()
    return 1 if lesson_text and any(term.lower() in lesson_text for term in terms if term) else 0


def select_skill(
    query: str,
    context_inputs: Optional[Dict[str, Any]] = None,
    accepted_lessons: Optional[List[Dict[str, Any]]] = None,
    current_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    入参:
      - query: 当前长任务目标。
      - context_inputs: 已确认输入, 如 image_path/classes/t1_path/t2_path。
      - accepted_lessons: 可选已准入教训, 仅作同分辅助。
      - current_plan: 当前显式计划, 已选技能在重复检索时获得稳定性加分。
    方法:
      - 基于关键词分、输入完整度和 accepted lesson 做确定性排序。
      - 契约不合法或初始输入缺失时不选择, 最多返回一个主技能。
    出参:
      - selected_skill、候选审计与明确匹配依据。
    """
    inputs = context_inputs if isinstance(context_inputs, dict) else {}
    lessons = accepted_lessons if isinstance(accepted_lessons, list) else []
    plan = current_plan if isinstance(current_plan, dict) else {}
    candidates = []
    for skill in loader.retrieve_skills(query, top_k=10):
        quality = validate_skill_contract(skill)
        contract = skill.get("contract") or {}
        missing_inputs = _missing_required_inputs(contract, inputs)
        lesson_bonus = _lesson_bonus(skill, lessons)
        plan_tiebreak = 1 if str(plan.get("selected_skill") or "").startswith(f"{skill.get('name')}@") else 0
        eligible = quality["valid"] and not missing_inputs
        candidates.append({
            "name": skill.get("name"),
            "title": skill.get("title"),
            "keyword_score": skill.get("score", 0),
            "lesson_bonus": lesson_bonus,
            "plan_tiebreak": plan_tiebreak,
            "total_score": skill.get("score", 0) + lesson_bonus,
            "eligible": eligible,
            "missing_inputs": missing_inputs,
            "contract_issues": quality["issues"],
            "skill": skill,
        })
    eligible = [item for item in candidates if item["eligible"]]
    eligible.sort(
        key=lambda item: (-item["total_score"], -item["plan_tiebreak"], item["name"] or "")
    )
    selected = eligible[0] if eligible else None
    selected_skill = None
    reasons = []
    if selected:
        skill = selected["skill"]
        selected_skill = {
            "name": skill["name"],
            "title": skill["title"],
            "version": skill["contract"]["version"],
            "score": selected["total_score"],
            "contract": skill["contract"],
            "content": skill["content"],
        }
        reasons = [
            f"keyword_score={selected['keyword_score']}",
            "required_inputs=satisfied",
            "contract=valid",
        ]
        if selected["lesson_bonus"]:
            reasons.append("accepted_lesson_bonus=1")
        if selected["plan_tiebreak"]:
            reasons.append("current_plan_tiebreak=1")
    return {
        "query": query,
        "selected_skill": selected_skill,
        "match_reasons": reasons,
        "candidates": [
            {key: value for key, value in item.items() if key != "skill"}
            for item in candidates
        ],
    }
