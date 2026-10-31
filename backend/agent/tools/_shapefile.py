"""
Shapefile 导出工具层 (Model 层 / 工具箱基础设施)

★ C3 归一改造 (v2.6):
  历史上 mask_tools / report_tools / vector_tools 各有一份 _ascii_safe 副本,
  且 SHP 写盘逻辑 (gdf.to_file + .shp 落地校验) 在 4+ 处重复。
  本模块作为 tools 层的统一薄包装, 复用底层 SamSeg.geoio 的能力:

  - ascii_stem: 复用 geoio.ascii_stem (全仓统一的 ASCII 文件名清洗)
  - export_shp_from_gdf: re-export geoio.export_shp_from_gdf (唯一 SHP 写盘入口)
  - zip_shp_dir: SHP 目录打包成 ZIP (mask/report 的 ZIP 需求)

  层次关系: geoio.py (底层, SamSeg 链路用) ← _shapefile.py (tools 层薄包装) ← 各工具
  这样 tools 不直接 import SamSeg.geoio, 保持层次清晰。
"""
import os
import zipfile
from pathlib import Path
from typing import Optional, Dict, Any

# 从底层 re-export (tools 层统一入口, 调用方只依赖本模块)
from backend.model.SamSeg.geoio import ascii_stem, export_shp_from_gdf


def zip_shp_dir(shp_dir: str, zip_path: str) -> Optional[str]:
    """
    把 Shapefile 多文件目录打包成 ZIP (供下载/GIS 交换)。

    入参:
      - shp_dir: SHP 目录 (含 .shp/.dbf/.shx/.prj/.cpg 等)
      - zip_path: 输出 ZIP 路径
    出参: zip_path 或 None (目录不存在/为空)
    """
    shp_dir_p = Path(shp_dir)
    if not shp_dir_p.is_dir():
        return None
    files = sorted(f for f in shp_dir_p.iterdir() if f.is_file())
    if not files:
        return None
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                # arcname 用文件名本身 (不带目录前缀, 解压后平铺)
                zf.write(str(f), f.name)
        # 校验 ZIP 落地
        if Path(zip_path).is_file() and Path(zip_path).stat().st_size > 0:
            return zip_path
        return None
    except Exception:
        return None


def write_shp_and_zip(
    gdf,
    output_dir: str,
    stem: str,
    do_zip: bool = False,
    zip_path: str = None,
) -> Dict[str, Any]:
    """
    一站式: GDF → 写 SHP 目录 → (可选) 打包 ZIP。
    供 mask_tools.export_mask_to_shapefile / report_tools.export_change_vector 复用。

    入参:
      - gdf: GeoDataFrame
      - output_dir: SHP 目录输出位置
      - stem: 文件名 (自动 ASCII 化)
      - do_zip: 是否打包 ZIP
      - zip_path: ZIP 输出路径 (do_zip=True 时必填)
    出参: {shp_path, shp_dir, shp_components, zip_path?(可选)} 或 {} (失败)
    """
    result = export_shp_from_gdf(gdf, output_dir, ascii_stem(stem))
    if not result:
        return {}
    out: Dict[str, Any] = {
        "shp_path": result["shp_path"],
        "shp_dir": result["shp_zip_path"],  # 实为目录路径
        "shp_components": result["shp_components"],
    }
    if do_zip and zip_path:
        z = zip_shp_dir(result["shp_zip_path"], zip_path)
        if z:
            out["zip_path"] = z
    return out
