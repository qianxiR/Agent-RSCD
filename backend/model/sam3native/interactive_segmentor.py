"""Interactive point / box segmentation backend for native SAM3.

Ported from the workstation ProC-SAM3 project; drives SAM 3's
promptable-concept head with cached text embeddings.  The same checkpoint also
contains an interactive (SAM-1 style) head which takes clicks and boxes.  This
module drives that head:

    service = InteractiveSegmentService()
    service.ensure_model(1008)       # ~20 s, ~4.5 GB VRAM
    service.set_image("aerial.png")  # ~10-20 s, visual backbone runs once
    result = service.predict(points=[(430, 380)], point_labels=[1])
    result["masks"][result["best"]]  # boolean mask, original resolution

Every further click re-uses the cached image features and takes tens of
milliseconds, which is what makes click-to-segment feel interactive.

Nothing here imports Tk; the GUI lives in ``interactive_app.py`` and the
headless self-test in ``main()`` uses the same code path.
"""

import gc
import json
import os
import time

import sys

import numpy as np
from PIL import Image

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Same palette the batch GUI uses for overlays (RGB).
DEFAULT_MASK_COLOUR = (0, 174, 255)

#: Colours cycled through accepted instances (label 0 = background = black).
INSTANCE_PALETTE = [
    (230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48),
    (145, 30, 180), (0, 128, 128), (170, 110, 40), (128, 0, 0),
    (0, 0, 128), (128, 128, 0), (255, 225, 25), (70, 240, 240),
    (240, 50, 230), (210, 245, 60), (250, 190, 212), (0, 128, 128),
    (220, 190, 255), (170, 255, 195), (255, 216, 177), (255, 150, 150),
]


def bf16_autocast():
    """显式 bf16 autocast 上下文，所有 SAM3 模型调用必须包一层。

    SAM3 的跟踪预测器（``sam3_tracking_predictor``）在构建时进入 bf16
    autocast 且不退出，该状态是**线程本地**的：构建线程算出的图像特征是
    bf16，而 GUI 每次推理都在新线程里跑（无 autocast），fp32 权重遇到
    bf16 特征会报 ``expected scalar type BFloat16 but found Float``。
     显式声明 autocast 后，任何线程下 dtype 一致。
    """
    import torch

    return torch.autocast(device_type="cuda", dtype=torch.bfloat16)


def instance_palette_lut():
    """256x3 uint8 lookup table: label id -> RGB (0 = black background)."""
    lut = np.zeros((256, 3), dtype=np.uint8)
    for index in range(1, 256):
        lut[index] = INSTANCE_PALETTE[(index - 1) % len(INSTANCE_PALETTE)]
    return lut


def load_rgb_image(path):
    """Open an image and return an RGB ``PIL.Image``.

    Handles the three cases remote-sensing tiles throw at PIL: 16-bit TIFFs,
    4-band (RGBA / CMYK) GeoTIFFs and plain 8-bit RGB.  ``cv2.imread`` is
    avoided on purpose because it cannot read non-ASCII Windows paths.
    """
    with Image.open(path) as raw:
        image = raw.copy()

    if image.mode == "RGB":
        return image
    if image.mode in ("L", "P", "LA", "RGBA", "CMYK"):
        return image.convert("RGB")

    array = np.asarray(image)
    if array.ndim == 2:
        array = np.stack([array] * 3, axis=-1)
    elif array.ndim == 3 and array.shape[2] >= 3:
        array = array[:, :, :3]
    else:
        return image.convert("RGB")

    if array.dtype != np.uint8:
        array = array.astype(np.float32)
        lo, hi = float(array.min()), float(array.max())
        if hi > lo:
            array = (array - lo) / (hi - lo) * 255.0
        else:
            array = np.zeros_like(array)
        array = array.astype(np.uint8)
    return Image.fromarray(array, mode="RGB")


def mask_bbox(mask):
    """(x0, y0, x1, y1) of the True pixels, or None for an empty mask."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def blend_mask(rgb, mask, colour=DEFAULT_MASK_COLOUR, alpha=0.55):
    """Return a copy of ``rgb`` (HxWx3 uint8) with ``mask`` tinted."""
    out = np.array(rgb, dtype=np.float32, copy=True)
    if mask is not None and mask.any():
        tint = np.asarray(colour, dtype=np.float32)
        region = mask.astype(bool)
        out[region] = out[region] * (1.0 - alpha) + tint * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


class InteractiveSegmentService:
    """Loads SAM 3 once and answers point / box prompts for one image at a time."""

    def __init__(self, logger=None, device=None):
        self.log = logger or (lambda message: None)
        self.device = device or ("cuda" if _cuda_available() else "cpu")
        self.model = None
        self.processor = None
        self.model_key = None
        self.image = None
        self.image_path = None
        self.state = None
        self.width = 0
        self.height = 0

    # -- model ------------------------------------------------------------
    def attach_model(self, model, processor, resolution=1008):
        """Reuse a SAM 3 model built elsewhere (shared with the OVSS head)."""
        if model is None:
            raise ValueError("model 不能为空。")
        if getattr(model, "inst_interactive_predictor", None) is None:
            raise RuntimeError(
                "共享模型未启用交互头（enable_inst_interactivity=False）。")
        self.model = model
        self.processor = processor
        self.model_key = (int(resolution), str(self.device))
        self._attached = True

    def ensure_model(self, resolution=1008):
        """Build the interactive SAM 3 model for ``resolution`` (idempotent)."""
        key = (int(resolution), str(self.device))
        if self.model is not None and self.model_key == key:
            return
        if self.model is not None and getattr(self, "_attached", False):
            # Shared weights are managed by the owner; only the processor
            # depends on the resolution.
            from sam3.model.sam3_image_processor import Sam3Processor
            self.processor = Sam3Processor(
                self.model, resolution=int(resolution),
                confidence_threshold=0.5, device=self.device)
            self.model_key = key
            return

        self.release()
        from backend.model.SamSeg.engine import ensure_installed_sam3
        ensure_installed_sam3()

        from sam3 import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor

        started = time.time()
        from backend.config import settings
        weight = str(settings.sam3_native_ckpt)
        if not os.path.exists(weight):
            raise FileNotFoundError(f"找不到 SAM3 权重：{weight}")
        self.log(f"加载交互式 SAM3（{self.device}，分辨率 {resolution}）…")

        model = build_sam3_image_model(
            bpe_path=str(settings.sam3_native_bpe),
            checkpoint_path=weight,
            compile=False,
            enable_inst_interactivity=True,
        )
        model = model.to(self.device).eval()
        if model.inst_interactive_predictor is None:
            raise RuntimeError(
                "SAM3 未启用交互头（enable_inst_interactivity=False），"
                "无法做点选/框选分割。")

        self.model = model
        self.processor = Sam3Processor(
            model, resolution=int(resolution), confidence_threshold=0.5,
            device=self.device)
        self.model_key = key
        self.log(f"模型加载完成，用时 {time.time() - started:.1f} 秒。")

    # -- image ------------------------------------------------------------
    def set_image(self, path, resolution=1008):
        """Encode ``path`` once and cache the features used by every prompt."""
        import torch

        self.ensure_model(resolution)
        started = time.time()
        image = load_rgb_image(path)
        self.image = image
        self.image_path = os.path.abspath(path)
        self.width, self.height = image.size
        self.log(f"影像编码中：{os.path.basename(path)}（{self.width}×{self.height}）…")

        with torch.inference_mode(), bf16_autocast():
            self.state = self.processor.set_image(image)

        if "sam2_backbone_out" not in self.state.get("backbone_out", {}):
            raise RuntimeError(
                "SAM3 交互头缺少 SAM2 特征；请确认 enable_inst_interactivity=True。")
        self.log(f"影像编码完成，用时 {time.time() - started:.1f} 秒。")
        return self.width, self.height

    @property
    def is_ready(self):
        return self.state is not None and self.image is not None

    def drop_state(self):
        """Free the cached image features (keeps the model and the image)."""
        self.state = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def reencode(self):
        """Re-run the image encoder after :meth:`drop_state`."""
        if self.image_path is None:
            return False
        resolution = 1008 if self.model_key is None else int(self.model_key[0])
        self.set_image(self.image_path, resolution)
        return True

    # -- prompts ----------------------------------------------------------
    def predict(self, points=(), point_labels=(), box=None, multimask=True):
        """Run the interactive head with the accumulated prompts.

        ``points`` are (x, y) pixel coordinates in the original image,
        ``point_labels`` are 1 (foreground) / 0 (background).  ``box`` is a
        single (x0, y0, x1, y1) box in the same pixel space.  Returns a dict
        with boolean masks, predicted IoUs and per-mask areas.
        """
        import torch

        if not self.is_ready:
            raise RuntimeError("请先调用 set_image() 载入影像。")
        if len(points) == 0 and box is None:
            raise ValueError("至少需要一个点或一个框。")

        coordinates = None
        labels = None
        if len(points):
            coordinates = np.asarray(points, dtype=np.float32).reshape(-1, 2)
            labels = np.asarray(point_labels, dtype=np.int32).reshape(-1)
            if coordinates.shape[0] != labels.shape[0]:
                raise ValueError("点坐标与标签数量不一致。")

        started = time.time()
        with torch.inference_mode(), bf16_autocast():
            masks, ious, _low_res = self.model.predict_inst(
                self.state,
                point_coords=coordinates,
                point_labels=labels,
                box=None if box is None else np.asarray(box, dtype=np.float32).reshape(4),
                multimask_output=bool(multimask),
                return_logits=False,
                normalize_coords=True,
            )
        elapsed = time.time() - started

        masks = np.asarray(masks)
        if masks.ndim == 2:                      # (H, W) -> (1, H, W)
            masks = masks[None]
        ious = np.asarray(ious, dtype=np.float32).reshape(-1)
        candidates = []
        for index in range(masks.shape[0]):
            mask = masks[index].astype(bool)
            area = int(mask.sum())
            candidates.append({
                "index": index,
                "mask": mask,
                "iou": float(ious[index]) if index < ious.size else 0.0,
                "area": area,
                "coverage": float(area) / float(mask.size) if mask.size else 0.0,
                "bbox": mask_bbox(mask),
            })
        best = self.select_best(candidates, anchor=_anchor_of(points, box))
        return {
            "masks": [item["mask"] for item in candidates],
            "candidates": candidates,
            "best": best,
            "seconds": elapsed,
        }

    @staticmethod
    def select_best(candidates, anchor=None):
        """Pick the best candidate: highest predicted IoU, non-empty preferred."""
        if not candidates:
            return 0
        scored = []
        for item in candidates:
            score = item["iou"]
            if item["area"] == 0:
                score -= 0.5
            if anchor is not None and item["area"]:
                x, y = anchor
                mask = item["mask"]
                if 0 <= int(y) < mask.shape[0] and 0 <= int(x) < mask.shape[1]:
                    if not mask[int(y), int(x)]:
                        score -= 0.02
            scored.append((score, item["index"]))
        return max(scored)[1]

    # -- output -----------------------------------------------------------
    def save(self, mask, out_dir, name="object", prompts=None, iou=None,
             overlay_alpha=0.55, colour=DEFAULT_MASK_COLOUR):
        """Write ``_mask.png``, ``_overlay.png`` and ``_mask.json``."""
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.image_path or "image"))[0]
        safe = "".join(ch for ch in str(name) if ch not in '\\/:*?"<>|').strip() or "object"
        index = 1
        while True:
            base = os.path.join(out_dir, f"{stem}_{safe}_{index:03d}")
            if not any(os.path.exists(base + suffix)
                       for suffix in ("_mask.png", "_overlay.png", "_mask.json")):
                break
            index += 1

        mask_array = np.asarray(mask).astype(bool)
        mask_u8 = (mask_array.astype(np.uint8) * 255)
        Image.fromarray(mask_u8, mode="L").save(base + "_mask.png")

        rgb = np.asarray(self.image.convert("RGB"))
        overlay = blend_mask(rgb, mask_array, colour=colour, alpha=overlay_alpha)
        Image.fromarray(overlay).save(base + "_overlay.png")

        payload = {
            "image": self.image_path,
            "width": self.width,
            "height": self.height,
            "name": safe,
            "iou": None if iou is None else float(iou),
            "area": int(mask_array.sum()),
            "coverage": float(mask_array.mean()),
            "bbox_xyxy": mask_bbox(mask_array),
            "prompts": prompts or {},
            "resolution": None if self.model_key is None else int(self.model_key[0]),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(base + "_mask.json", "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return base + "_mask.png", base + "_overlay.png", base + "_mask.json"

    def save_instances(self, instances, out_dir, overlay_alpha=0.5):
        """Write a multi-object label image, colour overlay and COCO JSON.

        ``instances`` is the list built by the GUI: each item has ``name``,
        ``mask``, ``iou``, ``area``, ``coverage``, ``bbox`` and ``prompts``.
        Pixel value ``i`` in the label image is the ``i``-th accepted object
        (``1..N``), ``0`` is background.  Up to 255 objects fit in a uint8 PNG.
        """
        if not instances:
            raise ValueError("没有已接受的目标。")
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.image_path or "image"))[0]
        index = 1
        while True:
            base = os.path.join(out_dir, f"{stem}_instances_{index:03d}")
            if not any(os.path.exists(base + suffix)
                       for suffix in ("_label.png", "_overlay.png", ".json")):
                break
            index += 1

        height, width = self.height, self.width
        label = np.zeros((height, width), dtype=np.uint8)
        used = instances[:255]
        for offset, item in enumerate(used, start=1):
            mask = np.asarray(item["mask"]).astype(bool)
            if mask.shape != (height, width):
                continue
            label[mask] = offset
        Image.fromarray(label, mode="L").save(base + "_label.png")

        lut = instance_palette_lut()
        rgb = np.asarray(self.image.convert("RGB")).astype(np.float32)
        overlay = rgb.copy()
        for offset, item in enumerate(used, start=1):
            mask = np.asarray(item["mask"]).astype(bool)
            if mask.shape != (height, width) or not mask.any():
                continue
            colour = lut[offset].astype(np.float32)
            overlay[mask] = overlay[mask] * (1.0 - overlay_alpha) + colour * overlay_alpha
        Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8)).save(
            base + "_overlay.png")

        # 格式 2：COCO 标注 JSON（掩码多边形 + bbox + 类别 + score）
        from backend.model.sam3native.coco_export import write_coco
        coco_path = base + "_coco.json"
        write_coco(coco_path, self.image_path or "image", width, height, used)
        return base + "_label.png", base + "_overlay.png", coco_path

    # -- teardown ---------------------------------------------------------
    def release(self):
        # Dropping the references is enough: CUDA memory is reclaimed by the
        # allocator, and moving 3.4 GB of weights to the CPU first only stalls
        # the window while closing or switching resolution.
        self.state = None
        self.processor = None
        self.model = None
        self.model_key = None
        self.image = None
        self.image_path = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def _anchor_of(points, box):
    """Point used to sanity-check which candidate mask contains the click."""
    if box is not None:
        x0, y0, x1, y1 = box
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    if len(points):
        return (float(points[-1][0]), float(points[-1][1]))
    return None


def _cuda_available():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Headless self-test:  python interactive_segmentor.py --image path [--point x y]
# ---------------------------------------------------------------------------
def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="交互式 SAM3 后端自检")
    parser.add_argument("--image", required=True)
    parser.add_argument("--point", nargs=2, type=int, action="append",
                        help="正样本点，可重复：--point 430 380")
    parser.add_argument("--negative", nargs=2, type=int, action="append",
                        help="负样本点，可重复")
    parser.add_argument("--box", nargs=4, type=int,
                        help="框选：--box x0 y0 x1 y1")
    parser.add_argument("--resolution", type=int, default=1008,
                        choices=[1008, 672, 504])
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--save", default="demo_out")
    args = parser.parse_args(argv)

    service = InteractiveSegmentService(
        logger=lambda message: print(message, flush=True), device=args.device)
    service.set_image(args.image, args.resolution)
    points = list(args.point or [])
    labels = [1] * len(points)
    if args.negative:
        points += args.negative
        labels += [0] * len(args.negative)
    if not points and args.box is None:
        points = [[service.width // 2, service.height // 2]]
        labels = [1]
    result = service.predict(points, labels, args.box)
    for item in result["candidates"]:
        print(f"候选 {item['index']}: iou={item['iou']:.3f} area={item['area']} "
              f"coverage={item['coverage'] * 100:.2f}% bbox={item['bbox']}")
    best = result["best"]
    mask = result["masks"][best]
    print(f"选中候选 {best}，用时 {result['seconds'] * 1000:.0f} ms")
    paths = service.save(mask, args.save, name="selftest", iou=result["candidates"][best]["iou"])
    print("已保存：")
    for path in paths:
        print("  " + path)
    service.release()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
