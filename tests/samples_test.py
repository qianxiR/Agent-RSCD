"""样本标注层测试（不依赖数据库、LLM、GPU）。

运行: python tests\\samples_test.py
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.model.sample.annotations import (  # noqa: E402
    lonlat_to_pixel,
    normalize_annotations,
    pixel_to_lonlat,
)
from backend.model.sample.export import export_coco, export_dataset, export_shp  # noqa: E402
from backend.model.sample.storage import SampleStore  # noqa: E402


def _payload() -> dict:
    return {
        "image": {
            "path": "a.tif",
            "width": 100,
            "height": 50,
            "bounds": [100.0, 30.0, 101.0, 30.5],
            "phases": [{"date": "2026-10-29", "path": "a.tif"}],
        },
        "instances": [
            {
                "class_name": "building",
                "points": [[10, 10], [40, 10], [40, 30], [10, 30]],
            },
            {
                "class_name": "water",
                "points": [[50, 10], [80, 10], [80, 40], [50, 40]],
            },
        ],
    }


def test_normalize_annotations() -> None:
    result = normalize_annotations(_payload())
    assert result["version"] == 1
    assert result["image"]["crs"] == "EPSG:4326"
    assert len(result["instances"]) == 2
    first = result["instances"][0]
    assert first["class_id"] == 1
    assert first["area_px"] == 600.0
    assert first["bbox_px"] == [10.0, 10.0, 40.0, 30.0]
    assert first["coordinates"][0] == [100.1, 30.4]


def test_pixel_lonlat_roundtrip() -> None:
    bounds = [100.0, 30.0, 101.0, 30.5]
    lon, lat = pixel_to_lonlat(25, 10, 100, 50, bounds)
    x, y = lonlat_to_pixel(lon, lat, 100, 50, bounds)
    assert abs(x - 25) < 1e-6
    assert abs(y - 10) < 1e-6


def test_reject_invalid_instance() -> None:
    payload = _payload()
    payload["instances"][0]["points"] = [[1, 2], [3, 4]]
    try:
        normalize_annotations(payload)
    except ValueError as exc:
        assert "至少需要 3 个点" in str(exc)
    else:
        raise AssertionError("少于 3 个点应当被拒绝")


def test_store_save_load_and_copy() -> None:
    root = Path(tempfile.mkdtemp())
    source_dir = root / "input"
    source_dir.mkdir()
    source = source_dir / "capture.tif"
    source.write_bytes(b"fake-tif")

    store = SampleStore(root)
    payload = _payload()
    payload["image"]["phases"][0]["path"] = str(source)
    saved = store.save("p1", "task_demo", payload)
    assert saved["instance_count"] == 2

    task_path = store.task_dir("p1", "task_demo")
    assert (task_path / "source_a.tif").is_file()
    data = store.load("p1", "task_demo")
    assert data["image"]["path"] == "source_a.tif"
    assert data["image"]["phases"][0]["path"] == "source_a.tif"
    assert store.list_tasks("p1")[0]["name"] == "task_demo"

    try:
        store.task_dir("p1", "../escape")
    except ValueError:
        pass
    else:
        raise AssertionError("task_dir 越界应当被拒绝")


def test_export_coco_and_shp() -> None:
    root = Path(tempfile.mkdtemp())
    store = SampleStore(root)
    store.save("p1", "task_export", _payload())
    task_path = store.task_dir("p1", "task_export")
    data = store.load("p1", "task_export")

    coco_path = export_coco(task_path, data)
    coco = json.loads(coco_path.read_text(encoding="utf-8"))
    assert len(coco["images"]) == 1
    assert len(coco["annotations"]) == 2
    assert {item["name"] for item in coco["categories"]} == {"building", "water"}

    archive = export_shp(task_path, data)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert {"annotations.shp", "annotations.shx", "annotations.dbf", "annotations.prj"} <= names


def test_export_dataset_with_crop() -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    root = Path(tempfile.mkdtemp())
    source_dir = root / "input"
    source_dir.mkdir()
    source = source_dir / "source.tif"
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=100,
        height=50,
        count=3,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_bounds(0, 0, 1000, 500, 100, 50),
    ) as dst:
        dst.write(np.full((50, 100), 10, dtype="uint8"), 1)
        dst.write(np.full((50, 100), 20, dtype="uint8"), 2)
        dst.write(np.full((50, 100), 30, dtype="uint8"), 3)

    payload = {
        "image": {
            "path": str(source),
            "width": 50,
            "height": 30,
            "bounds": [100.0, 30.0, 100.5, 30.3],
            "crop_px": [10, 5, 60, 35],
            "source_width": 100,
            "source_height": 50,
            "phases": [{"date": "2026-01-01", "path": str(source)}],
        },
        "instances": [
            {"class_name": "building", "points": [[0, 0], [20, 0], [20, 10]]},
        ],
    }
    store = SampleStore(root)
    store.save("p1", "task_dataset", payload)
    task_path = store.task_dir("p1", "task_dataset")
    data = store.load("p1", "task_dataset")
    assert data["image"]["crop_px"] == [10, 5, 60, 35]
    assert data["image"]["source_width"] == 100

    archive = export_dataset(task_path, data)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert "image/phase_a.tif" in names
        assert "image/phase_a.png" in names
        assert "annotations.json" in names
        assert "coco.json" in names
        assert "vector/annotations.shp" in names
        assert "meta.json" in names

    cropped = task_path / "dataset" / "image" / "phase_a.tif"
    with rasterio.open(cropped) as src:
        assert src.width == 50
        assert src.height == 30
        assert src.crs is not None


def main() -> None:
    test_normalize_annotations()
    test_pixel_lonlat_roundtrip()
    test_reject_invalid_instance()
    test_store_save_load_and_copy()
    test_export_coco_and_shp()
    test_export_dataset_with_crop()
    print("samples_test: PASS")


if __name__ == "__main__":
    main()
