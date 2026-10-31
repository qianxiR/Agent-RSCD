"""开源影像检索: 数据源归一化 / 排序 / 面积上限回归测试。

运行: python tests\\imagery_test.py
"""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.imagery.providers import (  # noqa: E402
    PROVIDERS,
    ImageryError,
    stac_interval,
)
from backend.data.imagery.service import ImageryService  # noqa: E402


def test_stac_interval() -> None:
    assert stac_interval("2024-09-01", "2024-09-30") == (
        "2024-09-01T00:00:00Z/2024-09-30T23:59:59Z"
    )
    try:
        stac_interval("2024-09-30", "2024-09-01")
    except ImageryError:
        pass
    else:
        raise AssertionError("倒置时间区间应报错")


def test_planetary_computer_item() -> None:
    raw = {
        "id": "S2A_TEST_20241027",
        "collection": "sentinel-2-l2a",
        "bbox": [103.9, 30.6, 104.1, 30.8],
        "geometry": {"type": "Polygon", "coordinates": [[
            [103.9, 30.6], [104.1, 30.6], [104.1, 30.8], [103.9, 30.8], [103.9, 30.6],
        ]]},
        "properties": {
            "datetime": "2024-10-27T03:38:41Z",
            "eo:cloud_cover": 1.234,
            "platform": "sentinel-2a",
        },
        "assets": {
            "rendered_preview": {"href": "https://example.com/preview.png"},
        },
    }
    item = PROVIDERS["pc"].parse_item(raw, "sentinel-2-l2a")
    assert item is not None
    assert item["provider"] == "pc"
    assert item["satellite"] == "Sentinel-2A"
    assert item["cloud_cover"] == 1.23
    assert item["gsd"] == 10
    assert item["tile_url"] and "{z}" in item["tile_url"]
    assert item["downloadable"] is True
    assert item["bbox"] == [103.9, 30.6, 104.1, 30.8]


def test_provider_capabilities() -> None:
    assert set(PROVIDERS) == {"pc", "es", "cdse", "gee"}
    assert PROVIDERS["pc"].supports_clip is True
    assert PROVIDERS["es"].supports_download is False
    assert PROVIDERS["cdse"].supports_download is False
    # GEE 需要 earthengine-api + 服务账号, 本机默认未配置
    assert PROVIDERS["gee"].configured() is False


def test_sort_and_public_item() -> None:
    service = ImageryService()
    older = {"id": "old", "datetime": "2024-01-01T00:00:00Z", "cloud_cover": 5, "gsd": 10}
    newer = {"id": "new", "datetime": "2024-06-01T00:00:00Z", "cloud_cover": 50, "gsd": 30}
    assert sorted([older, newer], key=lambda i: service._sort_key(i, "date_desc"))[0]["id"] == "new"
    assert sorted([older, newer], key=lambda i: service._sort_key(i, "cloud_asc"))[0]["id"] == "old"
    assert sorted([older, newer], key=lambda i: service._sort_key(i, "gsd_asc"))[0]["id"] == "old"

    item = {
        "provider": "pc", "collection": "sentinel-2-l2a", "id": "x",
        "thumbnail_source": "https://example.com/a.png", "tile_url": None,
    }
    public = service._public_item(item)
    assert public["thumbnail"].startswith("/api/v1/imagery/thumbnail?")
    assert "thumbnail_source" not in public


def test_download_area_guard() -> None:
    service = ImageryService()
    service._item_cache[("pc", "sentinel-2-l2a", "fake")] = (time.time(), {
        "provider": "pc",
        "collection": "sentinel-2-l2a",
        "id": "fake",
        "datetime": "2024-10-27T03:38:41Z",
        "satellite": "Sentinel-2A",
        "bbox": [100.0, 29.0, 106.0, 32.0],
    })
    try:
        asyncio.run(service.download(
            provider_key="pc",
            collection="sentinel-2-l2a",
            item_id="fake",
            bbox=[100.0, 29.0, 106.0, 32.0],
        ))
    except ImageryError as exc:
        assert "过大" in str(exc)
    else:
        raise AssertionError("超过面积上限应拒绝下载")


if __name__ == "__main__":
    test_stac_interval()
    test_planetary_computer_item()
    test_provider_capabilities()
    test_sort_and_public_item()
    test_download_area_guard()
    print("imagery: PASS")
