"""COCO instance-annotation export shared by both GUIs.

输出 COCO 目标格式 JSON（images / annotations / categories），分割多边形
由 ``cv2.findContours`` 从实例掩码提取；不依赖 pycocotools。
"""

import json
import os
import time

import cv2
import numpy as np


def _mask_polygons(mask):
    """二值掩码 → COCO polygon 列表（过小的轮廓丢弃）。"""
    contours, _ = cv2.findContours(
        np.asarray(mask, dtype=np.uint8), cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE)
    segments = []
    for contour in contours:
        if len(contour) < 3:
            continue
        segment = contour.flatten().astype(float).tolist()
        if len(segment) >= 6:
            segments.append([round(value, 1) for value in segment])
    return segments


def _bbox_xywh(bbox_xyxy, mask):
    """COCO bbox [x, y, w, h]；优先用掩码外接框，缺失时现算。"""
    if bbox_xyxy is not None:
        x0, y0, x1, y1 = (float(v) for v in bbox_xyxy)
        return [round(x0, 1), round(y0, 1), round(x1 - x0 + 1, 1),
                round(y1 - y0 + 1, 1)]
    x, y, w, h = cv2.boundingRect(np.asarray(mask, dtype=np.uint8))
    return [float(x), float(y), float(w), float(h)]


def write_coco(path, image_file, width, height, instances):
    """把实例列表写成 COCO 标注 JSON，返回类别列表。

    ``instances`` 每项含 ``mask``（布尔/0-1 数组）与可选 ``name``、
    ``bbox``（xyxy）、``score``/``iou``。
    """
    categories = []
    category_ids = {}
    annotations = []
    for index, item in enumerate(instances, start=1):
        name = str(item.get("name") or "object")
        if name not in category_ids:
            category_ids[name] = len(categories) + 1
            categories.append({"id": category_ids[name], "name": name,
                               "supercategory": name})
        mask = np.asarray(item["mask"]).astype(bool)
        annotation = {
            "id": index,
            "image_id": 1,
            "category_id": category_ids[name],
            "bbox": _bbox_xywh(item.get("bbox"), mask),
            "area": float(mask.sum()),
            "iscrowd": 0,
            "segmentation": _mask_polygons(mask),
        }
        score = item.get("score")
        if score is None:
            score = item.get("iou")
        if score is not None:
            annotation["score"] = round(float(score), 4)
        annotations.append(annotation)

    payload = {
        "info": {
            "description": "SAM3 native instance export",
            "date_created": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "licenses": [],
        "images": [{
            "id": 1,
            "file_name": os.path.basename(str(image_file)),
            "width": int(width),
            "height": int(height),
        }],
        "annotations": annotations,
        "categories": categories,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return [category["name"] for category in categories]
