# -*- coding: utf-8 -*-
"""一次性导出脚本：ChangeQwenNet → ONNX（后端运行时不再需要模型代码）。

用法:
  python scripts/export_change_qwen_onnx.py --model jzg
  python scripts/export_change_qwen_onnx.py --model cdcd

说明:
  - 模型代码从 H:\\TAVLCDNet 导入，只在导出时使用；
  - 文本分支预先算成 text_tokens/text_mask，导出图输入为 T1/T2 + 文本 token；
  - 导出产物落到后端 weights 目录，供 onnxruntime 直接加载。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

TRAIN_REPO = Path(r"H:\TAVLCDNet")
sys.path.insert(0, str(TRAIN_REPO))

from network import ChangeQwenNet  # noqa: E402
from vlpeft.adapter import forward_bitemporal_vit  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "backend" / "model" / "change_detection" / "change_qwen_runtime" / "weights"

MODELS = {
    "jzg": {
        "ckpt": Path(r"H:\exp_bcd\文本编码器消融_表10\Jiuzhaigou-CD\LLaVA-1.5-7B\best_model.pth"),
        "cache": Path(r"G:\dataes\text_cache\Jiuzhaigou-CD_text_cache_generic_llava15_7b.pt"),
        "prompt": "Detecting landslide changes in mountainous scenes from bi-temporal images.",
        "sample_root": Path(r"G:\dataes\Jiuzhaigou-CD\val"),
        "name": "change_qwen_jzg",
    },
    "cdcd": {
        "ckpt": Path(r"H:\exp_bcd\文本编码器消融_表10\CDCD\LLaVA-1.5-7B\best_model.pth"),
        "cache": Path(r"G:\dataes\text_cache\CDCDdataes_text_cache_generic_llava15_7b.pt"),
        "prompt": "Detecting building changes in urban scenes from bi-temporal images.",
        "sample_root": Path(r"G:\dataes\CDCDdataes\val"),
        "name": "change_qwen_cdcd",
    },
}

MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


class VisionTextWrapper(nn.Module):
    """只保留视觉主干、SAGF 解码器和二值头；文本 token 由外部输入。"""

    def __init__(self, model: ChangeQwenNet):
        super().__init__()
        self.model = model

    def forward(self, t1: torch.Tensor, t2: torch.Tensor,
                text_tokens: torch.Tensor, text_mask: torch.Tensor) -> torch.Tensor:
        t1_feat, t2_feat = forward_bitemporal_vit(self.model.sam3_vit, t1, t2)
        t1_ms = self.model.neck(t1_feat)
        t2_ms = self.model.neck(t2_feat)
        concat = [torch.cat([t1_ms[i], t2_ms[i]], dim=1) for i in range(4)]
        x = self.model.decoder(concat, text_tokens, text_mask)
        x = F.interpolate(x, size=(t1.shape[-2], t1.shape[-1]), mode="bilinear", align_corners=False)
        return self.model.head_binary(x)


def onnx_apply_rotary_enc(xq, xk, freqs_cis, repeat_freqs_k=False):
    """ONNX-friendly RoPE: real/imag arithmetic, no view_as_complex."""
    xq_ = xq.float().reshape(*xq.shape[:-1], -1, 2)
    xk_ = xk.float().reshape(*xk.shape[:-1], -1, 2) if xk.shape[-2] != 0 else None

    def broadcast(freqs, x):
        shape = [1] * (x.ndim - 3) + list(freqs.shape)
        return freqs.view(*shape)

    def rotate(x, freqs):
        xr, xi = x[..., 0], x[..., 1]
        fr, fi = freqs[..., 0], freqs[..., 1]
        return torch.stack([xr * fr - xi * fi, xr * fi + xi * fr], dim=-1).flatten(3)

    freqs = broadcast(freqs_cis, xq_)
    xq_out = rotate(xq_, freqs)
    if xk_ is None:
        return xq_out.type_as(xq), xk
    if repeat_freqs_k:
        repeat = xk_.shape[-3] // xq_.shape[-3]
        freqs = freqs.repeat(1, 1, repeat, 1, 1)
    xk_out = rotate(xk_, freqs)
    return xq_out.type_as(xq), xk_out.type_as(xk)


def patch_rope_for_onnx(model):
    import sam3.model.vitdet as vitdet

    vitdet.apply_rotary_enc = onnx_apply_rotary_enc
    replaced = 0
    for module in model.modules():
        buffer = getattr(module, "freqs_cis", None)
        if torch.is_tensor(buffer) and torch.is_complex(buffer):
            module.freqs_cis = torch.view_as_real(buffer).contiguous()
            replaced += 1
    print(f"[ONNX] RoPE patched: {replaced} freqs_cis buffers -> real pairs")


def build_model(ckpt_path: Path, cache_path: Path) -> tuple[ChangeQwenNet, dict]:
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    args = ckpt["args"]
    interaction_ids = () if args.no_bitemporal_interaction else (7, 15, 23, 31)
    model = ChangeQwenNet(
        checkpoint_path=None,
        img_size=args.img_size,
        num_classes=1,
        use_text_guided_decoder=not args.no_text_guided_decoder,
        use_adapter=not args.no_adapter,
        adapter_variant=args.adapter_variant,
        qwen_model_path=args.qwen_model_path,
        interaction_block_ids=interaction_ids,
        text_cache_path=str(cache_path),
        no_sigma_gate=args.no_sigma_gate,
        no_pool_broadcast=args.no_pool_broadcast,
        no_cross_attention=args.no_cross_attention,
        gate_mode=args.gate_mode,
        word_fusion=args.word_fusion,
        cross_direction=args.cross_direction,
        sagf_fusion_op=args.sagf_fusion_op,
        sagf_consistency_gate=args.sagf_consistency_gate,
        sagf_relcorr_gate=args.sagf_relcorr_gate,
        sagf_relcorr_tau=args.sagf_relcorr_tau,
        sagf_prompt_tokens=args.sagf_prompt_tokens,
        moe_experts=False,
        incremental=False,
        bit_ablation=getattr(args, "bit_ablation", "none"),
    )
    missing, unexpected = model.load_state_dict(ckpt["model_state_dict"], strict=False)
    print(f"[Load] missing={len(missing)} unexpected={len(unexpected)}")
    if missing:
        print("  missing head:", missing[:8])
    if unexpected:
        print("  unexpected head:", unexpected[:8])
    model.eval()
    patch_rope_for_onnx(model)
    return model, ckpt


def preprocess_image(loader, path: Path, size: int) -> torch.Tensor:
    from PIL import Image
    import torchvision.transforms.functional as TF

    with Image.open(path) as img:
        img = img.convert("RGB")
        tensor = TF.to_tensor(TF.resize(img, (size, size)))
    return TF.normalize(tensor, MEAN, STD).unsqueeze(0)


def pick_sample(sample_root: Path):
    t1_dir, t2_dir = sample_root / "t1", sample_root / "t2"
    if not t1_dir.is_dir() or not t2_dir.is_dir():
        return None, None
    t2_map = {p.stem: p for p in t2_dir.iterdir() if p.is_file()}
    for t1 in sorted(t1_dir.iterdir()):
        if t1.is_file() and t1.stem in t2_map:
            return t1, t2_map[t1.stem]
    return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), default="jzg")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--opset", type=int, default=17)
    parser.add_argument("--skip-verify", action="store_true")
    args = parser.parse_args()

    spec = MODELS[args.model]
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / f"{spec['name']}.onnx"
    text_path = out_dir / f"{spec['name']}_text.pt"
    meta_path = out_dir / f"{spec['name']}_export.json"

    print(f"[Export] {args.model}: {spec['ckpt']}")
    t0 = time.time()
    model, ckpt = build_model(spec["ckpt"], spec["cache"])
    print(f"[Build] {time.time() - t0:.1f}s, prompt={spec['prompt']!r}")

    with torch.no_grad():
        _cls, tokens, mask = model.text_encoder.encode_text_tokens([spec["prompt"]])
    tokens = tokens.detach().cpu().float()
    mask = mask.detach().cpu()
    torch.save({"prompt": spec["prompt"], "text_tokens": tokens, "text_mask": mask}, text_path)
    print(f"[Text] tokens={tuple(tokens.shape)} mask={tuple(mask.shape)} -> {text_path}")

    # 文本分支已经抽离，释放它（模型其余部分仍在 CPU）
    del model.text_encoder
    model.text_encoder = None

    wrapper = VisionTextWrapper(model).eval()
    size = int(ckpt["args"].img_size)
    dummy = (
        torch.randn(1, 3, size, size),
        torch.randn(1, 3, size, size),
        tokens,
        mask,
    )

    export_kwargs = dict(
        input_names=["t1", "t2", "text_tokens", "text_mask"],
        output_names=["logits"],
        dynamic_axes={
            "t1": {0: "batch"},
            "t2": {0: "batch"},
            "text_tokens": {0: "batch"},
            "text_mask": {0: "batch"},
        },
        opset_version=args.opset,
        do_constant_folding=True,
    )
    print(f"[ONNX] exporting -> {onnx_path}")
    t1 = time.time()
    try:
        torch.onnx.export(wrapper, dummy, str(onnx_path), dynamo=False, **export_kwargs)
    except TypeError:
        # 旧版 torch 没有 dynamo 参数
        torch.onnx.export(wrapper, dummy, str(onnx_path), **export_kwargs)
    print(f"[ONNX] done {time.time() - t1:.1f}s, size={onnx_path.stat().st_size / 1e9:.2f} GB")

    verification = {}
    if not args.skip_verify:
        import onnxruntime as ort

        providers = ["CPUExecutionProvider"]
        session = ort.InferenceSession(str(onnx_path), providers=providers)
        sample_t1, sample_t2 = pick_sample(spec["sample_root"])
        if sample_t1 is None:
            inp1, inp2 = dummy[0], dummy[1]
            label = "random"
        else:
            inp1 = preprocess_image(None, sample_t1, size)
            inp2 = preprocess_image(None, sample_t2, size)
            label = f"{sample_t1.stem}"
            print(f"[Verify] sample={label}")

        with torch.no_grad():
            ref = wrapper(inp1, inp2, tokens, mask).cpu().numpy()
        ort_out = session.run(["logits"], {
            "t1": inp1.numpy(), "t2": inp2.numpy(),
            "text_tokens": tokens.numpy(), "text_mask": mask.numpy(),
        })[0]
        diff = np.abs(ref - ort_out)
        ref_mask = (1 / (1 + np.exp(-ref))) > 0.5
        ort_mask = (1 / (1 + np.exp(-ort_out))) > 0.5
        inter = np.logical_and(ref_mask, ort_mask).sum()
        union = np.logical_or(ref_mask, ort_mask).sum()
        verification = {
            "sample": label,
            "max_abs_diff": float(diff.max()),
            "mean_abs_diff": float(diff.mean()),
            "mask_iou": float(inter / union) if union else 1.0,
            "changed_pixels_ref": int(ref_mask.sum()),
            "changed_pixels_onnx": int(ort_mask.sum()),
            "providers": session.get_providers(),
        }
        print(f"[Verify] max_abs={verification['max_abs_diff']:.6g} "
              f"mean_abs={verification['mean_abs_diff']:.6g} "
              f"IoU={verification['mask_iou']:.6f} changed={verification['changed_pixels_onnx']}")

    meta = {
        "model": args.model,
        "checkpoint": str(spec["ckpt"]),
        "text_cache": str(spec["cache"]),
        "prompt": spec["prompt"],
        "img_size": size,
        "opset": args.opset,
        "onnx": str(onnx_path),
        "text_tokens": str(text_path),
        "onnx_size_gb": round(onnx_path.stat().st_size / 1e9, 3),
        "verification": verification,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Done] {meta_path}")


if __name__ == "__main__":
    main()
