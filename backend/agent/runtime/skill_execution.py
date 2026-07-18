"""把已选择技能转换为显式 plan state 步骤。"""

from typing import Any, Dict, List

from backend.agent.memory.task_state import append_plan_step
from backend.model.skills.selector import validate_skill_contract


def apply_skill_selection_to_plan(
    plan: Dict[str, Any],
    selection: Dict[str, Any],
    context_inputs: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    入参:
      - plan: 阶段 6 显式计划。
      - selection: selector.select_skill 的完整结果。
      - context_inputs: 已确认的技能初始输入。
    方法:
      - 再次执行契约质量门, 防止调用边界传入伪造契约。
      - 将每个技能步骤追加为 pending 必需步骤, 不在此处执行任何工具。
      - 同一 plan 只应用一次同名同版本技能。
    出参:
      - 新增步骤列表; 无选择、非法或重复时为空。
    """
    selected = selection.get("selected_skill") if isinstance(selection, dict) else None
    if not isinstance(selected, dict):
        return []
    skill_identity = f"{selected.get('name')}@{selected.get('version')}"
    if plan.get("selected_skill") == skill_identity:
        return []
    quality = validate_skill_contract(selected)
    if not quality["valid"]:
        raise ValueError(f"技能契约未通过质量门: {quality['issues']}")
    contract = selected["contract"]
    inputs = context_inputs if isinstance(context_inputs, dict) else {}
    added = []
    for skill_step in contract["steps"]:
        step_inputs = {
            name: inputs[name] if name in inputs else f"<来自前序步骤:{name}>"
            for name in skill_step.get("required_inputs") or []
        }
        added.append(append_plan_step(
            plan=plan,
            goal=skill_step["goal"],
            executor="tool",
            inputs=step_inputs,
            expected_outputs=list(skill_step.get("expected_outputs") or []),
            required=bool(skill_step.get("required", True)),
            tool_name=skill_step["tool_name"],
        ))
    plan["selected_skill"] = skill_identity
    plan["skill_fallback_rule"] = contract["fallback_rule"]
    plan["skill_verification_rules"] = contract["verification_rules"]
    return added
