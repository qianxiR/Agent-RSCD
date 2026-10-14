"""
任务→工具决策规则 (Agent 层 / Prompt 静态规则层)

本模块是「任务类型 → 工具组合」决策表的唯一可信源。
  - build_decision_table() (tool_catalog.py) 由此生成 Markdown 表注入 system prompt
  - task_planning_eval.py 由此做评估对照基准
  - tests/test_decision_rules.py 由此校验工具名合法性

设计对标 DataFlow Construction Skill 的 MANDATORY 决策表:
  把 LLM 的工具选择空间从「34 个工具自由组合」收窄为
  「按任务类型查表 → 命中必用工具 + 规避禁用工具」。

forbidden_tools 语义约定:
  「不能作为完成此任务的主工具」, 前置探索调用不在禁用范围。
  例: 单影像分割任务的 forbidden 含 detect_change —— 不能用变化检测完成分割,
  但若 agent 先调 understand_image 探索再分割, 不触犯本表。

依赖方向: 仅标准库, 无跨层依赖。
"""
from typing import Any, Dict, List


# 任务→工具决策规则 (唯一可信源)
# 字段说明:
#   task_type        — 任务类型标识, 与 task_planning_eval 的 case.task_type 对齐
#   task_pattern     — 任务中文描述, 注入 prompt 表格「任务类型」列
#   trigger_keywords — 触发该任务类型的关键词 (供未来动态匹配, 当前仅作文档与检索)
#   required_tools   — 此类任务的必用工具 (完成任务的主体工具; 允许为空表示无单一主工具)
#   forbidden_tools  — 不能作为完成此任务的主工具 (前置探索除外)
#   key_constraint   — 关键参数/资源约束, 注入 prompt 表格「关键约束」列
DECISION_RULES: List[Dict[str, Any]] = [
    {
        "task_type": "semantic_segmentation",
        "task_pattern": "单影像地物提取 / 语义分割",
        "trigger_keywords": ["分割", "提取", "识别地物", "地物类别", "建筑物", "道路", "水体"],
        "required_tools": ["segment_image"],
        "forbidden_tools": ["detect_change", "generate_monitor_report"],
        "key_constraint": "classes 非空; 接受 agent-files/ 下本地路径, 无需导入沙盒",
    },
    {
        "task_type": "change_detection",
        "task_pattern": "多时相变化检测",
        "trigger_keywords": ["变化检测", "对比", "变化", "双时相", "T1/T2"],
        "required_tools": ["detect_change"],
        "forbidden_tools": ["segment_image"],
        "key_constraint": "需 T1/T2 两张影像, 缺一不可; 自动产出转移矩阵与业务类型判读",
    },
    {
        "task_type": "image_understanding",
        "task_pattern": "影像内容理解 / 描述",
        "trigger_keywords": ["理解", "描述", "影像里有什么", "地物分布"],
        "required_tools": ["understand_image"],
        "forbidden_tools": ["segment_image", "detect_change"],
        "key_constraint": "单张影像; 不修改影像, 不输出矢量, 不做定量统计",
    },
    {
        "task_type": "long_task_orchestration",
        "task_pattern": "长任务编排 (≥3 步, 跨多工具)",
        "trigger_keywords": ["入库", "发布", "流水线", "完整接入", "批量处理"],
        "required_tools": ["lookup_skill"],
        "forbidden_tools": ["toggle_layer_visibility"],
        "key_constraint": "先 lookup_skill 查步骤, 再逐步执行; 禁止跳过编排直接单工具操作",
    },
    {
        "task_type": "report_download",
        "task_pattern": "报告下载 (已由 report_agent 生成)",
        "trigger_keywords": ["下载报告", "打开报告", "PDF"],
        "required_tools": [],
        "forbidden_tools": ["download_file_from_sandbox", "generate_monitor_report", "run_shell_command"],
        "key_constraint": "报告由 report_agent 自动派发; 主控用 query_agent_task 查阅 URL",
    },
    {
        "task_type": "layer_control",
        "task_pattern": "地图图层显隐 / 定位",
        "trigger_keywords": ["显示图层", "隐藏", "定位", "放大到"],
        "required_tools": ["toggle_layer_visibility", "locate_to_layer_bounds"],
        "forbidden_tools": ["segment_image", "detect_change"],
        "key_constraint": "仅前端显隐/定位, 不修改数据, 不调遥感工具",
    },
]


def get_rules_by_task_type(task_type: str) -> List[Dict[str, Any]]:
    """
    入参:
      - task_type: 任务类型标识 (如 "semantic_segmentation")。
    方法:
      - 线性查找 DECISION_RULES 中 task_type 完全匹配的规则。
    出参:
      - 匹配的规则列表 (通常 0 或 1 条); 未匹配返回空列表。
    """
    return [rule for rule in DECISION_RULES if rule["task_type"] == task_type]


def get_forbidden_tools(task_type: str) -> List[str]:
    """
    入参:
      - task_type: 任务类型标识。
    方法:
      - 取该任务类型决策规则的 forbidden_tools (去重保序)。
    出参:
      - 禁用主工具名列表; 未配置规则返回空列表。
    约束:
      - 供 task_planning_eval 合并到判定集合, 不破坏 case 自身 forbidden。
    """
    seen: set = set()
    result: List[str] = []
    for rule in get_rules_by_task_type(task_type):
        for name in rule.get("forbidden_tools") or []:
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result


def validate_rules(exposed_tool_names) -> Dict[str, Any]:
    """
    入参:
      - exposed_tool_names: LLM 暴露工具白名单 (set 或 iterable)。
    方法:
      - 校验 DECISION_RULES 中所有 required_tools / forbidden_tools 均在白名单内。
    出参:
      - {valid: bool, issues: list[str]}; issues 为不在白名单的工具引用描述。
    约束:
      - 防止规则源笔误导致 prompt 引用不存在的工具, 由单元测试与 CI 守护。
    """
    exposed = set(exposed_tool_names)
    issues: List[str] = []
    for rule in DECISION_RULES:
        for field in ("required_tools", "forbidden_tools"):
            for name in rule.get(field) or []:
                if name not in exposed:
                    issues.append(
                        f"{rule['task_type']}.{field} 引用未暴露工具: {name}"
                    )
    return {"valid": not issues, "issues": issues}
