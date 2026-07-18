"""阶段 7 lesson_policy 准入与注入回归测试。"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.memory.lesson_policy import (
    build_lesson_contract,
    choose_strongest_lesson,
    format_accepted_lesson_for_prompt,
)
from backend.agent.memory.self_correction import detect_self_corrections
from backend.agent.tools.memory_tools import view_user_memory


def _distilled() -> dict:
    """
    入参:
      - 无。
    方法:
      - 构造通过严格字段校验的语义提炼结果。
    出参:
      - 五字段 distilled lesson。
    """
    return {
        "现象": "报告生成后找不到输出文件",
        "根因": "输出目录没有写入权限",
        "正确方法": "将输出目录切换到当前会话工作区后重新生成报告",
        "验证信号": "agent_validation.status=passed 且 PDF 文件存在",
        "严重度": "high",
    }


def _correction(validation_status: str = "passed", repair_success: bool = True) -> dict:
    """
    入参:
      - validation_status: 修复后验证状态。
      - repair_success: 修复成功观测值。
    方法:
      - 构造包含修复证据的候选事件。
    出参:
      - lesson_policy 可消费的 correction 字典。
    """
    return {
        "tool_name": "generate_monitor_report",
        "failure_type": "permission_or_path_error",
        "error_msg": "Permission denied: E:\\private\\report.pdf",
        "actual_repair_action": "generate_monitor_report",
        "next_agent_validation": {"status": validation_status},
        "repair_success": repair_success,
        "severity": "high",
        "evidence_refs": ["call-failed", "call-fixed"],
    }


def test_admission_requires_passed_verification() -> None:
    """
    入参:
      - 无。
    方法:
      - 比较 passed、failed 和中严重度缺证据候选的准入状态。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    accepted = build_lesson_contract(_correction(), _distilled())
    review = build_lesson_contract(_correction("failed", False), _distilled())
    rejected_source = _correction("unverified", False)
    rejected_source["severity"] = "medium"
    rejected = build_lesson_contract(rejected_source, _distilled())

    assert accepted["status"] == "accepted"
    assert review["status"] == "review"
    assert rejected["status"] == "rejected"
    assert "E:\\private" not in accepted["failure_signature"]


def test_only_accepted_rule_enters_prompt() -> None:
    """
    入参:
      - 无。
    方法:
      - 序列化 accepted/review lesson, 验证 prompt 只读取前者的规则和验证信号。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    accepted = build_lesson_contract(_correction(), _distilled())
    review = build_lesson_contract(_correction("failed", False), _distilled())
    accepted_text = format_accepted_lesson_for_prompt(json.dumps(accepted, ensure_ascii=False))
    review_text = format_accepted_lesson_for_prompt(json.dumps(review, ensure_ascii=False))

    assert "规则：" in accepted_text
    assert "验证：" in accepted_text
    assert "错误原文" not in accepted_text
    assert review_text == ""


def test_dedup_keeps_strongest_evidence() -> None:
    """
    入参:
      - 无。
    方法:
      - 构造同 dedup_key 的已有记录, 比较证据数量决定是否覆盖。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    candidate = build_lesson_contract(_correction(), _distilled())
    existing = dict(candidate)
    existing["evidence_refs"] = ["a", "b", "c"]
    assert choose_strongest_lesson(candidate, [json.dumps(existing, ensure_ascii=False)]) is False
    candidate["evidence_refs"].append("d")
    candidate["evidence_refs"].append("e")
    assert choose_strongest_lesson(candidate, [json.dumps(existing, ensure_ascii=False)]) is True


def test_detector_requires_repair_verification() -> None:
    """
    入参:
      - 无。
    方法:
      - 构造失败、修复调用与 ToolMessage, 验证仅 passed 修复被识别。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    failed_call = {"id": "call-failed", "name": "generate_monitor_report", "args": {}}
    fixed_call = {"id": "call-fixed", "name": "generate_monitor_report", "args": {}}
    failed_result = {
        "type": "error",
        "msg": "permission denied",
        "agent_validation": {
            "status": "failed",
            "repair_plan": {"failure_type": "permission_or_path_error"},
        },
    }
    messages = [
        AIMessage(content="", tool_calls=[failed_call]),
        ToolMessage(content=json.dumps(failed_result), tool_call_id="call-failed"),
        AIMessage(content="修复输出路径后重新执行", tool_calls=[fixed_call]),
        ToolMessage(
            content=json.dumps({
                "type": "success",
                "agent_validation": {"status": "passed", "evidence": ["pdf_exists"]},
            }),
            tool_call_id="call-fixed",
        ),
    ]
    corrections = detect_self_corrections(messages)
    assert len(corrections) == 1
    assert corrections[0]["repair_success"] is True
    assert corrections[0]["next_agent_validation"]["status"] == "passed"

    messages[-1] = ToolMessage(
        content=json.dumps({"type": "success", "agent_validation": {"status": "warning"}}),
        tool_call_id="call-fixed",
    )
    assert detect_self_corrections(messages) == []


def test_memory_tool_hides_unaccepted_lessons() -> None:
    """
    入参:
      - 无。
    方法:
      - 模拟一个 legacy lesson 与一个 accepted lesson, 检查工具读取出口统一过滤。
    出参:
      - 断言失败时抛出 AssertionError。
    """
    accepted = build_lesson_contract(_correction(), _distilled())
    shared = [
        {"key": "legacy", "value": "未经验证的旧教训", "category": "lesson"},
        {"key": accepted["dedup_key"], "value": json.dumps(accepted, ensure_ascii=False), "category": "lesson"},
    ]
    with patch("backend.agent.memory.agent_db.load_user_memory", side_effect=[[], shared]):
        result = view_user_memory.invoke({})

    assert result["type"] == "success"
    assert result["data"]["count"] == 1
    assert "未经验证的旧教训" not in result["summary"]
    assert "规则：" in result["summary"]


def main() -> None:
    """
    入参:
      - 无。
    方法:
      - 顺序运行阶段 7 的准入、去重、注入和证据关联测试。
    出参:
      - 全部通过时打印固定成功标记。
    """
    test_admission_requires_passed_verification()
    test_only_accepted_rule_enters_prompt()
    test_dedup_keeps_strongest_evidence()
    test_detector_requires_repair_verification()
    test_memory_tool_hides_unaccepted_lessons()
    print("lesson_policy_test: PASS")


if __name__ == "__main__":
    main()
