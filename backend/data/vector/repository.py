# -*- coding: utf-8 -*-
"""矢量数据仓储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.data.base import LocalFileRepository


class VectorRepository(LocalFileRepository):
    """GeoJSON / Shapefile / 矢量产物的本地仓储。"""

    def __init__(self, root: Path | str, catalog: Any = None) -> None:
        super().__init__("vector", root, catalog)
