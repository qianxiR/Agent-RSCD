"""Class-map post-processing ported from QwenSam (generalized to N classes).

QwenSam (``/g/Qwen_VLLM/QwenSam/postprocessing.py``) applies three steps to a
5-class lithology map:

1. ``smooth_foreground_classes``: per-class morphological close then open with
   an elliptical kernel. Pixels that survive keep their class; newly grown
   pixels take the nearest original class region; pixels removed by the
   smoothing are reassigned to the majority class of their 8-neighbourhood.
2. ``remove_small_components``: 8-connected foreground components smaller than
   ``min_area`` are reassigned to the majority neighbouring class.
3. ``fill_small_background_holes``: background components that do not touch the
   image border, are at most ``max_hole_area`` pixels and are surrounded by a
   single foreground class are filled with that class.

The original module hard-codes 5 classes and background = 0. This port accepts
any number of classes and an arbitrary ``background_idx`` (ProC-SAM3 datasets
such as Vaihingen use ``clutter`` at index 5 as background).

Run ``python mask_postprocess.py`` for a synthetic self-check.
"""

from __future__ import annotations

import cv2
import numpy as np

POSTPROCESS_DEFAULTS = {
    "smooth_kernel": 3,     # 0 disables morphological smoothing
    "min_area": 2056,       # pixels; 0 disables small-component removal
    "max_hole_area": 2056,  # pixels; 0 disables hole filling
}


def _majority_neighbor_class(class_map, region, num_classes, excluded_class=None):
    """Majority class in the one-pixel ring around ``region``."""
    kernel = np.ones((3, 3), dtype=np.uint8)
    boundary = cv2.dilate(region.astype(np.uint8), kernel, iterations=1).astype(bool)
    boundary &= ~region
    neighbors = class_map[boundary]
    if excluded_class is not None:
        neighbors = neighbors[neighbors != excluded_class]
    if neighbors.size == 0:
        return None
    counts = np.bincount(neighbors.astype(np.int64), minlength=num_classes)
    return int(counts.argmax())


def smooth_foreground_classes(class_map, kernel_size, background_idx=0,
                              num_classes=None):
    """Per-class close→open smoothing with distance/neighbour reassignment."""
    source = np.asarray(class_map, dtype=np.uint8)
    num_classes = int(num_classes or (source.max() + 1))
    if kernel_size == 0:
        return source.copy()
    if kernel_size < 0 or kernel_size % 2 == 0:
        raise ValueError("kernel_size must be 0 or a positive odd integer")

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    processed = np.zeros((num_classes, *source.shape), dtype=bool)
    distances = np.full(
        (num_classes, *source.shape), np.inf, dtype=np.float32)

    for class_id in range(num_classes):
        if class_id == background_idx:
            continue
        binary = (source == class_id).astype(np.uint8)
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        processed[class_id] = cv2.morphologyEx(
            closed, cv2.MORPH_OPEN, kernel).astype(bool)
        distances[class_id] = cv2.distanceTransform(
            1 - binary, cv2.DIST_L2, 3)

    result = source.copy()
    foreground = source != background_idx
    survives = np.zeros_like(foreground)
    for class_id in range(num_classes):
        if class_id == background_idx:
            continue
        survives |= (source == class_id) & processed[class_id]

    candidates = processed.copy()
    candidates[background_idx] = False
    candidate_count = candidates.sum(axis=0)
    assignable = (~foreground | (foreground & ~survives)) & (candidate_count > 0)
    candidate_distances = np.where(candidates, distances, np.inf)
    nearest = candidate_distances.argmin(axis=0).astype(np.uint8)
    result[assignable] = nearest[assignable]

    removed_without_candidate = foreground & ~survives & (candidate_count == 0)
    for class_id in range(num_classes):
        if class_id == background_idx:
            continue
        mask = (removed_without_candidate & (source == class_id)).astype(np.uint8)
        count, components = cv2.connectedComponents(mask, connectivity=8)
        for component_id in range(1, count):
            region = components == component_id
            replacement = _majority_neighbor_class(
                result, region, num_classes, excluded_class=class_id)
            if replacement is not None:
                result[region] = replacement
    return result


def remove_small_components(class_map, min_area, background_idx=0,
                            num_classes=None):
    """Reassign foreground components smaller than ``min_area`` pixels."""
    source = np.asarray(class_map, dtype=np.uint8)
    num_classes = int(num_classes or (source.max() + 1))
    if min_area == 0:
        return source.copy()
    if min_area < 0:
        raise ValueError("min_area must be non-negative")

    result = source.copy()
    replacements = []
    for class_id in range(num_classes):
        if class_id == background_idx:
            continue
        count, components, stats, _ = cv2.connectedComponentsWithStats(
            (source == class_id).astype(np.uint8), connectivity=8)
        for component_id in range(1, count):
            if int(stats[component_id, cv2.CC_STAT_AREA]) >= min_area:
                continue
            region = components == component_id
            replacement = _majority_neighbor_class(
                source, region, num_classes, excluded_class=class_id)
            if replacement is not None:
                replacements.append((region, replacement))
    for region, replacement in replacements:
        result[region] = replacement
    return result


def fill_small_background_holes(class_map, max_hole_area, background_idx=0,
                                num_classes=None):
    """Fill small, non-border background holes surrounded by one class."""
    source = np.asarray(class_map, dtype=np.uint8)
    num_classes = int(num_classes or (source.max() + 1))
    if max_hole_area == 0:
        return source.copy()
    if max_hole_area < 0:
        raise ValueError("max_hole_area must be non-negative")

    result = source.copy()
    count, components, stats, _ = cv2.connectedComponentsWithStats(
        (source == background_idx).astype(np.uint8), connectivity=8)
    height, width = source.shape
    kernel = np.ones((3, 3), dtype=np.uint8)

    for component_id in range(1, count):
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        left = int(stats[component_id, cv2.CC_STAT_LEFT])
        top = int(stats[component_id, cv2.CC_STAT_TOP])
        component_width = int(stats[component_id, cv2.CC_STAT_WIDTH])
        component_height = int(stats[component_id, cv2.CC_STAT_HEIGHT])
        touches_edge = (
            left == 0 or top == 0
            or left + component_width == width
            or top + component_height == height)
        if touches_edge or area > max_hole_area:
            continue
        region = components == component_id
        boundary = cv2.dilate(
            region.astype(np.uint8), kernel, iterations=1).astype(bool)
        boundary &= ~region
        surrounding = np.unique(source[boundary])
        surrounding = surrounding[surrounding != background_idx]
        if surrounding.size == 1:
            result[region] = int(surrounding[0])
    return result


def postprocess_class_map(class_map, smooth_kernel=3, min_area=2056,
                          max_hole_area=2056, background_idx=0,
                          num_classes=None):
    """Apply the full QwenSam pipeline: smooth → remove small → fill holes."""
    source = np.asarray(class_map, dtype=np.uint8)
    num_classes = int(num_classes or (source.max() + 1))
    if smooth_kernel < 0 or (smooth_kernel != 0 and smooth_kernel % 2 == 0):
        raise ValueError("smooth_kernel must be 0 or a positive odd integer")
    if min_area < 0 or max_hole_area < 0:
        raise ValueError("min_area and max_hole_area must be non-negative")

    smoothed = smooth_foreground_classes(
        source, smooth_kernel, background_idx, num_classes)
    filtered = remove_small_components(
        smoothed, min_area, background_idx, num_classes)
    return fill_small_background_holes(
        filtered, max_hole_area, background_idx, num_classes)


def _self_check():
    """Small synthetic test: holes filled, specks removed, shape kept."""
    class_map = np.zeros((120, 120), dtype=np.uint8)

    # Big building with a 5x5 background hole -> should be filled.
    class_map[10:60, 10:60] = 1
    class_map[30:35, 30:35] = 0

    # Tiny 3x3 speck far away -> should disappear (min_area=64).
    class_map[100:103, 100:103] = 2

    # A small hole touching the border -> must stay background.
    class_map[0:4, 60:64] = 0

    processed = postprocess_class_map(
        class_map, smooth_kernel=3, min_area=64, max_hole_area=64,
        background_idx=0, num_classes=3)

    assert processed[32, 32] == 1, "interior hole was not filled"
    assert not np.any(processed[100:103, 100:103] == 2), "speck was not removed"
    assert processed.shape == class_map.shape
    assert processed.dtype == np.uint8
    print("mask post-processing self-check passed")


if __name__ == "__main__":
    _self_check()
