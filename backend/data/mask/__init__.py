# -*- coding: utf-8 -*-
"""
掩膜分析子包 (Data 层)
- region: 二值/多类掩膜的连通域分析 + 几何特征提取 (OBB/多边形/方位)

来源: 从 E:\\1代码\\系统\\Tools\\DataProcessing\\mask_postprocess 提取
       (region_analysis.extract_connected_components 等), 内联化避免外部依赖。

依赖 (任一缺失则 region_analysis_available() 返回 False, 上层优雅降级):
  - numpy (必备)
  - cv2 (连通域标记 + minAreaRect + approxPolyDP)

设计: 与 vector 子包同套降级哲学 —— 依赖缺失时返回明确错误, 不影响其他工具。
"""
from backend.data.mask.region import (
    region_analysis_available,
    extract_connected_components,
    polygon_to_coords,
    compute_spatial_location,
)

__all__ = [
    "region_analysis_available",
    "extract_connected_components",
    "polygon_to_coords",
    "compute_spatial_location",
]
