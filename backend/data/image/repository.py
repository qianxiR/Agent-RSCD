# -*- coding: utf-8 -*-
"""图片数据仓储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.data.base import LocalFileRepository


class ImageRepository(LocalFileRepository):
    """普通图片、预览图、缩略图的本地仓储。"""

    def __init__(self, root: Path | str, catalog: Any = None) -> None:
        super().__init__("image", root, catalog)
