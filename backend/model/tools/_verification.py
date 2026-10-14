# -*- coding: utf-8 -*-
"""
工具产物客观校验 (Model 层 / 工具箱基础设施)

设计动机: 防止"伪装成功" —— 工具内部 try/except 吞异常时,
仍返回 success + 友好 summary, 让 LLM 误判任务完成 (典型: 0KB ZIP bug).

核心理念 (成功契约):
  工具返回值的 summary 是"自述", 可能在异常时撒谎;
  verification 是"客观证据", 由本模块独立校验后生成.
  LLM 必须看 verification, 不准只看 summary 就宣告完成.

提供的校验器 (按产物类型):
  - verify_file_exists:    通用基础校验 (文件存在 + 大小 >= 阈值)
  - verify_zip_integrity:  ZIP 内容完整性 (必含核心组件, 如 .shp/.dbf/.shx)
  - verify_vector_features: 矢量文件可读性 (geopandas 读回 + 要素数 + 几何有效)
  - verify_pdf_pages:       PDF/Word 页数 (>0)
  - verify_by_path:         按扩展名自动路由, 统一入口

返回值统一结构:
  {"ok": bool, "size_bytes": int, "reason": str(失败时), 其他字段...}
  ok=True 表示通过校验; ok=False 时 reason 必须给出可读的失败原因.

依赖策略 (与 vectorize_available 同套降级):
  - 基础校验 (文件存在/ZIP): 只依赖标准库, 永远可用
  - 矢量/PDF 校验: 依赖 geopandas/PyPDF2, 缺失时返回 "未校验" 而非报错
"""
import os
import logging
import zipfile
from pathlib import Path
from typing import Optional, Tuple, Sequence

logger = logging.getLogger(__name__)


# ==================== 基础校验: 文件存在 + 大小 ====================

def verify_file_exists(path, *, min_bytes: int = 100) -> dict:
    """
    通用基础校验: 文件存在 + 大小 >= 阈值.

    入参:
        - path (str|Path): 文件路径
        - min_bytes (int): 最小字节数阈值, 默认 100 (低于几乎必然是空文件/写入失败)

    出参:
        {"ok": bool, "size_bytes": int, "reason": str, "evidence": str}
        - ok=True: size_bytes 为实际大小
        - ok=False: reason 说明失败原因 (不存在/过小)
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "size_bytes": 0,
                "reason": f"文件未生成 (path 不存在): {path}",
                "evidence": "产物未落地, 工具可能写入失败"}
    if not p.is_file():
        return {"ok": False, "size_bytes": 0,
                "reason": f"路径不是文件: {path}",
                "evidence": "可能是目录或符号链接异常"}
    size = p.stat().st_size
    if size < min_bytes:
        return {"ok": False, "size_bytes": size,
                "reason": f"文件异常小 ({size}B < {min_bytes}B 阈值)",
                "evidence": "几乎必然是空文件/写入失败/驱动静默降级"}
    return {"ok": True, "size_bytes": size,
            "evidence": f"文件存在且大小正常 ({size}B)"}


# ==================== ZIP 完整性校验 (修 0KB bug 关键) ====================

def verify_zip_integrity(path, *, required_exts: Sequence[str] = (".shp", ".dbf", ".shx"),
                         min_bytes: int = 100) -> dict:
    """
    ZIP 校验: 解压清单必须含核心组件, 且每个核心文件非空.

    ★ 直接对抗"0KB ZIP / 空目录条目"伪装成功:
      若 ZIP 内只有空目录条目而无真实文件, 判定失败.

    入参:
        - path (str|Path): ZIP 文件路径
        - required_exts (Sequence[str]): 必须包含的扩展名, 默认 Shapefile 三件套
        - min_bytes (int): ZIP 整体最小字节数

    出参:
        {"ok": bool, "size_bytes": int, "entries": [...], "missing": [...],
         "reason": str, "evidence": str}
    """
    base = verify_file_exists(path, min_bytes=min_bytes)
    if not base["ok"]:
        return {**base, "entries": [], "missing": list(required_exts)}

    p = Path(path)
    try:
        with zipfile.ZipFile(str(p), "r") as zf:
            # 只看文件条目, 排除目录条目 (以 / 结尾)
            file_entries = [info.filename for info in zf.infolist()
                            if not info.is_dir() and info.file_size > 0]
    except zipfile.BadZipFile as e:
        return {"ok": False, "size_bytes": base["size_bytes"],
                "entries": [], "missing": list(required_exts),
                "reason": f"ZIP 损坏无法读取: {e}",
                "evidence": "文件不是合法 ZIP"}
    except Exception as e:
        return {"ok": False, "size_bytes": base["size_bytes"],
                "entries": [], "missing": list(required_exts),
                "reason": f"ZIP 读取异常: {e}",
                "evidence": str(e)}

    # 检查必含扩展名 (匹配文件名后缀, 不区分大小写)
    lower_entries = [e.lower() for e in file_entries]
    present_exts = []
    for ext in required_exts:
        if any(le.endswith(ext.lower()) for le in lower_entries):
            present_exts.append(ext)
    missing = [ext for ext in required_exts if ext not in present_exts]

    if missing:
        return {"ok": False, "size_bytes": base["size_bytes"],
                "entries": file_entries, "missing": missing,
                "reason": f"ZIP 缺少核心组件: {missing} (当前含: {file_entries})",
                "evidence": "Shapefile 三件套不全, GIS 软件无法打开"}

    return {"ok": True, "size_bytes": base["size_bytes"],
            "entries": file_entries, "missing": [],
            "evidence": f"ZIP 完整, 含 {len(file_entries)} 个文件, 核心组件齐全"}


# ==================== 矢量文件校验 (geopandas 读回) ====================

def verify_vector_features(path, *, min_features: int = 1, min_bytes: int = 100) -> dict:
    """
    矢量文件校验: 用 geopandas 读回, 验证要素数 >= 阈值 且几何有效.

    ★ 用于 GeoJSON / Shapefile / GeoPackage 等矢量产物.

    入参:
        - path (str|Path): 矢量文件路径 (.geojson/.shp/.gpkg)
        - min_features (int): 最少要素数, 默认 1
        - min_bytes (int): 文件最小字节数

    出参:
        {"ok": bool, "size_bytes": int, "feature_count": int,
         "geom_valid": bool, "reason": str, "evidence": str}
    """
    base = verify_file_exists(path, min_bytes=min_bytes)
    if not base["ok"]:
        return {**base, "feature_count": 0, "geom_valid": False}

    try:
        import geopandas as gpd
    except ImportError:
        # geopandas 缺失 → 退化为只做存在性校验, 标注"未深度校验"
        return {"ok": True, "size_bytes": base["size_bytes"],
                "feature_count": -1, "geom_valid": None,
                "evidence": "文件存在 (geopandas 未安装, 未做要素数深度校验)",
                "deep_checked": False}

    try:
        gdf = gpd.read_file(str(path))
    except Exception as e:
        return {"ok": False, "size_bytes": base["size_bytes"],
                "feature_count": 0, "geom_valid": False,
                "reason": f"矢量文件无法被 geopandas 读取: {e}",
                "evidence": "文件可能损坏或格式错误"}

    n = len(gdf)
    if n < min_features:
        return {"ok": False, "size_bytes": base["size_bytes"],
                "feature_count": n, "geom_valid": True,
                "reason": f"要素数为 {n} (< {min_features} 阈值), 矢量为空",
                "evidence": "无有效要素"}

    try:
        geom_valid = bool(gdf.geometry.is_valid.all()) if "geometry" in gdf.columns else True
    except Exception:
        geom_valid = None

    return {"ok": True, "size_bytes": base["size_bytes"],
            "feature_count": n, "geom_valid": geom_valid,
            "evidence": f"矢量可读, {n} 个要素, 几何{'有效' if geom_valid else '存在无效'}",
            "deep_checked": True}


# ==================== PDF / Word 页数校验 ====================

def verify_pdf_pages(path, *, min_pages: int = 1, min_bytes: int = 100) -> dict:
    """
    PDF/Word 校验: 页数 >= 阈值.

    ★ 用于 generate_monitor_report 等 PDF/Word 产物.
    PDF 用 PyPDF2/pypdf 读; docx 用 python-docx 读段落数近似页数.

    入参:
        - path (str|Path): PDF 或 Word 文件路径
        - min_pages (int): 最少页数, 默认 1
        - min_bytes (int): 文件最小字节数

    出参:
        {"ok": bool, "size_bytes": int, "pages": int,
         "reason": str, "evidence": str}
    """
    base = verify_file_exists(path, min_bytes=min_bytes)
    if not base["ok"]:
        return {**base, "pages": 0}

    p = Path(path)
    ext = p.suffix.lower()
    pages = 0

    if ext == ".pdf":
        # 优先 pypdf (新), 回退 PyPDF2 (旧)
        reader = None
        for mod_name in ("pypdf", "PyPDF2"):
            try:
                mod = __import__(mod_name)
                reader = mod.PdfReader(str(p))
                pages = len(reader.pages)
                break
            except ImportError:
                continue
            except Exception as e:
                return {"ok": False, "size_bytes": base["size_bytes"], "pages": 0,
                        "reason": f"PDF 读取失败: {e}",
                        "evidence": "文件可能损坏或非标准 PDF"}
        if reader is None:
            # 两个库都没装 → 退化为存在性校验
            return {"ok": True, "size_bytes": base["size_bytes"], "pages": -1,
                    "evidence": "文件存在 (pypdf/PyPDF2 未安装, 未做页数深度校验)",
                    "deep_checked": False}

    elif ext in (".docx", ".doc"):
        try:
            from docx import Document
            doc = Document(str(p))
            # docx 无原生页数概念, 用段落数近似 (每页约 30~50 段)
            paragraphs = len(doc.paragraphs)
            pages = max(1, paragraphs // 30)
        except ImportError:
            return {"ok": True, "size_bytes": base["size_bytes"], "pages": -1,
                    "evidence": "文件存在 (python-docx 未安装, 未做页数深度校验)",
                    "deep_checked": False}
        except Exception as e:
            return {"ok": False, "size_bytes": base["size_bytes"], "pages": 0,
                    "reason": f"Word 读取失败: {e}",
                    "evidence": "文件可能损坏"}
    else:
        return {"ok": True, "size_bytes": base["size_bytes"], "pages": -1,
                "evidence": f"不支持的扩展名 {ext}, 仅做存在性校验",
                "deep_checked": False}

    if pages < min_pages:
        return {"ok": False, "size_bytes": base["size_bytes"], "pages": pages,
                "reason": f"页数为 {pages} (< {min_pages} 阈值), 文档为空",
                "evidence": "无有效内容"}

    return {"ok": True, "size_bytes": base["size_bytes"], "pages": pages,
            "evidence": f"文档可读, {pages} 页", "deep_checked": True}


# ==================== Excel 校验 ====================

def verify_excel_rows(path, *, min_rows: int = 1, min_bytes: int = 100) -> dict:
    """
    Excel 校验: openpyxl 读回, 验证数据行数 >= 阈值.

    入参:
        - path (str|Path): Excel 文件路径 (.xlsx/.xls)
        - min_rows (int): 最少数据行数 (不含表头), 默认 1
        - min_bytes (int): 文件最小字节数

    出参:
        {"ok": bool, "size_bytes": int, "rows": int,
         "reason": str, "evidence": str}
    """
    base = verify_file_exists(path, min_bytes=min_bytes)
    if not base["ok"]:
        return {**base, "rows": 0}

    try:
        from openpyxl import load_workbook
    except ImportError:
        return {"ok": True, "size_bytes": base["size_bytes"], "rows": -1,
                "evidence": "文件存在 (openpyxl 未安装, 未做行数深度校验)",
                "deep_checked": False}

    try:
        wb = load_workbook(str(path), read_only=True)
        ws = wb.active
        # max_row 含表头, 数据行 = max_row - 1
        rows = max(0, ws.max_row - 1)
        wb.close()
    except Exception as e:
        return {"ok": False, "size_bytes": base["size_bytes"], "rows": 0,
                "reason": f"Excel 读取失败: {e}",
                "evidence": "文件可能损坏"}

    if rows < min_rows:
        return {"ok": False, "size_bytes": base["size_bytes"], "rows": rows,
                "reason": f"数据行数为 {rows} (< {min_rows} 阈值), 表格为空",
                "evidence": "无有效数据"}

    return {"ok": True, "size_bytes": base["size_bytes"], "rows": rows,
            "evidence": f"Excel 可读, {rows} 行数据", "deep_checked": True}


# ==================== 统一入口: 按扩展名自动路由 ====================

def verify_by_path(path, *, min_bytes: int = 100) -> dict:
    """
    按文件扩展名自动路由到对应校验器, 统一入口.

    ★ 工具层/传输层兜底都用这个函数, 不用关心产物类型.

    路由规则:
        .zip                          → verify_zip_integrity (Shapefile 三件套)
        .geojson/.json/.shp/.gpkg     → verify_vector_features (要素数 + 几何)
        .pdf                          → verify_pdf_pages (页数)
        .docx/.doc                    → verify_pdf_pages (段落数近似)
        .xlsx/.xls                    → verify_excel_rows (数据行数)
        .png/.jpg/.tif/.tiff/其他     → verify_file_exists (基础存在性)

    出参:
        {"ok": bool, "size_bytes": int, "checker": str(用了哪个校验器),
         "reason": str(失败时), "evidence": str, 其他类型相关字段...}
    """
    p = Path(path)
    ext = p.suffix.lower()

    if ext == ".zip":
        result = verify_zip_integrity(path, min_bytes=min_bytes)
        result["checker"] = "zip_integrity"
    elif ext in (".geojson", ".json", ".shp", ".gpkg"):
        result = verify_vector_features(path, min_bytes=min_bytes)
        result["checker"] = "vector_features"
    elif ext in (".pdf", ".docx", ".doc"):
        result = verify_pdf_pages(path, min_bytes=min_bytes)
        result["checker"] = "pdf_pages"
    elif ext in (".xlsx", ".xls"):
        result = verify_excel_rows(path, min_bytes=min_bytes)
        result["checker"] = "excel_rows"
    else:
        # 图片/GeoTIFF/其他: 只做基础存在性校验
        result = verify_file_exists(path, min_bytes=min_bytes)
        result["checker"] = "file_exists"

    return result


# ==================== 工程化 S8: 双时相空间一致性校验 ====================

def _read_raster_geo_metadata(path):
    """
    用 rasterio 读影像的空间元数据 (CRS/分辨率/范围), 失败返回 None。
    入参: path — 影像路径。
    出参: {crs, width, height, bounds(minx,miny,maxx,maxy), res_x, res_y} 或 None。
    方法: rasterio 缺失或文件无法读取时返回 None (降级, 不报错)。
    """
    try:
        import rasterio
        from affine import Affine
    except ImportError:
        return None
    try:
        with rasterio.open(str(path)) as ds:
            crs = str(ds.crs) if ds.crs else None
            bounds = ds.bounds  # (left, bottom, right, top)
            return {
                "crs": crs,
                "width": ds.width,
                "height": ds.height,
                "bounds": (bounds.left, bounds.bottom, bounds.right, bounds.top),
                "res_x": abs(ds.transform.a),
                "res_y": abs(ds.transform.e),
            }
    except Exception:
        return None


def _bounds_overlap_ratio(a, b):
    """
    计算两个 (minx,miny,maxx,maxy) 范围框的交集占 a 的面积比。
    入参: a, b — (minx, miny, maxx, maxy)。
    出参: 0.0~1.0, 0 表示无重叠。
    """
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    if area_a <= 0:
        return 0.0
    return (ix * iy) / area_a


def verify_spatial_consistency(t1_path, t2_path, *, res_tolerance: float = 0.1) -> dict:
    """
    双时相空间一致性校验: CRS / 分辨率 / 空间范围重叠。
    入参:
      - t1_path / t2_path: 两期影像路径。
      - res_tolerance: 分辨率容差比例 (默认 0.1 = 10%), 相对差超此值判 warning。
    方法:
      - rasterio 读两期元数据, 任一缺失返回降级 (不报错)。
      - CRS 不一致 → warning (变化检测会先 resize, 但语义上两期不同坐标系)。
      - 分辨率相对差 > res_tolerance → warning (边缘误检风险)。
      - 范围重叠率 < 0.5 → warning (两期几乎不重叠, 变化检测无意义)。
      - 全部一致 → ok=True。
    出参:
      {ok, crs_consistent, res_consistent, overlap_ratio, warnings: [...], evidence: str}
    """
    meta1 = _read_raster_geo_metadata(t1_path)
    meta2 = _read_raster_geo_metadata(t2_path)
    if not meta1 or not meta2:
        return {
            "ok": True,
            "deep_checked": False,
            "evidence": "rasterio 缺失或影像无法读取, 跳过空间一致性深度校验",
        }

    warnings = []
    crs_consistent = bool(meta1["crs"] and meta2["crs"] and meta1["crs"] == meta2["crs"])
    if not crs_consistent:
        warnings.append(f"CRS 不一致: T1={meta1['crs']}, T2={meta2['crs']}")

    rx1, ry1 = meta1["res_x"], meta1["res_y"]
    rx2, ry2 = meta2["res_x"], meta2["res_y"]
    res_diff = abs(rx1 - rx2) / max(rx1, rx2, 1e-9)
    res_consistent = res_diff <= res_tolerance
    if not res_consistent:
        warnings.append(
            f"分辨率差异 {res_diff:.1%} 超过 {res_tolerance:.0%} 容差 "
            f"(T1={rx1:.2f}m, T2={rx2:.2f}m), 边缘误检风险"
        )

    overlap = _bounds_overlap_ratio(meta1["bounds"], meta2["bounds"])
    if overlap < 0.5:
        warnings.append(f"空间范围重叠率仅 {overlap:.1%} (<50%), 两期可能非同区域")

    ok = crs_consistent and res_consistent and overlap >= 0.5
    evidence = (
        f"CRS{'一致' if crs_consistent else '不一致'}; "
        f"分辨率差 {res_diff:.1%}; 范围重叠 {overlap:.1%}"
    )
    return {
        "ok": ok,
        "deep_checked": True,
        "crs_consistent": crs_consistent,
        "res_consistent": res_consistent,
        "overlap_ratio": round(overlap, 3),
        "warnings": warnings,
        "evidence": evidence,
    }


# ==================== 工程化 S9: 矢量几何有效性 + 最小图斑校验 ====================

def verify_geometry_validity(
    path, *, min_area_m2: float = None, min_features: int = 1, min_bytes: int = 100
) -> dict:
    """
    矢量几何深度校验: 拓扑有效性 + 最小上图面积过滤。
    入参:
      - path: 矢量文件路径 (.geojson/.shp/.gpkg)。
      - min_area_m2: 最小上图面积阈值 (m²), 小于此值的图斑计为碎屑。
      - min_features: 最少有效要素数。
    方法:
      - geopandas 缺失时降级为 verify_vector_features (只做存在性)。
      - 读回后检查: ① 无效几何数 (自相交/无效环) ② 小于 min_area_m2 的碎屑图斑数。
      - 无效几何存在 → ok=False (拓扑错误会导致 GIS 分析异常)。
      - 碎屑图斑占比高 → warning (但不判失败, 因碎屑可后处理过滤)。
    出参:
      {ok, size_bytes, feature_count, invalid_geom_count, sliver_count,
       reason, evidence, deep_checked}
    """
    base = verify_file_exists(path, min_bytes=min_bytes)
    if not base["ok"]:
        return {**base, "feature_count": 0, "invalid_geom_count": 0, "sliver_count": 0}

    try:
        import geopandas as gpd
    except ImportError:
        return {
            "ok": True, "size_bytes": base["size_bytes"],
            "feature_count": -1, "invalid_geom_count": 0, "sliver_count": 0,
            "deep_checked": False,
            "evidence": "文件存在 (geopandas 未安装, 未做几何深度校验)",
        }

    try:
        gdf = gpd.read_file(str(path))
    except Exception as e:
        return {"ok": False, "size_bytes": base["size_bytes"],
                "feature_count": 0, "invalid_geom_count": 0, "sliver_count": 0,
                "reason": f"矢量文件无法读取: {e}", "evidence": "文件可能损坏"}

    n = len(gdf)
    if n < min_features:
        return {"ok": False, "size_bytes": base["size_bytes"],
                "feature_count": n, "invalid_geom_count": 0, "sliver_count": 0,
                "reason": f"要素数 {n} < {min_features}", "evidence": "无有效要素"}

    # 拓扑有效性
    invalid_count = 0
    if "geometry" in gdf.columns:
        try:
            invalid_count = int((~gdf.geometry.is_valid).sum())
        except Exception:
            invalid_count = 0

    # 碎屑图斑 (面积 < min_area_m2)
    sliver_count = 0
    if min_area_m2 and "geometry" in gdf.columns:
        try:
            areas = gdf.geometry.area
            sliver_count = int((areas < min_area_m2).sum())
        except Exception:
            sliver_count = 0

    evidence_parts = [f"{n} 个要素"]
    if invalid_count:
        evidence_parts.append(f"{invalid_count} 个无效几何")
    if sliver_count:
        evidence_parts.append(f"{sliver_count} 个碎屑(<{min_area_m2}m²)")

    if invalid_count > 0:
        return {
            "ok": False, "size_bytes": base["size_bytes"],
            "feature_count": n, "invalid_geom_count": invalid_count,
            "sliver_count": sliver_count, "deep_checked": True,
            "reason": f"{invalid_count} 个几何无效 (自相交/无效环)",
            "evidence": "; ".join(evidence_parts),
        }

    return {
        "ok": True, "size_bytes": base["size_bytes"],
        "feature_count": n, "invalid_geom_count": 0,
        "sliver_count": sliver_count, "deep_checked": True,
        "evidence": "; ".join(evidence_parts),
    }
