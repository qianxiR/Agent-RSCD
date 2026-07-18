"""阶段 6 显式 plan state 回归测试。"""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.memory.task_state import (
    apply_step_observation,
    build_plan_context_text,
    can_finalize_plan,
    create_plan_state,
    load_plan_state,
    persist_plan_state,
    start_tool_step,
)


def _passed_result() -> dict:
    """
    入参:
      - 无。
    方法:
      - 构造带客观通过信号的 Observation。
    出参:
      - 可用于完成计划步骤的工具结果。
    """
    return {
        "type": "success",
        "summary": "产物已生成并验证",
        "agent_validation": {"status": "passed", "evidence": ["artifact_exists"]},
    }


def test_plan_completion_requires_passed_evidence() -> None:
    """
    入参:
      - 无。
    方法:
      - 启动工具步骤并分别检查运行中、验证通过后的完成门。
    出参:
      - 断言失败时抛出 AssertionError；通过时无返回值。
    """
    plan = create_plan_state("conv-plan-1", "生成并验证监测结果")
    step = start_tool_step(plan, "generate_monitor_report", {"output_format": "pdf"})
    allowed, reason = can_finalize_plan(plan)
    assert allowed is False
    assert "running" in reason

    apply_step_observation(plan, step["step_id"], "generate_monitor_report", _passed_result())
    allowed, reason = can_finalize_plan(plan)
    assert allowed is True
    assert "passed" in reason
    assert plan["status"] == "completed"


def test_failed_validation_blocks_and_records_repair() -> None:
    """
    入参:
      - 无。
    方法:
      - 将 warning Observation 写入当前步骤, 检查阻塞状态和修复动作。
    出参:
      - 断言失败时抛出 AssertionError；通过时无返回值。
    """
    plan = create_plan_state("conv-plan-2", "发布图层")
    step = start_tool_step(plan, "upload_raster_layer", {"file_path": "missing.tif"})
    result = {
        "type": "error",
        "msg": "file not found",
        "agent_validation": {
            "status": "failed",
            "repair_plan": {
                "failure_type": "missing_input_file",
                "next_action": "request_valid_input_file_or_upload",
            },
        },
    }
    updated = apply_step_observation(plan, step["step_id"], "upload_raster_layer", result)
    allowed, _ = can_finalize_plan(plan)
    context = build_plan_context_text(plan)

    assert updated["status"] == "blocked"
    assert updated["repair_plan"]["failure_type"] == "missing_input_file"
    assert allowed is False
    assert "完成门: 禁止" in context
    assert "request_valid_input_file_or_upload" in context


def test_plan_persists_and_restores_from_ai_task() -> None:
    """
    入参:
      - 无。
    方法:
      - 模拟 ai_task 创建/更新与读取, 验证快照使用同一 plan_id 恢复。
    出参:
      - 断言失败时抛出 AssertionError；通过时无返回值。
    """
    plan = create_plan_state("conv-plan-3", "执行多步监测")
    start_tool_step(plan, "segment_image", {"classes": "water"})

    with patch("backend.agent.memory.task_state.agent_db.create_task", return_value=41) as create_task, patch(
        "backend.agent.memory.task_state.agent_db.update_task", return_value=True
    ) as update_task:
        task_id = persist_plan_state("conv-plan-3", "user-1", plan)

    assert task_id == 41
    assert create_task.call_args.kwargs["input_data"]["plan"]["plan_id"] == plan["plan_id"]
    assert update_task.call_args.kwargs["output"]["plan"]["current_step_id"] == "step_1"

    stored_task = {"id": 41, "input": {"plan": plan}, "output": {"plan": plan}}
    with patch("backend.agent.memory.task_state.agent_db.get_latest_plan_task", return_value=stored_task):
        restored, restored_task_id = load_plan_state("conv-plan-3")

    assert restored_task_id == 41
    assert restored["plan_id"] == plan["plan_id"]
    assert restored["current_step_id"] == "step_1"


def main() -> None:
    """
    入参:
      - 无。
    方法:
      - 顺序运行阶段 6 的状态迁移与持久化测试。
    出参:
      - 全部通过时打印固定成功标记。
    """
    test_plan_completion_requires_passed_evidence()
    test_failed_validation_blocks_and_records_repair()
    test_plan_persists_and_restores_from_ai_task()
    print("plan_state_test: PASS")


if __name__ == "__main__":
    main()
