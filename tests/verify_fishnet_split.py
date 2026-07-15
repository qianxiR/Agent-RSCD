"""
双时相渔网分割最小验证脚本。

用途：
- 只验证当前实现是否按 `cdcd` 现有规则生成同样的网格和切分比例
- 默认走 dry-run，不写任何切片文件
- 不依赖 pytest，直接 `python tests/verify_fishnet_split.py` 即可
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.model.SamSeg.fishnet import (
    DEFAULT_SEED,
    DEFAULT_TILE_SIZE,
    build_tile_grid,
    fishnet_split_bitemporal_dataset,
    split_tiles,
)

T1 = ROOT / "tests" / "data" / "test_t1.tif"
T2 = ROOT / "tests" / "data" / "test_t2.tif"
OUT = ROOT / "backend" / "model" / "SamSeg" / "SamSeg" / "resources" / "cdcd_verify_tmp"


def main() -> int:
    tiles, rows, cols = build_tile_grid(width=19968, height=15872, tile_size=DEFAULT_TILE_SIZE)
    assert rows == 31, f"rows 应为 31，实际 {rows}"
    assert cols == 39, f"cols 应为 39，实际 {cols}"
    assert len(tiles) == 1209, f"total tiles 应为 1209，实际 {len(tiles)}"

    split_result = split_tiles(tiles, seed=DEFAULT_SEED)
    assert len(split_result["train"]) == 846, f"train 应为 846，实际 {len(split_result['train'])}"
    assert len(split_result["val"]) == 120, f"val 应为 120，实际 {len(split_result['val'])}"
    assert len(split_result["test"]) == 243, f"test 应为 243，实际 {len(split_result['test'])}"

    result = fishnet_split_bitemporal_dataset(
        t1_path=T1,
        t2_path=T2,
        output_dir=OUT,
        dry_run=True,
    )
    assert result["grid"]["rows"] == 1
    assert result["grid"]["cols"] == 1
    assert result["grid"]["total_tiles"] == 1
    assert result["split_counts"] == {"train": 0, "val": 0, "test": 1}
    assert result["tile_size"] == 512
    assert result["seed"] == 42
    assert result["overlap"] == 0
    assert result["preserve_crs"] is True

    print("fishnet verify ok")
    print(result["split_counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())