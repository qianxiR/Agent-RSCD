# -*- coding: utf-8 -*-
"""其他数据仓储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.data.base import LocalFileRepository


class OtherRepository(LocalFileRepository):
    """表格、报告、沙盒产物、下载文件等 other 类型仓储。"""

    def __init__(self, root: Path | str, catalog: Any = None) -> None:
        super().__init__("other", root, catalog)
