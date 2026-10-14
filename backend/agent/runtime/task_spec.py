# -*- coding: utf-8 -*-
"""
结构化任务规格 (Agent 层 / Runtime)

把"用户自然语言目标 + 已确认输入 + 已选技能"提炼为统一的结构化 task_spec,
让意图解析、技能选择和验证策略共享同一语义输入。

入参 / 方法 / 出参 见 build_task_spec。

设计动机 (工程化 S11):
  当前意图主要依赖 prompt + 工具描述, 尚未形成统一结构化任务模型。
  task_spec 作为 plan 的结构化字段, 让 completion_gate / verification / report
  能基于 success_criteria 做判断, 而非只靠 step 状态。

  ★ 先以 JSON 存入 plan.task_spec (内存 + ai_task JSONB), 不新建数据库表。
     验证价值后再决定是否独立持久化 (与"复用 ai_task"的克制原则一致)。

  ★ 纯确定性规则推断, 不调 LLM, 零成本, O(1)。
"""
from typing import Any, Dict, List, Optional


# 任务类型枚举 (与工程化.md §2.2 对齐, 覆盖当前系统已支持的业务场景)
TASK_TYPES = {
    "image_understanding",
    "semantic_segmentation",
    "change_detection",
    "geoserver_publish",
    "monitoring_report_generation",
    "data_management",
    "unknown",
}

# 目标关键词 → 任务类型映射 (确定性推断, 覆盖中英文常见表述)
# ★ 关键词用子串匹配 (goal.lower() 中包含即命中), 故短词如"变化""分割"即可覆盖各种表述。
_KEYWORD_MAP = [
    ({"变化检测", "变化对比", "对比变化", "哪里变了", "变化", "双时相", "change detection", "detect change", "change"}, "change_detection"),
    ({"分割", "语义分割", "提取地物", "segmentation", "segment"}, "semantic_segmentation"),
    ({"影像理解", "识别地物", "描述影像", "描述这张", "描述这张图", "看图", "理解", "understand image", "describe"}, "image_understanding"),
    ({"发布", "上传到geoserver", "geoserver", "发布图层", "publish layer", "upload"}, "geoserver_publish"),
    ({"报告", "监测报告", "生成pdf", "report", "monitoring report"}, "monitoring_report_generation"),
    ({"入库", "登记元数据", "数据管理", "导入", "ingest", "register"}, "data_management"),
]


def _infer_task_type(goal: str, selection: Optional[Dict[str, Any]] = None) -> str:
    """
    入参:
      - goal: 用户目标文本。
      - selection: 已选技能结果 (可选, 优先用技能名判定)。
    方法:
      - 优先用已选技能名推断 (技能本身已是对任务的分类)。
      - 回退到 goal 关键词匹配 (中英文不敏感)。
    出参:
      - task_type 字符串, 无匹配返回 "unknown"。
    """
    # 优先用技能名 (技能名已是任务分类)
    sel = selection or {}
    selected = sel.get("selected_skill") if isinstance(sel.get("selected_skill"), dict) else {}
    skill_name = (selected.get("name") or "").lower()
    if "change" in skill_name or "cd" in skill_name:
        return "change_detection"
    if "segment" in skill_name:
        return "semantic_segmentation"
    if "geoserver" in skill_name or "publish" in skill_name or "upload" in skill_name:
        return "geoserver_publish"

    # 回退到 goal 关键词
    text = (goal or "").lower()
    for keywords, task_type in _KEYWORD_MAP:
        if any(kw.lower() in text for kw in keywords):
            return task_type
    return "unknown"


def _infer_spatial_scope(context_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参: context_inputs — 已确认输入 (可能含 image_path/t1_path/t2_path)。
    出参: {image_paths, image_count} 从输入提取的空间相关字段。
    方法: 收集所有 path 类输入, 统计数量 (单图/双时相)。
    """
    inputs = context_inputs if isinstance(context_inputs, dict) else {}
    paths = []
    for key in ("image_path", "t1_path", "t2_path", "file_path", "raster_path"):
        val = inputs.get(key)
        if isinstance(val, str) and val:
            paths.append(val)
    return {
        "image_paths": paths,
        "image_count": len(paths),
        "is_dual_temporal": bool(inputs.get("t1_path") and inputs.get("t2_path")),
    }


def _infer_success_criteria(task_type: str, selection: Optional[Dict[str, Any]]) -> List[str]:
    """
    入参:
      - task_type: 推断出的任务类型。
      - selection: 已选技能 (可选, 从契约 expected_outputs 提取)。
    方法:
      - 优先用技能契约的 expected_outputs (已是客观产物要求)。
      - 回退到 task_type 的默认成功条件 (覆盖核心业务)。
    出参:
      - 成功条件列表 (产物/验证要求), 供 completion_gate 参考。
    """
    # 优先用技能契约的 expected_outputs
    sel = selection or {}
    selected = sel.get("selected_skill") if isinstance(sel.get("selected_skill"), dict) else {}
    contract = selected.get("contract") if isinstance(selected.get("contract"), dict) else {}
    steps = contract.get("steps") if isinstance(contract.get("steps"), list) else []
    criteria = []
    for step in steps:
        for out in step.get("expected_outputs") or []:
            if out not in criteria:
                criteria.append(out)
    if criteria:
        return criteria

    # 回退到 task_type 默认成功条件
    defaults = {
        "change_detection": ["change_mask", "change_vector", "statistics", "verification_passed"],
        "semantic_segmentation": ["segment_mask", "segment_vector", "area_statistics", "verification_passed"],
        "image_understanding": ["text_description"],
        "geoserver_publish": ["layer_published", "verification_passed"],
        "monitoring_report_generation": ["report_file", "verification_passed"],
        "data_management": ["metadata_registered"],
        "unknown": [],
    }
    return defaults.get(task_type, [])


def build_task_spec(
    goal: str,
    context_inputs: Optional[Dict[str, Any]] = None,
    selection: Optional[Dict[str, Any]] = None,
    conversation_id: str = "",
) -> Dict[str, Any]:
    """
    入参:
      - goal: 用户自然语言目标 (plan.goal 或当前 prompt)。
      - context_inputs: 已确认输入 (image_path/t1_path/t2_path/classes 等)。
      - selection: selector.select_skill 的结果 (可选, 提升推断准确度)。
      - conversation_id: 当前会话 (溯源用)。
    方法:
      - 纯确定性规则推断 task_type / spatial_scope / success_criteria。
      - 不调 LLM, O(1), 可在技能写入 plan 时同步执行。
      - missing_fields 标注输入缺失, 供 LLM 决定是否请求补充。
    出参:
      - 结构化 task_spec dict:
        {task_type, goal, conversation_id, spatial_scope, success_criteria,
         missing_fields, status}
        status: "ready" (输入齐全) / "needs_input" (有缺失)。
    """
    task_type = _infer_task_type(goal, selection)
    spatial = _infer_spatial_scope(context_inputs or {})
    criteria = _infer_success_criteria(task_type, selection)

    # 缺失字段检测 (基于 task_type 推断必需输入)
    inputs = context_inputs if isinstance(context_inputs, dict) else {}
    required = []
    if task_type == "change_detection":
        required = ["t1_path", "t2_path"]
    elif task_type == "semantic_segmentation":
        required = ["image_path"]
    elif task_type == "image_understanding":
        required = ["image_path"]
    missing = [k for k in required if not inputs.get(k)]

    status = "ready" if not missing else "needs_input"
    return {
        "task_type": task_type,
        "goal": (goal or "").strip()[:300],
        "conversation_id": conversation_id,
        "spatial_scope": spatial,
        "success_criteria": criteria,
        "missing_fields": missing,
        "status": status,
    }


def task_spec_context_text(task_spec: Optional[Dict[str, Any]]) -> str:
    """
    入参: task_spec — build_task_spec 的输出 (或 None)。
    方法: 压成多行短文本, 供注入 system prompt 的动态段。
    出参: 可注入 prompt 的任务规格摘要; 无 task_spec 返回空串。
    """
    if not isinstance(task_spec, dict) or not task_spec.get("task_type"):
        return ""
    lines = [f"- 任务类型: {task_spec['task_type']}"]
    if task_spec.get("status") == "needs_input":
        lines.append(f"- ⚠️ 缺失输入: {', '.join(task_spec.get('missing_fields') or [])} (需先请求用户补充)")
    spatial = task_spec.get("spatial_scope") or {}
    if spatial.get("is_dual_temporal"):
        lines.append("- 时相: 双时相变化检测 (T1+T2)")
    elif spatial.get("image_count"):
        lines.append(f"- 影像: {spatial['image_count']} 张")
    criteria = task_spec.get("success_criteria") or []
    if criteria:
        lines.append(f"- 成功条件: {', '.join(criteria[:5])}")
    return "\n".join(lines)
