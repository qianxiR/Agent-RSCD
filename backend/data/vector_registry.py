# -*- coding: utf-8 -*-
"""
矢量产物登记 (Data 层 / vector_registry)

职责:
  - 把 SamSeg/矢量工具产出的 GeoJSON 登记进业务库 `vector_layer`，让“磁盘有文件、
    库里没元数据”的产物具备可检索的 bbox/要素数/真实面积/CRS/类别统计。
  - 同时被工具层（新产物自动登记）和 scripts/register_vector_outputs.py（存量回填）复用。

依赖方向: data 层模块，仅依赖 backend.data.business_db；不得反向依赖 model/agent。
"""
import hashlib
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _iter_coords(coords):
    """递归展开 GeoJSON coordinates，产出 (x, y) 点。"""
    if isinstance(coords, (list, tuple)) and len(coords) == 2 \
            and all(isinstance(v, (int, float)) for v in coords):
        yield float(coords[0]), float(coords[1])
        return
    if isinstance(coords, (list, tuple)):
        for item in coords:
            yield from _iter_coords(item)


def _bbox_from_geojson(data: dict) -> Optional[Tuple[float, float, float, float]]:
    """入参: GeoJSON dict；出参: (minx,miny,maxx,maxy) 或 None。"""
    xs, ys = [], []
    for feat in data.get("features") or []:
        geom = feat.get("geometry") or {}
        for x, y in _iter_coords(geom.get("coordinates")):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _epsg_from_wkt(wkt: str) -> Optional[int]:
    """入参: .prj 的 WKT；方法: osgeo 解析 EPSG；出参: epsg 或 None。"""
    try:
        from osgeo import osr
        srs = osr.SpatialReference()
        srs.ImportFromWkt(wkt)
        code = srs.GetAuthorityCode(None)
        return int(code) if code else None
    except Exception:
        return None


def _infer_srid(file_path: Path, bbox, source_image_path: Optional[str] = None) -> int:
    """
    入参: 矢量文件、bbox、可选源影像。
    方法: 优先级 —— 同目录 .prj → 源影像 rasterio CRS → bbox 数值范围启发式
          （经纬度→4490；Web Mercator 量级→3857）。
    出参: EPSG 整数；无法判断时 4490（系统统一空间参考）。
    """
    # 1) Shapefile .prj（GeoJSON 常与 SHP 同目录）
    prj = file_path.with_suffix(".prj")
    if prj.is_file():
        code = _epsg_from_wkt(prj.read_text(encoding="utf-8", errors="ignore"))
        if code:
            return code

    # 2) 源影像 CRS
    if source_image_path and Path(source_image_path).is_file():
        try:
            import rasterio
            with rasterio.open(source_image_path) as src:
                if src.crs:
                    code = src.crs.to_epsg()
                    if code:
                        return int(code)
        except Exception:
            pass

    # 3) bbox 启发式
    if bbox:
        minx, miny, maxx, maxy = bbox
        if -180 <= minx <= 180 and -90 <= miny <= 90:
            return 4490  # CGCS2000 / 地理坐标
        if 1_000_000 <= abs(minx) <= 20_000_000 and 0 <= abs(miny) <= 20_000_000:
            return 3857  # Web Mercator
    return 4490


def _layer_name_for(file_path: Path) -> str:
    """
    入参: 矢量文件路径。
    方法: 默认用 stem；若数据库同名但指向不同文件，追加路径 hash 后缀保证唯一。
    出参: 唯一 layer_name。
    """
    from backend.data import business_db

    stem = file_path.stem
    with business_db.get_conn() as conn:
        if conn is None:
            return stem
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT file_path FROM vector_layer WHERE layer_name = %s", (stem,)
                )
                row = cur.fetchone()
            if row and row[0] and Path(row[0]).resolve() != file_path.resolve():
                digest = hashlib.md5(str(file_path.resolve()).encode("utf-8")).hexdigest()[:6]
                return f"{stem}_{digest}"
        except Exception:
            pass
    return stem


def register_vector_product(
    file_path: str,
    kind: str = "change",
    workspace: str = "samseg",
    source_image_path: Optional[str] = None,
    attrs: Optional[dict] = None,
) -> Optional[int]:
    """
    入参: GeoJSON 路径、kind(segment/change/rule)、workspace、可选源影像、附加属性。
    方法: 解析要素数/bbox/面积/类别统计/CRS → business_db.register_vector_layer（UPSERT）。
    出参: vector_layer 主键；文件不存在/解析失败/DB 不可用返回 None。
    """
    path = Path(file_path)
    if not path.is_file() or path.suffix.lower() != ".geojson":
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[VectorRegistry] 读取 GeoJSON 失败 {path}: {e}")
        return None

    features = data.get("features") or []
    if not features:
        logger.info(f"[VectorRegistry] 空 GeoJSON, 跳过: {path}")
        return None

    bbox = _bbox_from_geojson(data)
    srid = _infer_srid(path, bbox, source_image_path)
    class_counts = Counter()
    total_area = 0.0
    for feat in features:
        props = feat.get("properties") or {}
        class_counts[str(props.get("class_name") or props.get("class_id") or "unknown")] += 1
        try:
            total_area += float(props.get("area_m2") or 0.0)
        except (TypeError, ValueError):
            pass

    layer_name = _layer_name_for(path)
    payload = {
        "feature_count": len(features),
        "class_counts": dict(class_counts),
        "source": "samseg",
        "crs_inferred": f"EPSG:{srid}",
    }
    if attrs:
        payload.update(attrs)

    from backend.data import business_db
    vid = business_db.register_vector_layer(
        layer_name=layer_name,
        kind=kind,
        workspace=workspace,
        file_path=str(path.resolve()),
        bbox=bbox,
        srid=srid,
        feature_count=len(features),
        total_area_m2=round(total_area, 2) if total_area else None,
        attrs=payload,
    )
    if vid:
        logger.info(
            f"[VectorRegistry] 登记成功: {layer_name} kind={kind} "
            f"features={len(features)} area={round(total_area, 2)} srid={srid}"
        )
    return vid
