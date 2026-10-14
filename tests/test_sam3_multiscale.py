from pathlib import Path
import sys

import pytest
import torch
from torch import nn


SAMSEG_ROOT = Path(__file__).resolve().parents[1] / "backend" / "model" / "SamSeg" / "SamSeg"
if str(SAMSEG_ROOT) not in sys.path:
    sys.path.insert(0, str(SAMSEG_ROOT))

from sam3.model_builder import _create_vision_backbone
from sam3.model.necks import Sam3DualViTDetNeck
from sam3.model.vl_combiner import SAM3VLBackbone


@pytest.fixture(scope="module")
def vision_backbone():
    if not torch.cuda.is_available():
        pytest.skip("SAM3 multiscale smoke requires CUDA")
    model = _create_vision_backbone().cuda().eval()
    yield model
    del model
    torch.cuda.empty_cache()


def test_intermediate_layers_are_four_336_tokens(vision_backbone):
    assert vision_backbone.trunk.full_attn_ids == [7, 15, 23, 31]

    with torch.inference_mode():
        features = vision_backbone.trunk(
            torch.zeros(1, 3, 336, 336, device="cuda")
        )

    shapes = [tuple(feature.shape) for feature in features]
    print(f"Block 8/16/24/32 captured shapes: {shapes}")
    assert shapes == [(1, 1024, 24, 24)] * 4


def test_single_and_double_image_feature_shapes(vision_backbone):
    expected = [
        (256, 96, 96),
        (256, 48, 48),
        (256, 24, 24),
        (256, 12, 12),
    ]

    with torch.inference_mode():
        single = vision_backbone(torch.zeros(1, 3, 336, 336, device="cuda"))[0]
        double = vision_backbone(torch.zeros(2, 3, 336, 336, device="cuda"))[0]

    single_shapes = [tuple(feature.shape) for feature in single]
    double_shapes = [tuple(feature.shape) for feature in double]
    print(f"single-image four-scale shapes: {single_shapes}")
    print(f"double-image four-scale shapes: {double_shapes}")
    assert [shape[1:] for shape in single_shapes] == expected
    assert [shape[1:] for shape in double_shapes] == expected


def test_runtime_backbone_preserves_all_four_levels(vision_backbone):
    backbone = SAM3VLBackbone(visual=vision_backbone, text=None, scalp=0).eval()

    with torch.inference_mode():
        output = backbone.forward_image(torch.zeros(1, 3, 336, 336, device="cuda"))

    shapes = [tuple(feature.shape) for feature in output["backbone_fpn"]]
    print(f"runtime backbone_fpn shapes: {shapes}")
    assert shapes == [
        (1, 256, 96, 96),
        (1, 256, 48, 48),
        (1, 256, 24, 24),
        (1, 256, 12, 12),
    ]


def test_neck_requires_all_intermediate_layers():
    class ShortTrunk(nn.Module):
        channel_list = [1024] * 4

        def forward(self, tensor_list):
            return [torch.zeros(1, 1024, 24, 24)]

    neck = Sam3DualViTDetNeck(
        trunk=ShortTrunk(),
        position_encoding=nn.Identity(),
        d_model=256,
        scale_factors=[4.0, 2.0, 1.0, 0.5],
    )

    with pytest.raises(ValueError, match="Expected 4 intermediate ViT features"):
        neck(torch.zeros(1, 3, 24, 24))


def test_neck_has_no_simple_fpn_operators(vision_backbone):
    operators = list(vision_backbone.modules())
    assert not any(isinstance(module, nn.ConvTranspose2d) for module in operators)
    assert not any(isinstance(module, nn.MaxPool2d) for module in operators)
    assert all(
        isinstance(module, nn.Conv2d) and module.kernel_size == (1, 1)
        for module in vision_backbone.convs
    )
