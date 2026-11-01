"""Minimal pure-PyTorch `mmcv.ops` replacement (offline deployment).

The original mmcv 2.1.0 wheel ships compiled CUDA/C++ extensions (``mmcv._ext``)
that are bound to a specific Python version (3.8 in the environment they were
copied from), so they cannot be imported by the Python 3.12 runtime used here.
Only the symbols imported by the bundled ``mmseg`` package are provided:

* ``point_sample``          – implemented with ``torch.nn.functional.grid_sample``
* ``sigmoid_focal_loss``    – implemented with native PyTorch ops
* ``PSAMask``, ``CrissCrossAttention``, ``DeformConv2d``,
  ``ModulatedDeformConv2d`` – import-safe placeholders. They are not used by the
  SAM3 / ProC-SAM3 evaluation path and raise ``NotImplementedError`` if called.

This module deliberately contains no compiled code.
"""

import torch
import torch.nn.functional as F
from torch import nn


def point_sample(input, point_coords, **kwargs):
    """Sample features at arbitrary (normalized) point coordinates.

    Mirrors ``mmcv.ops.point_sample``: coordinates are expected in the
    ``[0, 1]`` range and are converted to ``[-1, 1]`` for ``grid_sample``.
    """
    add_dim = False
    if point_coords.dim() == 3:
        add_dim = True
        point_coords = point_coords.unsqueeze(2)
    output = F.grid_sample(input, 2.0 * point_coords - 1.0, **kwargs)
    if add_dim:
        output = output.squeeze(3)
    return output


def sigmoid_focal_loss(pred,
                       target,
                       gamma=2.0,
                       alpha=0.25,
                       reduction='mean',
                       avg_factor=None):
    """Sigmoid focal loss implemented with native PyTorch operations."""
    pred_sigmoid = pred.sigmoid()
    target = target.type_as(pred)

    pt = (1 - pred_sigmoid) * target + pred_sigmoid * (1 - target)
    focal_weight = (alpha * target + (1 - alpha) * (1 - target)) * pt.pow(gamma)

    loss = F.binary_cross_entropy_with_logits(
        pred, target, reduction='none') * focal_weight

    if reduction == 'mean':
        if avg_factor is None:
            return loss.mean()
        return loss.sum() / max(float(avg_factor), 1.0)
    if reduction == 'sum':
        return loss.sum()
    if reduction == 'none':
        return loss
    raise ValueError(f'Unsupported reduction: {reduction}')


class _CompiledOpUnavailable(nn.Module):
    """Placeholder for extension-backed modules that are not available."""

    _op_name = 'mmcv compiled op'

    def __init__(self, *args, **kwargs):
        super().__init__()

    def forward(self, *args, **kwargs):
        raise NotImplementedError(
            f'{self._op_name} requires the compiled mmcv extension, which is '
            'not available in this offline PyTorch-only deployment. '
            'It is only used by mmseg models outside the SAM3 path.')

    def extra_repr(self):
        return 'unavailable (compiled mmcv extension not installed)'


class PSAMask(_CompiledOpUnavailable):
    _op_name = 'PSAMask'


class CrissCrossAttention(_CompiledOpUnavailable):
    _op_name = 'CrissCrossAttention'


class DeformConv2d(_CompiledOpUnavailable):
    _op_name = 'DeformConv2d'


class ModulatedDeformConv2d(_CompiledOpUnavailable):
    _op_name = 'ModulatedDeformConv2d'


__all__ = [
    'point_sample', 'sigmoid_focal_loss', 'PSAMask', 'CrissCrossAttention',
    'DeformConv2d', 'ModulatedDeformConv2d'
]
