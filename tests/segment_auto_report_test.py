"""
分割后自动报告集成测试。

运行方式:
python tests\\segment_auto_report_test.py
"""

import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.team.report_worker import build_segmentation_report


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


class _FakeReportTool:
    """
    入参:
      - 无。
    方法:
      - 模拟 LangChain StructuredTool.invoke。
      - 检查 overlay_image_path 已传入报告生成器。
    出参:
      - frontend_action download 结构。
    """

    def invoke(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        入参:
          - args: generate_monitor_report 参数。
        方法:
          - 写入一个最小 PDF 文件。
          - 返回标准 download action。
        出参:
          - dict, 模拟报告工具结果。
        """
        overlay_path = args.get("overlay_image_path") or ""
        _assert_true(Path(overlay_path).is_file(), "overlay image should exist")
        report_path = ROOT / "tests" / "artifacts" / "segment_auto_report.pdf"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(b"%PDF-1.4\n% mocked segment auto report\n")
        return {
            "type": "frontend_action",
            "action": "download",
            "summary": "已生成自动分割报告",
            "data": {
                "artifact_path": str(report_path),
                "artifact_url": "/api/v1/download/report/mock.pdf",
                "format": "pdf",
            },
            "verification": {"ok": True, "size_bytes": report_path.stat().st_size},
        }


def _patch_auto_report_dependencies() -> None:
    """
    入参:
      - 无。
    方法:
      - 替换 GeoServer 下载、矢量叠加和报告生成函数。
      - 避免测试触发真实 GeoServer、真实模型和真实 PDF 渲染。
    出参:
      - None。
    """
    from backend.data import geoserver_client
    from backend.model.tools import vector_tools
    from backend.model.tools import report_tools

    def fake_download_geojson(layer_name: str, workspace: str = None, output_path: str = None) -> str:
        target = Path(output_path or ROOT / "tests" / "artifacts" / "segment_auto_report.geojson")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        return str(target)

    def fake_download_raster(layer_name: str, workspace: str = None, output_path: str = None) -> str:
        target = Path(output_path or ROOT / "tests" / "artifacts" / "segment_auto_report.tif")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"mock raster")
        return str(target)

    def fake_rasterize_vector_to_png(
        geojson_path: str,
        background_image_path: str = "",
        alpha: float = 0.5,
        export_shp: bool = True,
    ) -> Dict[str, Any]:
        png_path = ROOT / "tests" / "artifacts" / "segment_auto_report_overlay.png"
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(b"\x89PNG\r\n\x1a\nmock")
        return {
            "ok": True,
            "png_path": str(png_path),
            "feature_count": 1,
            "legend": [{"name": "building", "hex": "#000075"}],
        }

    geoserver_client.download_geojson = fake_download_geojson
    geoserver_client.download_raster = fake_download_raster
    vector_tools.rasterize_vector_to_png = fake_rasterize_vector_to_png
    report_tools.generate_monitor_report = _FakeReportTool()


def run_segment_auto_report_test() -> None:
    """
    入参:
      - 无。
    方法:
      - 验证正常 GeoServer 图层输入能生成 success 自动报告。
      - 验证缺少图层时返回 skipped, 不伪装成功。
    出参:
      - None。
    """
    _patch_auto_report_dependencies()
    report = build_segmentation_report(
        image_path=str(ROOT / "agent-files" / "mock-source.tif"),
        classes="建筑物",
        polygon_layer={"workspace": "samseg", "layer_name": "seg_poly"},
        base_layer={"workspace": "samseg", "layer_name": "seg_original"},
        vector_stats={"total_features": 1},
    )
    artifacts = report.get("artifacts") or {}
    _assert_true(report["status"] == "passed", "auto report should pass")
    _assert_true(report["agent_role"] == "report_agent", "worker role should be report_agent")
    _assert_true(Path(artifacts["geojson_path"]).is_file(), "geojson should be downloaded")
    _assert_true(Path(artifacts["source_image_path"]).is_file(), "source image should be downloaded")
    _assert_true(Path(artifacts["overlay_image_path"]).is_file(), "overlay should exist")
    _assert_true(Path(artifacts["report_path"]).is_file(), "report should exist")
    _assert_true(artifacts["report_url"], "report url should exist")

    skipped = build_segmentation_report(
        image_path=str(ROOT / "agent-files" / "mock-source.tif"),
        classes="建筑物",
        polygon_layer={},
        base_layer={"workspace": "samseg", "layer_name": "seg_original"},
        vector_stats={},
    )
    _assert_true(skipped["status"] == "warning", "missing layer should warn")


if __name__ == "__main__":
    run_segment_auto_report_test()
    print("segment_auto_report_test: PASS")
