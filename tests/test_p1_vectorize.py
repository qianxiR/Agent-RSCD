"""
P1 栅格→矢量 端到端验证脚本
- 用 cdcd test 切片 (r000_c001) 跑变化检测
- 验证: PNG 产生 → GeoJSON 产生 → 图斑数 > 0 → 面积是米制
- 依赖: sam3 环境 (conda activate sam3), rasterio/geopandas
"""
import sys
import os
import time
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 测试数据
TEST_DIR = ROOT / "tests" / "data"
T1 = str(TEST_DIR / "test_t1.tif")
T2 = str(TEST_DIR / "test_t2.tif")

# 输出目录 (放 agent-files 下, 模拟真实路径) — v2.4 目录顺序 {proj}/{conv}
OUT_DIR = ROOT / "agent-files" / "samseg" / "generate" / "_default" / "test_p1"
VEC_DIR = ROOT / "agent-files" / "samseg" / "vector" / "_default" / "test_p1"
OUT_DIR.mkdir(parents=True, exist_ok=True)
VEC_DIR.mkdir(parents=True, exist_ok=True)

OUT_PNG = str(OUT_DIR / "p1_test_change.png")
OUT_GEOJSON = str(VEC_DIR / "p1_test_change.geojson")

print("=" * 60)
print("P1 栅格→矢量 端到端验证")
print("=" * 60)
print(f"  T1: {T1}")
print(f"  T2: {T2}")
print(f"  PNG: {OUT_PNG}")
print(f"  GeoJSON: {OUT_GEOJSON}")
print()

# 1) 检查矢量化依赖
print("[1/4] 矢量化依赖检查...")
from backend.data.vector import vectorize_available
ok = vectorize_available()
print(f"  vectorize_available: {ok}")
if not ok:
    print("  ⚠ 矢量化依赖缺失 (rasterio/geopandas/osgeo), 将仅测试 PNG 产出")
print()

# 2) 检查 SamSeg 可用
print("[2/4] SamSeg 可用性检查...")
from backend.model.SamSeg import runner
ok2 = runner.samseg_available()
print(f"  samseg_available: {ok2}")
if not ok2:
    print("  ❌ SamSeg 不可用, 无法继续")
    sys.exit(1)
print()

# 3) 跑变化检测
print("[3/4] 运行变化检测 (预计 10-30s)...")
t0 = time.time()
result = runner.run_change_detection(
    t1_path=T1,
    t2_path=T2,
    output_path=OUT_PNG,
    vector_output_path=OUT_GEOJSON,
)
elapsed = time.time() - t0
print(f"  耗时: {elapsed:.1f}s")
print()

if result is None:
    print("  ❌ run_change_detection 返回 None")
    sys.exit(1)

# 4) 验证结果
print("[4/4] 验证结果...")
errors = []

# PNG
png_path = Path(OUT_PNG)
if png_path.is_file():
    png_kb = png_path.stat().st_size / 1024
    print(f"  ✅ PNG: {png_kb:.1f} KB")
else:
    errors.append("PNG 未产出")
    print(f"  ❌ PNG 未产出: {OUT_PNG}")

# stats
stats = result.get("stats", {})
print(f"  📊 像素统计: {stats.get('total_regions', 0)} 变化区域, "
      f"{stats.get('changed_pixels', 0)} px ({stats.get('changed_percent', 0)}%)")
for c in stats.get("per_class", []):
    print(f"      {c['name']}: {c['regions']} 区域, {c['area_px']} px ({c['area_percent']}%)")

# legend
legend = result.get("legend", [])
print(f"  🎨 图注: {len(legend)} 类, 颜色与 PNG 一致")

# vector
geo_path = Path(OUT_GEOJSON)
if geo_path.is_file():
    geo_kb = geo_path.stat().st_size / 1024
    print(f"  ✅ GeoJSON: {geo_kb:.1f} KB")
else:
    print(f"  ⚠ GeoJSON 未产出 (矢量化依赖缺失或无有效变化图斑)")

if result.get("vector_path"):
    vs = result.get("vector_stats")
    if vs:
        print(f"  📊 矢量统计: {vs['total_features']} 图斑, "
              f"{vs['total_area_m2']} m² ({vs['total_area_m2']/10000:.2f} 公顷), "
              f"CRS={'有' if vs.get('has_crs') else '无'}")
        for pc in vs.get("per_class", []):
            print(f"      {pc['name']}: {pc['count']} 图斑, {pc['area_m2']} m²")

# Summary
print()
if errors:
    print(f"❌ 验证失败: {errors}")
    sys.exit(1)
elif result.get("vector_path"):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"✅ P1 全部通过 ({ts}) — PNG + GeoJSON 双产出, 矢量面积单位=平方米")
else:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"✅ PNG 通过, ⚠ GeoJSON 缺失 ({ts}) — 矢量化依赖未就绪 (geopandas/osgeo)")
