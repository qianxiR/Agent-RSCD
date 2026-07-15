from __future__ import annotations

"""
双时相影像渔网分割内核。

规则与 `resources/cdcd` 当前数据集保持一致：
- 512×512 默认瓦片
- train:val:test = 7:1:2
- 固定随机种子 42
- 不重叠
- 默认保留源坐标系与子窗口 transform
- 输出目录结构为 train/val/test × t1/t2
"""

import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

DEFAULT_TILE_SIZE = 512
DEFAULT_SEED = 42
DEFAULT_RATIO = (7, 1, 2)
DEFAULT_SPLITS = ("train", "val", "test")
DEFAULT_SUBDIRS = ("t1", "t2")
DEFAULT_OVERLAP = 0

_SAMSEG_INNER_DIR = Path(__file__).resolve().parent / "SamSeg"
DEFAULT_RESOURCES_DIR = _SAMSEG_INNER_DIR / "resources"
DEFAULT_T1_SRC = DEFAULT_RESOURCES_DIR / "XD2023.tif"
DEFAULT_T2_SRC = DEFAULT_RESOURCES_DIR / "XD2025.tif"
DEFAULT_CDCD_OUTPUT_DIR = DEFAULT_RESOURCES_DIR / "cdcd_generated"


def _default_source_hint(missing_path: Path) -> str:
    return (
        f"默认源影像不存在: {missing_path}。"
        "请显式传入 t1_path / t2_path，或先将默认源放到 "
        f"{DEFAULT_T1_SRC.name} 与 {DEFAULT_T2_SRC.name}。"
    )


def _configure_rasterio_env() -> None:
    import rasterio

    rio_base = Path(rasterio.__file__).resolve().parent
    proj_dir = rio_base / "proj_data"
    gdal_dir = rio_base / "gdal_data"
    if proj_dir.is_dir():
        os.environ["PROJ_LIB"] = str(proj_dir)
    if gdal_dir.is_dir():
        os.environ["GDAL_DATA"] = str(gdal_dir)


def build_tile_grid(width: int, height: int, tile_size: int, overlap: int = DEFAULT_OVERLAP) -> Tuple[List[Tuple[int, int]], int, int]:
    if tile_size <= 0:
        raise ValueError("tile_size 必须大于 0")
    if overlap != 0:
        raise ValueError("当前渔网分割工具固定为无重叠，overlap 必须为 0")

    cols = (width + tile_size - 1) // tile_size
    rows = (height + tile_size - 1) // tile_size
    tiles = [(row, col) for row in range(rows) for col in range(cols)]
    return tiles, rows, cols


def split_tiles(
    tiles: Sequence[Tuple[int, int]],
    ratio: Sequence[int] = DEFAULT_RATIO,
    seed: int = DEFAULT_SEED,
) -> Dict[str, List[Tuple[int, int]]]:
    if len(ratio) != 3:
        raise ValueError("ratio 必须是 3 个整数，对应 train/val/test")
    if any(part < 0 for part in ratio):
        raise ValueError("ratio 不能包含负数")
    if sum(ratio) <= 0:
        raise ValueError("ratio 总和必须大于 0")

    shuffled = list(tiles)
    random.Random(seed).shuffle(shuffled)

    total = len(shuffled)
    base = sum(ratio)
    train_count = total * ratio[0] // base
    val_count = total * ratio[1] // base

    return {
        "train": shuffled[:train_count],
        "val": shuffled[train_count:train_count + val_count],
        "test": shuffled[train_count + val_count:],
    }


def tile_filename(row: int, col: int) -> str:
    return f"r{row:03d}_c{col:03d}.tif"


def ensure_layout(output_dir: Path) -> None:
    for split in DEFAULT_SPLITS:
        for subdir in DEFAULT_SUBDIRS:
            (output_dir / split / subdir).mkdir(parents=True, exist_ok=True)


def collect_layout_counts(output_dir: Path) -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = {}
    for split in DEFAULT_SPLITS:
        result[split] = {}
        for subdir in DEFAULT_SUBDIRS:
            folder = output_dir / split / subdir
            result[split][subdir] = len(list(folder.glob("*.tif"))) if folder.exists() else 0
    return result


def _clear_existing_tifs(output_dir: Path) -> None:
    for split in DEFAULT_SPLITS:
        for subdir in DEFAULT_SUBDIRS:
            folder = output_dir / split / subdir
            if not folder.exists():
                continue
            for tif_path in folder.glob("*.tif"):
                tif_path.unlink()


def _has_existing_tiles(output_dir: Path) -> bool:
    for split in DEFAULT_SPLITS:
        for subdir in DEFAULT_SUBDIRS:
            folder = output_dir / split / subdir
            if folder.exists() and any(folder.glob("*.tif")):
                return True
    return False


def _validate_sources(t1_path: Path, t2_path: Path, preserve_crs: bool) -> Dict[str, Any]:
    _configure_rasterio_env()
    import rasterio

    if not t1_path.is_file():
        if t1_path == DEFAULT_T1_SRC.resolve():
            raise FileNotFoundError(_default_source_hint(t1_path))
        raise FileNotFoundError(f"缺少 t1 源影像: {t1_path}")
    if not t2_path.is_file():
        if t2_path == DEFAULT_T2_SRC.resolve():
            raise FileNotFoundError(_default_source_hint(t2_path))
        raise FileNotFoundError(f"缺少 t2 源影像: {t2_path}")

    with rasterio.open(t1_path) as src1, rasterio.open(t2_path) as src2:
        if (src1.width, src1.height) != (src2.width, src2.height):
            raise ValueError(
                "两期影像尺寸不一致: "
                f"t1={src1.width}×{src1.height}, t2={src2.width}×{src2.height}"
            )

        if preserve_crs and src1.crs != src2.crs:
            raise ValueError(f"两期影像 CRS 不一致: t1={src1.crs}, t2={src2.crs}")

        if not src1.transform.almost_equals(src2.transform):
            raise ValueError("两期影像未对齐到同一像素网格，无法按相同渔网稳定配对切片")

        return {
            "width": src1.width,
            "height": src1.height,
            "crs": str(src1.crs) if src1.crs else None,
            "bounds": {
                "left": src1.bounds.left,
                "bottom": src1.bounds.bottom,
                "right": src1.bounds.right,
                "top": src1.bounds.top,
            },
        }


def _write_tile(src: Any, window: Any, dst_path: Path, preserve_crs: bool) -> None:
    data = src.read(window=window)
    profile = src.profile.copy()
    profile.update(
        width=window.width,
        height=window.height,
        transform=src.window_transform(window),
    )

    if preserve_crs:
        profile["crs"] = src.crs
    else:
        profile.pop("crs", None)

    if int(window.width) >= 16 and int(window.height) >= 16:
        profile.update(
            tiled=True,
            blockxsize=min(512, int(window.width)),
            blockysize=min(512, int(window.height)),
        )
    else:
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)

    import rasterio

    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(data)


def fishnet_split_bitemporal_dataset(
    t1_path: str | Path = DEFAULT_T1_SRC,
    t2_path: str | Path = DEFAULT_T2_SRC,
    output_dir: str | Path = DEFAULT_CDCD_OUTPUT_DIR,
    tile_size: int = DEFAULT_TILE_SIZE,
    seed: int = DEFAULT_SEED,
    ratio: Sequence[int] = DEFAULT_RATIO,
    overlap: int = DEFAULT_OVERLAP,
    preserve_crs: bool = True,
    overwrite: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    t1_path = Path(t1_path).resolve()
    t2_path = Path(t2_path).resolve()
    output_dir = Path(output_dir).resolve()

    meta = _validate_sources(t1_path, t2_path, preserve_crs=preserve_crs)
    tiles, rows, cols = build_tile_grid(meta["width"], meta["height"], tile_size=tile_size, overlap=overlap)
    assignment = split_tiles(tiles, ratio=ratio, seed=seed)

    if _has_existing_tiles(output_dir) and not overwrite and not dry_run:
        raise FileExistsError(
            f"输出目录已存在切片，请更换 output_dir 或显式设置 overwrite=True: {output_dir}"
        )

    if overwrite and not dry_run:
        _clear_existing_tifs(output_dir)

    if not dry_run:
        ensure_layout(output_dir)
        _configure_rasterio_env()
        import rasterio
        from rasterio.windows import Window

        started_at = time.time()
        written_files = 0
        with rasterio.open(t1_path) as src1, rasterio.open(t2_path) as src2:
            for split_name in DEFAULT_SPLITS:
                for row, col in assignment[split_name]:
                    y_off = row * tile_size
                    x_off = col * tile_size
                    width = min(tile_size, meta["width"] - x_off)
                    height = min(tile_size, meta["height"] - y_off)
                    window = Window(x_off, y_off, width, height)
                    filename = tile_filename(row, col)

                    _write_tile(src1, window, output_dir / split_name / "t1" / filename, preserve_crs=preserve_crs)
                    _write_tile(src2, window, output_dir / split_name / "t2" / filename, preserve_crs=preserve_crs)
                    written_files += 2
        elapsed = round(time.time() - started_at, 2)
    else:
        written_files = 0
        elapsed = 0.0

    split_counts = {split_name: len(assignment[split_name]) for split_name in DEFAULT_SPLITS}
    layout_counts = collect_layout_counts(output_dir) if not dry_run else {
        split_name: {"t1": split_counts[split_name], "t2": split_counts[split_name]}
        for split_name in DEFAULT_SPLITS
    }

    return {
        "t1_path": str(t1_path),
        "t2_path": str(t2_path),
        "output_dir": str(output_dir),
        "tile_size": tile_size,
        "seed": seed,
        "ratio": list(ratio),
        "overlap": overlap,
        "preserve_crs": preserve_crs,
        "dry_run": dry_run,
        "grid": {
            "rows": rows,
            "cols": cols,
            "total_tiles": len(tiles),
        },
        "source": meta,
        "split_counts": split_counts,
        "layout_counts": layout_counts,
        "written_files": written_files,
        "elapsed_seconds": elapsed,
    }


def format_split_summary(result: Dict[str, Any]) -> str:
    split_counts = result["split_counts"]
    return (
        f"网格 {result['grid']['rows']}×{result['grid']['cols']}，共 {result['grid']['total_tiles']} 个瓦片；"
        f"train/val/test = {split_counts['train']}/{split_counts['val']}/{split_counts['test']}；"
        f"tile_size={result['tile_size']}，seed={result['seed']}，overlap={result['overlap']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="双时相影像渔网分割")
    parser.add_argument("--t1", default=str(DEFAULT_T1_SRC), help="前一时相 GeoTIFF 路径")
    parser.add_argument("--t2", default=str(DEFAULT_T2_SRC), help="后一时相 GeoTIFF 路径")
    parser.add_argument("--output_dir", default=str(DEFAULT_CDCD_OUTPUT_DIR), help="输出根目录")
    parser.add_argument("--tile_size", type=int, default=DEFAULT_TILE_SIZE, help="瓦片边长")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子")
    parser.add_argument("--overwrite", action="store_true", help="覆盖输出目录下已有 tif 切片")
    parser.add_argument("--dry_run", action="store_true", help="仅计算划分，不写文件")
    args = parser.parse_args(argv)

    result = fishnet_split_bitemporal_dataset(
        t1_path=args.t1,
        t2_path=args.t2,
        output_dir=args.output_dir,
        tile_size=args.tile_size,
        seed=args.seed,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )

    print("=" * 64)
    print("双时相影像渔网分割")
    print("=" * 64)
    print(f"t1: {result['t1_path']}")
    print(f"t2: {result['t2_path']}")
    print(f"output: {result['output_dir']}")
    print(format_split_summary(result))
    print(f"CRS: {result['source']['crs']}")
    print(
        f"bounds: left={result['source']['bounds']['left']:.3f}, "
        f"bottom={result['source']['bounds']['bottom']:.3f}, "
        f"right={result['source']['bounds']['right']:.3f}, "
        f"top={result['source']['bounds']['top']:.3f}"
    )
    for split_name in DEFAULT_SPLITS:
        counts = result["layout_counts"][split_name]
        print(f"{split_name}: t1={counts['t1']}, t2={counts['t2']}")
    if not result["dry_run"]:
        print(f"written_files: {result['written_files']}, elapsed: {result['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())