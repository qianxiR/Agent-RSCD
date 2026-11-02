"""按需工具发现与 schema 压缩回归测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.agent.tools  # noqa: E402,F401
from backend.agent.tools.discovery import (  # noqa: E402
    select_initial_tool_names,
    search_tool_cards,
)
from backend.agent.tools.registry import get_tool_by_name, get_tools_for_llm  # noqa: E402
from backend.agent.prompt.tool_catalog import build_tools_catalog  # noqa: E402


def test_initial_selection() -> None:
    names = select_initial_tool_names("提取建筑", [])
    assert "search_tools" in names
    assert "load_tool" in names
    assert "segment_image" in names
    assert len(names) <= 12


def test_search_and_load() -> None:
    cards = search_tool_cards("提取建筑", limit=5)
    assert any(card.name == "segment_image" for card in cards)

    loaded = get_tool_by_name("load_tool").invoke({"name": "segment_image"})
    assert loaded["type"] == "success"
    tool = loaded["data"]["tool"]
    assert tool["name"] == "segment_image"
    assert "parameters" in tool


def test_compact_binding_and_catalog() -> None:
    full = get_tool_by_name("segment_image")
    schema = full.args_schema.model_json_schema()
    assert schema["properties"]["image_path"]["description"]
    compact = get_tools_for_llm(["segment_image"], compact=True)[0]
    assert len(compact.description) < len(full.description)

    catalog = build_tools_catalog(["segment_image", "search_tools"])
    assert "segment_image" in catalog
    assert "search_tools" in catalog
    assert "detect_change" not in catalog


if __name__ == "__main__":
    test_initial_selection()
    test_search_and_load()
    test_compact_binding_and_catalog()
    print("tool_discovery: PASS")
