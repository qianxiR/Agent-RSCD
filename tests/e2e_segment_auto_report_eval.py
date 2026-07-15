"""
真实分割到自动报告的端到端评估 (解耦版)。

解耦后 segment_image 不再内嵌报告, 改为:
  1. segment_image 产物断言: data 保留 polygon_layer/base_layer/vector_stats/image_path/classes,
     移除 auto_report/artifact_path/artifact_url。
  2. 用 segment 产物组装 payload 调 run_report_worker, 验证叠加 PNG 与 PDF 生成。

运行方式:
conda run -n sam3 python tests\\e2e_segment_auto_report_eval.py
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.runtime.context_vars import set_runtime_context
from backend.model.tools.samseg_tools import segment_image

METRICS_PATH = ROOT / "tests" / "artifacts" / "e2e_segment_auto_report_metrics.json"
SOURCE_IMAGE_PATH = ROOT / "agent-files" / "geoserver" / "generate" / "_default" / "7ddbc1a4" / "r000_c006_t1.tif"


def _assert_true(condition: bool, label: str) -> None:
    """
    入参:
      - condition: 需要为 True 的条件。
      - label: 断言标签。
    方法:
      - 条件不成立时抛 AssertionError。
    出参:
      - None。
    """
    if not condition:
        raise AssertionError(label)


def _pdf_image_count(pdf_path: str) -> int:
    """
    入参:
      - pdf_path: PDF 报告路径。
    方法:
      - 在未安装 PDF 解析库时, 用 PDF 二进制对象标记粗略检查图片是否嵌入。
    出参:
      - int, `/Subtype /Image` 出现次数。
    """
    path = Path(pdf_path)
    if not path.is_file():
        return 0
    return path.read_bytes().count(b"/Subtype /Image")


def _write_metrics(report: Dict[str, Any]) -> None:
    """
    入参:
      - report: 端到端评估报告。
    方法:
      - 写入 tests\\artifacts\\e2e_segment_auto_report_metrics.json。
    出参:
      - None。
    """
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_e2e_segment_auto_report_eval() -> Dict[str, Any]:
    """
    入参:
      - 无。
    方法:
      - 调用真实 segment_image 执行建筑物分割。
      - 解耦断言: data 保留 5 个 worker payload 字段, 移除 auto_report/artifact_path/artifact_url。
      - 派发断言: 用 segment 产物组装 payload 调 run_report_worker, 验证叠加 PNG 与 PDF。
    出参:
      - dict, 包含 metrics 和 worker_report。
    """
    from backend.agent.team.report_worker import run_report_worker

    _assert_true(SOURCE_IMAGE_PATH.is_file(), f"测试原图不存在: {SOURCE_IMAGE_PATH}")
    set_runtime_context(
        conversation_id="auto_seg",
        user_id="eval_user",
        project_id="agent_eval",
    )
    result = segment_image.invoke({
        "image_path": str(SOURCE_IMAGE_PATH),
        "classes": "建筑物",
    })
    _assert_true(result.get("type") == "frontend_action", "segment_image should return frontend_action")
    data = result.get("data") or {}
    for key in ("image_path", "classes", "polygon_layer", "base_layer", "vector_stats"):
        _assert_true(key in data, f"data.{key} should remain after decoupling")
    for key in ("auto_report", "artifact_path", "artifact_url"):
        _assert_true(key not in data, f"data.{key} should be removed after decoupling")

    worker_out = run_report_worker({
        "task_type": "segmentation_auto_report",
        "image_path": data.get("image_path") or str(SOURCE_IMAGE_PATH),
        "classes": data.get("classes") or "建筑物",
        "polygon_layer": data.get("polygon_layer") or {},
        "base_layer": data.get("base_layer") or {},
        "vector_stats": data.get("vector_stats") or {},
    })
    _assert_true(worker_out.get("status") == "passed", f"report worker failed: {worker_out.get('issues')}")
    artifacts = worker_out.get("artifacts") or {}
    report_path = artifacts.get("report_path") or worker_out.get("report_path") or ""
    overlay_path = artifacts.get("overlay_image_path") or worker_out.get("overlay_image_path") or ""
    _assert_true(Path(report_path).is_file(), f"report missing: {report_path}")
    _assert_true(Path(overlay_path).is_file(), f"overlay missing: {overlay_path}")
    image_count = _pdf_image_count(report_path)
    _assert_true(image_count > 0, "PDF should contain image object")
    metrics = {
        "segment_executed": True,
        "worker_status": worker_out.get("status"),
        "overlay_image_generated": True,
        "report_generated": True,
        "report_pdf_image_count": image_count,
        "report_path": report_path,
        "overlay_image_path": overlay_path,
        "geojson_path": artifacts.get("geojson_path") or "",
        "source_image_path": artifacts.get("source_image_path") or "",
    }
    report = {
        "metrics": metrics,
        "worker_report": worker_out,
        "artifact": str(METRICS_PATH),
    }
    _write_metrics(report)
    return report


if __name__ == "__main__":
    result = run_e2e_segment_auto_report_eval()
    print("e2e_segment_auto_report_eval: PASS")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
