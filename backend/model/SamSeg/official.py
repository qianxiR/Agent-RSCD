# -*- coding: utf-8 -*-
"""
SegEarth-OV3 官方推理流程封装（Model 层 / SamSeg）

对齐仓库 earth-insights/SegEarth-OV-3 的官方实现:
  - demo.py / segearthov3_segmentor.py
  - segearthov3_change_detector.py

与旧 multipass 流程的核心差异:
  1. 单次前向 (single-pass)，不再多轮涂黑残差 + 阈值衰减；
  2. 大图按 slide_crop/slide_stride 滑窗，重叠区域简单平均（官方无 Gaussian / padding）；
  3. 每个 query 做双头融合:
       instance head logits × object_score  ─┐
       semantic head logits                 ─┼─ element-wise max
       × presence_score                     ─┘
  4. 同义词 query 用 one-hot 按类别取 max 聚合成 class logits；
  5. argmax 后用 prob_thd 把低置信像素压回背景；
  6. 变化检测 = 实例级(膨胀容差 + 覆盖率) ∩ 语义级(FPN 余弦 + Δlogits + Otsu)。

本模块只负责“算”，输出仍是 (H, W) 类别索引图，PNG/GeoTIFF/矢量/统计由 runner 负责。
依赖方向: official.py → SamSeg/infer.py（同层子项目），不依赖 tools/agent。
"""
from contextlib import nullcontext
import logging
from typing import List, Optional, Tuple

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

logger = logging.getLogger(__name__)


# ==================== 官方默认超参 ====================
# 与 demo.py 保持一致: confidence_threshold=0.1, prob_thd=0.1, slide=512/512
DEFAULT_PROB_THD = 0.1
DEFAULT_CONFIDENCE_THRESHOLD = 0.1
DEFAULT_SLIDE_CROP = 512
DEFAULT_SLIDE_STRIDE = 512
DEFAULT_INSTANCE_IOU = 0.3
DEFAULT_MIN_INSTANCE_AREA = 20
DEFAULT_INSTANCE_DILATION = 2


def _autocast(device: torch.device, enabled: bool):
    """
    入参: device / enabled。
    方法: 官方在 CUDA 上使用 bfloat16 autocast；CPU 或硬件不支持 bf16 时退回 float32。
    出参: 上下文管理器。
    """
    if enabled and device.type == "cuda":
        try:
            if torch.cuda.is_bf16_supported():
                return torch.autocast(device_type="cuda", dtype=torch.bfloat16)
        except Exception:
            pass
    return nullcontext()


def _fit_size(x: torch.Tensor, size: Tuple[int, int]) -> torch.Tensor:
    """
    入参: x 任意 (..., H, W) logits / mask，size=(h, w)。
    方法: 双线性插值到目标尺寸；已一致则原样返回。
    出参: 与 size 一致的张量。
    """
    if tuple(x.shape[-2:]) == tuple(size):
        return x
    if x.dim() == 2:
        return F.interpolate(
            x.view(1, 1, *x.shape), size=size, mode="bilinear", align_corners=False
        ).view(size)
    if x.dim() == 3:
        return F.interpolate(
            x.unsqueeze(0), size=size, mode="bilinear", align_corners=False
        ).squeeze(0)
    return F.interpolate(x, size=size, mode="bilinear", align_corners=False)


def _grounding(processor, model, word: str, state: dict, device: torch.device) -> dict:
    """
    入参: processor/model、文本 query、已 set_image 的 state、device。
    方法: 直接走官方 `forward_grounding`（绕过 processor 的过滤，保留 semantic/presence）。
    出参: SAM3 grounding 原始输出 dict。
    """
    state["backbone_out"].update(model.backbone.forward_text([word], device=str(device)))
    if "geometric_prompt" not in state:
        state["geometric_prompt"] = model._get_dummy_prompt()
    return model.forward_grounding(
        backbone_out=state["backbone_out"],
        find_input=processor.find_stage,
        geometric_prompt=state["geometric_prompt"],
        find_target=None,
    )


def _merge_grounding(seg_logits: torch.Tensor, q_idx: int, out: dict,
                     conf_threshold: float, h: int, w: int) -> None:
    """
    入参: 全量 logits、query 下标、grounding 输出、实例置信度阈值、图像高宽。
    方法: 官方双头融合 ——
          instance: sigmoid(pred_masks) × (sigmoid(pred_logits) × presence)
          semantic: sigmoid(semantic_seg) × presence
          两者 element-wise max，写入该 query 的 (H,W)。
    出参: 无（原地写 seg_logits[q_idx]）。
    """
    presence = out["presence_logit_dec"].sigmoid().reshape(())
    probs = out["pred_logits"].sigmoid().reshape(-1) * presence
    keep = probs > float(conf_threshold)

    masks = out["pred_masks"].sigmoid()  # (1, N, h0, w0)
    if masks.dim() == 4:
        masks = masks[0]
    if keep.any():
        kept_masks = masks[keep]
        if kept_masks.shape[-2:] != (h, w):
            kept_masks = F.interpolate(
                kept_masks.unsqueeze(1), size=(h, w),
                mode="bilinear", align_corners=False,
            ).squeeze(1)
        weighted = kept_masks * probs[keep].view(-1, 1, 1)
        seg_logits[q_idx] = torch.maximum(seg_logits[q_idx], weighted.amax(dim=0))

    semantic = out["semantic_seg"].sigmoid()  # (1, 1, h0, w0)
    if semantic.dim() == 4:
        semantic = semantic[0, 0]
    semantic = _fit_size(semantic, (h, w)) * presence
    seg_logits[q_idx] = torch.maximum(seg_logits[q_idx], semantic)


def infer_single_view(processor, query_words: List[str], device: torch.device,
                      image: Image.Image, autocast: bool = True) -> torch.Tensor:
    """
    入参: processor、query 词列表、device、PIL 图像、是否 autocast。
    方法: 官方单图推理 —— 图像编码一次，逐 query 走 forward_grounding 并双头融合。
    出参: (num_queries, H, W) float32 logits。
    """
    w, h = image.size
    seg_logits = torch.zeros((len(query_words), h, w), device=device, dtype=torch.float32)
    model = processor.model
    conf_threshold = float(getattr(processor, "confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD))

    with torch.inference_mode(), _autocast(device, autocast):
        state = processor.set_image(image)
        for q_idx, word in enumerate(query_words):
            processor.reset_all_prompts(state)
            out = _grounding(processor, model, word, state, device)
            _merge_grounding(seg_logits, q_idx, out, conf_threshold, h, w)

    return seg_logits


def slide_inference(processor, query_words: List[str], device: torch.device,
                    image: Image.Image, stride: int, crop_size: int,
                    autocast: bool = True) -> torch.Tensor:
    """
    入参: processor、query 词、device、图像、滑窗步长/裁剪尺寸、autocast。
    方法: 官方滑窗 —— 逐 tile 单图推理，重叠区域按次数简单平均（无 padding/Gaussian）。
    出参: (num_queries, H, W) logits。
    """
    w_img, h_img = image.size
    if isinstance(stride, int):
        stride = (stride, stride)
    if isinstance(crop_size, int):
        crop_size = (crop_size, crop_size)
    h_stride, w_stride = stride
    h_crop, w_crop = crop_size

    preds = torch.zeros((len(query_words), h_img, w_img), device=device, dtype=torch.float32)
    count_mat = torch.zeros((1, h_img, w_img), device=device, dtype=torch.float32)

    h_grids = max(h_img - h_crop + h_stride - 1, 0) // h_stride + 1
    w_grids = max(w_img - w_crop + w_stride - 1, 0) // w_stride + 1

    for h_idx in range(h_grids):
        for w_idx in range(w_grids):
            y1 = h_idx * h_stride
            x1 = w_idx * w_stride
            y2 = min(y1 + h_crop, h_img)
            x2 = min(x1 + w_crop, w_img)
            y1 = max(y2 - h_crop, 0)
            x1 = max(x2 - w_crop, 0)

            crop_img = image.crop((x1, y1, x2, y2))
            crop_logits = infer_single_view(processor, query_words, device, crop_img, autocast)
            preds[:, y1:y2, x1:x2] += crop_logits
            count_mat[:, y1:y2, x1:x2] += 1

    if bool((count_mat == 0).any()):
        raise RuntimeError("滑窗覆盖不完整（count_mat 存在 0 值）")
    return preds / count_mat


def infer_logits(processor, query_words: List[str], device: torch.device,
                 image: Image.Image, slide_crop: int = DEFAULT_SLIDE_CROP,
                 slide_stride: int = DEFAULT_SLIDE_STRIDE,
                 autocast: bool = True) -> torch.Tensor:
    """
    入参: 官方推理所需上下文 + 滑窗参数。
    方法: 图像任一边大于 slide_crop 时走滑窗，否则单图推理（与官方 predict 一致）。
    出参: (num_queries, H, W) logits。
    """
    w, h = image.size
    if slide_crop > 0 and (slide_crop < w or slide_crop < h):
        logger.info(f"[SamSeg-Official] 滑窗推理: crop={slide_crop}, stride={slide_stride}, image={w}x{h}")
        return slide_inference(processor, query_words, device, image,
                               slide_stride, slide_crop, autocast)
    logger.info(f"[SamSeg-Official] 单图推理: image={w}x{h}")
    return infer_single_view(processor, query_words, device, image, autocast)


def aggregate_logits(seg_logits: torch.Tensor, query_idx: torch.Tensor,
                     num_cls: int) -> torch.Tensor:
    """
    入参: (num_queries,H,W) logits、query→class 映射、类别数。
    方法: 官方 one-hot 聚合 —— 同类同义词 query 取 max。
    出参: (num_cls,H,W) class logits。
    """
    num_queries = seg_logits.shape[0]
    if num_cls == num_queries:
        return seg_logits
    cls_index = F.one_hot(query_idx, num_classes=num_cls).T.to(seg_logits.dtype)
    cls_index = cls_index.view(num_cls, num_queries, 1, 1)
    return (seg_logits.unsqueeze(0) * cls_index).max(dim=1)[0]


def discrete_pred(seg_logits: torch.Tensor, query_idx: torch.Tensor, num_cls: int,
                  prob_thd: float = DEFAULT_PROB_THD, bg_idx: int = 0) -> np.ndarray:
    """
    入参: query 级 logits、query→class 映射、类别数、官方 prob_thd、背景类别。
    方法: 类别聚合 → argmax → 置信度低于 prob_thd 的像素压回背景。
    出参: (H,W) int64 类别索引图。
    """
    class_logits = aggregate_logits(seg_logits, query_idx, num_cls)
    pred = class_logits.argmax(dim=0)
    max_vals = class_logits.max(dim=0)[0]
    pred = pred.clone()
    pred[max_vals < float(prob_thd)] = int(bg_idx)
    return pred.cpu().numpy().astype(np.int64)


def extract_fpn(processor, image: Image.Image, autocast: bool = True) -> tuple:
    """
    入参: processor、PIL 图像、是否 autocast。
    方法: 与官方 _extract_fpn_features 一致 —— 取 SAM3 backbone 的 backbone_fpn。
    出参: (fpn_features, (H, W))。
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with torch.inference_mode(), _autocast(device, autocast):
        state = processor.set_image(image)
        backbone = state.get("backbone_out") or {}
        fpn = backbone.get("backbone_fpn")
        if fpn is None:
            raise ValueError("backbone_out.backbone_fpn 不存在，无法计算 FPN 变化")
    return fpn, (image.size[1], image.size[0])


def fpn_change_map(fpn_t1, fpn_t2, target_size: Tuple[int, int]) -> torch.Tensor:
    """
    入参: 两期 FPN 特征、目标尺寸 (H, W)。
    方法: 与官方 _compute_fpn_similarity_map 一致 —— 最后一层上采样后算余弦相似度，
          再映射到 [0,1] 并取 1-sim 作为结构变化强度。
    出参: (H,W) [0,1] 变化强度图。
    """
    def _upsample(fpn_features, size):
        feats = []
        for feat in fpn_features[-1:]:
            if feat.shape[1] > 320:
                p1 = F.interpolate(feat[:, :320], size=size, mode="bilinear", align_corners=False)
                p2 = F.interpolate(feat[:, 320:], size=size, mode="bilinear", align_corners=False)
                feat = torch.cat([p1, p2], dim=1)
            else:
                feat = F.interpolate(feat, size=size, mode="bilinear", align_corners=False)
            feats.append(feat)
        return torch.cat(feats, dim=1)

    feat_t1 = _upsample(fpn_t1, target_size)
    feat_t2 = _upsample(fpn_t2, target_size)
    sim = F.cosine_similarity(feat_t1, feat_t2, dim=1)
    sim = (sim * 0.5 + 0.5).squeeze()
    return (1.0 - sim).clamp(0.0, 1.0)


def instance_change_map(t1_pred: np.ndarray, t2_pred: np.ndarray,
                        iou_threshold: float = DEFAULT_INSTANCE_IOU,
                        min_area: int = DEFAULT_MIN_INSTANCE_AREA,
                        dilation_radius: int = DEFAULT_INSTANCE_DILATION) -> np.ndarray:
    """
    入参: 两期离散类别图、覆盖率阈值、最小实例面积、配准容差膨胀半径。
    方法: 官方实例级变化 —— 前景连通域在另一期的覆盖率 < 阈值 且面积达标 → 变化；
          双向计算后取并集（膨胀用于吸收配准漂移）。
    出参: (H,W) int64 0/1 变化掩膜（类别由语义级结果提供）。
    """
    from scipy.ndimage import binary_dilation, label as scipy_label

    mask1 = t1_pred > 0
    mask2 = t2_pred > 0
    if not mask1.any() and not mask2.any():
        return np.zeros_like(t1_pred, dtype=np.int64)

    kernel = np.ones((dilation_radius * 2 + 1, dilation_radius * 2 + 1), dtype=bool)
    mask1_dil = binary_dilation(mask1, structure=kernel) if dilation_radius > 0 else mask1
    mask2_dil = binary_dilation(mask2, structure=kernel) if dilation_radius > 0 else mask2

    def _unidirectional(source: np.ndarray, target_dilated: np.ndarray) -> np.ndarray:
        lbl, _ = scipy_label(source, structure=np.ones((3, 3), dtype=int))
        area = np.bincount(lbl.ravel())
        cover = np.bincount(lbl.ravel(), weights=target_dilated.ravel().astype(np.float64))
        ratio = cover / np.maximum(area, 1)
        changed_ids = np.where((ratio < float(iou_threshold)) & (area >= int(min_area)))[0]
        changed_ids = changed_ids[changed_ids != 0]
        return np.isin(lbl, changed_ids)

    change = _unidirectional(mask1, mask2_dil) | _unidirectional(mask2, mask1_dil)
    return change.astype(np.int64)


def _otsu_threshold(values: np.ndarray) -> float:
    """
    入参: 一维/二维能量图。
    方法: 优先 skimage Otsu；依赖缺失时用 mean+std 兜底，保证官方链路可降级运行。
    出参: 阈值浮点数。
    """
    try:
        from skimage.filters import threshold_otsu
        if np.ptp(values) > 1e-4:
            return float(threshold_otsu(values))
        return 0.0
    except Exception:
        return float(values.mean() + values.std())


def semantic_change_map(logits_t1: torch.Tensor, logits_t2: torch.Tensor,
                        query_idx: torch.Tensor, num_cls: int,
                        feature_change_map: torch.Tensor) -> np.ndarray:
    """
    入参: 两期 query logits、query→class 映射、类别数、FPN 结构变化图。
    方法: 官方语义级变化 —— 每类 energy = max_conf × |Δlogits| × feature_change，
          逐类 Otsu 阈值（下限 0.01）得到该类变化区域；后写类别覆盖先写。
    出参: (H,W) int64 类别索引图（0=无变化）。
    """
    h, w = logits_t1.shape[-2:]
    change = np.zeros((h, w), dtype=np.int64)
    feature_change = feature_change_map
    if feature_change.shape[-2:] != (h, w):
        feature_change = F.interpolate(
            feature_change.view(1, 1, *feature_change.shape), size=(h, w),
            mode="bilinear", align_corners=False,
        ).view(h, w)

    for cls_id in range(num_cls):
        if cls_id == 0:
            continue
        prompt_indices = (query_idx == cls_id).nonzero(as_tuple=True)[0]
        if len(prompt_indices) == 0:
            continue

        delta_prompts = torch.abs(
            logits_t1[prompt_indices] - logits_t2[prompt_indices]
        )
        delta_max = delta_prompts.max(dim=0)[0]

        max_conf_prompts = torch.max(
            logits_t1[prompt_indices], logits_t2[prompt_indices]
        )
        max_conf = max_conf_prompts.max(dim=0)[0]

        energy = max_conf * delta_max * feature_change
        energy_np = energy.detach().cpu().numpy()
        otsu = _otsu_threshold(energy_np)
        valid = energy_np > max(float(otsu), 0.01)
        change[valid] = int(cls_id)

    return change


def combined_change_map(t1_pred: np.ndarray, t2_pred: np.ndarray,
                        logits_t1: torch.Tensor, logits_t2: torch.Tensor,
                        query_idx: torch.Tensor, num_cls: int,
                        feature_change_map: torch.Tensor,
                        iou_threshold: float = DEFAULT_INSTANCE_IOU,
                        min_area: int = DEFAULT_MIN_INSTANCE_AREA,
                        dilation_radius: int = DEFAULT_INSTANCE_DILATION) -> np.ndarray:
    """
    入参: 两期离散类别图/query logits、映射、类别数、FPN 变化图及实例参数。
    方法: 官方交集策略 —— 实例级与语义级都判为变化的像素才保留；
          类别取语义级结果（官方原实现取二值实例图，会丢失类别，此处修正）。
    出参: (H,W) int64 类别索引变化图。
    """
    inst_change = instance_change_map(
        t1_pred, t2_pred, iou_threshold=iou_threshold,
        min_area=min_area, dilation_radius=dilation_radius,
    )
    sem_change = semantic_change_map(
        logits_t1, logits_t2, query_idx, num_cls, feature_change_map,
    )
    agreed = (inst_change > 0) & (sem_change > 0)
    result = np.zeros_like(sem_change, dtype=np.int64)
    result[agreed] = sem_change[agreed]
    logger.info(
        f"[SamSeg-Official] 变化融合: 实例={int((inst_change > 0).sum())}px, "
        f"语义={int((sem_change > 0).sum())}px, 交集={int(agreed.sum())}px"
    )
    return result
