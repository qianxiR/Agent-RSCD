# -*- coding: utf-8 -*-
"""外部工具箱适配 (E:\\1代码\\系统\\Tools)。

精选高频、低耦合的数据处理/后处理能力，包装成系统标准工具：
- 栅格元信息
- 光谱指数统计
- 掩膜栅格转矢量
- GeoJSON 空间分析（buffer / intersection / erase）

外部 Tools 依赖 GDAL/torch 等重依赖，全部在函数内部延迟导入；
未安装或路径不存在时返回可读错误，不影响系统启动。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from langchain_core.tools import tool

from backend.agent.tools._result import build_error, build_success
from backend.agent.tools.registry import register_tool
from backend.config import settings


def _ensure_tools_path() -> Path:
    """把外部 Tools 的父目录加入 sys.path，并返回父目录。"""
    root = Path(settings.external_tools_root).expanduser().resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _load_geojson(value: Any) -> Dict[str, Any]:
    """把路径 / JSON 字符串 / dict 统一成 GeoJSON dict。"""
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("GeoJSON 输入为空")
    path = Path(text)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(text)


@register_tool("external")
@tool
def external_raster_info(file_path: str) -> Dict[str, Any]:
    """
    读取外部工具箱的栅格元信息（投影、仿射变换、尺寸）。

    入参:
        - file_path (str): GeoTIFF 绝对路径。
    出参: success/error；data 含 projection/geotransform/width/height。
    """
    if not settings.external_tools_enabled:
        return build_error("外部工具箱未启用（EXTERNAL_TOOLS_ENABLED=false）")
    if not Path(file_path).is_file():
        return build_error(f"栅格不存在: {file_path}")
    try:
        _ensure_tools_path()
        from Tools.DataProcessing.raster_io import get_geo_info
        import rasterio

        projection, geotransform = get_geo_info(file_path)
        with rasterio.open(file_path) as src:
            width, height, count = src.width, src.height, src.count
        return build_success(
            f"栅格元信息读取完成: {Path(file_path).name} ({width}x{height}, {count} bands)",
            data={
                "file_path": file_path,
                "projection": str(projection),
                "geotransform": list(geotransform) if geotransform else [],
                "width": width,
                "height": height,
                "band_count": count,
            },
            verification={"status": "passed", "evidence": "raster opened"},
        )
    except Exception as exc:
        return build_error(f"读取栅格元信息失败: {exc}")


@register_tool("external")
@tool
def external_spectral_index(
    file_path: str,
    index: str = "ndvi",
    red_band: int = 3,
    nir_band: int = 4,
    green_band: int = 2,
) -> Dict[str, Any]:
    """
    计算光谱指数并返回统计值（NDVI / NDWI）。

    入参:
        - file_path (str): GeoTIFF 路径。
        - index (str): ndvi / ndwi。
        - red_band/nir_band/green_band (int): 1-based 波段号。
    出参: success/error；data 含 min/max/mean/std。
    """
    if not settings.external_tools_enabled:
        return build_error("外部工具箱未启用（EXTERNAL_TOOLS_ENABLED=false）")
    if not Path(file_path).is_file():
        return build_error(f"栅格不存在: {file_path}")
    key = (index or "ndvi").lower()
    if key not in {"ndvi", "ndwi"}:
        return build_error("index 仅支持 ndvi / ndwi")
    bands = [red_band, nir_band] if key == "ndvi" else [green_band, nir_band]
    try:
        _ensure_tools_path()
        import numpy as np
        from Tools.DataProcessing.raster_io import read_geotiff
        from Tools.DataProcessing.spectral import calculate_ndvi, calculate_ndwi

        image, _, _, _ = read_geotiff(file_path, selected_bands=bands)
        array = np.asarray(image)
        if array.ndim == 3:
            first, second = array[0].astype("float32"), array[1].astype("float32")
        else:
            return build_error("影像波段不足，无法计算光谱指数")
        value = calculate_ndvi(first, second) if key == "ndvi" else calculate_ndwi(first, second)
        arr = np.asarray(value, dtype="float32")
        return build_success(
            f"{key.upper()} 计算完成: min={float(np.nanmin(arr)):.4f}, max={float(np.nanmax(arr)):.4f}",
            data={
                "file_path": file_path,
                "index": key,
                "bands": bands,
                "min": float(np.nanmin(arr)),
                "max": float(np.nanmax(arr)),
                "mean": float(np.nanmean(arr)),
                "std": float(np.nanstd(arr)),
            },
            verification={"status": "passed", "evidence": "spectral array computed"},
        )
    except Exception as exc:
        return build_error(f"光谱指数计算失败: {exc}")


@register_tool("external")
@tool
def external_raster_to_vector(
    mask_path: str,
    output_shp_path: str,
    min_area: float = 0.0,
    source_image_path: str = "",
) -> Dict[str, Any]:
    """
    使用外部后处理工具把掩膜栅格转为 Shapefile。

    入参:
        - mask_path (str): 掩膜栅格路径。
        - output_shp_path (str): 输出 .shp 路径。
        - min_area (float): 最小面积过滤。
        - source_image_path (str): 可选源影像，用于写 CRS。
    出参: success/error；data 含 shp_path。
    """
    if not settings.external_tools_enabled:
        return build_error("外部工具箱未启用（EXTERNAL_TOOLS_ENABLED=false）")
    if not Path(mask_path).is_file():
        return build_error(f"掩膜不存在: {mask_path}")
    try:
        _ensure_tools_path()
        from Tools.PostProcessing.mask_postprocess import raster_to_vector

        result = raster_to_vector(
            mask_path,
            output_shp_path,
            min_area=min_area,
            source_image_path=source_image_path or None,
        )
        shp_path = str(result) if result else output_shp_path
        if not Path(shp_path).exists():
            return build_error(f"矢量化未生成产物: {shp_path}")
        return build_success(
            f"掩膜矢量化完成: {shp_path}",
            data={"shp_path": shp_path, "source_mask": mask_path},
            verification={"status": "passed", "evidence": shp_path},
        )
    except Exception as exc:
        return build_error(f"掩膜矢量化失败: {exc}")


@register_tool("external")
@tool
def external_spatial_operation(
    geojson_a: str,
    operation: str = "intersection",
    geojson_b: str = "",
    radius: float = 100.0,
    units: str = "meters",
) -> Dict[str, Any]:
    """
    使用外部空间分析工具做 buffer / intersection / erase。

    入参:
        - geojson_a/geojson_b (str): GeoJSON 文件路径或 JSON 字符串。
        - operation (str): buffer / intersection / erase。
        - radius/units: buffer 参数。
    出参: success/error；data 含 result GeoJSON 与要素数。
    """
    if not settings.external_tools_enabled:
        return build_error("外部工具箱未启用（EXTERNAL_TOOLS_ENABLED=false）")
    op = (operation or "intersection").lower()
    try:
        _ensure_tools_path()
        from Tools.DataProcessing import spatial

        first = _load_geojson(geojson_a)
        if op == "buffer":
            result = spatial.buffer_analysis(first, radius=radius, units=units)
        elif op in {"intersection", "erase"}:
            second = _load_geojson(geojson_b)
            func = spatial.intersection_analysis if op == "intersection" else spatial.erase_analysis
            result = func(first, second)
        else:
            return build_error("operation 仅支持 buffer / intersection / erase")
        features = result.get("features") if isinstance(result, dict) else []
        return build_success(
            f"空间分析完成: {op}，结果 {len(features)} 个要素",
            data={"operation": op, "result": result, "feature_count": len(features)},
            verification={"status": "passed", "evidence": "geojson returned"},
        )
    except Exception as exc:
        return build_error(f"空间分析失败: {exc}")
