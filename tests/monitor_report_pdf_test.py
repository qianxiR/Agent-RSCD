"""
自然资源遥感监测 PDF 报告回归测试。

运行方式:
conda run -n sam3 python tests\\monitor_report_pdf_test.py
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.model.tools.report_tools import generate_monitor_report


def _assert_true(condition: bool, label: str) -> None:
    """
    入参:
      - condition: 需要为真的断言条件。
      - label: 断言失败时展示的标签。
    方法:
      - 对不满足条件的结果抛出 AssertionError。
    出参:
      - None。
    """
    if not condition:
        raise AssertionError(label)


def _write_fixture_geojson(path: Path) -> None:
    """
    入参:
      - path: 临时 GeoJSON 输出位置。
    方法:
      - 写入带面积属性的两个测试图斑，避免面积计算依赖坐标参考系。
    出参:
      - None。
    """
    feature_collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"class_name": "新增建筑", "area_m2": 1200.0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[116.0, 39.0], [116.01, 39.0], [116.01, 39.01], [116.0, 39.01], [116.0, 39.0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"class_name": "新增建筑", "area_m2": 800.0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[116.02, 39.0], [116.03, 39.0], [116.03, 39.01], [116.02, 39.01], [116.02, 39.0]]],
                },
            },
        ],
    }
    path.write_text(json.dumps(feature_collection, ensure_ascii=False), encoding="utf-8")


def run_monitor_report_pdf_test() -> None:
    """
    入参:
      - 无。
    方法:
      - 生成真实 PDF 报告并校验产物可读、页数有效和模板章节齐全。
      - 验证报告生成路径不降级为 Markdown。
    出参:
      - None。
    """
    artifact_dir = ROOT / "tests" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = artifact_dir / "monitor_report_fixture.geojson"
    _write_fixture_geojson(geojson_path)

    result = generate_monitor_report.invoke({
        "change_geojson_path": str(geojson_path),
        "output_format": "pdf",
        "region_name": "测试监测区",
        "t1_name": "2026 年基准影像",
        "t2_name": "2026 年监测影像",
        "change_type": "新增建筑",
        "change_reason": "测试图斑用于验证报告结构和 PDF 版式。",
    })
    _assert_true(result.get("type") == "frontend_action", "report should be a download action")
    report_path = Path((result.get("data") or {}).get("artifact_path") or "")
    _assert_true(report_path.suffix.lower() == ".pdf", "report should not downgrade to markdown")
    _assert_true(report_path.is_file(), "pdf report should exist")

    _assert_true(report_path.stat().st_size > 1024, "pdf should contain rendered report content")

    pdfinfo_wrapper = shutil.which("pdfinfo")
    _assert_true(pdfinfo_wrapper is not None, "pdfinfo should be available for PDF page verification")
    runtime_root = Path(pdfinfo_wrapper).resolve().parents[2]
    pdfinfo_path = runtime_root / "native" / "poppler" / "Library" / "bin" / "pdfinfo.exe"
    pdftoppm_path = runtime_root / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    _assert_true(pdfinfo_path.is_file(), "native pdfinfo executable should be available")
    _assert_true(pdftoppm_path.is_file(), "native pdftoppm executable should be available")

    qa_dir = Path(tempfile.gettempdir()) / "agent_rscd_pdf_qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_pdf_path = qa_dir / "monitor_report.pdf"
    shutil.copy2(report_path, qa_pdf_path)
    pdfinfo = subprocess.run(
        [str(pdfinfo_path), str(qa_pdf_path)], capture_output=True, text=True, check=True,
        encoding="gbk", errors="replace",
    )
    page_match = re.search(r"^Pages:\s+(\d+)", pdfinfo.stdout, re.MULTILINE)
    _assert_true(page_match is not None and int(page_match.group(1)) >= 1, "pdf should contain at least one page")

    render_prefix = qa_dir / "page"
    subprocess.run(
        [str(pdftoppm_path), "-png", "-f", "1", "-singlefile", str(qa_pdf_path), str(render_prefix)],
        check=True,
    )
    _assert_true((qa_dir / "page.png").is_file(), "first PDF page should render to PNG")

    template_path = ROOT / "backend" / "model" / "report_templates" / "monitor_report.md.j2"
    template_text = template_path.read_text(encoding="utf-8")
    for section in ("监测范围、数据与方法", "质量说明与适用边界", "核查与后续工作建议", "成果清单"):
        _assert_true(section in template_text, f"report template should include {section}")


if __name__ == "__main__":
    run_monitor_report_pdf_test()
    print("monitor_report_pdf_test: PASS")
