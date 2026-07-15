"""
worker 输出契约 (Agent 层)

统一专业 worker 返回结构, 让主控 Agent 能稳定读取 status / evidence / artifacts /
metrics / issues / recommendation / repair_plan / next_action / handoff_notes,
不依赖自然语言猜测 worker 是否完成。所有专业 worker (verification / report /
rs / publish) 必须通过 build_worker_result 构造汇报, 保证字段一致。
"""

from typing import Any, Dict, List, Optional


STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_WARNING = "warning"

RECOMMEND_CONTINUE = "continue"
RECOMMEND_RETRY = "retry"
RECOMMEND_SWITCH_TOOL = "switch_tool"
RECOMMEND_REPORT_ERROR = "report_error"

# worker 未显式指定 next_action / handoff_notes 时的默认值, 按 recommendation / status 派生。
# 这样做是为了让确定性 verification worker (只关心验收结论) 无须每处调用都填交接字段,
# 同时仍输出统一 12 字段, 不破坏主控 Agent 对 worker 汇报的稳定读取。
_DEFAULT_NEXT_ACTION: Dict[str, str] = {
    RECOMMEND_CONTINUE: "continue_to_next_step",
    RECOMMEND_RETRY: "retry_with_repair_plan",
    RECOMMEND_SWITCH_TOOL: "switch_tool",
    RECOMMEND_REPORT_ERROR: "collect_evidence_or_escalate",
}

_DEFAULT_HANDOFF_NOTES: Dict[str, str] = {
    STATUS_PASSED: "客观证据齐全, 主控可继续后续步骤。",
    STATUS_FAILED: "存在客观失败, 主控须按 repair_plan 修正后重试。",
    STATUS_WARNING: "缺少客观证据, 主控须补充产物或验证后再判断完成。",
}

# worker 汇报强制字段集合, 供契约测试校验: 任一 worker 输出缺字段即视为破坏契约。
WORKER_RESULT_FIELDS = (
    "agent_role",
    "task_id",
    "status",
    "summary",
    "evidence",
    "artifacts",
    "metrics",
    "issues",
    "recommendation",
    "repair_plan",
    "next_action",
    "handoff_notes",
)


def build_worker_result(
    agent_role: str,
    status: str,
    summary: str,
    evidence: Dict[str, Any] = None,
    artifacts: Dict[str, Any] = None,
    metrics: Dict[str, Any] = None,
    issues: List[str] = None,
    recommendation: str = RECOMMEND_CONTINUE,
    repair_plan: Dict[str, Any] = None,
    next_action: str = "",
    handoff_notes: str = "",
    task_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    入参:
      - agent_role: worker 角色名。
      - status: passed / failed / warning。
      - summary: 给主控 Agent 读取的验收或汇报摘要。
      - evidence: 客观证据字典, 如文件大小、要素数、校验器名称。
      - artifacts: worker 产出物清单, 如报告路径、图层名、下载 URL。
      - metrics: 量化指标, 如要素数、面积占比、文件大小。
      - issues: 问题列表, 每条应能直接驱动重试或报错。
      - recommendation: continue / retry / switch_tool / report_error。
      - repair_plan: 失败或证据不足时给主控 Agent 的下一步修正策略。
      - next_action: 主控 Agent 应执行的下一步语义动作; 留空时按 recommendation 派生默认值。
      - handoff_notes: worker 向主控 Agent 的交接说明; 留空时按 status 派生默认值。
      - task_id: ai_task 主键; 同步内嵌调用 (不经 task_manager) 时为 None。
    方法:
      - 统一补齐空字段, 固定 12 个输出字段名。
      - next_action / handoff_notes 未显式传入时, 用默认映射补全, 保证字段非空且语义一致。
      - 这样做是为了让 query_agent_task 的输出可直接进入 ReAct Observation, 主控无须按角色猜字段。
    出参:
      - dict, 可写入 ai_task.output 和 verification 字段。
    """
    return {
        "agent_role": agent_role,
        "task_id": task_id,
        "status": status,
        "summary": summary,
        "evidence": evidence or {},
        "artifacts": artifacts or {},
        "metrics": metrics or {},
        "issues": issues or [],
        "recommendation": recommendation,
        "repair_plan": repair_plan or {},
        "next_action": next_action or _DEFAULT_NEXT_ACTION.get(recommendation, ""),
        "handoff_notes": handoff_notes or _DEFAULT_HANDOFF_NOTES.get(status, ""),
    }
