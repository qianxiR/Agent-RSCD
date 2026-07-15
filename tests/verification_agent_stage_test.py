"""
verification_agent 验证阶段独立测试脚本。

运行方式:
python tests\\verification_agent_stage_test.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.team.validation_stage import run_validation_stage


def _assert_equal(actual, expected, label):
    """
    入参:
      - actual: 实际值。
      - expected: 期望值。
      - label: 断言标签。
    方法:
      - 比较实际值和期望值, 不一致时抛 AssertionError。
      - 这样做是为了保持 tests/ 下独立脚本风格, 不引入 pytest 依赖。
    出参:
      - 无返回; 失败时抛异常。
    """
    if actual != expected:
        raise AssertionError(f"{label}: expected={expected!r}, actual={actual!r}")


def test_passed_with_artifact_path():
    """
    入参:
      - 无。
    方法:
      - 创建一个临时 PNG 文件作为工具产物。
      - 构造带 artifact_path 的 success 工具结果。
      - 验证阶段应返回 passed。
    出参:
      - 无返回; 失败时抛异常。
    """
    with tempfile.TemporaryDirectory() as tmp:
        artifact = Path(tmp) / "result.png"
        artifact.write_bytes(b"x" * 256)
        result = run_validation_stage(
            "render_sandbox_image",
            {
                "type": "success",
                "summary": "产物已生成",
                "data": {"artifact_path": str(artifact)},
            },
            user_goal="验证图片产物",
        )
    _assert_equal(result["status"], "passed", "artifact path validation")


def test_failed_with_tool_error():
    """
    入参:
      - 无。
    方法:
      - 构造 error 工具结果。
      - 验证阶段应返回 failed, 并保留失败 issue。
    出参:
      - 无返回; 失败时抛异常。
    """
    result = run_validation_stage(
        "segment_image",
        {"type": "error", "msg": "模型权重不存在"},
        user_goal="执行分割",
    )
    _assert_equal(result["status"], "failed", "tool error validation")
    if not result["issues"]:
        raise AssertionError("tool error validation: issues should not be empty")
    _assert_equal(
        result["repair_plan"]["next_action"],
        "diagnose_error_then_retry",
        "tool error repair plan",
    )


def test_skipped_for_plain_success():
    """
    入参:
      - 无。
    方法:
      - 构造无产物路径、无 verification 的普通 success 结果。
      - 验证阶段应跳过, 避免普通查询结果被 warning 污染。
    出参:
      - 无返回; 失败时抛异常。
    """
    result = run_validation_stage(
        "list_image_catalog",
        {"type": "success", "summary": "查询完成", "data": {"count": 3}},
        user_goal="查询影像",
    )
    _assert_equal(result, None, "plain success should skip")


def test_missing_artifact_repair_plan():
    """
    入参:
      - 无。
    方法:
      - 构造指向不存在文件的产物结果。
      - 验证阶段应返回 failed, repair_plan 应建议检查输出路径后重跑原工具。
    出参:
      - 无返回; 失败时抛异常。
    """
    missing = str(ROOT / "agent-files" / "missing-for-test.png")
    result = run_validation_stage(
        "render_sandbox_image",
        {
            "type": "success",
            "summary": "产物已生成",
            "data": {"artifact_path": missing},
        },
        user_goal="验证缺失产物修复策略",
    )
    _assert_equal(result["status"], "failed", "missing artifact status")
    _assert_equal(
        result["repair_plan"]["failure_type"],
        "missing_artifact",
        "missing artifact repair type",
    )
    _assert_equal(
        result["repair_plan"]["next_action"],
        "retry_same_tool_with_checked_output_path",
        "missing artifact next action",
    )


def test_empty_vector_keeps_target_with_synonyms():
    """
    入参:
      - 无。
    方法:
      - 构造 verification 指示矢量要素数为 0 的工具结果。
      - repair_plan 应保持原目标类别, 用同义词扩展类别表达后回到分割阶段。
    出参:
      - 无返回; 失败时抛异常。
    """
    result = run_validation_stage(
        "export_mask_to_shapefile",
        {
            "type": "success",
            "summary": "矢量导出完成",
            "verification": {
                "ok": False,
                "reason": "要素数为 0 (< 1 阈值), 矢量为空",
                "feature_count": 0,
            },
        },
        user_goal="提取建筑物",
    )
    _assert_equal(result["status"], "failed", "empty vector status")
    _assert_equal(
        result["repair_plan"]["failure_type"],
        "empty_vector",
        "empty vector failure type",
    )
    _assert_equal(
        result["repair_plan"]["next_action"],
        "return_to_segmentation_with_same_target_and_synonyms",
        "empty vector next action",
    )
    _assert_equal(
        result["repair_plan"]["parameter_changes"]["preserve_user_target"],
        True,
        "empty vector preserves user target",
    )


def test_missing_input_file_has_explicit_repair_plan():
    """
    入参:
      - 无。
    方法:
      - 构造 import_file_to_sandbox 返回的本地输入文件不存在错误。
      - 验证阶段应将其分类为 missing_input_file, 而不是通用 tool_error。
      - repair_plan 应阻止同一路径无效重试, 并请求有效路径或上传文件。
    出参:
      - 无返回; 失败时抛异常。
    """
    result = run_validation_stage(
        "import_file_to_sandbox",
        {
            "type": "error",
            "msg": "本地文件不存在: H:\\西藏遥感Agent\\Agent\\agent-files\\missing-input.png。请确认路径正确。",
        },
        user_goal="对本地影像做建筑物分割",
    )
    _assert_equal(result["status"], "failed", "missing input file status")
    _assert_equal(
        result["repair_plan"]["failure_type"],
        "missing_input_file",
        "missing input file failure type",
    )
    _assert_equal(
        result["repair_plan"]["next_action"],
        "request_valid_input_file_or_upload",
        "missing input file next action",
    )
    _assert_equal(
        result["repair_plan"]["parameter_changes"]["do_not_retry_same_missing_path"],
        True,
        "missing input file should block same path retry",
    )


if __name__ == "__main__":
    test_passed_with_artifact_path()
    test_failed_with_tool_error()
    test_skipped_for_plain_success()
    test_missing_artifact_repair_plan()
    test_empty_vector_keeps_target_with_synonyms()
    test_missing_input_file_has_explicit_repair_plan()
    print("verification_agent_stage_test: PASS")
