"""样本标注导出：COCO JSON 与 Shapefile 压缩包。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from backend.model.sample.annotations import polygon_area


def _rasterize(annotations: Dict[str, Any]) -> List[Tuple[Dict[str, Any], np.ndarray]]:
    import cv2

    image = annotations["image"]
    width = int(image["width"])
    height = int(image["height"])
    masks: List[Tuple[Dict[str, Any], np.ndarray]] = []
    for item in annotations.get("instances") or []:
        mask = np.zeros((height, width), dtype=np.uint8)
        points = item.get("points") or []
        if len(points) >= 3:
            pts = np.asarray(points, dtype=np.float32)
            cv2.fillPoly(mask, [pts.astype(np.int32)], 1)
        masks.append((item, mask.astype(bool)))
    return masks


def export_coco(task_path: Path, annotations: Dict[str, Any]) -> Path:
    from backend.model.sam3native.coco_export import write_coco

    image = annotations["image"]
    width = int(image["width"])
    height = int(image["height"])
    name = "phase_a.tif" if image.get("crop_px") else Path(
        str(image.get("path") or "source.tif"),
    ).name
    items = [
        {
            "mask": mask,
            "name": item.get("class_name") or item.get("label"),
            "bbox": item.get("bbox_px"),
            "score": item.get("score"),
        }
        for item, mask in _rasterize(annotations)
    ]
    out = task_path / "coco.json"
    write_coco(str(out), name, width, height, items)
    return out


def export_shp(task_path: Path, annotations: Dict[str, Any]) -> Path:
    from shapely.geometry import Polygon

    from backend.integrations.vendor.spatial.export_utils import export_shapefile

    features = []
    for item in annotations.get("instances") or []:
        coordinates = item.get("coordinates") or []
        if len(coordinates) < 3:
            continue
        ring = [(float(p[0]), float(p[1])) for p in coordinates]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        polygon = Polygon(ring)
        if polygon.is_empty:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "class_name": item.get("class_name") or item.get("label"),
                "score": item.get("score"),
                "area_px": item.get("area_px") or round(polygon_area(item.get("points") or []), 1),
            },
            "geometry": polygon.__geo_interface__,
        })

    if not features:
        raise ValueError("没有可导出的要素")

    geojson = {"type": "FeatureCollection", "features": features}
    out = task_path / "annotations.shp"
    export_shapefile(geojson, out)
    (task_path / "annotations.geojson").write_text(
        json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    archive = task_path / "annotations_shp.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".geojson"):
            part = task_path / ("annotations" + suffix)
            if part.is_file():
                bundle.write(part, part.name)
    return archive


def _crop_raster(source: Path, dest_tif: Path, dest_png: Path, crop: List[int]) -> None:
    """按 crop_px 裁剪源 GeoTIFF，并写 PNG 预览。"""
    import rasterio
    from rasterio.windows import Window, transform as window_transform
    from PIL import Image

    x0, y0, x1, y1 = (int(v) for v in crop)
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        raise ValueError("crop_px 尺寸无效")

    with rasterio.open(source) as src:
        window = Window(x0, y0, width, height)
        data = src.read(window=window)
        profile = src.profile.copy()
        profile.update(
            width=width,
            height=height,
            transform=window_transform(window, src.transform),
        )
        dest_tif.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dest_tif, "w", **profile) as dst:
            dst.write(data)

    array = np.asarray(data)
    if array.ndim == 3:
        rgb = array[:3].transpose(1, 2, 0)
        if rgb.shape[2] == 1:
            rgb = np.repeat(rgb, 3, axis=2)
    else:
        rgb = np.repeat(array[:, :, None], 3, axis=2)
    rgb = rgb.astype(np.float32)
    peak = float(rgb.max()) if rgb.size else 0.0
    if peak > 255:
        rgb = rgb / peak * 255.0
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(dest_png)


def export_dataset(task_path: Path, annotations: Dict[str, Any]) -> Path:
    """导出数据集包：裁剪影像 + 标注 + COCO + SHP/GeoJSON。"""
    image = annotations["image"]
    crop = image.get("crop_px") or [
        0, 0, int(image["width"]), int(image["height"]),
    ]
    phases = image.get("phases") or [{"path": image.get("path") or ""}]
    dataset_dir = task_path / "dataset"
    image_dir = dataset_dir / "image"
    image_dir.mkdir(parents=True, exist_ok=True)

    image_files: List[Path] = []
    for index, phase in enumerate(phases):
        source = task_path / str(phase.get("path") or "")
        if not source.is_file():
            raise ValueError(f"源影像不存在: {phase.get('path')}")
        letter = chr(ord("a") + index)
        tif = image_dir / f"phase_{letter}.tif"
        png = image_dir / f"phase_{letter}.png"
        _crop_raster(source, tif, png, crop)
        image_files.extend([tif, png])

    coco_path = export_coco(task_path, annotations)
    try:
        shp_zip = export_shp(task_path, annotations)
    except ValueError:
        shp_zip = None

    archive = task_path / f"{task_path.name}_dataset.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in image_files:
            bundle.write(path, f"image/{path.name}")
        bundle.write(task_path / "annotations.json", "annotations.json")
        bundle.write(coco_path, "coco.json")
        if shp_zip and shp_zip.is_file():
            with zipfile.ZipFile(shp_zip) as vectors:
                for name in vectors.namelist():
                    bundle.writestr(f"vector/{name}", vectors.read(name))
        meta = task_path / "meta.json"
        if meta.is_file():
            bundle.write(meta, "meta.json")
    return archive
