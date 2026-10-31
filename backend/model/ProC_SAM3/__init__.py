"""ProC-SAM3 适配包。"""
from backend.model.ProC_SAM3.engine import (
    DEFAULT_CLASS_GROUPS,
    available as proc_sam3_available,
    normalize_class_groups,
    release,
    segment,
)

__all__ = [
    "DEFAULT_CLASS_GROUPS",
    "proc_sam3_available",
    "normalize_class_groups",
    "release",
    "segment",
]
