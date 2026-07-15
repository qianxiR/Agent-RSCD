# -*- coding: utf-8 -*-
"""
矢量转换子包 (Data 层)
- vectorize: 栅格掩膜 → 矢量 (GeoJSON/Shapefile), 支持多类别 + 真实地理面积

来源: 从 E:\\1代码\\系统\\Tools\\DataProcessing\\mask_postprocess 提取
       (raster_to_vector + coordinate.wkt_to_epsg), 内联依赖避免循环导入。

依赖 (任一缺失则 vectorize_available() 返回 False, 上层优雅降级):
  - rasterio (已用于 samseg/geoserver, 通常在位)
  - geopandas / shapely (栅格→矢量核心)
  - osgeo.osr (WKT→EPSG 解析, GDAL 自带)
  - numpy

设计: 与 samseg/sandbox 同套降级哲学 —— 依赖缺失时返回明确错误,
      不影响其他工具。
"""
from backend.data.vector.vectorize import (
    vectorize_available,
    raster_to_vector,
    mask_to_polygons,
    polygonize_mask,
    polygons_to_boundary_geojson,
)

__all__ = [
    "vectorize_available",
    "raster_to_vector",
    "mask_to_polygons",
    "polygonize_mask",
    "polygons_to_boundary_geojson",
]
