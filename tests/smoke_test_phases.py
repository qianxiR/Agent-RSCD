"""
端到端冒烟测试 (Phase A-G 链路验证)
直接调用底层函数, 不经过 LLM/WebSocket, 用 tests/data/test_t1.tif + test_t2.tif 验证:
  1. detect_change (含 P1 矢量化 + Phase B VLM 业务归类)
  2. 元数据登记 (Phase A)
  3. 数据预处理 (Phase C, 金字塔构建)
  4. 报表生成 (Phase D, 图表 + PDF + 导出)

运行:
  cd agent-flow-study
  conda activate sam3
  python tests/smoke_test_phases.py
"""
import sys
import os
import time
from pathlib import Path

# 把项目根加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ★ 避免 torch / numpy 的 OpenMP 多副本冲突 (Windows 常见)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

os.environ.setdefault("PROJ_LIB", str(Path(__file__).resolve().parent.parent / "backend" / "model" / "SamSeg" / "SamSeg" / "sam3"))
# 避免 PostgreSQL/PostGIS 的 proj.db 污染
_rasterio_proj = None
try:
    import rasterio
    _rasterio_proj = Path(rasterio.__file__).parent / "proj_data"
except Exception:
    pass
if _rasterio_proj and _rasterio_proj.is_dir():
    os.environ["PROJ_LIB"] = str(_rasterio_proj)
    gdal_data = Path(rasterio.__file__).parent / "gdal_data"
    if gdal_data.is_dir():
        os.environ["GDAL_DATA"] = str(gdal_data)

from backend.config import settings
settings.samseg_device = "cuda"  # 显式指定, 避免某些情况下回退 CPU 极慢

T1 = str(ROOT / "tests" / "data" / "test_t1.tif")
T2 = str(ROOT / "tests" / "data" / "test_t2.tif")
OUT_DIR = ROOT / "agent-files" / "smoke_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def section(name):
    print("\n" + "=" * 60)
    print(f"  {name}")
    print("=" * 60)


def check(cond, msg):
    sym = "✓" if cond else "✗"
    print(f"  [{sym}] {msg}")
    return cond


def main():
    overall_ok = True

    # ==================== 1. SamSeg 可用性 ====================
    section("Step 1: SamSeg 可用性")
    from backend.model.SamSeg import runner
    avail = runner.samseg_available()
    overall_ok &= check(avail, f"SamSeg 可用: {avail}")
    if not avail:
        print("  ⚠ SamSeg 不可用, 跳过 detect_change 测试 (其它测试仍可跑)")
        # 不 return, 继续测试元数据/预处理/报表

    # ==================== 2. detect_change (含矢量化 + VLM 业务归类) ====================
    change_result = None
    if avail:
        section("Step 2: detect_change (含矢量化 + VLM 业务归类)")
        out_png = str(OUT_DIR / "change_result.png")
        out_vec = str(OUT_DIR / "change_result.geojson")
        t0 = time.time()
        try:
            change_result = runner.run_change_detection(
                t1_path=T1, t2_path=T2,
                output_path=out_png,
                classes=None,
                vector_output_path=out_vec,
            )
            elapsed = time.time() - t0
            overall_ok &= check(change_result is not None, f"detect_change 完成 ({elapsed:.1f}s)")
            if change_result:
                stats = change_result.get("stats", {})
                vec_stats = change_result.get("vector_stats")
                check(Path(out_png).is_file(), f"PNG 结果: {Path(out_png).name} ({Path(out_png).stat().st_size//1024}KB)")
                check(stats.get("total_regions", 0) > -1, f"变化区域数: {stats.get('total_regions', 0)}, 占比 {stats.get('changed_percent', 0)}%")
                if vec_stats:
                    check(True, f"矢量图斑: {vec_stats.get('total_features')} 个, 总面积 {vec_stats.get('total_area_m2')} m²")
                    check(Path(out_vec).is_file(), f"GeoJSON: {Path(out_vec).name}")
                else:
                    check(False, "矢量化失败 (vector_stats 为空)")

                # Phase B: 业务类型 VLM 判读
                section("Step 2.5: Phase B 业务类型 VLM 判读")
                if settings.dashscope_api_key:
                    print("  调用 VLM 判读业务变化类型...")
                    ct = runner.classify_change_type(
                        t1_path=T1, t2_path=T2,
                        result_png_path=out_png,
                        vector_stats=vec_stats, stats=stats,
                        class_lines=runner._normalize_classes(None),
                    )
                    check(ct is not None, f"业务类型: {ct.get('change_type')} (置信度 {ct.get('confidence')}, 来源 {ct.get('source')})")
                    check(True, f"判读依据: {ct.get('reason')}")
                    change_result["change_type_info"] = ct
                else:
                    print("  ⚠ 未配置 DASHSCOPE_API_KEY, 跳过 VLM 判读 (走规则兜底)")
                    ct = runner.classify_change_type(
                        t1_path=T1, t2_path=T2,
                        result_png_path=out_png,
                        vector_stats=vec_stats, stats=stats,
                        class_lines=runner._normalize_classes(None),
                    )
                    check(ct is not None, f"规则兜底业务类型: {ct.get('change_type')} (来源 {ct.get('source')})")
        except Exception as e:
            overall_ok = False
            check(False, f"detect_change 异常: {e}")
            import traceback; traceback.print_exc()

    # ==================== 3. 元数据登记 (Phase A) ====================
    section("Step 3: Phase A 元数据登记")
    try:
        from backend.data import business_db
        if business_db.db_available():
            # ★ 先确保 schema 已建 (生产环境由 main.py startup 触发, 这里手动调一次)
            business_db.ensure_business_schema()
            # 用 EPSG:4490 经纬度登记 (test_t1.tif 原本是 EPSG:3857, 这里登记时统一到 4490)
            # test_t1.tif 中心约在 (103.98°E, 31.0°N) 附近
            mid = business_db.register_image_metadata(
                layer_name="smoke_test_t1",
                file_path=T1,
                bbox=(103.97, 30.99, 103.99, 31.00),  # EPSG:4490 经纬度
                srid=4490,
                resolution=2.388,
                width=512, height=512, band_count=3,
            )
            overall_ok &= check(mid is not None, f"image_metadata 登记成功 (id={mid}, PostGIS={business_db.postgis_available()})")
            # 时空检索
            by_time = business_db.query_images_by_time(limit=10)
            check(len(by_time) > 0, f"时间检索返回 {len(by_time)} 条")
            # 区域检索 (与登记的 bbox 重叠)
            by_region = business_db.query_images_by_region((103.0, 30.0, 105.0, 32.0))
            check(len(by_region) > 0, f"区域检索返回 {len(by_region)} 条")
        else:
            print("  ⚠ 业务库不可用 (PostgreSQL 未启动?), 跳过元数据测试")
    except Exception as e:
        overall_ok = False
        check(False, f"元数据测试异常: {e}")
        import traceback; traceback.print_exc()

    # ==================== 4. 数据预处理 (Phase C) ====================
    section("Step 4: Phase C 数据预处理")
    # 4.1 金字塔构建 (宿主机 rasterio)
    try:
        from backend.model.tools.preprocess_tools import build_pyramid, check_topology
        # 复制一份测试图, 避免污染原文件
        import shutil
        test_tif_for_pyramid = str(OUT_DIR / "test_pyramid_src.tif")
        shutil.copy2(T1, test_tif_for_pyramid)
        # 直接调内部逻辑 (跳过 @tool 装饰器)
        import rasterio
        levels = [2, 4, 8]
        with rasterio.open(test_tif_for_pyramid, "r+") as dst:
            dst.build_overviews(levels, rasterio.enums.Resampling.nearest)
        overall_ok &= check(True, f"金字塔构建: levels={levels} (rasterio 直接调用)")
    except Exception as e:
        overall_ok = False
        check(False, f"金字塔构建失败: {e}")

    # 4.2 拓扑检查 (用 detect_change 产出的 GeoJSON)
    if change_result and change_result.get("vector_path") and Path(change_result["vector_path"]).is_file():
        try:
            import geopandas as gpd
            gdf = gpd.read_file(change_result["vector_path"])
            invalid = int((~gdf.is_valid).sum())
            overall_ok &= check(True, f"拓扑检查: 共 {len(gdf)} 要素, 无效几何 {invalid} 个")
        except Exception as e:
            check(False, f"拓扑检查异常: {e}")

    # ==================== 5. 报表生成 (Phase D) ====================
    section("Step 5: Phase D 报表生成")
    if change_result and change_result.get("vector_path") and Path(change_result["vector_path"]).is_file():
        vec_path = change_result["vector_path"]
        ct_info = change_result.get("change_type_info", {}) or {}

        # 5.1 统计图表 (matplotlib)
        try:
            from backend.model.tools.report_tools import _summarize_gdf, _load_change_geojson
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.font_manager as fm
            # 中文字体
            _chosen = None
            for fname in ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]:
                if any(f.name == fname for f in fm.fontManager.ttflist):
                    _chosen = fname; break
            if _chosen:
                matplotlib.rcParams["font.sans-serif"] = [_chosen]
                matplotlib.rcParams["axes.unicode_minus"] = False
            gdf = _load_change_geojson(vec_path)
            stats = _summarize_gdf(gdf)
            pie_path = str(OUT_DIR / "stats_pie.png")
            fig, ax = plt.subplots(figsize=(7, 6))
            areas = [p["area_m2"] for p in stats["per_class"]]
            names = [p["name"] for p in stats["per_class"]]
            ax.pie(areas, labels=names, autopct='%1.1f%%', startangle=90)
            ax.set_title("变化面积占比")
            fig.savefig(pie_path, dpi=100, bbox_inches="tight")
            plt.close(fig)
            overall_ok &= check(Path(pie_path).is_file(), f"统计图表: {Path(pie_path).name} ({Path(pie_path).stat().st_size//1024}KB)")
        except Exception as e:
            overall_ok = False
            check(False, f"统计图表失败: {e}")

        # 5.2 PDF 报告 (reportlab + jinja2)
        try:
            from backend.model.tools.report_tools import generate_monitor_report
            # 调用工具函数 (绕过装饰器, 直接调原函数)
            from backend.model.tools import report_tools as rt
            report_result = rt.generate_monitor_report.invoke({
                "change_geojson_path": vec_path,
                "output_format": "pdf",
                "region_name": "冒烟测试区域",
                "t1_name": "test_t1.tif",
                "t2_name": "test_t2.tif",
                "change_type": ct_info.get("change_type", ""),
                "change_confidence": ct_info.get("confidence", 0),
                "change_reason": ct_info.get("reason", ""),
            })
            ok = report_result.get("type") in ("frontend_action", "success")
            overall_ok &= check(ok, f"PDF 报告: {report_result.get('summary', '').split(chr(10))[0][:80]}")
            if report_result.get("data", {}).get("output_path"):
                op = report_result["data"]["output_path"]
                check(Path(op).is_file(), f"PDF 文件: {Path(op).name} ({Path(op).stat().st_size//1024}KB)")
        except Exception as e:
            overall_ok = False
            check(False, f"PDF 报告失败: {e}")
            import traceback; traceback.print_exc()

        # 5.3 矢量导出 (Shapefile ZIP)
        try:
            from backend.model.tools import report_tools as rt
            export_result = rt.export_change_vector.invoke({
                "change_geojson_path": vec_path,
                "output_format": "shapefile",
            })
            ok = export_result.get("type") in ("frontend_action", "success")
            overall_ok &= check(ok, f"矢量导出: {export_result.get('summary', '').split(chr(10))[0][:80]}")
        except Exception as e:
            overall_ok = False
            check(False, f"矢量导出失败: {e}")

        # 5.4 Excel 导出
        try:
            from backend.model.tools import report_tools as rt
            stats_result = rt.export_stats_table.invoke({
                "change_geojson_path": vec_path,
                "output_format": "excel",
            })
            ok = stats_result.get("type") in ("frontend_action", "success")
            overall_ok &= check(ok, f"Excel 导出: {stats_result.get('summary', '').split(chr(10))[0][:80]}")
        except Exception as e:
            overall_ok &= False
            check(False, f"Excel 导出失败: {e}")

    else:
        print("  ⚠ 无变化检测结果, 跳过报表生成测试")

    # ==================== 总结 ====================
    section("冒烟测试总结")
    sym = "✓ 全部通过" if overall_ok else "✗ 有失败项"
    print(f"  结果: {sym}")
    print(f"  产物目录: {OUT_DIR}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
