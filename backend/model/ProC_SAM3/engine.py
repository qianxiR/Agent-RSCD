"""ProC-SAM3 适配层。

把 `E:\\1代码\\模型\\变化检测\\ProC-SAM3-main` 的 CachedSAM3OpenSegmentor 接到
现有 `run_segment` 契约上：输入影像 + 英文类别（可带同义词），输出类别索引掩膜。

- 类别格式：`building, house, roof; water, river, lake`（分号分组，逗号同义词）
- 首类自动补 `background, other`，并固定为索引 0
- 模型按「类别文件 + 分辨率」缓存；换类别会重载（SAM3 权重加载较慢）
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.config import settings

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MODEL: Any = None
_MODEL_KEY: Optional[str] = None
_PROMPT_KEY: Optional[str] = None
_PATHS_READY = False

# 七大类默认英文提示（含同义词）；用户从 UI 传入时按分号分组覆盖
DEFAULT_CLASS_GROUPS: List[Tuple[str, List[str]]] = [
    ("building", ["house", "roof", "structure"]),
    ("road", ["street", "highway", "pavement"]),
    ("water", ["river", "lake", "pond", "sea"]),
    ("vegetation", ["tree", "grass", "forest", "shrub"]),
    ("farmland", ["crop", "field", "agricultural", "cropland"]),
    ("bareland", ["soil", "ground", "dirt", "sand"]),
]

# 兼容旧接口/旧会话中的中文类别；新 UI 一律发送英文
_CN_TO_EN = {
    "建筑": "building", "建筑物": "building", "房屋": "building", "屋顶": "building",
    "道路": "road", "公路": "road", "街道": "road",
    "水体": "water", "水": "water", "河流": "water", "湖泊": "water",
    "植被": "vegetation", "森林": "vegetation", "草地": "vegetation", "树": "vegetation",
    "农田": "farmland", "耕地": "farmland", "农作物": "farmland",
    "裸地": "bareland", "裸土": "bareland", "土壤": "bareland",
}

_EN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _\-/]*$")


def _root() -> Path:
    return Path(settings.proc_sam3_root).expanduser().resolve()


def _proj_data() -> None:
    """本机 PROJ_LIB 指向 PostGIS 旧库时，避免 ProC 内部 GDAL 读取异常。"""
    try:
        import rasterio
        proj = Path(rasterio.__file__).resolve().parent / "proj_data"
        if (proj / "proj.db").is_file():
            os.environ.setdefault("PROJ_DATA", str(proj))
            os.environ.setdefault("PROJ_LIB", str(proj))
    except Exception:  # noqa: BLE001
        pass


def _ensure_paths() -> None:
    global _PATHS_READY
    if _PATHS_READY:
        return
    root = _root()
    if not root.is_dir():
        raise RuntimeError(f"ProC-SAM3 目录不存在: {root}")
    vendor = root / "_vendor"
    for path in (str(root), str(vendor)):
        if path not in sys.path:
            sys.path.insert(0, path)
    os.environ.setdefault("PROC_SAM3_DISABLE_YAPF", "1")
    os.environ.setdefault("PROC_SAM3_WEIGHTS_ROOT", str(settings.proc_sam3_weights_root))
    _proj_data()
    _PATHS_READY = True


def available() -> bool:
    root = _root()
    if not root.is_dir():
        return False
    if not (root / "sam3_segmentor_cached.py").is_file():
        return False
    if not (root / "configs" / "cfg_vaihingen.py").is_file():
        return False
    weights = Path(settings.proc_sam3_weights_root) / "sam3" / "sam3.pt"
    if not weights.is_file():
        return False
    return importlib.util.find_spec("torch") is not None


def _norm_token(token: str) -> str:
    text = str(token or "").strip().strip(",;")
    if not text:
        return ""
    if text in _CN_TO_EN:
        return _CN_TO_EN[text]
    if text.lower() in ("background", "bg"):
        return "background"
    if not _EN_RE.match(text):
        raise ValueError(
            f"类别 '{text}' 不是英文；请使用英文类别名（可带英文同义词），例如 "
            "building, house, roof"
        )
    return text


def normalize_class_groups(classes: Optional[str]) -> List[Tuple[str, List[str]]]:
    """把输入解析为 [(primary, [synonyms...]), ...]；不包含 background。"""
    text = (classes or "").strip()
    groups: List[Tuple[str, List[str]]] = []
    if not text:
        return [(name, list(syns)) for name, syns in DEFAULT_CLASS_GROUPS]

    # 容错：Agent 若把“提取建筑和水体”这类中文自然语言直接传入，先抽取已知类别词。
    if not re.search(r"[A-Za-z]", text):
        found: List[str] = []
        for cn, en in sorted(_CN_TO_EN.items(), key=lambda item: len(item[0]), reverse=True):
            if cn in text and en not in found:
                found.append(en)
        if found:
            text = "; ".join(found)

    # 分号/换行 = 不同类别；没有分隔符时整行按逗号解析，由下方判定
    # “逗号后是同义词”还是“多个独立类别”。
    raw_groups = [g.strip() for g in re.split(r"[;\n]+", text) if g.strip()]

    seen = set()
    default_syns = {name: set(syns) for name, syns in DEFAULT_CLASS_GROUPS}
    for raw in raw_groups:
        tokens = []
        for part in re.split(r"[,，]", raw):
            token = _norm_token(part)
            if token:
                tokens.append(token)
        if not tokens:
            continue
        # 没有分号时: "building, house, roof" 若后项都是首词的同义词，则合并为一组；
        # 否则按 "building, water" 视为两个独立类别（保持输入顺序）。
        if len(tokens) > 1 and all(t in default_syns.get(tokens[0], ()) for t in tokens[1:]):
            pairs = [(tokens[0], tokens[1:])]
        else:
            pairs = [(token, []) for token in tokens]
        for primary, synonyms in pairs:
            if primary == "background":
                continue
            key = primary.lower()
            if key in seen:
                continue
            seen.add(key)
            groups.append((primary, synonyms))
    if not groups:
        return [(name, list(syns)) for name, syns in DEFAULT_CLASS_GROUPS]
    # 只给了主类名时，自动补齐默认英文同义词（保证模型仍拿到同义词提示）
    default_map = {name: syns for name, syns in DEFAULT_CLASS_GROUPS}
    expanded = []
    for primary, synonyms in groups:
        if not synonyms and primary.lower() in default_map:
            synonyms = list(default_map[primary.lower()])
        expanded.append((primary, synonyms))
    return expanded


def _class_lines(classes: Optional[str]) -> List[str]:
    groups = normalize_class_groups(classes)
    lines = ["background, other"]
    for primary, synonyms in groups:
        lines.append(", ".join([primary] + [s for s in synonyms if s != primary]))
    return lines


def _class_file(classes: Optional[str]) -> Tuple[Path, List[str]]:
    lines = _class_lines(classes)
    digest = hashlib.md5("\n".join(lines).encode("utf-8")).hexdigest()[:12]
    root = Path(settings.file_storage_root).resolve() / "proc_sam3" / "classes"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"classes_{digest}.txt"
    if not path.is_file():
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, lines


def _apply_prompt_bank(model: Any, class_file: Path) -> None:
    """只替换文本提示，不重载 SAM3 权重。

    ProC 的 PromptBank 负责类别解析 + 文本编码；SAM3 图像编码器/解码器与类别无关。
    换类别时重建 PromptBank（文本嵌入有磁盘/内存缓存），把 segmentor 与执行引擎的
    prompt 引用一起替换，避免每次改类别都重新加载 3GB 权重。
    """
    from core.prompt_bank import PromptBank

    prompt_bank = PromptBank(
        processor=model.processor,
        classname_path=str(class_file),
        device=model.device,
        expanded_prompt_pool_path=None,
        cache_text_embeddings=True,
        enable_expanded_prompt=False,
        inference_dtype=getattr(model, "inference_dtype", "fp32"),
    )
    model.prompt_bank = prompt_bank
    model.query_words = prompt_bank.query_words
    model.query_idx = prompt_bank.query_idx
    model.text_embeddings = prompt_bank.text_embeddings
    model.cache_key = prompt_bank.cache_key
    model.num_cls = prompt_bank.num_cls
    model.num_queries = prompt_bank.num_queries
    for engine in (getattr(model, "execution_engine", None),
                   getattr(model, "coarse_execution_engine", None)):
        if engine is None:
            continue
        engine.prompt_bank = prompt_bank
        engine.num_queries = prompt_bank.num_queries
        engine.prompt_batch_size = prompt_bank.num_queries


def _release_model() -> None:
    global _MODEL, _MODEL_KEY, _PROMPT_KEY
    _MODEL = None
    _MODEL_KEY = None
    _PROMPT_KEY = None
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def _device() -> str:
    try:
        import torch
        if settings.proc_sam3_device:
            return settings.proc_sam3_device
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _load_model(classes: Optional[str]):
    global _MODEL, _MODEL_KEY, _PROMPT_KEY
    _ensure_paths()
    class_file, lines = _class_file(classes)
    # ★ 权重复用：模型只由分辨率/设备决定；类别变化只换 PromptBank。
    key = f"{settings.proc_sam3_resolution}|{_device()}"
    if _MODEL is not None and _MODEL_KEY == key:
        if _PROMPT_KEY != str(class_file):
            logger.info("[ProC-SAM3] 复用模型，切换提示词: %s", class_file.name)
            _apply_prompt_bank(_MODEL, class_file)
            _PROMPT_KEY = str(class_file)
        return _MODEL, lines

    from mmengine.config import Config
    from sam3_segmentor_cached import CachedSAM3OpenSegmentor

    cfg = Config.fromfile(str(_root() / "configs" / "cfg_vaihingen.py"))
    model_cfg = {k: v for k, v in cfg.model.items() if k != "type"}
    model_cfg.update({
        "classname_path": str(class_file),
        "expanded_prompt_pool_path": None,
        "enable_expanded_prompt": False,
        "bg_idx": 0,
        "device": _device(),
        "processor_resolution": int(settings.proc_sam3_resolution),
        "image_batch_size": 1,
        "slot_batch_size": 4,
        "execution_mode": "per_image",
        "group_images_by_size": False,
        "shared_image_encoder_batch": False,
    })
    logger.info("[ProC-SAM3] 加载模型: classes=%s resolution=%s device=%s",
                class_file.name, settings.proc_sam3_resolution, model_cfg["device"])
    _release_model()
    model = CachedSAM3OpenSegmentor(**model_cfg)
    _MODEL = model
    _MODEL_KEY = key
    _PROMPT_KEY = str(class_file)
    logger.info("[ProC-SAM3] 模型加载完成")
    return model, lines


def segment(image_path: str, classes: Optional[str] = None) -> Dict[str, Any]:
    """单图语义分割，返回 {seg, class_lines, bg_idx, resolution}。"""
    import numpy as np
    from PIL import Image

    _ensure_paths()
    with _LOCK:
        model, lines = _load_model(classes)
        import torch
        from mmseg.structures import SegDataSample

        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        sample = SegDataSample()
        sample.set_metainfo({"img_path": str(image_path), "ori_shape": (height, width)})
        with torch.inference_mode():
            result = model.predict([image], [sample])[0]
        seg = result.pred_sem_seg.data.cpu().numpy().squeeze().astype(np.int64)
        if seg.shape != (height, width):
            seg = np.array(Image.fromarray(seg.astype(np.uint8)).resize(
                (width, height), Image.NEAREST))
        return {
            "seg": seg,
            "class_lines": lines,
            "bg_idx": 0,
            "resolution": int(settings.proc_sam3_resolution),
        }


def release() -> None:
    with _LOCK:
        _release_model()
