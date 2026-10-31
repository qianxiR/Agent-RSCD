"""ProC-SAM3 类别解析回归测试（不加载模型）。

运行: python tests\\proc_sam3_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.model.ProC_SAM3.engine import (  # noqa: E402
    _class_lines,
    normalize_class_groups,
)


def test_semicolon_groups() -> None:
    groups = normalize_class_groups(
        "building, house, roof; water, river, lake"
    )
    assert groups == [
        ("building", ["house", "roof"]),
        ("water", ["river", "lake"]),
    ]
    lines = _class_lines("building, house, roof; water, river, lake")
    assert lines[0] == "background, other"
    assert lines[1] == "building, house, roof"
    assert lines[2] == "water, river, lake"


def test_primary_only_gets_synonyms() -> None:
    groups = normalize_class_groups("building; water")
    assert groups[0][0] == "building"
    assert "house" in groups[0][1]
    assert "river" in groups[1][1]


def test_comma_separated_classes() -> None:
    groups = normalize_class_groups("building, water")
    assert [g[0] for g in groups] == ["building", "water"]


def test_chinese_compatibility() -> None:
    lines = _class_lines("建筑,水体,植被")
    assert lines[0] == "background, other"
    assert lines[1].startswith("building")
    assert lines[2].startswith("water")
    assert lines[3].startswith("vegetation")


if __name__ == "__main__":
    test_semicolon_groups()
    test_primary_only_gets_synonyms()
    test_comma_separated_classes()
    test_chinese_compatibility()
    print("proc_sam3: PASS")
