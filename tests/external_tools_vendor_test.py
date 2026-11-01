"""Vendored Tools 子集回归测试（不依赖外部 Tools 路径）。

运行: python tests\\external_tools_vendor_test.py
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.agent.tools  # noqa: E402,F401
from backend.agent.tools.registry import get_all_tools, get_tool_by_name  # noqa: E402


def test_vendor_loaded_without_external_tools() -> None:
    assert "external_raster_info" in get_all_tools()
    assert not any(name == "Tools" or name.startswith("Tools.") for name in sys.modules)


def test_vendored_spatial_and_knowledge() -> None:
    geojson = json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[103, 30], [104, 30], [104, 31], [103, 31], [103, 30]]],
            },
        }],
    })
    spatial = get_tool_by_name("external_spatial_operation").invoke({
        "geojson_a": geojson,
        "operation": "buffer",
        "radius": 100,
    })
    assert spatial["type"] == "success"

    knowledge = get_tool_by_name("external_knowledge_query").invoke({
        "question": "植被恢复 监测",
        "top_k": 2,
    })
    assert knowledge["type"] == "success"
    assert knowledge["data"]["matches"]


def test_vendored_raster_when_gdal_available() -> None:
    if importlib.util.find_spec("osgeo") is None:
        return
    raster = next((ROOT / "agent-files").rglob("*.tif"), None)
    if raster is None:
        return
    result = get_tool_by_name("external_raster_info").invoke({"file_path": str(raster)})
    assert result["type"] == "success"


if __name__ == "__main__":
    test_vendor_loaded_without_external_tools()
    test_vendored_spatial_and_knowledge()
    test_vendored_raster_when_gdal_available()
    print("external_tools_vendor: PASS")
