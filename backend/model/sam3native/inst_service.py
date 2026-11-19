"""Native SAM 3 instance segmentation service.

Two official SAM 3 heads share one checkpoint:

* interactive head (SAM-1/2 style) — point / box prompt -> one object per
  prompt, three masks of different granularity;
* grounding head — text prompt (class name) -> every instance of that concept
  with its own mask, score and box, plus a per-prompt semantic map
  (``semantic_seg``).

``InteractiveSegmentService`` (``lib/interactive_segmentor.py``) already drives
the interactive head, so it is composed here instead of re-implemented.
Nothing in this module touches the workstation's prompt pool / text cache
machinery: class prompts go straight to ``Sam3Processor.set_text_prompt``.
"""

import gc
import os
import sys
import time

import numpy as np
import torch
from PIL import Image

from backend.model.sam3native.interactive_segmentor import (
    InteractiveSegmentService,
    bf16_autocast,
    instance_palette_lut,
    mask_bbox,
)

BG_NAMES = {"background", "bg", "背景", "其他", "other", "clutter",
            "clutter/background"}
SEMANTIC_THRESHOLD = 0.5
BOX_IOU_MERGE = 0.6          # class prompts of one class producing the same box
MAX_INSTANCES = 255


def resolve_classes(spec):
    """Return (classes, palette, bg_idx) with a guaranteed background slot.

    ``spec`` comes from ``apps.desktop_app.dataset_spec`` / ``custom_spec``.
    When the spec has no background entry, a black ``background`` class is
    appended so the semantic map always has a fallback index.
    """
    classes = [str(name) for name in spec["classes"]]
    palette = [[int(v) for v in colour] for colour in spec["palette"]]
    bg_idx = spec.get("bg_idx")
    if bg_idx is None:
        lowered = [name.lower() for name in classes]
        bg_idx = next((i for i, name in enumerate(lowered)
                       if name in BG_NAMES), None)
    if bg_idx is None:
        classes.append("background")
        palette.append([0, 0, 0])
        bg_idx = len(classes) - 1
    return classes, palette, int(bg_idx)


class NativeInstanceService:
    """Owns one SAM 3 model and answers point / box / class prompts."""

    def __init__(self, logger=None, device=None):
        self.log = logger or (lambda message: None)
        self.device = device or ("cuda" if _cuda_available() else "cpu")
        self.interactive = InteractiveSegmentService(
            logger=self.log, device=self.device)
        self.processor = None
        self.image_path = None
        self.class_result = None
        self.class_key = None

    # -- model / image ----------------------------------------------------
    def ensure_model(self, resolution=1008):
        """Build the interactive SAM 3 model (grounding head shares weights)."""
        self.interactive.ensure_model(resolution)
        self.processor = self.interactive.processor
        if self.processor is None:
            raise RuntimeError("SAM3 processor 未就绪。")

    def set_image(self, path, resolution=1008):
        """Encode the image once; both heads reuse the same state."""
        result = self.interactive.set_image(path, resolution)
        self.processor = self.interactive.processor
        self.image_path = os.path.abspath(path)
        self.class_result = None
        self.class_key = None
        return result

    @property
    def image(self):
        return self.interactive.image

    @property
    def width(self):
        return self.interactive.width

    @property
    def height(self):
        return self.interactive.height

    @property
    def is_ready(self):
        return self.interactive.is_ready

    def predict(self, points=(), point_labels=(), box=None, multimask=True):
        """Point / box prompt -> one object (three candidates)."""
        return self.interactive.predict(points, point_labels, box, multimask)

    def predict_box_instances(self, box):
        """Native box-exemplar grounding -> every matching instance.

        SAM 3 treats a geometric box prompt as an exemplar: the grounding head
        returns all instances similar to the boxed object (image-wide), not a
        ROI crop. ``box`` is (x0, y0, x1, y1) in original pixel coordinates.
        """
        state = self.interactive.state
        if state is None:
            raise RuntimeError("请先载入影像。")
        width, height = self.width, self.height
        x0, y0, x1, y1 = (float(v) for v in box)
        centre_x = (x0 + x1) / 2.0 / width
        centre_y = (y0 + y1) / 2.0 / height
        norm_w = max((x1 - x0) / width, 1e-4)
        norm_h = max((y1 - y0) / height, 1e-4)

        started = time.time()
        self.processor.reset_all_prompts(state)
        with torch.inference_mode(), bf16_autocast():
            # add_geometric_prompt feeds the dummy "visual" text itself
            self.processor.add_geometric_prompt(
                [centre_x, centre_y, norm_w, norm_h], True, state)

        masks = state["masks"].detach().cpu().numpy()
        scores = state["scores"].detach().float().cpu().numpy()
        items = []
        for index in range(masks.shape[0]):
            raw = np.squeeze(masks[index])
            if raw.ndim != 2:
                continue
            mask = np.array(raw, dtype=bool, copy=True)
            area = int(mask.sum())
            if area == 0:
                continue
            items.append({
                "name": None,            # assigned by the GUI (标注名称)
                "class_index": None,
                "score": float(scores[index]),
                "iou": None,
                "mask": mask,
                "area": area,
                "coverage": float(area) / float(mask.size),
                "bbox": mask_bbox(mask),
                "prompts": {"box": [round(x0, 1), round(y0, 1),
                                    round(x1, 1), round(y1, 1)]},
                "source": "box",
            })
        items = _merge_same_box(items)
        elapsed = time.time() - started
        self.log(f"框选实例分割：{len(items)} 个实例，用时 {elapsed:.1f} 秒。")
        return {"instances": items, "seconds": elapsed}

    # -- class prompts ----------------------------------------------------
    def run_classes(self, spec, prompts_per_class=None, resolution="1008",
                    postprocess=None, force=False):
        """Segment every instance of every class with native text grounding.

        ``prompts_per_class`` holds one synonym list per class (same order as
        ``spec["classes"]``); missing entries fall back to the class name.
        Returns a dict with ``instances``, ``pred`` (semantic class map),
        ``classes``, ``palette``, ``bg_idx``, ``stats`` and ``seconds``.
        """
        key = (self.image_path, spec["key"], int(resolution),
               _postprocess_key(postprocess),
               _prompts_key(prompts_per_class))
        if self.class_result is not None and self.class_key == key and not force:
            return self.class_result

        self.ensure_model(int(resolution))
        state = self.interactive.state
        if state is None:
            raise RuntimeError("请先载入影像。")

        classes, palette, bg_idx = resolve_classes(spec)
        prompts = _normalise_prompts(classes, prompts_per_class)
        post = postprocess or {}
        min_area = int(post.get("min_area", 0)) if post.get("enabled") else 0
        max_hole = int(post.get("max_hole_area", 0)) if post.get("enabled") else 0

        builder = _ClassMapBuilder(self.height, self.width, len(classes))
        instances = []
        started = time.time()
        for class_index, name in enumerate(classes):
            if class_index == bg_idx:
                continue
            collected = []
            for prompt in prompts[class_index]:
                items, semantic = self._prompt_once(prompt, class_index, name)
                if semantic is not None:
                    builder.update(class_index, semantic)
                collected.extend(items)
                self.log(f"类别 {name}／提示 {prompt}：{len(items)} 个实例")
            collected = _merge_same_box(collected)
            for item in collected:
                item = _refine_instance(item, max_hole)
                if item is not None and item["area"] >= max(min_area, 1):
                    instances.append(item)

        pred = builder.finish(bg_idx, SEMANTIC_THRESHOLD)
        pred_raw = pred.copy()
        changed = 0.0
        if post.get("enabled"):
            from backend.model.sam3native.mask_postprocess import postprocess_class_map

            pred = postprocess_class_map(
                pred,
                smooth_kernel=int(post.get("smooth_kernel", 0)),
                min_area=int(post.get("min_area", 0)),
                max_hole_area=int(post.get("max_hole_area", 0)),
                background_idx=bg_idx,
                num_classes=len(classes),
            ).astype(np.int64)
            changed = float((pred != pred_raw).mean())

        elapsed = time.time() - started
        counts = np.bincount(pred.ravel(), minlength=len(classes))
        total = max(int(counts.sum()), 1)
        stats = [(name, float(counts[i]) / total, palette[i])
                 for i, name in enumerate(classes)
                 if i < counts.size and counts[i] > 0]

        if len(instances) > MAX_INSTANCES:
            self.log(f"实例数 {len(instances)} 超过 {MAX_INSTANCES}，已截断。")
            instances = instances[:MAX_INSTANCES]

        result = {
            "instances": instances,
            "pred": pred,
            "pred_raw": pred_raw,
            "changed_ratio": changed,
            "classes": classes,
            "palette": palette,
            "bg_idx": bg_idx,
            "stats": stats,
            "spec": spec,
            "resolution": int(resolution),
            "postprocess": dict(post),
            "seconds": elapsed,
        }
        self.class_result = result
        self.class_key = key
        self.log(f"类别实例分割完成：{len(instances)} 个实例，"
                 f"{len(stats)} 类，用时 {elapsed:.1f} 秒。")
        return result

    def _prompt_once(self, prompt, class_index, class_name):
        """Run one native text prompt; return (instances, semantic map)."""
        state = self.interactive.state
        self.processor.reset_all_prompts(state)
        _install_semantic_hook(self.processor.model)
        with torch.inference_mode(), bf16_autocast():
            self.processor.set_text_prompt(prompt, state)
            # 本机 pip 版 sam3 处理器不写 semantic_mask_logits，从暂存的模型输出补齐
            semantic = state.get("semantic_mask_logits")
            if semantic is None:
                semantic = _semantic_from_last_grounding(self.processor, state)

        masks = state["masks"].detach().cpu().numpy()
        scores = state["scores"].detach().float().cpu().numpy()
        if semantic is not None:
            semantic = np.squeeze(semantic.detach().float().cpu().numpy())
            if semantic.ndim != 2:
                semantic = None

        items = []
        for index in range(masks.shape[0]):
            mask = np.squeeze(masks[index])
            if mask.ndim != 2:
                continue
            mask = np.array(mask, dtype=bool, copy=True)
            area = int(mask.sum())
            if area == 0:
                continue
            items.append({
                "name": class_name,
                "class_index": int(class_index),
                "score": float(scores[index]),
                "iou": float(scores[index]),
                "mask": mask,
                "area": area,
                "coverage": float(area) / float(mask.size),
                "bbox": mask_bbox(mask),
                "prompts": {"text": prompt},
                "source": "class",
            })
        return items, semantic

    # -- output -----------------------------------------------------------
    def save_result(self, instances, out_dir, overlay_alpha=0.55):
        """写实例 label / colour / overlay 掩码与 COCO JSON（含语义图三件）。"""
        if not instances:
            raise ValueError("没有可导出的实例。")
        semantic = self.class_result
        width, height = self.width, self.height
        if not width or not height:
            raise RuntimeError("请先载入影像。")
        out_dir = os.path.abspath(out_dir)
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.image_path or "image"))[0]
        base = _unique_base(out_dir, f"{stem}_instances")

        used = instances[:MAX_INSTANCES]
        label = np.zeros((height, width), dtype=np.uint8)
        lut = instance_palette_lut()
        # 实例颜色：点选/框选 = 组颜色，类别 = 类别色；未指定时用默认调色板
        for offset, item in enumerate(used, start=1):
            colour = item.get("colour")
            if colour is not None:
                lut[offset] = np.asarray(colour, dtype=np.uint8)
        rgb = np.asarray(self.image.convert("RGB")).astype(np.float32)
        overlay = rgb.copy()
        for offset, item in enumerate(used, start=1):
            mask = np.asarray(item["mask"]).astype(bool)
            if mask.shape != (height, width):
                continue
            label[mask] = offset
            colour = lut[offset].astype(np.float32)
            overlay[mask] = (overlay[mask] * (1.0 - overlay_alpha)
                             + colour * overlay_alpha)
        Image.fromarray(label, mode="L").save(base + "_label.png")
        Image.fromarray(lut[label]).save(base + "_colour.png")
        Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8)).save(
            base + "_overlay.png")

        paths = [base + "_label.png", base + "_colour.png", base + "_overlay.png"]
        semantic_paths = []
        if semantic is not None:
            semantic_paths = self._save_semantic(semantic, base, rgb,
                                                 overlay_alpha)
            paths.extend(semantic_paths)

        # 格式 2：COCO 标注 JSON（掩码多边形 + bbox + 类别 + score）
        from backend.model.sam3native.coco_export import write_coco
        coco_path = base + "_coco.json"
        write_coco(coco_path, self.image_path or "image", width, height, used)
        paths.append(coco_path)
        return paths

    def _save_semantic(self, semantic, base, rgb, overlay_alpha):
        """Write the per-class semantic map as label / colour / overlay."""
        pred = semantic["pred"].astype(np.int64)
        palette = np.asarray(semantic["palette"], dtype=np.uint8)
        lut = np.zeros((max(256, palette.shape[0]), 3), dtype=np.uint8)
        lut[:palette.shape[0]] = palette
        Image.fromarray(pred.astype(np.uint8), mode="L").save(
            base + "_semantic_label.png")
        Image.fromarray(lut[pred]).save(base + "_semantic_colour.png")
        colour = lut[pred].astype(np.float32)
        non_bg = pred != int(semantic["bg_idx"])
        overlay = rgb.copy()
        overlay[non_bg] = (overlay[non_bg] * (1.0 - overlay_alpha)
                           + colour[non_bg] * overlay_alpha)
        Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8)).save(
            base + "_semantic_overlay.png")
        return [base + "_semantic_label.png", base + "_semantic_colour.png",
                base + "_semantic_overlay.png"]

    # -- teardown ---------------------------------------------------------
    def release_all(self):
        self.class_result = None
        self.class_key = None
        self.interactive.release()
        self.processor = None
        gc.collect()
        _empty_cache()


class _ClassMapBuilder:
    """Incremental argmax over per-class semantic maps (memory bounded)."""

    def __init__(self, height, width, num_classes):
        self.best = np.zeros((height, width), dtype=np.float32)
        self.index = np.full((height, width), -1, dtype=np.int16)
        self.num_classes = int(num_classes)

    def update(self, class_index, prob):
        """Keep, per pixel, the most probable class seen so far."""
        if prob is None or prob.shape != self.best.shape:
            return
        better = prob > self.best
        self.best[better] = prob[better]
        self.index[better] = int(class_index)

    def finish(self, bg_idx, threshold):
        confident = self.best >= float(threshold)
        return np.where(confident, self.index, int(bg_idx)).astype(np.int64)


def _normalise_prompts(classes, prompts_per_class):
    """One synonym list per class, class name as the first prompt."""
    prompts = []
    for index, name in enumerate(classes):
        line = None
        if prompts_per_class is not None and index < len(prompts_per_class):
            line = prompts_per_class[index]
        if isinstance(line, str):
            line = [line]
        cells = [str(cell).strip() for cell in (line or []) if str(cell).strip()]
        if name not in cells:
            cells.insert(0, name)
        prompts.append(cells)
    return prompts


def _merge_same_box(items, threshold=BOX_IOU_MERGE):
    """Drop near-duplicate instances produced by synonym prompts."""
    kept = []
    for item in sorted(items, key=lambda value: -value["score"]):
        box = item.get("bbox")
        if box is not None and any(
                _box_iou(box, other["bbox"]) >= threshold for other in kept
                if other.get("bbox") is not None):
            continue
        kept.append(item)
    return kept


def _box_iou(a, b):
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    inter = max(0, x1 - x0 + 1) * max(0, y1 - y0 + 1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0] + 1) * (a[3] - a[1] + 1)
    area_b = (b[2] - b[0] + 1) * (b[3] - b[1] + 1)
    return inter / float(area_a + area_b - inter)


def _refine_instance(item, max_hole_area):
    """Optionally fill small closed holes, then refresh derived fields."""
    if item is None:
        return None
    mask = item["mask"]
    if max_hole_area > 0:
        mask = _fill_small_holes(mask, int(max_hole_area))
    area = int(mask.sum())
    if area == 0:
        return None
    item["mask"] = mask
    item["area"] = area
    item["coverage"] = float(area) / float(mask.size)
    item["bbox"] = mask_bbox(mask)
    return item


def _fill_small_holes(mask, max_area):
    """Fill background holes that are small and do not touch the border."""
    import cv2

    height, width = mask.shape
    inverse = (~mask).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        inverse, connectivity=4)
    filled = mask.copy()
    for component in range(1, count):
        x, y, box_w, box_h, area = stats[component]
        if area > max_area:
            continue
        if x == 0 or y == 0 or x + box_w >= width or y + box_h >= height:
            continue
        filled[labels == component] = True
    return filled


def _unique_base(out_dir, stem):
    index = 1
    while True:
        base = os.path.join(out_dir, f"{stem}_{index:03d}")
        if not any(os.path.exists(base + suffix)
                   for suffix in ("_label.png", "_overlay.png", ".json")):
            return base
        index += 1


def _prompts_key(prompts_per_class):
    if not prompts_per_class:
        return ()
    return tuple(tuple(cells) if not isinstance(cells, str) else (cells,)
                 for cells in prompts_per_class)


def _postprocess_key(postprocess):
    if not postprocess:
        return ()
    return tuple(sorted((k, int(v) if isinstance(v, (int, float)) else str(v))
                        for k, v in postprocess.items()))


def _install_semantic_hook(model):
    """包一层模型 forward_grounding，暂存原始输出 dict（幂等）。

    工作站版 sam3 处理器会把 outputs["semantic_seg"] 插值后写入
    state["semantic_mask_logits"]；本机 pip 版处理器丢弃了该键。
    分割头本身两端一致（checkpoint 含 semantic_seg_head 权重），
    这里只暂存输出，由 _semantic_from_last_grounding 按同口径补齐。
    """
    if getattr(model, "_sam3native_semantic_hook", False):
        return
    original = model.forward_grounding

    def wrapped(*args, **kwargs):
        out = original(*args, **kwargs)
        model._sam3native_last_grounding = out
        return out

    model.forward_grounding = wrapped
    model._sam3native_semantic_hook = True


def _semantic_from_last_grounding(processor, state):
    """与工作站处理器同口径：semantic_seg 双线性插值到原图尺寸 + sigmoid。"""
    out = getattr(processor.model, "_sam3native_last_grounding", None)
    if not out or "semantic_seg" not in out:
        return None
    import torch.nn.functional as F

    size = (int(state["original_height"]), int(state["original_width"]))
    return F.interpolate(
        out["semantic_seg"], size, mode="bilinear", align_corners=False
    ).sigmoid()


def _cuda_available():
    try:
        return torch.cuda.is_available()
    except Exception:
        return False


def _empty_cache():
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
