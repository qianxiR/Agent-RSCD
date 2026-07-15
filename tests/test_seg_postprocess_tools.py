# -*- coding: utf-8 -*-
"""
分割检测结果产物链路 — 验证脚本 (runner 层)

★ 验证目标: 确认 runner 层新增的"5 类产物"全部正常落地:
    1. 彩色掩码 PNG            (output_path, 原有)
    2. 掩码叠加原图 PNG        (overlay_path, 原有 _build_overlay_image)
    3. GeoJSON                 (vector_path, 原有 _vectorize_mask_to_geojson)
    4. Shapefile               (shp_path, ★ 新增 同源导出)
    5. 边缘叠加 GeoTIFF        (edge_tif_path, ★ 新增 _build_edge_overlay_geotiff)

★ 测试策略: 不加载 SAM 模型 (太慢), 而是用现成的测试掩码构造一个合成 seg 数组
  (类别索引图), 直接调用 runner 的两个新辅助函数 + 现有 _build_overlay_image,
  断言 5 类产物全部生成且带正确坐标系。

★ 测试输入:
    掩码: tests/data/8fb75bc3_1782029898655_r000_c006_建筑_seg_t1cd.png
    原图: tests/data/r000_c006_t1.tif (Web Mercator EPSG:3857, 提供 CRS/transform)

依赖: sam3 环境
运行:
    D:/anaconda3/envs/sam3/python.exe tests/test_seg_postprocess_tools.py
"""
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DIR = ROOT / "tests" / "data"
MASK = str(TEST_DIR / "8fb75bc3_1782029898655_r000_c006_建筑_seg_t1cd.png")
SRC_IMG = str(TEST_DIR / "r000_c006_t1.tif")
OUT_DIR = TEST_DIR  # 最终产物落盘目录


def hr(title: str = ""):
    print("=" * 70)
    if title:
        print(title)
        print("=" * 70)


def main():
    import numpy as np
    from PIL import Image

    hr("分割检测结果产物链路 验证 (runner 层, 不加载 SAM)")
    print(f"掩码: {Path(MASK).name}")
    print(f"原图: {Path(SRC_IMG).name} (Web Mercator)")
    print(f"输出: {OUT_DIR}")

    # ---- 依赖检查 ----
    print("\n[0/5] 依赖检查...")
    from backend.data.vector import vectorize_available
    from backend.data.mask import region_analysis_available
    print(f"  vectorize_available: {vectorize_available()}")
    print(f"  region_analysis_available: {region_analysis_available()}")
    assert vectorize_available(), "geopandas/rasterio 缺失"

    # ---- 构造合成 seg (类别索引图): 前景=1(建筑), 背景=0 ----
    mask_arr = np.array(Image.open(MASK).convert("L"))
    seg = (mask_arr > 10).astype(np.int64)  # (H,W) 0/1
    print(f"  合成 seg: shape={seg.shape}, class1(建筑)像素={int((seg == 1).sum())}")

    # 模拟 runner 的彩色 mask (palette 上色)
    color_mask = np.zeros((*seg.shape, 3), dtype=np.uint8)
    color_mask[seg == 1] = [255, 0, 0]   # 建筑=红
    color_mask[seg == 0] = [0, 0, 0]     # 背景=黑

    from backend.model.SamSeg import runner

    # 临时工作目录 (模拟 runner 落盘位置)
    work = ROOT / "tests" / "_tmp_seg_test"
    work.mkdir(parents=True, exist_ok=True)
    out_png = str(work / "seg_mask.png")
    Image.fromarray(color_mask).save(out_png)

    class_lines = ["background,背景", "building,建筑"]
    produced = {}

    # ============================================================
    # 产物 1: 彩色掩码 PNG (已生成)
    # ============================================================
    hr("[1/5] 彩色掩码 PNG")
    assert Path(out_png).is_file() and Path(out_png).stat().st_size > 0
    dst = OUT_DIR / "seg_test_mask.png"
    shutil.copyfile(out_png, dst)
    produced["掩码PNG"] = dst
    print(f"  ✅ {dst.name} ({dst.stat().st_size} bytes)")

    # ============================================================
    # 产物 2: 掩码叠加原图 PNG (_build_overlay_image)
    # ============================================================
    hr("[2/5] 掩码叠加原图 (_build_overlay_image)")
    overlay_path = str(work / "seg_overlay.png")
    ov = runner._build_overlay_image(SRC_IMG, color_mask, overlay_path)
    assert ov and Path(ov).is_file(), "叠加图未生成"
    dst = OUT_DIR / "seg_test_overlay.png"
    shutil.copyfile(ov, dst)
    produced["叠加图PNG"] = dst
    print(f"  ✅ {dst.name} ({dst.stat().st_size} bytes)")

    # ============================================================
    # 产物 3 + 4: GeoJSON + Shapefile (_vectorize_mask_to_geojson, ★ 新增 shp 同源导出)
    # ============================================================
    hr("[3/5] GeoJSON + [4/5] Shapefile (_vectorize_mask_to_geojson)")
    geojson_path = str(work / "seg_vector.geojson")
    vec = runner._vectorize_mask_to_geojson(
        seg, SRC_IMG, class_lines, geojson_path,
        min_area_m2=0.0,
        output_shp_dir=str(work),         # ★ 新参数: 触发 shp 同源导出
        shp_stem="seg_test",              # ★ ASCII stem
    )
    assert vec and vec.get("vector_path"), "矢量化未产出 GeoJSON"
    # GeoJSON
    assert Path(vec["vector_path"]).is_file()
    dst_gj = OUT_DIR / "seg_test_vector.geojson"
    shutil.copyfile(vec["vector_path"], dst_gj)
    produced["GeoJSON"] = dst_gj
    vs = vec["vector_stats"]
    print(f"  ✅ GeoJSON: {dst_gj.name} ({vs['total_features']} 要素, CRS={'有' if vs['has_crs'] else '无'})")
    # Shapefile (★ 新增)
    assert vec.get("shp_path") and Path(vec["shp_path"]).is_file(), "Shapefile 未产出"
    assert vec.get("shp_zip_path") and Path(vec["shp_zip_path"]).is_file(), "Shapefile ZIP 未产出"
    shp_dir = Path(vec["shp_path"]).parent
    dst_shp_dir = OUT_DIR / "seg_test_shp"
    if dst_shp_dir.exists():
        shutil.rmtree(dst_shp_dir)
    shutil.copytree(shp_dir, dst_shp_dir)
    dst_zip = OUT_DIR / "seg_test_shp.zip"
    shutil.copyfile(vec["shp_zip_path"], dst_zip)
    produced["Shapefile"] = dst_shp_dir
    produced["ShapefileZIP"] = dst_zip
    print(f"  ✅ Shapefile: {dst_shp_dir.name}/ ({len(vec['shp_components'])} 个组件)")
    print(f"     组件: {vec['shp_components']}")
    print(f"  ✅ ZIP: {dst_zip.name} ({dst_zip.stat().st_size} bytes)")

    # ============================================================
    # 产物 5: 边缘叠加 GeoTIFF (_build_edge_overlay_geotiff, ★ 新增)
    # ============================================================
    hr("[5/5] 边缘叠加 GeoTIFF (_build_edge_overlay_geotiff)")
    edge_tif = str(work / "seg_edge.tif")
    edge_out = runner._build_edge_overlay_geotiff(seg, SRC_IMG, edge_tif, method="distance")
    assert edge_out and Path(edge_out).is_file(), "边缘叠加 TIF 未生成"
    dst_edge = OUT_DIR / "seg_test_edge.tif"
    shutil.copyfile(edge_out, dst_edge)
    produced["边缘TIF"] = dst_edge
    print(f"  ✅ {dst_edge.name} ({dst_edge.stat().st_size} bytes)")

    # ---- 深度校验: GeoTIFF/SHP 带正确坐标系 ----
    hr("深度校验: 坐标系 + 几何有效性")
    import rasterio
    import geopandas as gpd

    with rasterio.open(dst_edge) as s:
        assert s.crs is not None, "边缘 TIF 丢了 CRS!"
        print(f"  边缘TIF: crs={s.crs.to_epsg() or s.crs}, size={s.width}x{s.height}, bands={s.count}")

    gdf_gj = gpd.read_file(dst_gj)
    print(f"  GeoJSON: 要素={len(gdf_gj)}, CRS={gdf_gj.crs}, 几何有效={bool(gdf_gj.geometry.is_valid.all())}")
    gdf_shp = gpd.read_file(dst_shp_dir / Path(vec['shp_path']).name)
    assert gdf_shp.crs is not None, "SHP 丢了 CRS!"
    print(f"  Shapefile: 要素={len(gdf_shp)}, CRS={gdf_shp.crs}, 几何有效={bool(gdf_shp.geometry.is_valid.all())}")

    # ---- 清理临时目录 ----
    shutil.rmtree(str(work), ignore_errors=True)

    # ---- 汇总 ----
    hr("完成 — 5 类产物清单 (tests/data)")
    for name, p in produced.items():
        if isinstance(p, Path) and p.exists():
            if p.is_dir():
                n = sum(1 for _ in p.iterdir())
                print(f"  ✅ {name}: {p.name}/ ({n} 文件)")
            else:
                print(f"  ✅ {name}: {p.name} ({p.stat().st_size} bytes)")
        else:
            print(f"  ❌ {name}: 缺失")
    print(f"\n输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
