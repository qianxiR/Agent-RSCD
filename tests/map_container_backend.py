# -*- coding: utf-8 -*-
"""
地图容器后端产物验证脚本 (用 sam3 环境跑真实 SamSeg 推理)

目的: 验证地图容器改造后, 后端各工具产出的文件真实落地且可被前端渲染。
设计: 直接调用工具函数 (不经 WebSocket), 更快更可控, 产物路径打印到 stdout。

运行方式 (必须用 sam3 环境, 含 torch/CUDA/geopandas/cv2):
    D:/anaconda3/envs/sam3/python.exe -m tests.map_container_backend
    或: conda run -n sam3 python -m tests.map_container_backend

测试矩阵:
    ① segment_image(t1.tif)        → 验证 mask_tif / geojson / shp 目录
    ② detect_change(t1,t2)         → 验证变化 mask_tif / geojson / shp
    ③ visualize_vector(成都市_县)  → 验证 GeoJSON→PNG + shp
    ④ export_change_vector(shp)    → 验证 geojson→shp 转换链路
    ⑤ overlay_edge_on_image        → 验证边缘叠加 PNG 生成
    ⑥ shp 回读校验                 → geopandas 读回 segment 产出的 shp 目录

每个测试断言: 文件存在 + size>0 + 关键字段非空。产物路径全部打印, 供前端核对。
"""
import sys
import time
from pathlib import Path

# 测试数据
DATA_DIR = Path(__file__).parent / "data"
T1 = str(DATA_DIR / "r000_c006_t1.tif")
T2 = str(DATA_DIR / "r000_c006_t2.tif")
GEOJSON_COUNTY = str(DATA_DIR / "成都市_县.geojson")

# 颜色: 通过/失败/警告
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

_passed = 0
_failed = 0
_artifacts = []  # 收集所有产物路径, 最后汇总打印


def _section(title):
    print(f"\n{CYAN}{BOLD}{'='*60}\n  {title}\n{'='*60}{RESET}")


def _check(condition, msg, artifact=None):
    """断言 + 计数; artifact 为产物路径时收集。"""
    global _passed, _failed
    if condition:
        print(f"  {GREEN}✓ {msg}{RESET}")
        _passed += 1
        if artifact:
            _artifacts.append(artifact)
    else:
        print(f"  {RED}✗ {msg}{RESET}")
        _failed += 1


def _file_exists(path_str, label):
    """检查文件存在且非空, 返回 bool。"""
    if not path_str:
        _check(False, f"{label}: 路径为空")
        return False
    p = Path(path_str)
    if not p.exists():
        _check(False, f"{label}: 文件不存在 {path_str}")
        return False
    if p.is_file():
        size = p.stat().st_size
        _check(size > 0, f"{label}: 存在且非空 ({size//1024}KB) → {path_str}", path_str)
        return size > 0
    # 目录
    files = list(p.iterdir()) if p.is_dir() else []
    _check(len(files) > 0, f"{label}: 目录非空 ({len(files)} 个文件) → {path_str}", path_str)
    return len(files) > 0


def _check_env():
    """前置: 确认依赖与测试数据就绪。"""
    _section("环境与测试数据检查")
    import torch
    print(f"  torch: {torch.__version__} | CUDA: {torch.cuda.is_available()}")
    import geopandas
    print(f"  geopandas: {geopandas.__version__}")
    import rasterio
    print(f"  rasterio: {rasterio.__version__}")

    from backend.model.SamSeg import runner
    _check(runner.samseg_available(), "SamSeg 模型可用")
    _check(Path(T1).is_file(), f"T1 测试影像存在: {T1}")
    _check(Path(T2).is_file(), f"T2 测试影像存在: {T2}")
    _check(Path(GEOJSON_COUNTY).is_file(), f"测试 GeoJSON 存在: {GEOJSON_COUNTY}")

    # 注入运行时上下文 (工具内部读 conversation_id/project_id)
    from backend.agent.runtime.context_vars import set_runtime_context
    set_runtime_context(f"test_map_{int(time.time())}", "test_user", "test_proj")


def test_segment_image():
    """① segment_image → 验证 mask_tif / geojson / shp 目录产物。"""
    _section("① segment_image 语义分割")
    from backend.model.tools.samseg_tools import segment_image

    t0 = time.time()
    result = segment_image.invoke({"image_path": T1, "classes": "建筑,道路,水"})
    elapsed = time.time() - t0
    print(f"  耗时: {elapsed:.1f}s")

    _check(result.get("type") != "error", f"分割成功 (type={result.get('type')})")
    if result.get("type") == "error":
        print(f"  {RED}错误: {result.get('msg')}{RESET}")
        return None

    data = result.get("data", {})
    # ★ 关键产物校验
    _file_exists(data.get("mask_tif_path"), "mask_tif (带 CRS 掩膜)")
    _file_exists(data.get("vector_path"), "GeoJSON 矢量图斑")
    _file_exists(data.get("shp_dir_path"), "shp 目录 (多文件)")
    # instruction.params 里的 URL (前端渲染用)
    params = result.get("instruction", {}).get("params", {})
    _check(bool(params.get("mask_tif_url")), f"mask_tif_url 非空: {params.get('mask_tif_url')}")
    # ★ input_image_url 仅当原图在 agent-files/samseg/send/ 下才有值;
    #   测试数据在 tests/data/ 下 → 为 None 是正常边界 (生产环境用户上传后非空)
    if params.get("input_image_url"):
        _check(True, f"input_image_url 非空 (上传场景): {params.get('input_image_url')}")
    else:
        print(f"  {YELLOW}⊙ input_image_url 为空 (本地测试数据, 非 upload 路径, 属正常){RESET}")
    _check(bool(params.get("legend")), f"legend 非空 ({len(params.get('legend', []))} 类)")
    # 统计
    stats = data.get("stats", {})
    _check(stats.get("total_regions", 0) > 0, f"分割出 {stats.get('total_regions')} 个区域")
    print(f"  {YELLOW}summary 摘要:{RESET}")
    print(f"    {result.get('summary', '')[:200]}...")
    return data


def test_detect_change():
    """② detect_change → 验证变化 mask_tif / geojson / shp 产物。"""
    _section("② detect_change 变化检测")
    from backend.model.tools.samseg_tools import detect_change

    t0 = time.time()
    result = detect_change.invoke({"t1_path": T1, "t2_path": T2, "classes": "建筑"})
    elapsed = time.time() - t0
    print(f"  耗时: {elapsed:.1f}s")

    _check(result.get("type") != "error", f"变化检测成功 (type={result.get('type')})")
    if result.get("type") == "error":
        print(f"  {RED}错误: {result.get('msg')}{RESET}")
        return None

    data = result.get("data", {})
    _file_exists(data.get("mask_tif_path"), "变化 mask_tif")
    _file_exists(data.get("vector_path"), "变化 GeoJSON")
    _file_exists(data.get("shp_dir_path"), "变化 shp 目录")
    _check(data.get("change_type", {}).get("source") == "rule", "业务类型判读不阻塞主流程")
    stats = data.get("stats", {})
    print(f"  变化区域: {stats.get('total_regions')} 个, 占比 {stats.get('changed_percent')}%")
    return data


def test_visualize_vector(geojson_path):
    """③ visualize_vector → GeoJSON 栅格化为 PNG + 导出 shp。"""
    _section("③ visualize_vector 矢量可视化")
    from backend.model.tools.vector_tools import visualize_vector

    result = visualize_vector.invoke({
        "geojson_path": geojson_path,
        "background_image_path": "",
        "alpha": 0.5,
        "export_shp": True,
    })
    _check(result.get("type") != "error", f"矢量可视化成功 (type={result.get('type')})")
    if result.get("type") == "error":
        print(f"  {RED}错误: {result.get('msg')}{RESET}")
        return None

    data = result.get("data", {})
    _file_exists(data.get("artifact_path"), "矢量可视化 PNG")
    _file_exists(data.get("shp_path"), "矢量 shp")
    _check(data.get("feature_count", 0) > 0, f"图斑数: {data.get('feature_count')}")
    params = result.get("instruction", {}).get("params", {})
    _check(bool(params.get("image_url")), f"PNG URL 非空: {params.get('image_url')}")
    return data


def test_export_change_vector(geojson_path):
    """④ export_change_vector → geojson 转 shp (extract_shp=True 验证 .shp 真实落地)。"""
    _section("④ export_change_vector GeoJSON→SHP 转换")
    from backend.model.tools.report_tools import export_change_vector

    result = export_change_vector.invoke({
        "change_geojson_path": geojson_path,
        "output_format": "shapefile",
        "extract_shp": True,
    })
    _check(result.get("type") != "error", f"导出成功 (type={result.get('type')})")
    if result.get("type") == "error":
        print(f"  {RED}错误: {result.get('msg')}{RESET}")
        return None

    data = result.get("data", {})
    _file_exists(data.get("output_path"), "导出 ZIP")
    _file_exists(data.get("shp_path"), "解压后 .shp")
    _check(data.get("feature_count", 0) > 0, f"要素数: {data.get('feature_count')}")
    return data


def test_overlay_edge_on_image(source_image, mask_tif):
    """⑤ overlay_edge_on_image → 边缘叠加 PNG + GeoTIFF。"""
    _section("⑤ overlay_edge_on_image 边缘提取")
    from backend.model.tools.mask_tools import overlay_edge_on_image

    if not mask_tif or not Path(mask_tif).is_file():
        _check(False, f"跳过: 无有效 mask_tif ({mask_tif})")
        return None

    result = overlay_edge_on_image.invoke({
        "source_image_path": source_image,
        "mask_path": mask_tif,
        "method": "distance",
        "edge_color": "255,0,0",
        "edge_thickness": 2,
        "export_geotiff": True,
    })
    _check(result.get("type") != "error", f"边缘提取成功 (type={result.get('type')})")
    if result.get("type") == "error":
        print(f"  {RED}错误: {result.get('msg')}{RESET}")
        return None

    data = result.get("data", {})
    params = result.get("instruction", {}).get("params", {})
    # ★ overlay_edge 工具: PNG 走 URL (instruction.params.image_url), GeoTIFF 是磁盘主产物 (data.geotiff_path)
    #   PNG 磁盘路径从 image_url 反推 (download 端点 filepath = agent-files 之后部分)
    _file_exists(data.get("geotiff_path"), "边缘 GeoTIFF (带 CRS, 主产物)")
    _check(bool(params.get("image_url")), f"边缘 PNG URL: {params.get('image_url')}")
    # verification 校验 PNG 真实落地 (工具自带, verify_by_path)
    verification = result.get("verification", {})
    _check(verification.get("ok"), f"verification 校验通过 (PNG {verification.get('size_bytes', 0)//1024}KB)")
    return data


def test_shp_roundtrip_read(shp_dir_or_path):
    """⑥ shp 回读校验 → geopandas 读回 segment 产出的 shp, 验证可解析。"""
    _section("⑥ Shapefile 回读校验 (geopandas)")
    import geopandas as gpd

    if not shp_dir_or_path:
        _check(False, "跳过: 无 shp 产物可回读")
        return

    p = Path(shp_dir_or_path)
    shp_file = None
    if p.is_dir():
        candidates = sorted(p.glob("*.shp"))
        shp_file = candidates[0] if candidates else None
    elif p.is_file() and p.suffix == ".shp":
        shp_file = p

    if not shp_file:
        _check(False, f"未找到 .shp 文件: {shp_dir_or_path}")
        return

    try:
        gdf = gpd.read_file(str(shp_file))
        _check(not gdf.empty, f"shp 回读成功: {len(gdf)} 个要素, 几何类型={set(gdf.geom_type)}")
        _check("geometry" in gdf.columns, "含 geometry 字段")
        print(f"  {YELLOW}shp 字段:{RESET} {list(gdf.columns)}")
    except Exception as e:
        _check(False, f"shp 回读失败: {e}")


def _summary():
    _section("产物路径汇总 (供前端核对)")
    for a in _artifacts:
        print(f"  {CYAN}{a}{RESET}")
    print(f"\n{BOLD}总计: {GREEN}{_passed} 通过{RESET}{BOLD}, {RED}{_failed} 失败{RESET}")
    sys.exit(0 if _failed == 0 else 1)


def main():
    print(f"{BOLD}{CYAN}地图容器后端产物验证 (sam3 环境){RESET}")
    print(f"Python: {sys.executable}")

    try:
        _check_env()
    except Exception as e:
        print(f"{RED}环境检查失败: {e}{RESET}")
        sys.exit(1)

    # ① 语义分割
    seg_data = test_segment_image()
    seg_mask = seg_data.get("mask_tif_path") if seg_data else None
    seg_shp = seg_data.get("shp_dir_path") if seg_data else None

    # ② 变化检测
    cd_data = test_detect_change()

    # ③ 矢量可视化 (用成都市 GeoJSON)
    vec_data = test_visualize_vector(GEOJSON_COUNTY)
    vec_shp = vec_data.get("shp_path") if vec_data else None

    # ④ geojson → shp 转换
    export_data = test_export_change_vector(GEOJSON_COUNTY)
    exported_shp = export_data.get("shp_path") if export_data else None

    # ⑤ 边缘提取 (用 segment 的 mask_tif)
    test_overlay_edge_on_image(T1, seg_mask)

    # ⑥ shp 回读 (优先用 export 解压的 .shp, 否则 segment 的 shp 目录)
    test_shp_roundtrip_read(exported_shp or vec_shp or seg_shp)

    _summary()


if __name__ == "__main__":
    main()
