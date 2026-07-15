"""
GeoServer 图层到监测报告的端到端评估。

运行方式:
python tests\\e2e_geoserver_report_eval.py
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.runtime.context_vars import set_runtime_context
from backend.data import geoserver_client
from backend.model.tools.report_tools import generate_monitor_report
from backend.model.tools.vector_tools import rasterize_vector_to_png

ARTIFACT_DIR = ROOT / "tests" / "artifacts" / "e2e_geoserver_report"
METRICS_PATH = ROOT / "tests" / "artifacts" / "e2e_geoserver_report_metrics.json"

SEG_LAYER = "9ff53e2a__seg_s47d"
EDGE_LAYER = "9ff53e2a__seg_s47d_edge"


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


def _download_layer(layer_name: str) -> Dict[str, Any]:
    """
    入参:
      - layer_name: GeoServer 图层裸名或全名。
    方法:
      - 自动解析工作空间。
      - 通过 WFS 下载 GeoJSON 到 tests\\artifacts\\e2e_geoserver_report。
    出参:
      - dict, 包含 resolved_name、output_path、bbox。
    """
    resolved = geoserver_client.resolve_layer_name(layer_name)
    _assert_true(bool(resolved), f"GeoServer 图层不存在: {layer_name}")
    output_path = ARTIFACT_DIR / f"{layer_name}.geojson"
    downloaded = geoserver_client.download_geojson(resolved, output_path=str(output_path))
    _assert_true(bool(downloaded), f"WFS 下载失败: {resolved}")
    bbox = geoserver_client.get_layer_bbox(resolved)
    return {
        "layer_name": layer_name,
        "resolved_name": resolved,
        "output_path": str(output_path),
        "bbox": bbox,
    }


def _summarize_geojson(path: str) -> Dict[str, Any]:
    """
    入参:
      - path: 本地 GeoJSON 路径。
    方法:
      - 优先使用 geopandas 读取要素、字段、几何类型和面积。
      - geopandas 不可用时退回 JSON FeatureCollection 统计。
    出参:
      - dict, 图层摘要。
    """
    try:
        import geopandas as gpd
        gdf = gpd.read_file(path)
        geom_types = sorted({str(item) for item in gdf.geometry.geom_type.dropna().unique()})
        columns = [str(col) for col in gdf.columns if col != gdf.geometry.name]
        area_m2 = 0.0
        if "area_m2" in gdf.columns:
            area_m2 = float(gdf["area_m2"].fillna(0).sum())
        else:
            try:
                projected = gdf.to_crs(epsg=3857)
                area_m2 = float(projected.geometry.area.fillna(0).sum())
            except Exception:
                area_m2 = float(gdf.geometry.area.fillna(0).sum())
        return {
            "feature_count": int(len(gdf)),
            "geometry_types": geom_types,
            "columns": columns,
            "total_area_m2": round(area_m2, 2),
            "reader": "geopandas",
        }
    except Exception:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        features = data.get("features") or []
        geom_types = sorted({
            str((feature.get("geometry") or {}).get("type") or "")
            for feature in features
            if isinstance(feature, dict)
        })
        columns = sorted({
            str(key)
            for feature in features
            for key in ((feature.get("properties") or {}).keys() if isinstance(feature, dict) else [])
        })
        return {
            "feature_count": len(features),
            "geometry_types": geom_types,
            "columns": columns,
            "total_area_m2": 0.0,
            "reader": "json",
        }


def _bbox_tuple(bbox: Dict[str, float]) -> tuple[float, float, float, float]:
    """
    入参:
      - bbox: GeoServer 返回的 bbox 字典。
    方法:
      - 按 minx、miny、maxx、maxy 顺序转为元组。
    出参:
      - tuple, WGS84 bbox。
    """
    return (
        float(bbox["minx"]),
        float(bbox["miny"]),
        float(bbox["maxx"]),
        float(bbox["maxy"]),
    )


def _overlap_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """
    入参:
      - a: 第一个 bbox。
      - b: 第二个 bbox。
    方法:
      - 计算两个 bbox 的交集面积相对 a 面积的比例。
    出参:
      - float, 0 到 1 的重叠比例。
    """
    left = max(a[0], b[0])
    bottom = max(a[1], b[1])
    right = min(a[2], b[2])
    top = min(a[3], b[3])
    if right <= left or top <= bottom:
        return 0.0
    area_a = max((a[2] - a[0]) * (a[3] - a[1]), 0.0)
    if area_a <= 0:
        return 0.0
    return ((right - left) * (top - bottom)) / area_a


def _find_source_image_for_bbox(bbox: Dict[str, float]) -> str:
    """
    入参:
      - bbox: GeoServer 图层 WGS84 bbox。
    方法:
      - 扫描 agent-files 下的本地 tif/tiff 栅格。
      - 将栅格 bounds 转到 EPSG:4326 后按重叠比例匹配。
      - 优先返回覆盖目标 bbox 的本地原图。
    出参:
      - str, 匹配到的本地原图路径; 找不到时返回空字符串。
    """
    try:
        import rasterio
        from rasterio.warp import transform_bounds
    except ImportError:
        return ""

    target = _bbox_tuple(bbox)
    best_path = ""
    best_score = 0.0
    for path in (ROOT / "agent-files").rglob("*"):
        if path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        try:
            with rasterio.open(path) as src:
                if not src.crs:
                    continue
                if getattr(src.crs, "is_geographic", False):
                    bounds = (src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top)
                else:
                    bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
                candidate = (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
                score = _overlap_ratio(target, candidate)
                if score > best_score:
                    best_score = score
                    best_path = str(path.resolve())
        except Exception:
            continue
    return best_path if best_score >= 0.95 else ""


def _build_overlay_image(geojson_path: str, source_image_path: str) -> Dict[str, Any]:
    """
    入参:
      - geojson_path: GeoServer 下载后的图斑 GeoJSON。
      - source_image_path: 与图斑 bbox 匹配的本地原始影像。
    方法:
      - 调用矢量可视化核心函数生成原图叠加图斑 PNG。
      - 校验 PNG 已真实落盘。
    出参:
      - dict, 包含 overlay_image_path、source_image_path 和 verification。
    """
    _assert_true(bool(source_image_path), "未找到与 GeoServer 图层 bbox 匹配的原图")
    overlay = rasterize_vector_to_png(
        geojson_path=geojson_path,
        background_image_path=source_image_path,
        alpha=0.35,
        export_shp=False,
    )
    _assert_true(bool(overlay.get("ok")), f"叠加图生成失败: {overlay.get('reason')}")
    overlay_path = overlay.get("png_path") or ""
    _assert_true(Path(overlay_path).is_file(), f"叠加图未生成: {overlay_path}")
    return {
        "overlay_image_path": overlay_path,
        "source_image_path": source_image_path,
        "feature_count": overlay.get("feature_count"),
        "legend": overlay.get("legend") or [],
    }


def _write_summary(layer_reports: List[Dict[str, Any]], report_result: Dict[str, Any]) -> str:
    """
    入参:
      - layer_reports: 两个 GeoServer 图层的读取摘要。
      - report_result: generate_monitor_report 返回结果。
    方法:
      - 生成端到端评估 Markdown 摘要, 记录数据来源和报告产物。
    出参:
      - str, Markdown 文件路径。
    """
    lines = [
        "# GeoServer 图层端到端报告评估",
        "",
        "## 输入图层",
        "",
        "| 图层 | GeoServer 全名 | 要素数 | 几何类型 | 面积 m2 | 本地 GeoJSON |",
        "|---|---|---:|---|---:|---|",
    ]
    for item in layer_reports:
        summary = item["summary"]
        lines.append(
            f"| `{item['layer_name']}` | `{item['resolved_name']}` | "
            f"{summary['feature_count']} | {', '.join(summary['geometry_types'])} | "
            f"{summary['total_area_m2']} | `{item['output_path']}` |"
        )
    data = report_result.get("data") or {}
    verification = report_result.get("verification") or {}
    overlay_image_path = data.get("overlay_image_path") or ""
    lines.extend([
        "",
        "## 报告产物",
        "",
        f"- `type`: {report_result.get('type')}",
        f"- `artifact_path`: `{data.get('artifact_path', '')}`",
        f"- `overlay_image_path`: `{overlay_image_path}`",
        f"- `verification.ok`: {verification.get('ok')}",
        f"- `verification.evidence`: {verification.get('evidence', '')}",
        "",
        "## 工具摘要",
        "",
        report_result.get("summary") or "",
    ])
    summary_path = ARTIFACT_DIR / "e2e_geoserver_report_summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return str(summary_path)


def _write_metrics(report: Dict[str, Any]) -> None:
    """
    入参:
      - report: 端到端评估报告。
    方法:
      - 写入 tests\\artifacts\\e2e_geoserver_report_metrics.json。
    出参:
      - None。
    """
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_e2e_geoserver_report_eval() -> Dict[str, Any]:
    """
    入参:
      - 无。
    方法:
      - 从 GeoServer 读取 9ff53e2a__seg_s47d 和 9ff53e2a__seg_s47d_edge。
      - 下载为本地 GeoJSON 并统计要素。
      - 基于分割结果图层生成监测报告。
    出参:
      - dict, 包含 metrics、layers 和 report_result。
    """
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    set_runtime_context(
        conversation_id="e2e_geoserver_report_eval",
        user_id="eval_user",
        project_id="agent_eval",
    )
    _assert_true(geoserver_client.geoserver_available(), "GeoServer 不可用")
    layer_reports = []
    for layer_name in [SEG_LAYER, EDGE_LAYER]:
        downloaded = _download_layer(layer_name)
        downloaded["summary"] = _summarize_geojson(downloaded["output_path"])
        _assert_true(downloaded["summary"]["feature_count"] > 0, f"图层无要素: {layer_name}")
        layer_reports.append(downloaded)

    source_image_path = _find_source_image_for_bbox(layer_reports[0]["bbox"])
    overlay_result = _build_overlay_image(layer_reports[0]["output_path"], source_image_path)

    report_result = generate_monitor_report.invoke({
        "change_geojson_path": layer_reports[0]["output_path"],
        "output_format": "pdf",
        "region_name": "GeoServer端到端评估区域",
        "t1_name": SEG_LAYER,
        "t2_name": EDGE_LAYER,
        "change_type": "分割结果监测",
        "change_confidence": 1.0,
        "change_reason": "从 GeoServer 读取分割面图层和边界图层后生成报告。",
        "overlay_image_path": overlay_result["overlay_image_path"],
        "overlay_image_caption": "图斑矢量叠加到原始遥感影像后的空间位置示意。",
    })
    _assert_true(report_result.get("type") != "error", f"报告生成失败: {report_result.get('msg')}")
    _assert_true((report_result.get("verification") or {}).get("ok") is True, "报告产物未通过校验")
    report_data = report_result.get("data") or {}
    _assert_true(report_data.get("format") == "pdf", f"报告未生成 PDF: {report_data.get('format')}")
    _assert_true(str(report_data.get("artifact_path") or "").lower().endswith(".pdf"), "报告产物不是 PDF")
    _assert_true(bool(report_data.get("overlay_image_path")), "报告结果缺少 overlay_image_path")
    summary_path = _write_summary(layer_reports, report_result)

    metrics = {
        "geoserver_available": True,
        "layers_requested": 2,
        "layers_read_rate": "2 / 2",
        "overlay_image_generated": True,
        "source_image_path": source_image_path,
        "overlay_image_path": overlay_result["overlay_image_path"],
        "report_generated": True,
        "report_verification_ok": True,
        "summary_path": summary_path,
        "report_artifact_path": (report_result.get("data") or {}).get("artifact_path", ""),
    }
    report = {
        "metrics": metrics,
        "layers": layer_reports,
        "overlay_result": overlay_result,
        "report_result": report_result,
        "artifact": str(METRICS_PATH),
    }
    _write_metrics(report)
    return report


if __name__ == "__main__":
    result = run_e2e_geoserver_report_eval()
    print("e2e_geoserver_report_eval: PASS")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
