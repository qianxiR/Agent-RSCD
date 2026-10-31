# -*- coding: utf-8 -*-
"""文本数据仓储。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.data.base import LocalFileRepository


class TextRepository(LocalFileRepository):
    """知识库文档、报告模板、文本语料的本地仓储。"""

    def __init__(self, root: Path | str, catalog: Any = None) -> None:
        super().__init__("text", root, catalog)
