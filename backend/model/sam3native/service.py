# -*- coding: utf-8 -*-
"""原生 SAM3 引擎包装：对齐 SamSeg.runner 的产物契约。

segment(): 单图 → 语义类别图 seg (H,W) int64（0=background）+ class_lines + 实例列表。
change():  T1/T2 → 两期语义图 + 实例级变化掩码。
runner 负责调色板/PNG/GeoTIFF/矢量/统计；本模块只负责感知。
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# 全局单例（一次加载，多请求复用）；交互头与接地头共享同一份权重
_SERVICE = None
_SERVICE_LOCK = threading.Lock()

# 类别行（首行 background）——与 SamSeg.runner 口径一致，保证产物契约不变
_DEFAULT_CLASSES = [
    "background",
    "building,house,roof,structure",
    "road,highway,street,pavement,expressway",
    "water,lake,river,pond,pool,sea",
    "bareland,barren,soil,ground,dirt,sand",
    "vegetation,forest,tree,grass,lawn,meadow,shrub,woodland",
    "farmland,agricultural,crop,farm,field,cropland,plantation",
]

_CN_TO_EN_CLASS = {
    "建筑": "building", "建筑物": "building", "房屋": "building", "屋顶": "building",
    "房子": "building", "构筑物": "building",
    "道路": "road", "公路": "road", "街道": "road", "路": "road", "高速": "road",
    "水": "water", "水体": "water", "河流": "water", "湖泊": "water", "湖": "water",
    "河": "water", "池塘": "water", "水域": "water", "海": "water", "海洋": "water",
    "裸地": "bareland", "裸土": "bareland", "土壤": "bareland", "沙地": "bareland",
    "荒地": "bareland", "泥地": "bareland",
    "植被": "vegetation", "森林": "vegetation", "树林": "vegetation", "树": "vegetation",
    "草地": "vegetation", "草": "vegetation", "灌木": "vegetation", "林地": "vegetation",
    "农田": "farmland", "耕地": "farmland", "庄稼": "farmland", "田地": "farmland",
    "农作物": "farmland", "田": "farmland",
}


def normalize_classes(classes: Optional[str]) -> List[str]:
    """
    入参: classes 逗号分隔类别名（中文/英文/任意开放词汇词）。
    方法: 中文映射英文标准名、已知类走同义词增强、去重保序；空则回退默认 7 类。
    出参: ["background", "<类1同义词行>", ...]，第 0 行固定 background。
    """
    if not classes or not classes.strip():
        return list(_DEFAULT_CLASSES)

    parts = [c.strip() for c in (classes or "").replace("，", ",").split(",") if c.strip()]
    if not parts:
        return list(_DEFAULT_CLASSES)

    syn_map = {line.split(",")[0]: line for line in _DEFAULT_CLASSES}
    result_lines: List[str] = []
    seen = set()
    for part in parts:
        en = _CN_TO_EN_CLASS.get(part, part.lower()).strip()
        if not en or en == "background":
            continue
        dedup_key = en.split(",")[0]
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        result_lines.append(syn_map.get(dedup_key, en))

    if not result_lines:
        return list(_DEFAULT_CLASSES)
    return ["background"] + result_lines


def _get_service():
    """惰性创建 NativeInstanceService 单例。"""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            from backend.model.sam3native.inst_service import NativeInstanceService
            device = None
            try:
                from backend.config import settings
                device = settings.sam3_native_device or None
            except Exception:
                device = None
            _SERVICE = NativeInstanceService(logger=logger.info, device=device)
        return _SERVICE


def reset_service() -> None:
    """释放全局服务与显存（release 接口调用）。"""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is not None:
            try:
                _SERVICE.release_all()
            except Exception as exc:
                logger.warning(f"释放 SAM3 服务失败: {exc}")
            _SERVICE = None


def _infer_module():
    """加载 SamSeg 子项目的 infer 工具模块（palette/postprocess/change_map）。

    sam3 先由 engine.ensure_installed_sam3() 解析到 site-packages 官方包，
    再把 SamSeg/SamSeg 目录 append 到 sys.path 末尾导入 infer，避免旧 sam3 遮蔽。
    """
    global _INFER
    if _INFER is not None:
        return _INFER
    from backend.model.SamSeg.engine import ensure_installed_sam3
    ensure_installed_sam3()
    samseg_dir = str(Path(__file__).resolve().parent.parent / "SamSeg" / "SamSeg")
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(samseg_dir).resolve()]
    if samseg_dir not in sys.path:
        sys.path.append(samseg_dir)
    import infer  # noqa: E402
    _INFER = infer
    return _INFER


_INFER = None


def _resolution() -> str:
    try:
        from backend.config import settings
        value = str(getattr(settings, "sam3_native_resolution", "1008"))
        return value if value in _RESOLUTIONS else "1008"
    except Exception:
        return "1008"


def _postprocess_params() -> Dict:
    try:
        from backend.config import settings
        enabled = bool(getattr(settings, "samseg_postprocess", False))
    except Exception:
        enabled = False
    if not enabled:
        return {"enabled": False}
    return {
        "enabled": True,
        "smooth_kernel": 3,
        "min_area": 2056,
        "max_hole_area": 4096,
    }


def _build_spec(class_lines: List[str]) -> Dict:
    """class_lines（background 在第 0 行）→ run_classes 所需 spec。"""
    classes = [line.split(",")[0].strip() for line in class_lines]
    palette = []
    base = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48)]
    for idx in range(len(classes)):
        palette.append([0, 0, 0] if idx == 0 else list(base[(idx - 1) % len(base)]))
    return {
        "kind": "agent", "tag": "agent", "key": ":".join(classes),
        "classes": classes, "palette": palette, "bg_idx": 0,
    }


def _prompts_for(class_lines: List[str]) -> List[List[str]]:
    """class_lines 同义词行 → run_classes 的 prompts_per_class（背景行不发提示）。"""
    prompts = [["background"]]
    for line in class_lines[1:]:
        synonyms = [s.strip() for s in line.split(",") if s.strip()]
        prompts.append(synonyms or [line])
    return prompts


def _resize_nearest(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    if arr.shape == (height, width):
        return arr
    from PIL import Image as _Image
    return np.array(_Image.fromarray(arr.astype(np.uint8)).resize((width, height), _Image.NEAREST))


def segment(image_path: str, classes: Optional[str] = None) -> Dict:
    """
    入参: image_path 影像路径；classes 逗号分隔类别（中文/英文/开放词汇）。
    方法: NativeInstanceService.run_classes 文本接地逐实例推理 → 语义图；nearest 缩放回原图尺寸。
    出参: {seg (H,W) int64（0=background）, class_lines, instances, resolution, seconds}。
    """
    started = time.perf_counter()
    from PIL import Image as _Image
    _Image.MAX_IMAGE_PIXELS = None
    with _Image.open(image_path) as probe:
        width, height = probe.size

    service = _get_service()
    resolution = _resolution()
    # 换图或服务未就绪时重新编码图像特征（同图复用上次编码）
    if service.image_path != image_path or not service.is_ready:
        service.set_image(image_path, resolution)
    class_lines = normalize_classes(classes)
    result = service.run_classes(
        _build_spec(class_lines), _prompts_for(class_lines),
        resolution=resolution, postprocess=_postprocess_params(),
    )
    seg = _resize_nearest(np.asarray(result["pred"]), width, height)
    return {
        "seg": seg,
        "class_lines": class_lines,
        "instances": result.get("instances") or [],
        "resolution": result.get("resolution"),
        "seconds": round(time.perf_counter() - started, 3),
    }


def change(t1_path: str, t2_path: str, classes: Optional[str] = None,
           iou_threshold: float = 0.3, min_area: int = 200,
           harmonize: Optional[str] = None) -> Dict:
    """
    入参: t1/t2 双时相影像；classes 类别；iou/min_area 变化对齐口径；
          harmonize 色调一致性口径（none/lab/lum/hist/linear，缺省读配置）。
    方法: 可选先把 T2 拉齐到 T1 色调（避免亮度漂移把暗图整幅判成背景、凭空造变化）
          → 两期各跑一次 segment → 尺寸对齐 → 实例级对比出变化掩码。
    出参: {t1_mask, t2_mask, change_mask, class_lines, harmonize, seconds}。
    """
    started = time.perf_counter()
    from PIL import Image as _Image
    _Image.MAX_IMAGE_PIXELS = None
    with _Image.open(t1_path) as p1, _Image.open(t2_path) as p2:
        width = max(p1.size[0], p2.size[0])
        height = max(p1.size[1], p2.size[1])

    mode = harmonize
    if mode is None:
        try:
            from backend.config import settings
            mode = settings.samseg_change_harmonize
        except Exception:
            mode = "none"
    mode = str(mode or "none").lower()

    t2_for_seg = t2_path
    temp_t2 = None
    if mode != "none":
        from backend.model.SamSeg.harmonize import harmonize as _harmonize
        import tempfile
        ref = _Image.open(t1_path).convert("RGB")
        target = _Image.open(t2_path).convert("RGB")
        if target.size != ref.size:
            target = target.resize(ref.size, _Image.BILINEAR)
        matched = _harmonize(np.asarray(target), np.asarray(ref), mode)
        handle = tempfile.NamedTemporaryFile(suffix="_harm_t2.png", delete=False)
        handle.close()
        _Image.fromarray(matched).save(handle.name)
        temp_t2 = handle.name
        t2_for_seg = temp_t2

    try:
        t1 = segment(t1_path, classes)
        t2 = segment(t2_for_seg, classes)
    finally:
        if temp_t2:
            import os as _os
            try:
                _os.unlink(temp_t2)
            except OSError:
                pass
    t1_mask = _resize_nearest(np.asarray(t1["seg"]), width, height)
    t2_mask = _resize_nearest(np.asarray(t2["seg"]), width, height)

    infer = _infer_module()
    change_mask = infer.compute_instance_change_map(
        t1_mask, t2_mask, len(t1["class_lines"]),
        iou_threshold=iou_threshold, min_area=max(min_area, 4),
    )
    return {
        "t1_mask": t1_mask,
        "t2_mask": t2_mask,
        "change_mask": np.asarray(change_mask),
        "class_lines": t1["class_lines"],
        "harmonize": mode,
        "seconds": round(time.perf_counter() - started, 3),
    }
