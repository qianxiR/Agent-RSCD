"""
SAM3 官方视觉 backbone / neck 形状回归测试（阶段 24 起对齐官方实现）。

背景:
  旧测试针对本地简化 neck（336 输入、4 层 intermediate feature、无 ConvTranspose），
  官方 sam3.pt 实际是 1008 输入 + SimpleFPN（ConvTranspose/MaxPool + 1x1 + 3x3）。
  本测试改为校验官方 site-packages `sam3` 的 backbone 输出，防止再次出现“权重键名
  对不上、neck 全随机”导致推理全空的问题。
"""
import pytest
import torch

from sam3.model_builder import _create_vision_backbone


@pytest.fixture(scope="module")
def vision_backbone():
    """入参: 无；方法: 构建官方 vision backbone；出参: CUDA eval 模型（无 CUDA 跳过）。"""
    if not torch.cuda.is_available():
        pytest.skip("SAM3 neck smoke requires CUDA")
    model = _create_vision_backbone().cuda().eval()
    yield model
    del model
    torch.cuda.empty_cache()


def test_official_neck_outputs_four_scales_at_1008(vision_backbone):
    """
    入参: 官方 backbone。
    方法: 1008×1008 输入前向，检查 SimpleFPN 四个尺度。
    出参: 形状不符时断言失败。
    """
    with torch.inference_mode():
        features = vision_backbone(
            torch.zeros(1, 3, 1008, 1008, device="cuda")
        )[0]

    shapes = [tuple(feature.shape) for feature in features]
    print(f"official neck shapes: {shapes}")
    assert shapes == [
        (1, 256, 288, 288),
        (1, 256, 144, 144),
        (1, 256, 72, 72),
        (1, 256, 36, 36),
    ]


def test_official_neck_uses_simple_fpn_ops(vision_backbone):
    """
    入参: 官方 backbone。
    方法: 检查 neck 内存在 ConvTranspose2d / MaxPool2d（官方 SimpleFPN 特征）。
    出参: 若退化成单层 1x1 conv（旧本地实现）则断言失败。
    """
    modules = list(vision_backbone.modules())
    assert any(isinstance(module, torch.nn.ConvTranspose2d) for module in modules)
    assert any(isinstance(module, torch.nn.MaxPool2d) for module in modules)


def test_backbone_accepts_batch_of_two(vision_backbone):
    """
    入参: 官方 backbone。
    方法: batch=2 前向，确认四个尺度 batch 维一致。
    出参: batch 维不等于 2 时断言失败。
    """
    with torch.inference_mode():
        features = vision_backbone(
            torch.zeros(2, 3, 1008, 1008, device="cuda")
        )[0]

    assert all(feature.shape[0] == 2 for feature in features)
