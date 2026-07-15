"""
report_agent worker (Agent 层)

report_agent 负责受控报告子任务: 从 GeoServer 回读证据、生成叠加图、
生成 PDF 报告, 并把结构化汇报返回给主控 Agent。
"""

from pathlib import Path
from typing import Any, Dict, Optional

from backend.agent.team.agent_roles import REPORT_AGENT
from backend.agent.team.result_contract import (
    RECOMMEND_CONTINUE,
    RECOMMEND_REPORT_ERROR,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_WARNING,
    build_worker_result,
)
from backend.config import settings
from backend.model.tools._paths import rand_suffix, session_subpath, short_conv


def _layer_full_name(layer: Dict[str, Any]) -> str:
    """
    入参:
      - layer: GeoServer 图层参数, 包含 workspace 和 layer_name。
    方法:
      - 将前端图层参数转换为 GeoServer 全名。
      - 缺少必要字段时返回空字符串。
    出参:
      - str, 形如 workspace:layer_name。
    """
    if not isinstance(layer, dict):
        return ""
    workspace = layer.get("workspace") or ""
    layer_name = layer.get("layer_name") or ""
    if not workspace or not layer_name:
        return ""
    return f"{workspace}:{layer_name}"


def _build_auto_report_dir() -> Path:
    """
    入参:
      - 无。
    方法:
      - 构造 report_agent 的 GeoServer 回读证据目录。
      - 目录按 project_id/conversation_id 分区, 与报告产物目录保持同一会话边界。
    出参:
      - Path, 已创建的目录。
    """
    out_dir = Path(settings.file_storage_root).resolve() / "report" / session_subpath() / "auto_report_inputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _build_report_payload(
    status: str,
    summary: str,
    evidence: Dict[str, Any],
    issues: list[str] = None,
    recommendation: str = RECOMMEND_CONTINUE,
) -> Dict[str, Any]:
    """
    入参:
      - status: passed / failed / warning。
      - summary: report_agent 汇报摘要。
      - evidence: 文件、图层、URL、verification 等客观证据。
      - issues: 问题列表。
      - recommendation: 给主控 Agent 的后续建议。
    方法:
      - 复用统一 worker result contract。
      - 额外补齐 artifacts / metrics / next_action / handoff_notes, 方便主控读取。
    出参:
      - dict, report_agent worker 汇报。
    """
    next_action = "return_report_to_main_agent" if status == STATUS_PASSED else "repair_report_generation"
    return build_worker_result(
        agent_role=REPORT_AGENT,
        status=status,
        summary=summary,
        evidence=evidence,
        artifacts=evidence.get("artifacts") or {},
        metrics=evidence.get("metrics") or {},
        issues=issues or [],
        recommendation=recommendation,
        next_action=next_action,
        handoff_notes=evidence.get("handoff_notes") or "",
    )


def build_segmentation_report(
    image_path: str,
    classes: str,
    polygon_layer: Dict[str, Any],
    base_layer: Dict[str, Any],
    vector_stats: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    入参:
      - image_path: 原始分割影像路径, 用于报告标题和兜底说明。
      - classes: 用户要求的分割类别。
      - polygon_layer: 当前分割面图层的 GeoServer 参数。
      - base_layer: 当前原始影像图层的 GeoServer 参数。
      - vector_stats: 分割矢量统计, 用于报告说明。
    方法:
      - 从 GeoServer WFS 回读当前分割图斑为 GeoJSON。
      - 从 GeoServer WMS 回读当前原始影像为 GeoTIFF。
      - 生成原图叠加图斑 PNG。
      - 调用 generate_monitor_report 生成带叠加图的 PDF。
      - 返回 report_agent 的结构化 worker 汇报。
    出参:
      - dict, status 为 passed / failed / warning。
    """
    polygon_full_name = _layer_full_name(polygon_layer)
    base_full_name = _layer_full_name(base_layer)
    base_evidence = {
        "polygon_layer": polygon_layer,
        "base_layer": base_layer,
        "metrics": {"vector_stats": vector_stats or {}},
    }
    if not polygon_full_name or not base_full_name:
        return _build_report_payload(
            status=STATUS_WARNING,
            summary="report_agent 跳过自动报告: 缺少 GeoServer 分割面图层或原始影像图层。",
            evidence=base_evidence,
            issues=["缺少 polygon_layer 或 base_layer"],
            recommendation=RECOMMEND_REPORT_ERROR,
        )

    try:
        from backend.data import geoserver_client as gs
        from backend.model.tools.report_tools import generate_monitor_report
        from backend.model.tools.vector_tools import rasterize_vector_to_png
    except ImportError as exc:
        return _build_report_payload(
            status=STATUS_FAILED,
            summary=f"report_agent 自动报告依赖不可用: {exc}",
            evidence=base_evidence,
            issues=[str(exc)],
            recommendation=RECOMMEND_REPORT_ERROR,
        )

    evidence_dir = _build_auto_report_dir()
    suffix = rand_suffix(4)
    safe_conv = short_conv() or "anon"
    geojson_path = evidence_dir / f"{safe_conv}_seg_polygon_{suffix}.geojson"
    raster_path = evidence_dir / f"{safe_conv}_original_{suffix}.tif"

    downloaded_geojson = gs.download_geojson(polygon_full_name, output_path=str(geojson_path))
    if not downloaded_geojson:
        return _build_report_payload(
            status=STATUS_FAILED,
            summary=f"report_agent 回读 GeoServer 分割图斑失败: {polygon_full_name}",
            evidence=base_evidence,
            issues=[f"GeoServer 分割图斑回读失败: {polygon_full_name}"],
            recommendation=RECOMMEND_REPORT_ERROR,
        )

    downloaded_raster = gs.download_raster(base_full_name, output_path=str(raster_path))
    if not downloaded_raster:
        evidence = {
            **base_evidence,
            "artifacts": {"geojson_path": str(geojson_path)},
        }
        return _build_report_payload(
            status=STATUS_FAILED,
            summary=f"report_agent 回读 GeoServer 原始影像失败: {base_full_name}",
            evidence=evidence,
            issues=[f"GeoServer 原始影像回读失败: {base_full_name}"],
            recommendation=RECOMMEND_REPORT_ERROR,
        )

    overlay = rasterize_vector_to_png(
        geojson_path=str(geojson_path),
        background_image_path=str(raster_path),
        alpha=0.35,
        export_shp=False,
    )
    if not overlay.get("ok"):
        evidence = {
            **base_evidence,
            "artifacts": {
                "geojson_path": str(geojson_path),
                "source_image_path": str(raster_path),
            },
        }
        return _build_report_payload(
            status=STATUS_FAILED,
            summary=f"report_agent 叠加图生成失败: {overlay.get('reason', '未知错误')}",
            evidence=evidence,
            issues=[overlay.get("reason", "叠加图生成失败")],
            recommendation=RECOMMEND_REPORT_ERROR,
        )

    report_result = generate_monitor_report.invoke({
        "change_geojson_path": str(geojson_path),
        "output_format": "pdf",
        "region_name": "自动分割报告区域",
        "t1_name": Path(image_path).stem,
        "t2_name": polygon_full_name,
        "change_type": "语义分割结果",
        "change_confidence": 1.0,
        "change_reason": (
            "report_agent 在分割工具完成后, 从 GeoServer 回读当前分割图斑和原始影像, "
            "生成原图叠加矢量图斑的报告证据。"
        ),
        "overlay_image_path": overlay.get("png_path") or "",
        "overlay_image_caption": f"分割类别 {classes or '默认类别'} 的图斑矢量叠加到原始影像。",
    })
    if report_result.get("type") == "error":
        evidence = {
            **base_evidence,
            "artifacts": {
                "geojson_path": str(geojson_path),
                "source_image_path": str(raster_path),
                "overlay_image_path": overlay.get("png_path") or "",
            },
            "report_result": report_result,
        }
        return _build_report_payload(
            status=STATUS_FAILED,
            summary=f"report_agent 报告生成失败: {report_result.get('msg') or '未知错误'}",
            evidence=evidence,
            issues=[report_result.get("msg") or "报告生成失败"],
            recommendation=RECOMMEND_REPORT_ERROR,
        )

    report_data = report_result.get("data") or {}
    report_path = report_data.get("artifact_path") or ""
    report_url = report_data.get("artifact_url") or ""
    overlay_path = overlay.get("png_path") or ""
    evidence = {
        **base_evidence,
        "artifacts": {
            "geojson_path": str(geojson_path),
            "source_image_path": str(raster_path),
            "overlay_image_path": overlay_path,
            "report_path": report_path,
            "report_url": report_url,
        },
        "metrics": {
            "vector_stats": vector_stats or {},
            "overlay_feature_count": overlay.get("feature_count"),
        },
        "report_verification": report_result.get("verification") or {},
        "report_result": report_result,
        "handoff_notes": "报告已生成, 主控 Agent 可以向用户汇报 PDF 下载链接和叠加图证据。",
    }
    result = _build_report_payload(
        status=STATUS_PASSED,
        summary=f"report_agent 已生成带原图叠加矢量图斑的 PDF 报告: {Path(report_path).name}",
        evidence=evidence,
        issues=[],
        recommendation=RECOMMEND_CONTINUE,
    )
    result.update({
        "report_path": report_path,
        "report_url": report_url,
        "overlay_image_path": overlay_path,
        "geojson_path": str(geojson_path),
        "source_image_path": str(raster_path),
        "report_result": report_result,
    })
    return result


def run_report_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    入参:
      - payload: report_agent 输入载荷。
    方法:
      - 按 task_type 分派报告任务。
      - 当前支持 segmentation_auto_report。
    出参:
      - dict, report_agent worker 汇报。
    """
    safe_payload = payload if isinstance(payload, dict) else {}
    task_type = safe_payload.get("task_type") or "segmentation_auto_report"
    if task_type == "segmentation_auto_report":
        return build_segmentation_report(
            image_path=str(safe_payload.get("image_path") or ""),
            classes=str(safe_payload.get("classes") or ""),
            polygon_layer=safe_payload.get("polygon_layer") or {},
            base_layer=safe_payload.get("base_layer") or {},
            vector_stats=safe_payload.get("vector_stats") or {},
        )
    return _build_report_payload(
        status=STATUS_FAILED,
        summary=f"report_agent 不支持的任务类型: {task_type}",
        evidence={"payload": safe_payload},
        issues=[f"unsupported report task_type: {task_type}"],
        recommendation=RECOMMEND_REPORT_ERROR,
    )
