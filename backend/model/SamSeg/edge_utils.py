# -*- coding: utf-8 -*-
"""
边缘提取工具 (移植自 E:/1代码/系统/Tools/DataProcessing/mask_postprocess/edge_utils.py)

提供 3 种边缘算子, 用于从分割掩码/连通域掩码中提取类别边界, 叠加到原图可视化:
  - distance_transform_boundary: 距离变换 (最精确, 推荐)
  - canny_edge: Canny 边缘检测
  - sobel_edge: Sobel 梯度

★ 用途: 把 segment_image/detect_change/analyze_connected_components 产出的掩码
  转换为边缘线, 叠加到原图, 让用户直观看到"每个图斑的精确边界在哪里"。
"""
import numpy as np

try:
    import cv2
    from scipy.ndimage import distance_transform_edt
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False


def distance_transform_boundary(mask, threshold=2, padding_size=10):
    """
    距离变换边界提取 (最精确的边缘算子)。

    入参:
      - mask: 二值掩码 (H, W), 值域 0/1 或 0/255
      - threshold: 距离阈值, 控制边界粗细 (默认 2)
      - padding_size: 边缘填充像素数, 避免图像边缘信息丢失

    方法:
      - padding 后分别计算前景/背景的欧几里得距离变换
      - 距离之和 ≤ threshold 且前景/背景距离均 ≤ threshold 的像素为边界

    出参: 边界掩码 float32 (H, W), 值域 0.0-1.0
    """
    if not _DEPS_OK:
        raise ImportError("edge_utils 需要 cv2 + scipy.ndimage")
    binary = (mask > 0).astype(np.float32)
    padded = np.pad(binary, padding_size, mode='constant', constant_values=0)
    fg_dist = distance_transform_edt(padded > 0)
    bg_dist = distance_transform_edt(padded == 0)
    combined = fg_dist + bg_dist
    boundary = (combined <= threshold).astype(np.float32)
    boundary = boundary * (fg_dist <= threshold) * (bg_dist <= threshold)
    return boundary[padding_size:-padding_size, padding_size:-padding_size]


def canny_edge(mask, low_threshold=50, high_threshold=150):
    """Canny 边缘检测。入参 uint8 掩码, 出参 uint8 边缘图。"""
    if not _DEPS_OK:
        raise ImportError("edge_utils 需要 cv2")
    if mask.dtype != np.uint8:
        mask = (mask > 0).astype(np.uint8) * 255
    return cv2.Canny(mask, low_threshold, high_threshold)


def sobel_edge(image):
    """Sobel 梯度边缘。入参灰度或彩色图, 出参 uint8 边缘图。"""
    if not _DEPS_OK:
        raise ImportError("edge_utils 需要 cv2")
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(image, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(image, cv2.CV_64F, 0, 1, ksize=3)
    return np.sqrt(gx ** 2 + gy ** 2).astype(np.uint8)


def extract_edge(mask, method="distance", threshold=2, padding_size=10,
                 canny_low=50, canny_high=150):
    """
    统一边缘提取入口。

    入参:
      - mask: 二值掩码 (H, W)
      - method: "distance" (距离变换, 推荐) | "canny" | "sobel"
      - threshold/canny_low/canny_high: 各算子的参数

    出参: uint8 边缘掩码 (H, W), 0/255 二值
    """
    if method == "distance":
        edge = distance_transform_boundary(mask, threshold, padding_size)
        return (edge * 255).astype(np.uint8)
    elif method == "canny":
        return canny_edge(mask, canny_low, canny_high)
    elif method == "sobel":
        return sobel_edge(mask)
    raise ValueError(f"未知边缘方法: {method} (可选 distance/canny/sobel)")


def overlay_edge_on_image(original, edge_mask, color=(255, 0, 0), thickness=2):
    """
    把边缘线叠加到原图上 (可视化用)。

    入参:
      - original: 原图 (H, W, 3) uint8, RGB 或 BGR
      - edge_mask: 边缘掩码 (H, W) uint8, 0/255
      - color: 边缘线颜色 (R, G, B), 默认红色
      - thickness: 边缘线粗细 (膨胀核大小, 让线更可见)

    出参: 叠加后的图 (H, W, 3) uint8 (不修改原图)
    """
    if not _DEPS_OK:
        raise ImportError("overlay_edge_on_image 需要 cv2")
    import numpy as np
    result = original.copy()
    h, w = result.shape[:2]
    # 边缘掩码 resize 到原图尺寸 (掩码可能来自降采样的推理)
    if edge_mask.shape != (h, w):
        edge_mask = cv2.resize(edge_mask, (w, h), interpolation=cv2.INTER_NEAREST)
    # 膨胀让边缘线更粗 (更可见)
    if thickness > 1:
        kernel = np.ones((thickness, thickness), np.uint8)
        edge_mask = cv2.dilate(edge_mask, kernel, iterations=1)
    # 在边缘像素位置涂色
    mask_bool = edge_mask > 0
    color_arr = np.array(color, dtype=result.dtype)
    result[mask_bool] = color_arr
    return result


def extract_multiclass_edges(color_mask, palette_index, method="distance", **kwargs):
    """
    从彩色分割掩码中提取指定类别的边缘 (按颜色通道分离)。

    入参:
      - color_mask: 彩色掩码 (H, W, 3) uint8 (segment_image 的产物)
      - palette_index: dict {类别名: (R,G,B)} 颜色到类别的映射
      - method: 边缘算子 (默认 distance)

    出参: dict {类别名: edge_mask(uint8)}, 每个类别的边缘
    """
    import numpy as np
    edges = {}
    for cls_name, color in palette_index.items():
        # 按颜色提取该类别的二值掩码
        color_arr = np.array(color, dtype=np.uint8)
        binary = np.all(color_mask == color_arr, axis=-1).astype(np.uint8) * 255
        if binary.sum() == 0:
            continue  # 该类别不存在
        edge = extract_edge(binary, method=method, **kwargs)
        edges[cls_name] = edge
    return edges
