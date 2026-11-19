"""样本制作标注层。

- annotations.py: 标注契约归一化与像素/经纬度换算
- storage.py:     任务目录、越界守卫、标注读写
- export.py:      COCO / SHP 导出
"""
from backend.model.sample.annotations import (
    DEFAULT_CLASSES,
    lonlat_to_pixel,
    normalize_annotations,
    pixel_to_lonlat,
)
from backend.model.sample.storage import SampleStore

__all__ = [
    "DEFAULT_CLASSES",
    "SampleStore",
    "lonlat_to_pixel",
    "normalize_annotations",
    "pixel_to_lonlat",
]
