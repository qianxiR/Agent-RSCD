"""变化检测任务收尾与矢量产物契约回归测试。"""

import tempfile
import time
import sys
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.model.SamSeg.geoio import vectorize_mask_to_geojson
from backend.model.tools import samseg_tools


T1 = ROOT / "tests" / "data" / "r000_c006_t1.tif"
T2 = ROOT / "tests" / "data" / "r000_c006_t2.tif"


def test_geojson_contract() -> None:
    """
    入参: 使用仓库内带 CRS 的测试影像和内存分类掩膜。
    方法: 执行矢量化并用 geopandas 回读，验证标准交换文件真实写盘。
    出参: 无；vector_path、文件、要素或 CRS 缺失时触发断言失败。
    """
    with tempfile.TemporaryDirectory(prefix="agent-rscd-vector-") as temp_dir:
        output_path = Path(temp_dir) / "change.geojson"
        mask = np.zeros((512, 512), dtype=np.uint8)
        mask[100:220, 120:280] = 1
        result = vectorize_mask_to_geojson(
            mask,
            str(T1),
            ["background", "building"],
            str(output_path),
            min_area_m2=1,
        )
        frame = gpd.read_file(output_path)

        assert result is not None
        assert result["vector_path"] == str(output_path.resolve())
        assert output_path.is_file()
        assert len(frame) == 1
        assert frame.crs is not None


def test_detect_change_skips_blocking_vlm() -> None:
    """
    入参: 使用真实带 CRS 影像，模型推理与 GeoServer 发布由确定性替身隔离。
    方法: 执行 detect_change 包装层，并禁止任何同步 VLM 判读调用。
    出参: 无；任务超过一秒、调用 VLM 或缺失 GeoJSON 返回契约时触发断言失败。
    """
    with tempfile.TemporaryDirectory(prefix="agent-rscd-change-") as temp_dir:
        temp_root = Path(temp_dir)

        def fake_change_detection(**kwargs):
            """
            入参: 接收 runner.run_change_detection 的完整关键字参数。
            方法: 写出最小 PNG、TIF 和 GeoJSON，模拟已完成的模型与矢量化阶段。
            出参: 与真实 runner 一致的结果字典。
            """
            output_path = Path(kwargs["output_path"])
            vector_path = Path(kwargs["vector_output_path"])
            mask_tif_path = output_path.with_suffix(".tif")
            output_path.write_bytes(b"png")
            mask_tif_path.write_bytes(b"tif")
            vector_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
            stats = {
                "total_regions": 1,
                "changed_pixels": 100,
                "changed_percent": 1.0,
                "per_class": [
                    {"name": "building", "regions": 1, "area_px": 100, "area_percent": 1.0}
                ],
            }
            return {
                "output_path": str(output_path),
                "mask_tif_path": str(mask_tif_path),
                "vector_path": str(vector_path),
                "vector_stats": {
                    "total_features": 1,
                    "total_area_m2": 100.0,
                    "per_class": [{"name": "building", "count": 1, "area_m2": 100.0}],
                },
                "stats": stats,
                "legend": [],
                "has_crs": True,
            }

        with (
            patch.object(samseg_tools, "_ensure_output_dir", return_value=temp_root),
            patch.object(samseg_tools, "_ensure_vector_dir", return_value=temp_root),
            patch.object(samseg_tools, "_build_polygon_layer_params", return_value={}),
            patch.object(samseg_tools, "_build_edge_layer_params", return_value={}),
            patch.object(samseg_tools, "_build_base_layer_params", return_value={}),
            patch("backend.model.SamSeg.runner.samseg_available", return_value=True),
            patch("backend.model.SamSeg.runner.run_change_detection", side_effect=fake_change_detection),
            patch(
                "backend.model.SamSeg.runner.classify_change_type_by_vlm",
                side_effect=AssertionError("detect_change 不得同步调用 VLM"),
            ),
        ):
            started_at = time.monotonic()
            result = samseg_tools.detect_change.invoke(
                {"t1_path": str(T1), "t2_path": str(T2), "classes": "建筑"}
            )
            elapsed = time.monotonic() - started_at

        data = result["data"]
        assert elapsed < 1.0
        assert result["type"] == "frontend_action"
        assert Path(data["vector_path"]).is_file()
        assert data["change_type"]["source"] == "rule"


if __name__ == "__main__":
    test_geojson_contract()
    test_detect_change_skips_blocking_vlm()
    print("CHANGE_DETECTION_CONTRACT_OK")
