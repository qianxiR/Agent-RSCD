# -*- coding: utf-8 -*-
"""ChangeQwenNet ONNX 运行时（不依赖训练模型代码）。

输入: T1/T2 带坐标影像；输出: 二值变化 mask、彩色图、概率 GeoTIFF、GeoJSON。
模型以 ONNX 图加载，文本分支使用预计算 text_tokens/text_mask。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import cv2
import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

logger = logging.getLogger(__name__)

WEIGHTS_ROOT = Path(os.environ.get(
    "CHANGE_QWEN_RUNTIME_DIR",
    str(Path(__file__).resolve().parent / "weights"),
))

MODELS: Dict[str, Dict[str, Any]] = {
    "jzg": {
        "onnx": "change_qwen_jzg.onnx",
        "text": "change_qwen_jzg_text.npz",
        "prompt": "Detecting landslide changes in mountainous scenes from bi-temporal images.",
    },
    "jiuzhaigou": {
        "onnx": "change_qwen_jzg.onnx",
        "text": "change_qwen_jzg_text.npz",
        "prompt": "Detecting landslide changes in mountainous scenes from bi-temporal images.",
    },
    "cdcd": {
        "onnx": "change_qwen_cdcd.onnx",
        "text": "change_qwen_cdcd_text.npz",
        "prompt": "Detecting building changes in urban scenes from bi-temporal images.",
    },
}

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
TILE_SIZE = 336
STRIDE = 168
OVERLAP = TILE_SIZE - STRIDE

_SESSIONS: Dict[Tuple[str, Tuple[str, ...]], Any] = {}


def _resolve_model(model: str) -> dict:
    key = (model or "jzg").lower()
    if key not in MODELS:
        raise ValueError(f"未知模型: {model}，可选 {sorted(MODELS)}")
    spec = dict(MODELS[key])
    spec["key"] = key
    return spec


def _add_cuda_dll_path() -> None:
    """Windows: onnxruntime-gpu 需要 CUDA/cuDNN DLL，torch/lib 通常已带齐。"""
    if not hasattr(os, "add_dll_directory"):
        return
    try:
        import ctypes
        import sysconfig

        torch_lib = Path(sysconfig.get_paths()["purelib"]) / "torch" / "lib"
        if not torch_lib.is_dir():
            return
        os.add_dll_directory(str(torch_lib))
        os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
        for name in (
            "cudart64_12.dll",
            "cublas64_12.dll",
            "cublasLt64_12.dll",
            "cudnn64_9.dll",
            "cudnn_graph64_9.dll",
            "cudnn_ops64_9.dll",
            "cudnn_cnn64_9.dll",
            "cudnn_adv64_9.dll",
            "cudnn_engines_precompiled64_9.dll",
            "cudnn_heuristic64_9.dll",
        ):
            dll = torch_lib / name
            if dll.is_file():
                try:
                    ctypes.WinDLL(str(dll))
                except OSError:
                    pass
    except Exception:
        pass


def _get_session(onnx_path: Path, device: str = "auto"):
    if device in ("auto", "cuda"):
        _add_cuda_dll_path()
    import onnxruntime as ort

    available = ort.get_available_providers()
    if device in ("auto", "cuda") and "CUDAExecutionProvider" in available:
        providers = ("CUDAExecutionProvider", "CPUExecutionProvider")
    else:
        providers = ("CPUExecutionProvider",)
    cache_key = (str(onnx_path), providers)
    if cache_key in _SESSIONS:
        return _SESSIONS[cache_key]
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=list(providers))
    _SESSIONS[cache_key] = session
    return session


def _load_text(text_path: Path) -> Tuple[np.ndarray, np.ndarray, str]:
    data = np.load(text_path, allow_pickle=False)
    tokens = data["text_tokens"].astype(np.float32)
    mask = data["text_mask"].astype(bool)
    prompt = str(data["prompt"]) if "prompt" in data else ""
    return tokens, mask, prompt


def _band_indices(dataset) -> list[int]:
    return [1, 2, 3] if dataset.count >= 3 else [1, 1, 1]


def _read_rgb(dataset, window: Window) -> np.ndarray:
    arr = dataset.read(_band_indices(dataset), window=window)
    arr = np.transpose(arr, (1, 2, 0)).astype(np.float32)
    dtype = dataset.dtypes[0]
    if dtype == "uint8":
        pass
    elif dtype in ("uint16", "int16"):
        arr = arr / 257.0
    else:
        lo, hi = np.percentile(arr, [2, 98])
        arr = (arr - lo) / max(float(hi - lo), 1e-6) * 255.0
    return np.clip(arr, 0.0, 255.0)


def _pad_tile(arr: np.ndarray, tile: int) -> np.ndarray:
    h, w, _ = arr.shape
    if h == tile and w == tile:
        return arr
    pad_h, pad_w = tile - h, tile - w
    mode = "reflect" if h > 1 and w > 1 else "edge"
    return np.pad(arr, ((0, pad_h), (0, pad_w), (0, 0)), mode=mode)


def _normalize(arr: np.ndarray) -> np.ndarray:
    x = arr.astype(np.float32) / 255.0
    x = (x - MEAN) / STD
    return np.transpose(x, (2, 0, 1))


def _axis_positions(size: int, tile: int, stride: int) -> list[int]:
    if size <= tile:
        return [0]
    last = size - tile
    positions = list(range(0, last + 1, stride))
    if positions[-1] != last:
        positions.append(last)
    return positions


def _tile_windows(width: int, height: int, tile: int, stride: int) -> Iterator[Tuple[int, int, int, int]]:
    for y in _axis_positions(height, tile, stride):
        for x in _axis_positions(width, tile, stride):
            yield x, y, min(tile, width - x), min(tile, height - y)


def _filter_small_components(mask: np.ndarray, min_area_px: int) -> Tuple[np.ndarray, int]:
    mask_u8 = mask.astype(np.uint8)
    if min_area_px <= 0:
        num, _ = cv2.connectedComponents(mask_u8, connectivity=8)
        return mask, max(num - 1, 0)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)
    keep = np.zeros_like(mask, dtype=bool)
    regions = 0
    for idx in range(1, num):
        if stats[idx, cv2.CC_STAT_AREA] >= min_area_px:
            keep |= labels == idx
            regions += 1
    return keep, regions


def _save_color(mask: np.ndarray, path: Path) -> None:
    rgb = np.full((mask.shape[0], mask.shape[1], 3), 255, dtype=np.uint8)
    rgb[mask] = (255, 0, 0)
    Image.fromarray(rgb).save(path)


def _vectorize(mask: np.ndarray, transform, crs, geojson_path: Path, min_area_m2: float):
    try:
        from backend.data.vector.vectorize import polygonize_mask
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ChangeQwen] 矢量化依赖不可用: %s", exc)
        return None
    gdf = polygonize_mask(
        mask.astype(np.uint8),
        transform,
        crs,
        class_names=["背景", "变化"],
        min_area_m2=min_area_m2,
    )
    if gdf is None or len(gdf) == 0:
        return None
    geojson_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(geojson_path, driver="GeoJSON", encoding="utf-8")
    return gdf


def predict_change(
    t1_path: str,
    t2_path: str,
    *,
    model: str = "jzg",
    output_dir: Optional[str] = None,
    threshold: float = 0.5,
    tile_size: int = TILE_SIZE,
    stride: int = STRIDE,
    batch_size: int = 0,
    device: str = "auto",
    min_area_px: int = 0,
    min_area_m2: float = 50.0,
    save_prob: bool = True,
) -> Dict[str, Any]:
    """对 T1/T2 影像执行二值变化检测。

    入参:
      - t1_path/t2_path: 带 CRS 的影像路径；T2 与 T1 网格不一致时自动重采样到 T1 网格。
      - model: jzg / jiuzhaigou / cdcd。
      - output_dir: 产物目录；缺省临时目录。
      - threshold: sigmoid 阈值。
      - tile_size/stride: 分块尺寸与步长。
      - batch_size: 0=自动（GPU 4 / CPU 1）。
      - device: auto / cuda / cpu。
      - min_area_px: 小于该像素数的连通域剔除；0=不过滤。
      - min_area_m2: 矢量化最小面积。
      - save_prob: 是否输出概率 GeoTIFF。
    出参:
      - {status, model, prompt, mask_path, color_path, vector_path, prob_path, stats, performance, providers}
    """
    started = time.perf_counter()
    spec = _resolve_model(model)
    onnx_path = WEIGHTS_ROOT / spec["onnx"]
    text_path = WEIGHTS_ROOT / spec["text"]
    if not onnx_path.is_file():
        raise FileNotFoundError(f"ONNX 模型不存在: {onnx_path}")
    if not text_path.is_file():
        raise FileNotFoundError(f"文本 token 不存在: {text_path}")

    out_dir = Path(output_dir or tempfile.mkdtemp(prefix=f"change_qwen_{spec['key']}_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    mask_path = out_dir / "change_mask.png"
    color_path = out_dir / "change_color.png"
    vector_path = out_dir / "change.geojson"
    shp_path = out_dir / "change.shp"
    prob_path = out_dir / "change_prob.tif"

    session = _get_session(onnx_path, device=device)
    tokens, text_mask, prompt = _load_text(text_path)

    with rasterio.open(t1_path) as src1, rasterio.open(t2_path) as src2:
        if src1.crs is None:
            raise ValueError(f"T1 影像无 CRS: {t1_path}")
        same_grid = (
            src2.crs == src1.crs
            and src2.transform == src1.transform
            and src2.width == src1.width
            and src2.height == src1.height
        )
        vrt = None
        if not same_grid:
            if src2.crs is None:
                raise ValueError(f"T2 影像无 CRS: {t2_path}")
            vrt = WarpedVRT(
                src2,
                crs=src1.crs,
                transform=src1.transform,
                width=src1.width,
                height=src1.height,
                resampling=Resampling.bilinear,
            )
        src2_use = vrt or src2

        height, width = src1.height, src1.width
        transform, crs = src1.transform, src1.crs
        prob_sum = np.zeros((height, width), dtype=np.float32)
        count = np.zeros((height, width), dtype=np.float32)

        if batch_size <= 0:
            providers = session.get_providers()
            batch_size = 4 if "CUDAExecutionProvider" in providers else 1

        batch: list[dict] = []
        tile_count = 0

        def flush():
            nonlocal batch, tile_count
            if not batch:
                return
            t1 = np.stack([item["t1"] for item in batch], axis=0)
            t2 = np.stack([item["t2"] for item in batch], axis=0)
            b = len(batch)
            tok = np.repeat(tokens, b, axis=0)
            tmask = np.repeat(text_mask, b, axis=0)
            logits = session.run(["logits"], {
                "t1": t1, "t2": t2,
                "text_tokens": tok, "text_mask": tmask,
            })[0]
            prob = 1.0 / (1.0 + np.exp(-logits[:, 0]))
            for item, p in zip(batch, prob):
                h, w = item["h"], item["w"]
                prob_sum[item["y"]:item["y"] + h, item["x"]:item["x"] + w] += p[:h, :w]
                count[item["y"]:item["y"] + h, item["x"]:item["x"] + w] += 1.0
            tile_count += b
            batch = []

        for x, y, w, h in _tile_windows(width, height, tile_size, stride):
            win = Window(x, y, w, h)
            rgb1 = _read_rgb(src1, win)
            rgb2 = _read_rgb(src2_use, win)
            batch.append({
                "x": x, "y": y, "h": h, "w": w,
                "t1": _normalize(_pad_tile(rgb1, tile_size)),
                "t2": _normalize(_pad_tile(rgb2, tile_size)),
            })
            if len(batch) >= batch_size:
                flush()
        flush()

    infer_done = time.perf_counter()
    prob_map = prob_sum / np.maximum(count, 1.0)
    mask = prob_map >= float(threshold)
    mask, regions = _filter_small_components(mask, int(min_area_px))

    Image.fromarray((mask.astype(np.uint8) * 255)).save(mask_path)
    _save_color(mask, color_path)
    if save_prob:
        with rasterio.open(
            prob_path, "w", driver="GTiff", height=prob_map.shape[0], width=prob_map.shape[1],
            count=1, dtype="float32", crs=crs, transform=transform, compress="deflate",
        ) as dst:
            dst.write(prob_map.astype(np.float32), 1)

    gdf = _vectorize(mask, transform, crs, vector_path, min_area_m2)
    if gdf is not None:
        gdf.to_file(shp_path, driver="ESRI Shapefile", encoding="utf-8")
    changed_pixels = int(mask.sum())
    total_area_m2 = float(gdf["area_m2"].sum()) if gdf is not None else 0.0
    stats = {
        "changed_pixels": changed_pixels,
        "changed_percent": round(changed_pixels / max(mask.size, 1) * 100.0, 4),
        "regions": int(regions),
        "total_area_m2": round(total_area_m2, 2),
        "threshold": float(threshold),
        "tile_count": int(tile_count),
    }
    performance = {
        "inference_seconds": round(infer_done - started, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
    }
    return {
        "status": "success",
        "model": spec["key"],
        "prompt": prompt,
        "mask_path": str(mask_path),
        "color_path": str(color_path),
        "vector_path": str(vector_path) if gdf is not None else None,
        "shp_path": str(shp_path) if gdf is not None else None,
        "shp_dir_path": str(out_dir) if gdf is not None else None,
        "prob_path": str(prob_path) if save_prob else None,
        "stats": stats,
        "performance": performance,
        "providers": session.get_providers(),
        "size": {"width": width, "height": height},
    }


def main():
    parser = argparse.ArgumentParser(description="ChangeQwenNet ONNX 推理")
    parser.add_argument("--t1", required=True)
    parser.add_argument("--t2", required=True)
    parser.add_argument("--model", default="jzg", choices=sorted(MODELS))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--tile-size", type=int, default=TILE_SIZE)
    parser.add_argument("--stride", type=int, default=STRIDE)
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--min-area-px", type=int, default=0)
    parser.add_argument("--min-area-m2", type=float, default=50.0)
    parser.add_argument("--no-prob", action="store_true")
    args = parser.parse_args()
    result = predict_change(
        args.t1, args.t2,
        model=args.model,
        output_dir=args.output_dir,
        threshold=args.threshold,
        tile_size=args.tile_size,
        stride=args.stride,
        batch_size=args.batch_size,
        device=args.device,
        min_area_px=args.min_area_px,
        min_area_m2=args.min_area_m2,
        save_prob=not args.no_prob,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
