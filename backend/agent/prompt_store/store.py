# -*- coding: utf-8 -*-
"""Prompt 模板存储。

Prompt 不再只存在于 Python 字符串：支持从 prompt_store/templates/ 读取 Markdown/文本模板，
按名称/版本管理；调用方通过 PromptStore 获取，不再直接 import 大段模板。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class PromptTemplate:
    """一个可版本化的 Prompt 模板。"""

    name: str
    text: str
    version: str = "1.0"
    path: str = ""


class PromptStore:
    """文件型 Prompt 模板库。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else Path(__file__).resolve().parent / "templates"

    def list_templates(self) -> List[str]:
        """列出可用模板名（去掉 .<version> 后缀）。"""
        if not self.root.is_dir():
            return []
        names = set()
        for path in self.root.iterdir():
            if path.suffix in {".md", ".txt"}:
                names.add(re.sub(r"\.\d+(?:\.\d+)*$", "", path.stem))
        return sorted(names)

    def get(self, name: str, version: str = "1.0") -> Optional[PromptTemplate]:
        """按名称读取模板；支持 <name>.<version>.md 优先，回退 <name>.md。"""
        for candidate in (self.root / f"{name}.{version}.md", self.root / f"{name}.md", self.root / f"{name}.txt"):
            if candidate.is_file():
                return PromptTemplate(
                    name=name,
                    text=candidate.read_text(encoding="utf-8"),
                    version=version,
                    path=str(candidate),
                )
        return None

    def render(self, template_name: str, version: str = "1.0", **kwargs) -> str:
        """读取并格式化模板；不存在时抛 KeyError。"""
        template = self.get(template_name, version)
        if template is None:
            raise KeyError(f"prompt template not found: {template_name}")
        return template.text.format(**kwargs)

    def put(self, name: str, text: str, version: str = "1.0") -> PromptTemplate:
        """写入模板（用于管理端/测试）。"""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{name}.{version}.md"
        path.write_text(text, encoding="utf-8")
        return PromptTemplate(name=name, text=text, version=version, path=str(path))


_DEFAULT_STORE = PromptStore()


def get_prompt_store() -> PromptStore:
    """返回进程默认 PromptStore。"""
    return _DEFAULT_STORE
