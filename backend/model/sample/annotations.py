"""样本标注数据契约。

标注几何以 EPSG:4326 为事实源；参考影像像素坐标用于编辑与 COCO 导出。
像素坐标约定: (0, 0) 为影像左上角，x 向右，y 向下。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 与 SamSeg 默认 6 类对齐（background 不参与标注）
DEFAULT_CLASSES: List[Dict[str, Any]] = [
    {"id": 1, "name": "building", "color": [230, 25, 75]},
    {"id": 2, "name": "road", "color": [245, 130, 48]},
    {"id": 3, "name": "water", "color": [0, 130, 200]},
    {"id": 4, "name": "bareland", "color": [170, 110, 40]},
    {"id": 5, "name": "vegetation", "color": [60, 180, 75]},
    {"id": 6, "name": "farmland", "color": [0, 128, 0]},
]

# 自定义类别按顺序取色，保证新增类别有稳定的可视颜色
CUSTOM_CLASS_COLORS: List[List[int]] = [
    [148, 103, 189], [227, 119, 194], [188, 189, 34],
    [23, 190, 207], [174, 199, 232], [255, 187, 120],
]


def polygon_area(points: Sequence[Sequence[float]]) -> float:
    """鞋带公式，返回像素面积。"""
    total = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = float(points[index][0]), float(points[index][1])
        x2, y2 = float(points[(index + 1) % count][0]), float(points[(index + 1) % count][1])
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polygon_bbox(points: Sequence[Sequence[float]]) -> List[float]:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def pixel_to_lonlat(
    x: float, y: float, width: int, height: int, bounds: Sequence[float],
) -> Tuple[float, float]:
    minx, miny, maxx, maxy = (float(v) for v in bounds)
    lon = minx + (float(x) / width) * (maxx - minx)
    lat = maxy - (float(y) / height) * (maxy - miny)
    return lon, lat


def lonlat_to_pixel(
    lon: float, lat: float, width: int, height: int, bounds: Sequence[float],
) -> Tuple[float, float]:
    minx, miny, maxx, maxy = (float(v) for v in bounds)
    x = ((float(lon) - minx) / (maxx - minx)) * width
    y = ((maxy - float(lat)) / (maxy - miny)) * height
    return x, y


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 不是有效数字: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} 不是有限数字: {value!r}")
    return number


def _clean_ring(points: Any, field: str) -> List[List[float]]:
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise ValueError(f"{field} 至少需要 3 个点")
    ring: List[List[float]] = []
    for item in points:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            raise ValueError(f"{field} 含非法点: {item!r}")
        ring.append([_finite(item[0], field), _finite(item[1], field)])
    # 去掉重复闭合首点
    if len(ring) > 3 and ring[0] == ring[-1]:
        ring.pop()
    return ring


def _normalize_classes(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return [dict(item) for item in DEFAULT_CLASSES]
    classes: List[Dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        color = item.get("color") or DEFAULT_CLASSES[min(index, len(DEFAULT_CLASSES) - 1)]["color"]
        classes.append({
            "id": int(item.get("id") or len(classes) + 1),
            "name": name,
            "color": [int(v) for v in color[:3]],
        })
    return classes or [dict(item) for item in DEFAULT_CLASSES]


def normalize_annotations(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验并归一化 annotations.json，返回可直接落盘的字典。"""
    if not isinstance(payload, dict):
        raise ValueError("annotations 必须是对象")

    image = payload.get("image") or {}
    if not isinstance(image, dict):
        raise ValueError("image 必须是对象")
    width = int(_finite(image.get("width"), "image.width"))
    height = int(_finite(image.get("height"), "image.height"))
    if width <= 0 or height <= 0:
        raise ValueError("image.width / image.height 必须大于 0")
    bounds = image.get("bounds")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise ValueError("image.bounds 必须是 [minx, miny, maxx, maxy]")
    bounds = [_finite(v, "image.bounds") for v in bounds]
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError("image.bounds 范围无效")

    crop_raw = image.get("crop_px")
    if crop_raw is None:
        crop = [0, 0, width, height]
    else:
        if not isinstance(crop_raw, (list, tuple)) or len(crop_raw) != 4:
            raise ValueError("image.crop_px 必须是 [x0, y0, x1, y1]")
        crop = [int(round(_finite(v, "image.crop_px"))) for v in crop_raw]
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            raise ValueError("image.crop_px 范围无效")
        if crop[2] - crop[0] != width or crop[3] - crop[1] != height:
            raise ValueError("image.width/height 与 crop_px 不一致")

    source_width = int(round(_finite(
        image.get("source_width") or max(crop[2], width), "image.source_width",
    )))
    source_height = int(round(_finite(
        image.get("source_height") or max(crop[3], height), "image.source_height",
    )))
    if crop[2] > source_width or crop[3] > source_height:
        raise ValueError("image.crop_px 超出 source_width/source_height")

    classes = _normalize_classes(payload.get("classes"))
    class_by_name = {item["name"]: item for item in classes}
    class_by_id = {item["id"]: item for item in classes}

    def ensure_class(name: str) -> Dict[str, Any]:
        item = class_by_name.get(name)
        if item is not None:
            return item
        next_id = max(class_by_id) + 1 if class_by_id else 1
        color = CUSTOM_CLASS_COLORS[len(classes) % len(CUSTOM_CLASS_COLORS)]
        item = {"id": next_id, "name": name, "color": list(color)}
        classes.append(item)
        class_by_name[name] = item
        class_by_id[next_id] = item
        return item

    raw_instances = payload.get("instances") or []
    if not isinstance(raw_instances, (list, tuple)):
        raise ValueError("instances 必须是数组")

    instances: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_instances):
        if not isinstance(raw, dict):
            continue
        class_name = str(raw.get("class_name") or raw.get("label") or "").strip()
        if class_name:
            class_item = ensure_class(class_name)
        else:
            class_item = class_by_id.get(int(raw.get("class_id") or 0))
        if class_item is None:
            raise ValueError(f"第 {index + 1} 个实例缺少有效类别")
        class_name = class_item["name"]

        if raw.get("points") and raw.get("coordinates"):
            points = _clean_ring(raw.get("points"), f"instances[{index}].points")
            coordinates = _clean_ring(
                raw.get("coordinates"), f"instances[{index}].coordinates",
            )
            if len(points) != len(coordinates):
                raise ValueError(f"第 {index + 1} 个实例的 points 与 coordinates 顶点数不一致")
        elif raw.get("points"):
            points = _clean_ring(raw.get("points"), f"instances[{index}].points")
            coordinates = [
                [round(value, 8) for value in pixel_to_lonlat(x, y, width, height, bounds)]
                for x, y in points
            ]
        elif raw.get("coordinates"):
            coordinates = _clean_ring(raw.get("coordinates"), f"instances[{index}].coordinates")
            points = [
                [round(value, 1) for value in lonlat_to_pixel(lon, lat, width, height, bounds)]
                for lon, lat in coordinates
            ]
        else:
            raise ValueError(f"第 {index + 1} 个实例缺少 points / coordinates")

        for x, y in points:
            if x < -0.5 or y < -0.5 or x > width + 0.5 or y > height + 0.5:
                raise ValueError(
                    f"第 {index + 1} 个实例的顶点超出工作范围: ({x}, {y})",
                )

        score = raw.get("score")
        instances.append({
            "id": str(raw.get("id") or f"i{index + 1:04d}"),
            "label": class_name,
            "class_id": int(class_item["id"]),
            "class_name": class_name,
            "score": None if score is None else round(_finite(score, "score"), 4),
            "source": str(raw.get("source") or "manual"),
            "shape_type": str(raw.get("shape_type") or "polygon"),
            "points": points,
            "coordinates": coordinates,
            "bbox_px": [round(v, 1) for v in polygon_bbox(points)],
            "area_px": round(polygon_area(points), 1),
            "edited": bool(raw.get("edited", True)),
        })

    phases = []
    for item in image.get("phases") or []:
        if not isinstance(item, dict):
            continue
        phases.append({
            "date": str(item.get("date") or ""),
            "path": str(item.get("path") or ""),
        })

    return {
        "version": 1,
        "task": {
            "name": str((payload.get("task") or {}).get("name") or ""),
            "project": str((payload.get("task") or {}).get("project") or ""),
            "created_at": str((payload.get("task") or {}).get("created_at") or ""),
        },
        "image": {
            "path": str(image.get("path") or ""),
            "width": width,
            "height": height,
            "bounds": [round(v, 8) for v in bounds],
            "crs": str(image.get("crs") or "EPSG:4326"),
            "crop_px": crop,
            "source_width": source_width,
            "source_height": source_height,
            "phases": phases,
        },
        "classes": classes,
        "instances": instances,
    }
