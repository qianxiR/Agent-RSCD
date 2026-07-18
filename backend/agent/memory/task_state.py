"""
任务状态提炼器 (Agent 层 / Working Memory)
- 入参: conversation_id / current_prompt / recent_history / conversation_summary
- 方法: 从近期消息、工具结果、ai_task 状态中提炼一张轻量任务状态卡
- 出参: 可直接注入 system prompt 的多行文本

设计目标:
  1. 把“工作记忆”从隐式历史中抽出来, 让模型每轮都能看到当前做到哪一步
  2. 复用现有 message 历史和 ai_task 表, 不新增数据库结构
  3. 只保留任务推进真正需要的状态, 避免把整段历史原样重复注入
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage

from . import agent_db


PLAN_EXECUTORS = {"main_agent", "tool", "verification_agent", "report_agent"}
PLAN_STEP_STATUSES = {"pending", "running", "blocked", "completed", "failed", "cancelled"}
PLAN_OPEN_STATUSES = {"pending", "running", "blocked", "failed"}


def _now_iso() -> str:
    """
    入参:
      - 无。
    方法:
      - 生成精确到秒的本地时间, 供计划审计字段统一使用。
    出参:
      - ISO 8601 时间文本。
    """
    return datetime.now().isoformat(timespec="seconds")


def create_plan_state(conversation_id: str, goal: str) -> Dict[str, Any]:
    """
    入参:
      - conversation_id: 当前会话 ID, 不允许为空。
      - goal: 当前用户目标, 用作计划的审计主线。
    方法:
      - 创建无步骤的结构化计划, 完成门默认关闭。
      - 计划步骤由真实工具调用或已选择技能逐步加入。
    出参:
      - 可 JSON 持久化的 plan state。
    """
    now = _now_iso()
    return {
        "plan_id": f"plan_{conversation_id}_{uuid.uuid4().hex[:10]}",
        "conversation_id": conversation_id,
        "goal": (goal or "").strip(),
        "status": "running",
        "steps": [],
        "current_step_id": None,
        "completion_gate": {"allowed": False, "reason": "计划尚无已验证步骤"},
        "created_at": now,
        "updated_at": now,
    }


def append_plan_step(
    plan: Dict[str, Any],
    goal: str,
    executor: str,
    inputs: Optional[Dict[str, Any]] = None,
    expected_outputs: Optional[List[str]] = None,
    required: bool = True,
    tool_name: str = "",
) -> Dict[str, Any]:
    """
    入参:
      - plan: create_plan_state 创建的计划。
      - goal/executor: 步骤目标与执行者; executor 必须属于允许集合。
      - inputs/expected_outputs: 执行输入和客观产物要求。
      - required: 是否属于完成门必需步骤。
      - tool_name: 可选真实工具名, 用于执行匹配。
    方法:
      - 追加 pending 步骤并刷新完成门, 不隐式启动执行。
    出参:
      - 新增的步骤对象。
    """
    if executor not in PLAN_EXECUTORS:
        raise ValueError(f"不支持的计划执行者: {executor}")
    steps = plan.setdefault("steps", [])
    step = {
        "step_id": f"step_{len(steps) + 1}",
        "goal": (goal or tool_name or "执行计划步骤").strip(),
        "executor": executor,
        "status": "pending",
        "required": bool(required),
        "tool_name": tool_name,
        "inputs": inputs if isinstance(inputs, dict) else {},
        "expected_outputs": expected_outputs if isinstance(expected_outputs, list) else [],
        "evidence": [],
        "repair_plan": {},
        "next_action": tool_name or "",
        "updated_at": _now_iso(),
    }
    steps.append(step)
    _refresh_completion_gate(plan)
    return step


def start_tool_step(
    plan: Dict[str, Any],
    tool_name: str,
    tool_args: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    入参:
      - plan: 当前计划。
      - tool_name/tool_args: 即将真实执行的工具与参数。
    方法:
      - 优先激活技能预置的同名 pending/blocked 步骤。
      - 没有匹配步骤时追加一个工具步骤, 保证每次执行都有审计载体。
    出参:
      - 已进入 running 状态的步骤。
    """
    step = next(
        (
            item for item in plan.get("steps", [])
            if item.get("tool_name") == tool_name and item.get("status") in {"pending", "blocked", "failed"}
        ),
        None,
    )
    if step is None:
        step = append_plan_step(
            plan,
            goal=f"执行工具 {tool_name} 并验证结果",
            executor="tool",
            inputs=tool_args,
            expected_outputs=["agent_validation.status=passed"],
            tool_name=tool_name,
        )
    step["status"] = "running"
    step["inputs"] = tool_args if isinstance(tool_args, dict) else {}
    step["next_action"] = f"等待 {tool_name} 的工具结果与验证"
    step["updated_at"] = _now_iso()
    plan["current_step_id"] = step["step_id"]
    plan["updated_at"] = step["updated_at"]
    _refresh_completion_gate(plan)
    return step


def apply_step_observation(
    plan: Dict[str, Any],
    step_id: str,
    tool_name: str,
    tool_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    入参:
      - plan/step_id: 当前计划及刚执行的步骤 ID。
      - tool_name/tool_result: Observation 层增强后的真实工具结果。
    方法:
      - 仅 validation=passed 时完成步骤。
      - failed/warning 进入 blocked 并保存 repair_plan; 无验证结果进入 failed。
    出参:
      - 更新后的步骤。
    """
    step = next((item for item in plan.get("steps", []) if item.get("step_id") == step_id), None)
    if step is None:
        raise ValueError(f"计划步骤不存在: {step_id}")
    result = tool_result if isinstance(tool_result, dict) else {}
    validation = result.get("agent_validation")
    validation = validation if isinstance(validation, dict) else {}
    validation_status = validation.get("status") or "unverified"
    evidence = {
        "tool_name": tool_name,
        "result_type": result.get("type") or "unknown",
        "validation_status": validation_status,
        "summary": result.get("summary") or result.get("msg") or "",
        "observed_at": _now_iso(),
    }
    step.setdefault("evidence", []).append(evidence)
    if validation_status == "passed":
        step["status"] = "completed"
        step["repair_plan"] = {}
        step["next_action"] = "进入下一必需步骤或完成门检查"
    elif validation_status in {"failed", "warning"}:
        step["status"] = "blocked"
        step["repair_plan"] = validation.get("repair_plan") or {}
        step["next_action"] = step["repair_plan"].get("next_action") or "按修复计划处理后重新验证"
    else:
        step["status"] = "failed"
        step["repair_plan"] = {"failure_type": "missing_validation"}
        step["next_action"] = "补充客观验证结果"
    step["updated_at"] = _now_iso()
    plan["updated_at"] = step["updated_at"]
    _refresh_completion_gate(plan)
    return step


def _refresh_completion_gate(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - plan: 待重新计算完成门的计划。
    方法:
      - 必需步骤至少一项且全部 completed 时允许收尾。
      - 任一必需步骤处于开放状态时返回具体阻塞原因。
    出参:
      - completion_gate 字典, 同时原位写回 plan。
    """
    required_steps = [item for item in plan.get("steps", []) if item.get("required", True)]
    open_steps = [item for item in required_steps if item.get("status") in PLAN_OPEN_STATUSES]
    allowed = bool(required_steps) and not open_steps
    if allowed:
        reason = "所有必需步骤均已有 passed 验证证据"
        plan["status"] = "completed"
    elif open_steps:
        details = "、".join(f"{item.get('step_id')}:{item.get('status')}" for item in open_steps[:4])
        reason = f"仍有未完成必需步骤: {details}"
        plan["status"] = "blocked" if any(item.get("status") == "blocked" for item in open_steps) else "running"
    else:
        reason = "计划尚无必需步骤"
        plan["status"] = "running"
    plan["completion_gate"] = {"allowed": allowed, "reason": reason}
    return plan["completion_gate"]


def can_finalize_plan(plan: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    入参:
      - plan: 当前计划; None 表示本轮尚未发生需验证的计划执行。
    方法:
      - 无计划时允许普通问答收尾; 有计划时读取确定性完成门。
    出参:
      - (是否允许最终回复, 原因)。
    """
    if not plan:
        return True, "当前轮没有工具执行计划"
    gate = _refresh_completion_gate(plan)
    return bool(gate["allowed"]), str(gate["reason"])


def build_plan_context_text(plan: Optional[Dict[str, Any]]) -> str:
    """
    入参:
      - plan: 当前结构化计划。
    方法:
      - 只输出当前步骤、未完成必需步骤、最近证据与完成门, 控制上下文长度。
    出参:
      - 可注入动态 system prompt 的审计摘要。
    """
    if not plan:
        return "暂无显式执行计划；普通问答可直接回复，工具任务需先建立步骤。"
    lines = [
        f"- plan_id: {plan.get('plan_id')}",
        f"- 目标: {_clip_text(plan.get('goal') or '', 160)}",
        f"- 当前步骤: {plan.get('current_step_id') or '尚未开始'}",
    ]
    if plan.get("selected_skill"):
        lines.append(f"- 选中技能: {plan['selected_skill']}")
        lines.append(f"- 技能回退: {_clip_text(plan.get('skill_fallback_rule') or '', 140)}")
    for step in plan.get("steps", []):
        if step.get("required", True) and step.get("status") != "completed":
            lines.append(
                f"- 未完成: {step.get('step_id')} | {step.get('status')} | "
                f"{_clip_text(step.get('goal') or '', 90)} | 下一步: {_clip_text(step.get('next_action') or '', 90)}"
            )
    gate = _refresh_completion_gate(plan)
    lines.append(f"- 完成门: {'允许' if gate['allowed'] else '禁止'}；{gate['reason']}")
    lines.append("- 约束: 完成门禁止时不得向用户宣告任务完成，必须继续执行 next_action 或 repair_plan。")
    return "\n".join(lines)


def persist_plan_state(
    conversation_id: str,
    user_id: str,
    plan: Dict[str, Any],
    task_id: Optional[int] = None,
) -> Optional[int]:
    """
    入参:
      - conversation_id/user_id: 计划归属。
      - plan: 当前计划快照。
      - task_id: 已存在的 ai_task ID; 为空时创建计划任务。
    方法:
      - 首次把计划写入 ai_task.input, 后续快照写入 output。
      - 数据库不可用时返回原 task_id, 主对话仍使用内存计划继续执行。
    出参:
      - 计划任务 ID; 数据库不可用时可能为 None。
    """
    plan_task_id = task_id
    if plan_task_id is None:
        plan_task_id = agent_db.create_task(
            conversation_id=conversation_id,
            task_type="agent_plan",
            tool_name="plan_state",
            user_id=user_id,
            input_data={"plan": plan},
            agent_role="main_agent",
            goal=plan.get("goal") or "",
        )
    if plan_task_id is not None:
        allowed, reason = can_finalize_plan(plan)
        agent_db.update_task(
            plan_task_id,
            status="done" if allowed else "running",
            progress=100 if allowed else 50,
            output={"plan": plan, "plan_summary": reason},
        )
    return plan_task_id


def load_plan_state(conversation_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """
    入参:
      - conversation_id: 当前会话 ID。
    方法:
      - 读取最近一个 agent_plan 任务, 优先使用 output 中的最新快照。
    出参:
      - (plan, task_id); 无持久化计划时返回 (None, None)。
    """
    task = agent_db.get_latest_plan_task(conversation_id)
    if not task:
        return None, None
    output = task.get("output") if isinstance(task.get("output"), dict) else {}
    input_data = task.get("input") if isinstance(task.get("input"), dict) else {}
    plan = output.get("plan") or input_data.get("plan")
    return (plan if isinstance(plan, dict) else None), task.get("id")


def _clip_text(text: str, limit: int = 120) -> str:
    """
    入参:
      - text: 待截断文本
      - limit: 最大保留长度
    方法:
      - 去除首尾空白, 长文本截断后追加省略标记
      - 这样做是为了在 prompt 中保留关键信号, 同时控制 token 膨胀
    出参:
      - 适合放入任务状态卡的短文本
    """
    clean = (text or "").strip()
    return clean if len(clean) <= limit else clean[:limit] + "...(截断)"


def _find_latest_user_goal(history: List[BaseMessage]) -> str:
    """
    入参:
      - history: 当前会话近期历史消息
    方法:
      - 从后往前找最近一条 HumanMessage
      - 这样做是为了拿到“当前输入之前”的上一个用户目标, 便于恢复连续任务
    出参:
      - 最近一条用户目标摘要, 不存在时返回空串
    """
    for message in reversed(history):
        if isinstance(message, HumanMessage):
            return _clip_text(str(message.content))
    return ""


def _find_latest_tool_action(history: List[BaseMessage]) -> str:
    """
    入参:
      - history: 当前会话近期历史消息
    方法:
      - 从后往前找最近一条带 tool_calls 的 AIMessage
      - 提取工具名列表, 因为工具选择本身就是“上一动作”最稳定的事实
    出参:
      - 最近一次工具动作摘要, 不存在时返回空串
    """
    for message in reversed(history):
        if isinstance(message, AIMessage) and message.tool_calls:
            tool_names = [item.get("name", "") for item in message.tool_calls if item.get("name")]
            if tool_names:
                return " -> ".join(tool_names[:3])
    return ""


def _summarize_tool_result(raw_content: str) -> str:
    """
    入参:
      - raw_content: ToolMessage.content 原始文本
    方法:
      - 优先解析 JSON 结构并提取 summary / msg / description / type
      - 这样做是为了把工具观察结果压成一句话, 而不是把大 JSON 整段塞回 prompt
    出参:
      - 工具结果摘要, 无法解析时返回截断后的原文
    """
    content = (raw_content or "").strip()
    if not content:
        return ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return _clip_text(content)

    if not isinstance(data, dict):
        return _clip_text(str(data))

    summary = data.get("summary") or data.get("msg") or data.get("description")
    if isinstance(summary, str) and summary.strip():
        return _clip_text(summary)

    result_type = data.get("type")
    if result_type == "frontend_action":
        return "前端已接管展示或交互"
    if result_type == "error":
        return _clip_text(f"工具报错: {data.get('msg', '未知错误')}")
    return _clip_text(str(data))


def _find_latest_observation(history: List[BaseMessage]) -> str:
    """
    入参:
      - history: 当前会话近期历史消息
    方法:
      - 从后往前找最近一条 ToolMessage, 并压缩成一句观察结果
      - 这样做是为了让模型看到“行动之后得到了什么反馈”
    出参:
      - 最近一次观察摘要, 不存在时返回空串
    """
    for message in reversed(history):
        if isinstance(message, ToolMessage):
            return _summarize_tool_result(str(message.content))
    return ""


def _format_recent_tasks(conversation_id: str) -> str:
    """
    入参:
      - conversation_id: 当前会话 ID
    方法:
      - 读取 ai_task 最近记录, 按状态压缩成短句
      - 这样做是为了把“持久化任务状态”注入给模型, 帮助续跑和错误恢复
    出参:
      - 任务状态短句, 无记录时返回空串
    """
    if not agent_db.agent_db_available():
        return ""

    tasks = agent_db.list_tasks(conversation_id=conversation_id, limit=5)
    if not tasks:
        return ""

    items = []
    for task in tasks[:3]:
        tool_name = task.get("tool_name") or task.get("task_type") or "unknown"
        status = task.get("status") or "pending"
        progress = task.get("progress")
        error = task.get("error")
        if status in ("running", "pending"):
            suffix = f"({progress}%)" if progress is not None else ""
            items.append(f"{tool_name}:{status}{suffix}")
        elif status == "failed":
            items.append(f"{tool_name}:failed({_clip_text(error or '无错误详情', 40)})")
        else:
            items.append(f"{tool_name}:{status}")
    return "；".join(items)


def build_task_state_text(
    conversation_id: str,
    current_prompt: str,
    history: List[BaseMessage],
    conversation_summary: str = "",
) -> str:
    """
    入参:
      - conversation_id: 当前会话 ID
      - current_prompt: 当前轮用户输入
      - history: 最近历史消息 (不含当前 HumanMessage)
      - conversation_summary: 会话摘要文本
    方法:
      - 提炼当前目标、上一轮目标、最近动作、最近观察、持久化任务状态
      - 这样做是为了把隐式工作记忆转成显式状态卡, 降低多步任务中“失忆”和“误收尾”的概率
    出参:
      - 多行任务状态文本, 可直接注入 system prompt
    """
    current_goal = _clip_text(current_prompt, 160) or "暂无当前输入"
    latest_goal = _find_latest_user_goal(history)
    latest_action = _find_latest_tool_action(history)
    latest_observation = _find_latest_observation(history)
    recent_tasks = _format_recent_tasks(conversation_id)

    lines = [f"- 当前目标: {current_goal}"]
    if latest_goal:
        lines.append(f"- 前序目标: {latest_goal}")
    if latest_action:
        lines.append(f"- 最近动作: {latest_action}")
    if latest_observation:
        lines.append(f"- 最近观察: {latest_observation}")
    if recent_tasks:
        lines.append(f"- 持久化任务状态: {recent_tasks}")
    if conversation_summary:
        lines.append("- 早期上下文: 更早轮次已压缩进会话摘要, 恢复长链任务前应先参考摘要")
    return "\n".join(lines)
