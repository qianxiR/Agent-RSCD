"""Data / Model 层契约回归测试（不依赖数据库、LLM、GPU）。

运行: python tests\\backend_contracts_test.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.base import AssetRef, validate_asset_ref  # noqa: E402
from backend.data.catalog import AssetCatalog  # noqa: E402
from backend.model.base import ModelRequest, ModelResult  # noqa: E402
from backend.model.builtins import ensure_builtin_models  # noqa: E402
from backend.model.change_detection.proc_sam3 import ProCSam3ChangeModel  # noqa: E402
from backend.model.registry import ModelRegistry  # noqa: E402
from backend.model.segmentation.proc_sam3 import ProCSam3SegmentModel  # noqa: E402


class FakeSegmentModel:
    name = "fake-segment"
    capabilities = {"segmentation"}

    def __init__(self) -> None:
        self.calls = 0

    def warmup(self) -> None:
        return None

    def predict(self, request: ModelRequest) -> ModelResult:
        self.calls += 1
        return ModelResult(status="passed", data={"task": request.task})

    def release(self) -> None:
        return None


def test_asset_ref_and_catalog() -> None:
    ref = AssetRef(
        asset_id="img_1",
        kind="imagery",
        uri="/tmp/a.tif",
        crs="EPSG:4326",
        bbox=[103.0, 30.0, 104.0, 31.0],
        acquired_at="2026-01-01T00:00:00",
        metadata={"sensor": "test"},
    )
    assert validate_asset_ref(ref) == []

    catalog = AssetCatalog()
    catalog.register(ref)
    catalog.register(AssetRef(
        asset_id="vec_1", kind="vector", uri="/tmp/a.geojson",
        bbox=[103.5, 30.5, 104.5, 31.5], metadata={"name": "buildings"},
    ))
    assert catalog.get("img_1") is ref
    assert len(catalog.query(kind="imagery")) == 1
    assert len(catalog.query(bbox=[103.2, 30.2, 103.8, 30.8])) == 2
    assert len(catalog.query(text="buildings")) == 1
    assert len(catalog.query(start="2025-01-01T00:00:00")) == 1


def test_invalid_asset() -> None:
    issues = validate_asset_ref(AssetRef(asset_id="", kind="bad", uri="", bbox=[1, 2]))
    assert issues


def test_model_registry() -> None:
    registry = ModelRegistry()
    registry.register(FakeSegmentModel, capabilities=["segmentation"])
    first = registry.resolve("segmentation")
    second = registry.resolve("segmentation")
    assert first is second
    assert isinstance(first, FakeSegmentModel)
    assert first.predict(ModelRequest(task="segmentation")).status == "passed"

    registry.clear()
    registry.register(FakeSegmentModel, capabilities=["segmentation"], singleton=False)
    assert registry.resolve("segmentation") is not registry.resolve("segmentation")

    try:
        registry.resolve("detection")
    except LookupError:
        pass
    else:
        raise AssertionError("unregistered capability should raise")


class FakeRunner:
    """模拟 SamSeg.runner，只写占位产物，不加载模型。"""

    def samseg_available(self) -> bool:
        return True

    def run_segment(self, image_path, output_path, classes=None, vector_output_path=None, save_mask=False):
        Path(output_path).write_bytes(b"mask")
        Path(vector_output_path).write_text("{}", encoding="utf-8")
        return {"output_path": output_path, "stats": {"regions": 1}, "vector_path": vector_output_path}

    def run_change_detection(self, t1_path, t2_path, output_path, classes=None, vector_output_path=None, save_mask=False):
        Path(output_path).write_bytes(b"change")
        Path(vector_output_path).write_text("{}", encoding="utf-8")
        return {"output_path": output_path, "stats": {"regions": 1}, "vector_path": vector_output_path}


def test_proc_sam3_adapters() -> None:
    runner = FakeRunner()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        t1 = tmp_path / "t1.tif"
        t2 = tmp_path / "t2.tif"
        t1.write_bytes(b"t1")
        t2.write_bytes(b"t2")

        seg = ProCSam3SegmentModel(runner=runner).predict(ModelRequest(
            task="segmentation",
            inputs=[AssetRef(asset_id="t1", kind="imagery", uri=str(t1))],
            classes="building",
            params={"output_dir": str(tmp_path / "seg")},
        ))
        assert seg.status == "passed"
        assert any(item.kind == "vector" for item in seg.outputs)

        change = ProCSam3ChangeModel(runner=runner).predict(ModelRequest(
            task="change_detection",
            inputs=[
                AssetRef(asset_id="t1", kind="imagery", uri=str(t1)),
                AssetRef(asset_id="t2", kind="imagery", uri=str(t2)),
            ],
            classes="building",
            params={"output_dir": str(tmp_path / "change")},
        ))
        assert change.status == "passed"
        assert change.vectors and Path(change.vectors[0].uri).is_file()


def test_builtin_registration() -> None:
    registry = ModelRegistry()
    ensure_builtin_models(registry)
    rows = registry.list_models()
    names = {(row["capability"], row["name"]) for row in rows}
    assert ("segmentation", "proc_sam3") in names
    assert ("change_detection", "proc_sam3_change") in names


if __name__ == "__main__":
    test_asset_ref_and_catalog()
    test_invalid_asset()
    test_model_registry()
    test_proc_sam3_adapters()
    test_builtin_registration()
    print("backend_contracts: PASS")
