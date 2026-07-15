# -*- coding: utf-8 -*-
"""
矢量可视化工具集 (Model 层 / 工具箱)
- visualize_vector: GeoJSON 图斑 → PNG 可视化 (按类别配色 + 图例) → 工作区卡片墙,
                    可选同时导出 Shapefile 供 GIS 使用.

设计动机:
  segment_image / detect_change 已产出矢量 GeoJSON, 但工作区卡片墙只稳定渲染图片。
  本工具把 GeoJSON 栅格化为 PNG, 复用 render_image 链路展示；同时作为独立 Agent 工具,
  支持对历史/外部 GeoJSON 单独可视化。

依赖策略:
  - geopandas/shapely: 读取和绘制矢量
  - matplotlib: Agg 后端栅格化输出 PNG
  - PIL/numpy: 可选底图叠加
  缺依赖时返回标准 error, 不影响 samseg 主流程。
"""
import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from backend.config import settings
from backend.model.tools._paths import build_download_url, rand_suffix, session_subpath, short_conv
from backend.model.tools._result import build_error, build_render_image_action
from backend.model.tools._shapefile import ascii_stem
from backend.model.tools._verification import verify_by_path
from backend.model.tools.tool_registry import register_tool

logger = logging.getLogger(__name__)


_VECTOR_PALETTE = [
    "#e6194B", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990", "#dcbeff",
    "#9A6324", "#800000", "#aaffc3", "#808000", "#000075", "#a9a9a9",
]


def _get_vector_output_dir() -> Path:
    """矢量可视化产物目录: agent-files/analysis/{project_id}/{conversation_id}/"""
    out_dir = Path(settings.file_storage_root).resolve() / "analysis" / session_subpath()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _stable_color(name: str) -> str:
    if not name:
        return _VECTOR_PALETTE[0]
    h = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
    return _VECTOR_PALETTE[h % len(_VECTOR_PALETTE)]


def _build_legend(class_names: List[str]) -> List[Dict[str, str]]:
    seen = set()
    legend = []
    for name in class_names:
        if name and name not in seen:
            seen.add(name)
            legend.append({"name": name, "hex": _stable_color(name)})
    return legend


# ★ _ascii_safe 已统一为 backend.model.tools._shapefile.ascii_stem (复用 geoio 公共函数)


def _setup_matplotlib() -> bool:
    """配置 matplotlib Agg + 常见中文字体。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.font_manager as fm

        cjk_candidates = [
            "Microsoft YaHei", "SimHei", "SimSun", "KaiTi",
            "Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei",
            "Source Han Sans SC", "PingFang SC",
        ]
        available = {f.name for f in fm.fontManager.ttflist}
        chosen = next((f for f in cjk_candidates if f in available), None)
        if chosen:
            matplotlib.rcParams["font.sans-serif"] = [chosen] + matplotlib.rcParams.get("font.sans-serif", [])
            matplotlib.rcParams["axes.unicode_minus"] = False
            return True

        for fp in [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]:
            if Path(fp).is_file():
                fm.fontManager.addfont(fp)
                matplotlib.rcParams["font.sans-serif"] = [fm.FontProperties(fname=fp).get_name()] + matplotlib.rcParams.get("font.sans-serif", [])
                matplotlib.rcParams["axes.unicode_minus"] = False
                break
        return True
    except ImportError:
        return False


def _export_shp_from_gdf(gdf: Any, out_dir: Path, stem: str) -> Optional[str]:
    """导出 Shapefile, 返回 .shp 主文件路径；失败返回 None。"""
    shp_path = out_dir / f"{ascii_stem(stem)}.shp"
    try:
        gdf.to_file(str(shp_path), driver="ESRI Shapefile", encoding="utf-8")
        if shp_path.is_file() and shp_path.stat().st_size > 0:
            return str(shp_path)
        logger.warning(f"[VectorViz] Shapefile 写入后未落地或为空: {shp_path}")
    except Exception as exc:
        logger.warning(f"[VectorViz] Shapefile 导出失败 (不影响 PNG 可视化): {exc}")
    return None


def _class_names_for_gdf(gdf: Any) -> Any:
    if "class_name" in gdf.columns:
        return gdf["class_name"].fillna("unknown").astype(str)
    if "class_id" in gdf.columns:
        return gdf["class_id"].apply(lambda x: f"class_{x}")
    return gdf.index.to_series().apply(lambda _: "图斑")


def _prepare_background(ax: Any, gdf: Any, background_image_path: str) -> tuple[Any, Optional[tuple]]:
    """
    可选绘制底图，并尽量用 rasterio bounds 对齐 GeoJSON。
    返回 (可能重投影后的 gdf, extent)。extent 为 None 表示未使用底图。
    """
    if not background_image_path or not Path(background_image_path).is_file():
        return gdf, None

    try:
        import numpy as np
        from PIL import Image

        bg_arr = np.array(Image.open(background_image_path).convert("RGB"))
        h, w = bg_arr.shape[:2]
        extent = (0, w, h, 0)

        try:
            import rasterio
            with rasterio.open(background_image_path) as src:
                extent = (src.bounds.left, src.bounds.right, src.bounds.bottom, src.bounds.top)
                if src.crs and getattr(gdf, "crs", None) and str(gdf.crs) != str(src.crs):
                    gdf = gdf.to_crs(src.crs)
        except Exception as exc:
            logger.info(f"[VectorViz] 底图地理范围读取失败, 使用像素范围叠加: {exc}")

        ax.imshow(bg_arr, extent=extent, origin="upper")
        return gdf, extent
    except Exception as exc:
        logger.warning(f"[VectorViz] 底图加载失败, 改用纯矢量底: {exc}")
        return gdf, None


def rasterize_vector_to_png(
    geojson_path: str,
    background_image_path: str = "",
    alpha: float = 0.5,
    export_shp: bool = True,
) -> Dict[str, Any]:
    """
    GeoJSON → PNG 可视化核心函数。

    成功返回:
      {ok, png_path, png_url, legend, feature_count, shp_path?}
    失败返回:
      {ok: False, reason}
    """
    geojson = Path(geojson_path)
    if not geojson.is_file():
        return {"ok": False, "reason": f"GeoJSON 不存在: {geojson_path}"}

    try:
        import geopandas as gpd
    except ImportError:
        return {"ok": False, "reason": "geopandas 未安装, 无法读取矢量"}

    try:
        gdf = gpd.read_file(str(geojson))
    except Exception as exc:
        return {"ok": False, "reason": f"GeoJSON 读取失败: {exc}"}

    if gdf is None or gdf.empty:
        return {"ok": False, "reason": "矢量为空 (无有效图斑)"}

    if not _setup_matplotlib():
        return {"ok": False, "reason": "matplotlib 未安装, 无法栅格化矢量"}

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    out_dir = _get_vector_output_dir()
    conv = short_conv() or "anon"
    suffix = rand_suffix(4)
    out_stem = f"{conv}_vector_{suffix}"
    png_path = out_dir / f"{out_stem}.png"

    source_gdf = gdf.copy()
    class_names = _class_names_for_gdf(gdf)
    unique_names = list(dict.fromkeys(class_names.tolist()))
    color_map = {name: _stable_color(name) for name in unique_names}
    legend = _build_legend(unique_names)
    alpha = max(0.0, min(float(alpha), 1.0))

    fig, ax = plt.subplots(figsize=(9, 9), dpi=120)
    gdf, extent = _prepare_background(ax, gdf, background_image_path)
    class_names = _class_names_for_gdf(gdf)
    fill_alpha = alpha if extent else 0.75
    edge_color = "white" if extent else "black"

    for name in unique_names:
        subset = gdf[class_names == name]
        if subset.empty:
            continue
        try:
            subset.plot(
                ax=ax,
                color=color_map[name],
                alpha=fill_alpha,
                edgecolor=edge_color,
                linewidth=0.7,
            )
        except Exception as exc:
            logger.warning(f"[VectorViz] 类别 {name} 绘制失败: {exc}")

    if extent:
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[2], extent[3])
    ax.set_aspect("equal")
    ax.axis("off")

    # ★ 不在 PNG 内画图例: 图例由前端 seg-legend 统一渲染 (showImageInPanel 的 legend 参数),
    #   放在图片下方的标题栏区域, 与其他分割/变化卡片保持一致。
    #   之前用 ax.legend() + bbox_inches="tight" 会把图例画进图片右侧,
    #   既污染了图片内容、又与前端 seg-legend 重复。

    try:
        fig.savefig(str(png_path), dpi=120, bbox_inches="tight", pad_inches=0.1, facecolor="white")
    finally:
        plt.close(fig)

    storage_root = Path(settings.file_storage_root).resolve()
    png_url = build_download_url(png_path, storage_root)
    if png_url:
        png_url = f"{png_url}?t={suffix}"

    shp_path = None
    if export_shp:
        shp_path = _export_shp_from_gdf(source_gdf, out_dir, out_stem)

    result: Dict[str, Any] = {
        "ok": True,
        "png_path": str(png_path),
        "png_url": png_url,
        "legend": legend,
        "feature_count": len(gdf),
    }
    if shp_path:
        result["shp_path"] = shp_path
    return result


@register_tool("analysis")
@tool
def visualize_vector(
    geojson_path: str,
    background_image_path: str = "",
    alpha: float = 0.5,
    export_shp: bool = True,
) -> Dict[str, Any]:
    """
    把矢量 GeoJSON 图斑栅格化为 PNG 并渲染到工作区卡片墙，可选同时导出 Shapefile。

    触发场景:
      - 用户要求查看 / 可视化分割或变化检测生成的 GeoJSON 图斑。
      - 用户要求把历史 GeoJSON 结果显示到工作区。
      - 用户要求把 GeoJSON 同步保存为 Shapefile。

    入参:
      - geojson_path: GeoJSON 绝对路径。
      - background_image_path: 可选底图路径；有 CRS 时自动按底图 bounds 对齐，无 CRS 时按像素叠加。
      - alpha: 矢量填充透明度，范围 0~1，默认 0.5。
      - export_shp: 是否同时输出 Shapefile，默认 True。

    成功条件:
      - 返回 type=frontend_action/action=render_image。
      - verification.ok=True。
      - data.artifact_path 指向真实存在的 PNG。
      - data.feature_count > 0。
      - export_shp=True 且 Shapefile 成功时，data.shp_path 指向真实存在的 .shp 文件。
    """
    raster = rasterize_vector_to_png(
        geojson_path=geojson_path,
        background_image_path=background_image_path,
        alpha=alpha,
        export_shp=export_shp,
    )
    if not raster.get("ok"):
        return build_error(msg=f"矢量可视化失败: {raster.get('reason', '未知错误')}")

    png_path = Path(raster["png_path"])
    png_url = raster.get("png_url", "")
    legend = raster.get("legend", [])
    feature_count = raster.get("feature_count", 0)
    shp_path = raster.get("shp_path")
    stem = Path(geojson_path).stem

    legend_text = ", ".join(f"{item['name']}({item['hex']})" for item in legend[:6])
    summary_parts = [
        f"已把矢量 {stem} 可视化到工作区 ({feature_count} 个图斑, {len(legend)} 个类别)。",
        f"类别配色: {legend_text}{'...' if len(legend) > 6 else ''}",
    ]
    if shp_path:
        summary_parts.append(f"已同时导出 Shapefile: {Path(shp_path).name}。")
    elif export_shp:
        summary_parts.append("Shapefile 导出未成功，但 PNG 可视化已完成。")

    data = {
        "artifact_path": str(png_path),
        "artifact_url": png_url,
        "geojson_path": geojson_path,
        "legend": legend,
        "feature_count": feature_count,
    }
    if shp_path:
        data["shp_path"] = shp_path

    return build_render_image_action(
        params={
            "image_url": png_url,
            "caption": f"矢量图斑可视化: {stem}",
            "legend": legend,
            "input_image_path": background_image_path or "",
        },
        summary="\n".join(summary_parts),
        description="正在生成矢量可视化图...",
        data=data,
        verification=verify_by_path(png_path),
    )
