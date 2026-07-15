# -*- coding: utf-8 -*-
"""
栅格 → 矢量转换 (Data 层 / vector 子包)

来源: 提取自 E:\\1代码\\系统\\Tools\\DataProcessing\\mask_postprocess
      - vector.raster_to_vector (多类别栅格文件 → Shapefile/GeoJSON)
      - vector.mask_to_polygons (二值 mask → shapely 多边形列表)
      - coordinate.wkt_to_epsg (WKT → EPSG 代码)

适配改造:
  1. 内联 wkt_to_epsg 依赖 (避免相对导入, 自包含)
  2. 加 vectorize_available() 可用性检测 (依赖缺失返回 False, 上层优雅降级)
  3. 新增 polygonize_mask(): 直接吃内存 mask 数组 + rasterio transform,
     供 runner.run_segment/run_change_detection 在内存中矢量化 (避免落盘 mask 再读回)
  4. 统一面积单位为平方米 (地理坐标系自动转 UTM; 投影坐标系直接用)
  5. 输出 GeoJSON 优先 (前端可直接渲染), Shapefile 可选

依赖: rasterio / geopandas / shapely / osgeo.osr / numpy
      (sam3 环境通常齐全; base 环境可能缺 geopandas, 走降级)
"""
import os
import logging
from typing import Optional, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ==================== 可用性检测 (惰性, 缓存) ====================
_availability_checked = False
_vectorize_available = False


def vectorize_available() -> bool:
    """
    检测矢量化依赖是否齐全 (rasterio + geopandas + shapely + osgeo + numpy).
    只检测一次并缓存 (与 runner.samseg_available 同套模式).
    """
    global _availability_checked, _vectorize_available
    if _availability_checked:
        return _vectorize_available
    _availability_checked = True
    try:
        import rasterio          # noqa: F401
        import geopandas as gpd  # noqa: F401
        from shapely.geometry import shape  # noqa: F401
        from osgeo import osr   # noqa: F401
        _vectorize_available = True
        logger.info("[Vectorize] 依赖就绪 (rasterio/geopandas/shapely/osgeo)")
    except ImportError as e:
        _vectorize_available = False
        logger.info(f"[Vectorize] 依赖缺失, 矢量化不可用: {e}")
    return _vectorize_available


# ==================== WKT → EPSG (内联, 避免 Tools 依赖) ====================

def _wkt_to_epsg(wkt: str) -> Optional[str]:
    """
    从 WKT 投影字符串解析 EPSG 代码.
    来源: Tools.DataProcessing.mask_postprocess.coordinate.wkt_to_epsg
    返回: 如 "4326" / "3857" / None
    """
    if not wkt:
        return None
    try:
        from osgeo import osr
        srs = osr.SpatialReference()
        srs.ImportFromWkt(wkt)
        return srs.GetAuthorityCode(None)
    except Exception as e:
        logger.warning(f"[Vectorize] WKT→EPSG 解析失败: {e}")
        return None


# ==================== UTM 面积计算 (地理坐标系下转 UTM 算真实面积) ====================

def _geom_area_m2(geom, src_crs) -> float:
    """
    计算几何体的真实面积 (平方米).
    - 投影坐标系 (如 EPSG:3857 米制): 直接 .area
    - 地理坐标系 (如 EPSG:4326 经纬度): 转对应 UTM zone 后再算
    """
    import geopandas as gpd
    try:
        if src_crs is None:
            return float(geom.area)
        if src_crs.is_projected:
            return float(geom.area)
        # 地理坐标系 → UTM
        centroid = geom.centroid
        utm_zone = int((centroid.x + 180) / 6) + 1
        utm_crs = f"EPSG:{32600 + utm_zone}" if centroid.y >= 0 else f"EPSG:{32700 + utm_zone}"
        reprojected = gpd.GeoSeries([geom], crs=src_crs).to_crs(utm_crs).iloc[0]
        return float(reprojected.area)
    except Exception as e:
        logger.warning(f"[Vectorize] 面积计算失败, 回退到几何面积: {e}")
        return float(geom.area)


# ==================== 核心: 内存 mask → 矢量 (runner 用) ====================

def polygonize_mask(
    mask: np.ndarray,
    transform,
    crs,
    class_names: Optional[List[str]] = None,
    min_area_m2: float = 50.0,
    simplify_tolerance: float = 0.0,
) -> Optional["object"]:
    """
    把内存中的类别掩膜矢量化为 GeoDataFrame (带真实面积/周长/类别名).

    ★ 这是 runner.run_segment/run_change_detection 在内存中调用的主入口,
      避免落盘 mask 再读回的开销.

    入参:
      - mask (np.ndarray): (H, W) 类别索引图, 0=背景/无变化, >0=类别 ID
      - transform: rasterio Affine 变换 (像素↔地理坐标)
      - crs: rasterio CRS 对象 (可为 None, 此时面积用像素单位)
      - class_names: 类别 ID→名称映射列表, 索引 = 类别 ID.
                     如 ["背景", "建筑", "道路", ...]. None 则用 "class_<id>"
      - min_area_m2: 最小面积过滤 (平方米). 投影坐标系下直接用; 地理坐标系转 UTM.
                     设 0 表示不过滤.
      - simplify_tolerance: Douglas-Peucker 简化容差 (地理单位). 0=不简化.

    出参:
      - geopandas.GeoDataFrame, 列 = [class_id, class_name, area_m2, perimeter_m, geometry]
        CRS 与输入 crs 一致. 无有效要素时返回 None.
      - 依赖不可用时返回 None.
    """
    if not vectorize_available():
        return None
    from rasterio import features
    from shapely.geometry import shape, Polygon, MultiPolygon
    import geopandas as gpd

    if mask is None or mask.size == 0:
        return None
    if mask.ndim > 2:
        mask = mask[:, :, 0]

    unique = np.unique(mask)
    process_ids = [int(c) for c in unique if c != 0]

    rows = []
    for cid in process_ids:
        binary = (mask == cid).astype(np.uint8)
        if binary.sum() == 0:
            continue
        # rasterio.features.shapes: 按像素几何 + transform 直接产出地理坐标几何
        for geom_json, val in features.shapes(binary, mask=binary > 0,
                                              transform=transform, connectivity=8):
            if val != 1:
                continue
            geom = shape(geom_json)
            if not isinstance(geom, (Polygon, MultiPolygon)):
                continue
            if not geom.is_valid:
                geom = geom.buffer(0)
            if geom.is_empty or not geom.is_valid:
                continue
            # 真实面积过滤
            area = _geom_area_m2(geom, crs) if min_area_m2 > 0 else geom.area
            if min_area_m2 > 0 and area < min_area_m2:
                continue
            # 简化
            if simplify_tolerance > 0:
                geom = geom.simplify(simplify_tolerance, preserve_topology=True)
                if geom.is_empty or not geom.is_valid:
                    continue
            name = (class_names[cid] if class_names and cid < len(class_names)
                    else f"class_{cid}")
            rows.append({
                "class_id": cid,
                "class_name": name,
                "area_m2": round(area, 2),
                "perimeter_m": round(float(geom.length), 2),
                "geometry": geom,
            })

    if not rows:
        return None

    gdf = gpd.GeoDataFrame(rows, geometry="geometry")
    if crs is not None:
        gdf.set_crs(crs, inplace=True)
    return gdf


def polygons_to_boundary_geojson(
    gdf,
    output_geojson_path: str,
    simplify_tolerance: float = 0.0,
):
    """
    把多边形 GeoDataFrame 的边界提取为线 (LineString/MultiLineString), 写成 GeoJSON.

    ★ 复用 polygonize_mask 已产出的 gdf (同一份面要素, 不重复矢量化)。
    ★ 用 shapely 的 geom.boundary (Polygon→LineString, MultiPolygon→MultiLineString)。
    ★ 保留 class_name 等属性列 (供前端属性查看/图例)。

    入参:
      - gdf: polygonize_mask 返回的 GeoDataFrame (geometry=Polygon/MultiPolygon, 含 class_name/area_m2)
      - output_geojson_path: 输出 .geojson 路径
      - simplify_tolerance: Douglas-Peucker 简化容差 (地理单位), 0=不简化 (减少锯齿)

    出参: output_geojson_path (成功) 或 None (失败/空 gdf)
    """
    try:
        import geopandas as gpd
    except ImportError:
        logger.warning("[Boundary] geopandas 不可用, 跳过边界线生成")
        return None

    if gdf is None or len(gdf) == 0:
        logger.info("[Boundary] gdf 为空, 跳过边界线生成")
        return None

    try:
        # ★ geom.boundary: Polygon→LineString, MultiPolygon→MultiLineString
        boundary_geom = gdf.geometry.boundary
        # 可选简化 (减少栅格锯齿, 提升前端渲染性能)
        if simplify_tolerance and simplify_tolerance > 0:
            boundary_geom = boundary_geom.simplify(simplify_tolerance, preserve_topology=True)

        gdf_boundary = gpd.GeoDataFrame(
            # 保留原属性列 (class_name/area_m2 等), geometry 换成边界线
            gdf.drop(columns=["geometry"]),
            geometry=boundary_geom,
        )
        if gdf.crs is not None:
            gdf_boundary.set_crs(gdf.crs, inplace=True)

        import os
        os.makedirs(os.path.dirname(output_geojson_path) or ".", exist_ok=True)
        gdf_boundary.to_file(output_geojson_path, driver="GeoJSON", encoding="utf-8")
        logger.info(
            f"[Boundary] 边界线已生成: {output_geojson_path} "
            f"({len(gdf_boundary)} 条线, crs={'有' if gdf.crs is not None else '无'})"
        )
        return output_geojson_path
    except Exception as e:
        logger.warning(f"[Boundary] 边界线生成失败: {e}", exc_info=True)
        return None


# ==================== 栅格文件 → 矢量文件 (通用, 工具层用) ====================

def raster_to_vector(
    mask_path: str,
    output_path: str,
    filter_classes: Optional[List[int]] = None,
    min_area_m2: float = 50.0,
    source_image_path: Optional[str] = None,
    class_names: Optional[List[str]] = None,
) -> Optional["object"]:
    """
    栅格掩膜文件 → 矢量文件 (GeoJSON/Shapefile).

    来源: Tools.DataProcessing.mask_postprocess.vector.raster_to_vector
    适配: 加 class_names 映射 + min_area 单位统一为平方米 + 输出格式按扩展名自动判定.

    入参:
      - mask_path: 分割/变化 mask 栅格文件路径 (单波段, 像素值=类别 ID)
      - output_path: 输出矢量路径 (.geojson / .shp 按扩展名判定驱动)
      - filter_classes: 只处理这些类别 ID, None=处理所有非 0
      - min_area_m2: 最小面积过滤 (平方米)
      - source_image_path: 原始影像路径 (优先取其 CRS/transform; 否则用 mask 自身)
      - class_names: 类别 ID→名称映射

    出参:
      - geopandas.GeoDataFrame 或 None (无有效要素/依赖缺失)
    """
    if not vectorize_available():
        return None
    import rasterio
    import geopandas as gpd

    # 优先从源影像取 CRS/transform (mask 可能丢失地理信息)
    src_crs, src_transform = None, None
    if source_image_path and os.path.exists(source_image_path):
        with rasterio.open(source_image_path) as src:
            src_crs, src_transform = src.crs, src.transform

    with rasterio.open(mask_path) as f:
        image = f.read(1)
        crs = src_crs or f.crs
        transform = src_transform or f.transform
        nodata = f.nodata if f.nodata else 0
        image = np.where(image == nodata, 0, image)

    gdf = polygonize_mask(
        image, transform, crs,
        class_names=class_names,
        min_area_m2=min_area_m2,
    )
    if gdf is None:
        return None

    # 按扩展名选驱动
    ext = os.path.splitext(output_path)[1].lower()
    driver = "GeoJSON" if ext == ".geojson" else "ESRI Shapefile"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    gdf.to_file(output_path, driver=driver, encoding="utf-8")
    logger.info(f"[Vectorize] 矢量导出: {output_path} ({len(gdf)} 个要素, driver={driver})")
    return gdf


# ==================== 二值 mask → 多边形列表 (兼容 Tools 原签名) ====================

def mask_to_polygons(mask: np.ndarray, min_area: float = 100,
                     simplify: bool = True, simplify_tolerance: float = 0.5):
    """
    二值 mask → shapely 多边形列表 + 面积列表.
    来源: Tools.DataProcessing.mask_postprocess.vector.mask_to_polygons (原样保留)

    ★ 像素坐标系 (无地理参考), 供需要纯几何形态的场景用.
    带 transform 的场景请用 polygonize_mask.
    """
    if not vectorize_available():
        return [], []
    from rasterio import features
    from shapely.geometry import shape, Polygon, MultiPolygon
    import cv2

    if mask is None or mask.size == 0:
        return [], []
    if len(mask.shape) > 2:
        mask = mask[:, :, 0]

    # 类型归一化到 uint8 二值
    if mask.dtype in (np.bool_, bool):
        binary = mask.astype(np.uint8)
    elif np.issubdtype(mask.dtype, np.floating):
        binary = (mask > 0.5).astype(np.uint8)
    else:
        binary = (mask > (127 if mask.max() > 1 else 0)).astype(np.uint8)

    # 形态学平滑 (开运算去噪 → 闭运算填洞 → 中值 → 高斯)
    k3 = np.ones((3, 3), np.uint8)
    k5 = np.ones((5, 5), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k3, iterations=2)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k5, iterations=2)
    binary = cv2.medianBlur(binary, 5)
    smoothed = cv2.GaussianBlur(binary.astype(np.float32), (9, 9), 2.0)
    binary = (smoothed > 0.5).astype(np.uint8)

    if binary.sum() == 0:
        return [], []

    shapes_gen = features.shapes(binary, mask=binary > 0, connectivity=8)
    polygons, areas = [], []
    for geom, value in shapes_gen:
        if value == 0:
            continue
        poly = shape(geom)
        if not isinstance(poly, (Polygon, MultiPolygon)):
            continue
        if poly.area < min_area:
            continue
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_valid or poly.is_empty:
            continue
        if simplify and simplify_tolerance > 0:
            poly = poly.simplify(simplify_tolerance * 2, preserve_topology=True)
        if not poly.is_valid or poly.is_empty or poly.area < min_area:
            continue
        polygons.append(poly)
        areas.append(poly.area)
    return polygons, areas
