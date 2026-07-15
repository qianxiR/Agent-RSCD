"""
SamSeg 推理封装层 (Model 层 / SamSeg)
- 入参: 图片路径、类别、输出路径
- 方法: 加载 SAM 3 模型(全局缓存)、语义分割、变化检测、结果上色存 PNG
- 出参: 成功返回输出 PNG 路径; 失败返回 None

设计要点:
  1. 导入修复: SamSeg 子项目原用 `from infer import ...` / `from sam3 import ...` 裸导入,
     在 backend.model.SamSeg 包结构下会 ImportError。本模块用 sys.path.insert 方案
     零侵入接入 (不改 SamSeg 源码几十处 import)。
  2. 模型缓存: SAM 3 权重 3.3GB, load_model 一次几十秒。全局 _processor 单例, 多请求复用。
  3. 类别映射: 支持中文类别名 (建筑/道路/水...) → SamSeg 英文同义词 query;
     未指定或映射失败时回退到 SamSeg 默认 7 类。
  4. 优雅降级: torch/权重缺失时 samseg_available() 返回 False, 工具层据此返回明确错误。

依赖方向: model.SamSeg.runner 仅依赖 SamSeg/infer.py (本子项目), 无跨层依赖。
"""
import sys
import logging
from pathlib import Path
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# ==================== 职责拆分: 从同级模块 re-export (保持对外契约不破) ====================
# runner.py 原本混了三类职责 (推理核心 + 可视化 + GeoIO), 现拆为:
#   - runner.py  : 纯推理核心 (模型加载/推理/统计/VLM 判读)
#   - visualize.py: 可视化辅助 (PNG 上色 + 颜色图注 legend)
#   - geoio.py   : 地理 IO + 产物落盘 (GeoTIFF/shp/矢量)
# ★ samseg_tools.py 用 runner.xxx() 调这些函数, 此处 re-export 保证旧符号仍可访问 (契约不破)
from .visualize import (
    build_legend as _build_legend,           # 兼容 runner._build_legend 旧引用
    save_color_mask_png as _save_color_mask_png,
    _EN_TO_CN_CLASS,                          # 模块级常量 re-export
)
from .geoio import (
    read_source_geo as _read_source_geo,
    save_geotiff_mask as _save_geotiff_mask,
    vectorize_mask_to_geojson as _vectorize_mask_to_geojson,
)

# ==================== 路径与导入修复 ====================

# SamSeg 子项目根目录 (含 infer.py / sam3/ / resources/)
_SAMSEG_PROJECT_DIR = Path(__file__).resolve().parent / "SamSeg"

# 模型权重与 BPE 词表默认路径 (绝对路径, 不依赖 CWD)
_DEFAULT_CKPT = _SAMSEG_PROJECT_DIR / "sam3" / "sam3.pt"
_DEFAULT_BPE = _SAMSEG_PROJECT_DIR / "sam3" / "assets" / "bpe_simple_vocab_16e6.txt.gz"

# 全局缓存: (processor, device, query_idx_tensor_holder)
# 首次推理时加载, 后续复用。不同类别集复用同一 processor (模型本身与类别无关)。
_processor = None
_device = None

# 推理依赖可用性标记 (惰性检测一次)
_availability_checked = False
_torch = None
_infer_module = None


def _ensure_imports():
    """
    把 SamSeg 子项目目录加入 sys.path, 使其内部的裸导入 (from infer / from sam3) 生效。
    返回 infer 模块; 失败抛 ImportError。
    """
    global _infer_module
    if _infer_module is not None:
        return _infer_module

    samseg_dir = str(_SAMSEG_PROJECT_DIR)
    if samseg_dir not in sys.path:
        sys.path.insert(0, samseg_dir)

    # 此时 infer.py 顶层的 `from sam3 import ...` 才能找到 sam3/ 目录
    import infer  # noqa: E402
    _infer_module = infer
    return _infer_module


def samseg_available() -> bool:
    """
    检测 SamSeg 是否完全可用 (torch + 所有依赖 + 模型权重)。

    不仅检查 torch 和权重, 还会实际尝试 `import infer` 来捕获 pycocotools /
    scikit-image / scipy 等深层依赖缺失。
    惰性检测一次后缓存结果。
    """
    global _availability_checked, _torch
    if _availability_checked:
        return _torch is not None
    _availability_checked = True

    try:
        import torch  # noqa: F401
        _torch = torch
    except ImportError:
        logger.warning("SamSeg 不可用: 未安装 torch")
        return False

    if not _DEFAULT_CKPT.is_file():
        logger.warning(f"SamSeg 不可用: 模型权重不存在 {_DEFAULT_CKPT}")
        _torch = None
        return False

    if not _DEFAULT_BPE.is_file():
        logger.warning(f"SamSeg 不可用: BPE 词表不存在 {_DEFAULT_BPE}")
        _torch = None
        return False

    # ★ 实际尝试 import infer, 捕获深层依赖缺失 (pycocotools/scikit-image 等)
    try:
        _ensure_imports()
    except Exception as e:
        logger.warning(f"SamSeg 不可用: infer 模块导入失败 ({e})")
        _torch = None
        return False

    logger.info(f"SamSeg 就绪: torch={torch.__version__}, 权重={_DEFAULT_CKPT.name}")
    return True


# ==================== 类别系统 ====================

# SamSeg 默认 7 类 (与 infer.py DEFAULT_CLASSES 一致)
# 每类首项是该类的标准英文名 (用于颜色匹配), 其余是同义词
_DEFAULT_CLASSES = [
    "background",
    "building,house,roof,structure",
    "road,highway,street,pavement,expressway",
    "water,lake,river,pond,pool,sea",
    "bareland,barren,soil,ground,dirt,sand",
    "vegetation,forest,tree,grass,lawn,meadow,shrub,woodland",
    "farmland,agricultural,crop,farm,field,cropland,plantation",
]

# 中文类别名 → 英文标准名 映射 (用户对话指定类别时用)
# 覆盖常见中文遥感地物词汇; 未命中的英文类别名直接透传
_CN_TO_EN_CLASS = {
    # building
    "建筑": "building", "建筑物": "building", "房屋": "building", "屋顶": "building",
    "房子": "building", "构筑物": "building",
    # road
    "道路": "road", "公路": "road", "街道": "road", "路": "road", "高速": "road",
    # water
    "水": "water", "水体": "water", "河流": "water", "湖泊": "water", "湖": "water",
    "河": "water", "池塘": "water", "水域": "water", "海": "water", "海洋": "water",
    # bareland
    "裸地": "bareland", "裸土": "bareland", "土壤": "bareland", "沙地": "bareland",
    "荒地": "bareland", "泥地": "bareland",
    # vegetation
    "植被": "vegetation", "森林": "vegetation", "树林": "vegetation", "树": "vegetation",
    "草地": "vegetation", "草": "vegetation", "灌木": "vegetation", "林地": "vegetation",
    # farmland
    "农田": "farmland", "耕地": "farmland", "庄稼": "farmland", "田地": "farmland",
    "农作物": "farmland", "田": "farmland",
}


def _normalize_classes(classes: Optional[str]) -> List[str]:
    """
    把用户传入的类别字符串解析为 SamSeg 类别行列表 (含 background)。

    ★ 开放词汇模式 (v2.2):
      SamSeg 底层是开放词汇模型 (SegEarth-OV3 / SAM 3), 理论上接受任意类别 query。
      本函数保留 7 类默认值, 同时支持用户传入任意自定义类别:
        - 已知类 (中文/英文命中 7 大类): 走同义词增强 (如"建筑"→ building,house,roof,...)
        - 未知类 (7 大类之外的词, 如"大棚/光伏板/parking lot"): 透传作为新 query, 不再丢弃
      只有用户完全不传 classes 时才回退到默认 7 类。

    - 入参 classes: 逗号分隔的类别名 (中文/英文/任意), 如 "建筑,大棚,光伏板" / "building,water"
    - 出参: ["background", "<类1同义词行>", "<类2>", ...] 形式, 第 0 行固定 background
    """
    if not classes or not classes.strip():
        return list(_DEFAULT_CLASSES)

    parts = [c.strip() for c in classes.replace("，", ",").split(",") if c.strip()]
    if not parts:
        return list(_DEFAULT_CLASSES)

    # 预建: 7 大类标准名 → 同义词行 (用于已知类的同义词增强)
    syn_map = {}
    for line in _DEFAULT_CLASSES:
        primary = line.split(",")[0]
        syn_map[primary] = line

    # 逐项处理, 去重保序 (按小写英文标准名去重)
    result_lines: List[str] = []
    seen: set = set()
    for p in parts:
        # 中文 → 英文标准名; 英文直接小写; 任意词透传
        en = _CN_TO_EN_CLASS.get(p, p.lower()).strip()
        if not en or en == "background":
            continue
        dedup_key = en.split(",")[0]  # 同义词行的首词作为去重键
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        # 已知 7 大类 → 用同义词行增强 (提升识别率)
        # 未知类 → 单用该词 (开放词汇, 模型自行理解)
        result_lines.append(syn_map.get(dedup_key, en))

    if not result_lines:
        logger.info(f"类别 '{classes}' 解析后为空, 回退默认 7 类")
        return list(_DEFAULT_CLASSES)

    logger.info(
        f"类别解析(开放词汇): '{classes}' → {len(result_lines)} 类: "
        f"{[l.split(',')[0] for l in result_lines]}"
    )
    return ["background"] + result_lines


def _build_query_tensors(class_lines: List[str]) -> Tuple[List[str], "torch.Tensor", int, int]:
    """
    从类别行列表构建 SamSeg 推理所需的 query 数据。
    - 入参: class_lines (含 background 行), 如 ["background", "building,house", "water"]
    - 出参: (query_words, query_idx_tensor, num_cls, num_queries)
      - query_words: 同义词展开后的提示词列表
      - query_idx_tensor: 每个 query 对应的 class id (int64 tensor)
      - num_cls: 总类别数 (含 background)
      - num_queries: 提示词总数
    """
    import torch  # 由 samseg_available 保证已装

    query_words: List[str] = []
    query_idx: List[int] = []
    for idx, line in enumerate(class_lines):
        if idx == 0:
            continue  # background 不发提示
        for syn in line.split(","):
            w = syn.strip()
            if w:
                query_words.append(w)
                query_idx.append(idx)

    num_cls = len(class_lines)
    num_queries = len(query_words)
    query_idx_tensor = torch.tensor(query_idx, dtype=torch.int64)
    return query_words, query_idx_tensor, num_cls, num_queries


# ==================== 模型加载 (全局缓存) ====================

def _get_processor(ckpt: str, bpe: str, conf: float, device_str: str):
    """
    获取 Sam3Processor 单例 (首次加载, 后续复用)。
    - ckpt/bpe/conf/device_str 首次加载时生效; 已加载后忽略参数变化 (模型本身与类别无关)
    - 返回: (processor, device, query_idx_tensor 所在 device)
    """
    global _processor, _device
    if _processor is not None:
        return _processor, _device

    infer = _ensure_imports()
    import torch

    device = torch.device(device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu"))
    logger.info(f"[SamSeg] 加载模型 (首次, 约需几十秒): ckpt={Path(ckpt).name}, device={device}")
    processor, dev = infer.load_model(ckpt=ckpt, bpe=bpe, conf=conf, device=device)
    _processor = processor
    _device = dev
    logger.info("[SamSeg] 模型加载完成, 后续推理将复用")
    return _processor, _device


# ==================== 推理入口 ====================


def run_segment(
    image_path: str,
    output_path: str,
    classes: Optional[str] = None,
    ckpt: Optional[str] = None,
    bpe: Optional[str] = None,
    device: Optional[str] = None,
    conf: float = 0.5,
    prob: float = 0.6,
    max_passes: int = 10,
    coverage_target: float = 0.95,
    vector_output_path: Optional[str] = None,
    min_area_m2: float = 50.0,
) -> Optional[str]:
    """
    语义分割: 单张图 → 类别掩膜 → 彩色 PNG (+ 可选矢量 GeoJSON)。
    - 入参:
      - image_path: 输入图片绝对路径
      - output_path: 输出彩色 PNG 绝对路径
      - classes: 逗号分隔类别名 (中文/英文), 留空用默认 7 类
      - vector_output_path: 矢量 GeoJSON 输出路径 (None=不产出矢量)
      - min_area_m2: 矢量化的最小面积过滤 (平方米)
      - 其余参数: 模型路径/设备/推理参数, 一般用默认
    - 出参: 成功返回 {output_path, stats, legend, [vector_stats, shp_path, edge_shp_path]};
            失败返回 None. 矢量化失败不影响 PNG/统计.
    """
    import numpy as np
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None  # 大图不告警

    infer = _ensure_imports()
    processor, dev = _get_processor(
        ckpt or str(_DEFAULT_CKPT), bpe or str(_DEFAULT_BPE), conf, device or ""
    )

    # 类别 → query
    class_lines = _normalize_classes(classes)
    query_words, query_idx_tensor, num_cls, num_queries = _build_query_tensors(class_lines)
    query_idx_tensor = query_idx_tensor.to(dev)

    # 读图
    img = Image.open(image_path).convert("RGB")
    logger.info(f"[SamSeg] 分割开始: {image_path} ({img.size[0]}x{img.size[1]}), {num_cls} 类")

    # 多轮迭代推理
    seg = infer.multipass_inference(
        processor, query_words, query_idx_tensor, num_cls, num_queries,
        dev, img, prob=prob, max_passes=max_passes, coverage_target=coverage_target,
    )
    # 后处理 (形态学平滑/去碎片/填洞)
    seg = infer.postprocess(seg, num_cls)

    # 上色存 PNG (★ 可视化逻辑收敛到 visualize.save_color_mask_png)
    cls_name_map = {idx: [s.strip() for s in line.split(",")] for idx, line in enumerate(class_lines)}
    palette = infer.build_palette(num_cls, cls_name_map)
    color_mask = palette[seg]  # (H, W, 3) uint8 索引上色 (保留引用: geoio 落 GeoTIFF 复用)
    _save_color_mask_png(color_mask, output_path)

    # ★ 连通域分析: 统计各类别的区域数量与面积
    stats = _analyze_change_stats(seg, num_cls, class_lines)  # seg 也是 (H,W) int64 类别索引
    # ★ 图注: 颜色→类别 (直接复用上色用的 palette, 保证与 PNG 颜色严格一致)
    legend = _build_legend(palette, class_lines)

    result = {"output_path": output_path, "stats": stats, "legend": legend}

    # ★ GeoTIFF 带 CRS 掩膜 (源影像有 CRS 时生成, 供 GIS 直接打开)
    import os
    mask_tif_path = output_path.rsplit(".", 1)[0] + ".tif"
    geo_result = _save_geotiff_mask(color_mask, image_path, mask_tif_path)
    if geo_result:
        result["mask_tif_path"] = geo_result["mask_tif_path"]
        result["has_crs"] = True
    else:
        result["has_crs"] = False

    # ★ 叠加图 / 边缘描线已移除: 前端地图容器用「原图层 + 掩膜半透明叠加层」
    #   达到同样的视觉效果, 后端不再自动生成 overlay PNG / edge GeoTIFF。
    #   (如需手动提取边缘, AI 可调用 mask_tools.overlay_edge_on_image 工具)

    # ★ P1 栅格→矢量 (可选): mask → GeoJSON + 真实面积统计, 同源导出 Shapefile
    if vector_output_path:
        # ★ shp 输出目录与 GeoJSON 同目录; stem 用 ASCII (中文会写空文件)
        import os as _os
        shp_dir = _os.path.dirname(vector_output_path) or "."
        vec = _vectorize_mask_to_geojson(
            seg, image_path, class_lines, vector_output_path,
            min_area_m2=min_area_m2,
            output_shp_dir=shp_dir,
            shp_stem=Path(vector_output_path).stem,
        )
        if vec:
            result["vector_stats"] = vec["vector_stats"]
            if vec.get("shp_path"):
                result["shp_path"] = vec["shp_path"]
            if vec.get("shp_zip_path"):
                result["shp_zip_path"] = vec["shp_zip_path"]
            # ★ 边线 SHP 已在 geoio 中直接从面 GDF 导出, 无需二次 I/O
            if vec.get("edge_shp_path"):
                result["edge_shp_path"] = vec["edge_shp_path"]

    logger.info(f"[SamSeg] 分割完成, 结果存: {output_path}, 区域={stats['total_regions']}个")
    return result


def run_change_detection(
    t1_path: str,
    t2_path: str,
    output_path: str,
    classes: Optional[str] = None,
    ckpt: Optional[str] = None,
    bpe: Optional[str] = None,
    device: Optional[str] = None,
    conf: float = 0.5,
    prob: float = 0.6,
    max_passes: int = 10,
    coverage_target: float = 0.95,
    min_area: int = 200,
    iou_threshold: float = 0.3,
    vector_output_path: Optional[str] = None,
    min_area_m2: float = 50.0,
) -> Optional[str]:
    """
    变化检测: 双时相 T1/T2 → 各自分割 → 实例级变化对比 → 彩色变化图 PNG。
    - 入参:
      - t1_path / t2_path: 两个时相图片绝对路径 (尺寸不同会自动 resize 对齐)
      - output_path: 输出彩色变化图 PNG 绝对路径
      - classes: 逗号分隔类别名, 留空用默认 7 类
      - prob: 首轮置信度阈值 (P1-2 默认 0.6, 比原隐式 0.7 略松但远严于衰减后的 0.343)
              两期统一用较高阈值 → 对稳定地物预测更一致 → 减少假"类别变化"
      - iou_threshold: 实例匹配 IoU 阈值 (P0 默认 0.3, 要求至少 30% 重叠才算同一对象,
                       砍掉擦边误配; 原 0.0 过宽松导致大量误判)
      - min_area: 变化掩膜最小连通域面积 (P0 默认 200, 比分割的 64 更大, 清掉细碎变化点)
    - 出参: 成功返回 output_path; 失败返回 None

    ★ 误判优化 (P0+P1, 2026-06):
      原 iou_threshold=0.0 + min_area=64 + 两期独立分割阈值衰减到 0.343, 导致:
        (a) 擦边实例被错误配对 → 假"类别变化"
        (b) 细碎变化碎片满图 → 假"新增/消失"
        (c) 两期对同一稳定地物划到不同类 → 假"类别变化"
      优化: iou 提到 0.3, min_area 提到 200, prob 提到 0.6 让两期更一致。
    """
    import numpy as np
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None

    infer = _ensure_imports()
    processor, dev = _get_processor(
        ckpt or str(_DEFAULT_CKPT), bpe or str(_DEFAULT_BPE), conf, device or ""
    )

    # 类别 → query
    class_lines = _normalize_classes(classes)
    query_words, query_idx_tensor, num_cls, num_queries = _build_query_tensors(class_lines)
    query_idx_tensor = query_idx_tensor.to(dev)

    # 读图并尺寸对齐
    img_t1 = Image.open(t1_path).convert("RGB")
    img_t2 = Image.open(t2_path).convert("RGB")
    w1, h1 = img_t1.size
    w2, h2 = img_t2.size
    target_w, target_h = max(w1, w2), max(h1, h2)
    if (w1, h1) != (target_w, target_h):
        img_t1 = img_t1.resize((target_w, target_h), Image.BILINEAR)
    if (w2, h2) != (target_w, target_h):
        img_t2 = img_t2.resize((target_w, target_h), Image.BILINEAR)
    logger.info(f"[SamSeg] 变化检测开始: T1={t1_path}, T2={t2_path} ({target_w}x{target_h}), {num_cls} 类")

    # T1 / T2 各自多轮分割
    seg_t1 = infer.multipass_inference(
        processor, query_words, query_idx_tensor, num_cls, num_queries,
        dev, img_t1, prob=prob, max_passes=max_passes, coverage_target=coverage_target,
    )
    t1_mask = infer.postprocess(seg_t1, num_cls, min_area=min_area)

    seg_t2 = infer.multipass_inference(
        processor, query_words, query_idx_tensor, num_cls, num_queries,
        dev, img_t2, prob=prob, max_passes=max_passes, coverage_target=coverage_target,
    )
    t2_mask = infer.postprocess(seg_t2, num_cls, min_area=min_area)

    # 实例级变化检测 (IoU 匹配)
    change_pred = infer.compute_instance_change_map(
        t1_mask, t2_mask, num_cls, iou_threshold=iou_threshold, min_area=max(min_area, 4),
    )
    change_mask = infer.postprocess(change_pred, num_cls, min_area=min_area)

    # 上色存 PNG (★ 可视化逻辑收敛到 visualize.save_color_mask_png)
    cls_name_map = {idx: [s.strip() for s in line.split(",")] for idx, line in enumerate(class_lines)}
    palette = infer.build_palette(num_cls, cls_name_map)
    color_mask = palette[change_mask]  # (H, W, 3) uint8 索引上色 (保留引用: geoio 落 GeoTIFF 复用)
    _save_color_mask_png(color_mask, output_path)

    # ★ 连通域分析: 统计各类别变化区域的数量与面积
    stats = _analyze_change_stats(change_mask, num_cls, class_lines)
    # ★ 图注: 颜色→类别 (直接复用上色用的 palette, 保证与 PNG 颜色严格一致)
    legend = _build_legend(palette, class_lines)

    result = {"output_path": output_path, "stats": stats, "legend": legend}

    # ★ GeoTIFF 带 CRS 掩膜 (坐标系从 T1 影像读, T1/T2 同区域)
    mask_tif_path = output_path.rsplit(".", 1)[0] + ".tif"
    geo_result = _save_geotiff_mask(color_mask, t1_path, mask_tif_path)
    if geo_result:
        result["mask_tif_path"] = geo_result["mask_tif_path"]
        result["has_crs"] = True
    else:
        result["has_crs"] = False

    # ★ 叠加图 / 边缘描线已移除: 前端地图容器用「T1 原图层 + 变化掩膜半透明叠加层」
    #   达到同样的视觉效果, 后端不再自动生成 overlay PNG / edge GeoTIFF。
    #   (如需手动提取变化边缘, AI 可调用 mask_tools.overlay_edge_on_image 工具)

    # ★ P1 栅格→矢量 (可选): change_mask → GeoJSON (变化图斑 + 真实面积) + 同源 Shapefile
    #   坐标系从 T1 影像读 (T1/T2 同区域, transform 一致)
    if vector_output_path:
        import os as _os
        shp_dir = _os.path.dirname(vector_output_path) or "."
        vec = _vectorize_mask_to_geojson(
            change_mask, t1_path, class_lines, vector_output_path,
            min_area_m2=min_area_m2,
            output_shp_dir=shp_dir,
            shp_stem=Path(vector_output_path).stem,
        )
        if vec:
            result["vector_stats"] = vec["vector_stats"]
            if vec.get("shp_path"):
                result["shp_path"] = vec["shp_path"]
            if vec.get("shp_zip_path"):
                result["shp_zip_path"] = vec["shp_zip_path"]
            # ★ 边线 SHP 已在 geoio 中直接从面 GDF 导出, 无需二次 I/O
            if vec.get("edge_shp_path"):
                result["edge_shp_path"] = vec["edge_shp_path"]

    logger.info(f"[SamSeg] 变化检测完成, 结果存: {output_path}, 变化区域={stats['total_regions']}个, 面积={stats['changed_percent']}%")
    return result


# ==================== 可视化 / GeoIO 已迁移至独立模块 ====================
# 以下函数随职责拆分迁出 runner.py (对外契约通过顶部 re-export 保持):
#   - visualize.py: build_legend (颜色图注) + save_color_mask_png (PNG 上色)
#   - geoio.py:     read_source_geo / save_geotiff_mask / export_shp_from_gdf /
#                   vectorize_mask_to_geojson (GeoTIFF/shp/矢量)
#   - _EN_TO_CN_CLASS 常量随 build_legend 迁至 visualize.py
# runner.py 仅保留纯推理核心 (模型加载/推理/统计/VLM 判读)。


# ==================== 业务类型语义判读 (#7, VLM 二次判读 + 规则兜底) ====================
# 变化检测结果 → 业务变化类型 (新增建筑/耕地转林地/推土/水体变化/其他)
# 实现策略: VLM 看图判读优先, 规则映射兜底 (VLM 不可用时).


# ==================== 业务类型语义判读 (#7, VLM 二次判读 + 规则兜底) ====================
# 变化检测结果 → 业务变化类型 (新增建筑/耕地转林地/推土/水体变化/其他)
# 实现策略: VLM 看图判读优先, 规则映射兜底 (VLM 不可用时).

# 规则映射的判定逻辑 (基于 stats.per_class 的类别分布)
# 规则映射永远兜底, 用于 VLM 不可用 / VLM 结果异常时
_RULE_FALLBACK_ORDER = [
    # (业务类型, 判定函数: 接受 (present_classes, stats), 返回 bool)
]


def classify_change_type_by_rule(present_classes: list, stats: dict) -> dict:
    """
    规则映射兜底: 基于 stats.per_class 的类别分布推断业务类型.
    - present_classes: 出现在变化结果里的类别英文标准名列表
    - stats: runner._analyze_change_stats 的输出

    出参: {change_type, confidence, reason}
      change_type ∈ {新增建筑, 建筑消失, 耕地转林地, 推土, 水体变化, 其他}
    """
    if not present_classes:
        return {"change_type": "其他", "confidence": 0.3, "reason": "无有效变化类别"}

    # 找面积最大的主导类别
    per_class = stats.get("per_class", []) if stats else []
    if per_class:
        dominant = max(per_class, key=lambda c: c.get("area_px", 0))
        dom_name = dominant.get("name", "")
    else:
        dom_name = present_classes[0]

    has_building = "building" in present_classes
    has_farmland = "farmland" in present_classes
    has_vegetation = "vegetation" in present_classes
    has_bareland = "bareland" in present_classes
    has_water = "water" in present_classes

    # 按主导类别映射业务类型
    if has_building and dom_name == "building":
        return {"change_type": "新增建筑", "confidence": 0.7,
                "reason": f"建筑为变化主导类别 ({dominant.get('area_percent', 0)}%)"}
    if has_farmland and has_vegetation:
        return {"change_type": "耕地转林地", "confidence": 0.6,
                "reason": "同时存在农田与植被变化, 疑似耕地转为林草地"}
    if has_bareland and dom_name == "bareland":
        return {"change_type": "推土", "confidence": 0.6,
                "reason": f"裸地为变化主导类别 ({dominant.get('area_percent', 0)}%), 疑似土地平整"}
    if has_water:
        return {"change_type": "水体变化", "confidence": 0.55,
                "reason": "变化区域含水体类别"}
    if has_vegetation and dom_name == "vegetation":
        return {"change_type": "植被变化", "confidence": 0.5,
                "reason": f"植被为变化主导类别 ({dominant.get('area_percent', 0)}%)"}
    return {"change_type": "其他", "confidence": 0.4,
            "reason": f"主导类别 {dom_name}, 无明确业务类型对应"}


def classify_change_type_by_vlm(
    t1_path: str,
    t2_path: str,
    result_png_path: str,
    vector_stats: dict,
    stats: dict,
) -> Optional[dict]:
    """
    ★ 业务类型语义判读主入口 (VLM 二次判读):
    把 T1/T2 + 变化结果图 + 统计摘要喂给视觉多模态模型, 让 VLM 输出业务变化类型.

    策略:
      - 构造描述性 prompt (含每类变化的面积/区域数)
      - 让 VLM 输出 JSON: {change_type, confidence, reason}
      - VLM 不可用 / 调用失败 → 返回 None, 调用方走 classify_change_type_by_rule 兜底

    入参:
      - t1_path / t2_path: T1/T2 影像路径 (VLM 同时看双时相 + 变化图)
      - result_png_path: 变化检测彩色 PNG 路径
      - vector_stats: runner 矢量统计 (total_features/total_area_m2/per_class)
      - stats: runner 像素统计 (per_class)

    出参: {change_type, confidence, reason} 或 None
    """
    try:
        from backend.config import settings
        from backend.model.llm_client import create_llm
        from langchain_core.messages import HumanMessage, SystemMessage
        import base64
    except Exception as e:
        logger.warning(f"[VLM-Classify] 依赖缺失, 跳过 VLM 判读: {e}")
        return None

    # 检查 DashScope key
    if not settings.dashscope_api_key:
        logger.info("[VLM-Classify] 未配置 DASHSCOPE_API_KEY, 跳过 VLM 判读")
        return None

    def _img_to_data_uri(path: str) -> Optional[str]:
        try:
            p = Path(path)
            if not p.is_file():
                return None
            data = p.read_bytes()
            if len(data) > 20 * 1024 * 1024:
                return None
            ext = p.suffix.lstrip(".").lower()
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "tif": "image/tiff", "tiff": "image/tiff"}.get(ext, "image/png")
            return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        except Exception:
            return None

    # 把 T1/T2 + 变化结果图都喂给 VLM (最多 3 张)
    content = []
    desc_parts = []
    for label, p in [("T1", t1_path), ("T2", t2_path), ("变化检测结果", result_png_path)]:
        uri = _img_to_data_uri(p)
        if uri:
            content.append({"type": "image_url", "image_url": {"url": uri}})
            desc_parts.append(label)
    if not content:
        logger.warning("[VLM-Classify] 无可用图片, 跳过 VLM 判读")
        return None

    # 构造统计摘要 (供 VLM 参考)
    stat_lines = []
    if stats and stats.get("per_class"):
        for c in stats["per_class"]:
            stat_lines.append(f"  - {c['name']}: {c['regions']}个区域, 占{c['area_percent']}%")
    if vector_stats and vector_stats.get("per_class"):
        stat_lines.append("真实面积统计:")
        for pc in vector_stats["per_class"][:5]:
            stat_lines.append(f"  - {pc['name']}: {pc['count']}个图斑, {pc['area_m2']}m²")
    stats_text = "\n".join(stat_lines) if stat_lines else "  (无统计)"

    sys_msg = SystemMessage(content=(
        "你是遥感影像变化检测业务分析专家。给定双时相影像(T1/T2)与变化检测结果图, "
        "判断变化区域的业务类型。严格按 JSON 输出, 不要额外解释。"
        "\n业务类型候选: 新增建筑、建筑消失、耕地转林地、推土、水体变化、植被变化、道路变化、其他"
        "\nJSON 格式: {\"change_type\":\"新增建筑\",\"confidence\":0.85,\"reason\":\"T2 出现成片新建建筑\"}"
    ))
    user_msg = HumanMessage(content=[
        {"type": "text", "text": (
            f"请分析这组影像的变化业务类型。附图顺序: {', '.join(desc_parts)}。\n"
            f"变化统计:\n{stats_text}\n"
            f"请输出业务变化类型 JSON (change_type/confidence 0~1/reason 中文一句话)。"
        )},
    ] + content)

    try:
        vlm = create_llm(model=settings.vision_model, temperature=0.2)
        resp = vlm.invoke([sys_msg, user_msg])
        text = resp.content if hasattr(resp, "content") else str(resp)
        text = text.strip()
        # 抽取 JSON (容忍 ```json 代码块包裹)
        import json as _json
        import re as _re
        m = _re.search(r"\{[^{}]*\}", text.replace("\n", " "))
        if not m:
            logger.warning(f"[VLM-Classify] 响应未找到 JSON: {text[:200]}")
            return None
        data = _json.loads(m.group(0))
        ct = str(data.get("change_type", "")).strip()
        conf = float(data.get("confidence", 0.7))
        reason = str(data.get("reason", "")).strip()
        if not ct:
            return None
        # 标准化置信度到 [0,1]
        conf = max(0.0, min(1.0, conf))
        logger.info(f"[VLM-Classify] VLM 判读: {ct} (conf={conf:.2f}) — {reason}")
        return {"change_type": ct, "confidence": round(conf, 2), "reason": reason, "source": "vlm"}
    except Exception as e:
        logger.warning(f"[VLM-Classify] VLM 调用失败, 走规则兜底: {e}")
        return None


def classify_change_type(
    t1_path: str,
    t2_path: str,
    result_png_path: str,
    vector_stats: dict,
    stats: dict,
    class_lines: list,
) -> dict:
    """
    业务类型判读统一入口: VLM 优先, 规则兜底.
    - VLM 不可用/失败 → 走 classify_change_type_by_rule
    - 始终返回 {change_type, confidence, reason, source}
    """
    # 准备 present_classes (从 stats 提取)
    present_classes = []
    if stats and stats.get("per_class"):
        present_classes = [c.get("name", "") for c in stats["per_class"]]

    # 优先 VLM
    vlm_result = classify_change_type_by_vlm(t1_path, t2_path, result_png_path, vector_stats, stats)
    if vlm_result and vlm_result.get("change_type"):
        return vlm_result

    # 兜底规则
    rule_result = classify_change_type_by_rule(present_classes, stats)
    rule_result["source"] = "rule"
    return rule_result


def _analyze_change_stats(change_mask, num_cls, class_lines):
    """
    对变化检测掩膜做连通域分析, 统计各类别的变化区域数量和面积。

    入参:
      - change_mask: (H, W) int64 类别索引图, 0=无变化, >0=变为该类别
      - num_cls: 总类别数 (含 background)
      - class_lines: 类别行列表 (含 background)
    出参: {
        total_pixels, changed_pixels, changed_percent, total_regions,
        per_class: [{id, name, regions, area_px, area_percent}, ...]
    }
    """
    import numpy as np
    from scipy.ndimage import label as connected_label

    H, W = change_mask.shape
    total_px = int(H * W)
    class_stats = []

    cls_names = [line.split(",")[0].strip() for line in class_lines]

    present_ids = sorted(set(np.unique(change_mask)) - {0})
    if not present_ids:
        return {
            "total_pixels": total_px, "changed_pixels": 0,
            "changed_percent": 0.0, "total_regions": 0, "per_class": [],
        }

    all_regions = 0
    changed_px = 0
    for cid in present_ids:
        cid_int = int(cid)
        binary = (change_mask == cid_int)
        area_px = int(binary.sum())
        changed_px += area_px

        labeled, n_regions = connected_label(binary, structure=np.ones((3, 3), dtype=bool))

        class_stats.append({
            "id": cid_int,
            "name": cls_names[cid_int] if cid_int < len(cls_names) else f"class_{cid_int}",
            "regions": int(n_regions),
            "area_px": area_px,
            "area_percent": round(100.0 * area_px / total_px, 2),
        })
        all_regions += int(n_regions)

    return {
        "total_pixels": total_px,
        "changed_pixels": int(changed_px),
        "changed_percent": round(100.0 * changed_px / total_px, 2),
        "total_regions": int(all_regions),
        "per_class": class_stats,
    }
