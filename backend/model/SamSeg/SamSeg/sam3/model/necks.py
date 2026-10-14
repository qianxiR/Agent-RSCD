# Copyright (c) Meta Platforms, Inc. and affiliates. All Rights Reserved

"""Necks are the interface between a vision backbone and the rest of the detection model"""

from copy import deepcopy
from typing import List, Optional, Tuple

import torch

import torch.nn as nn
import torch.nn.functional as F


class Sam3DualViTDetNeck(nn.Module):
    def __init__(
        self,
        trunk: nn.Module,
        position_encoding: nn.Module,
        d_model: int,
        scale_factors=(4.0, 2.0, 1.0, 0.5),
        add_sam2_neck: bool = False,
    ):
        """
        The semantic multi-scale neck for the SAM3 ViT intermediate features.
        It supports a "dual neck" setting, where we have two identical necks
        (for SAM3 and SAM2), with different weights.

        :param trunk: the backbone
        :param position_encoding: the positional encoding to use
        :param d_model: the dimension of the model
        """
        super().__init__()
        self.trunk = trunk
        self.position_encoding = position_encoding
        self.scale_factors = tuple(scale_factors)
        self.convs = nn.ModuleList(
            nn.Conv2d(self.trunk.channel_list[i], d_model, kernel_size=1)
            for i in range(len(self.scale_factors))
        )

        self.sam2_convs = None
        if add_sam2_neck:
            # Assumes sam2 neck is just a clone of the original neck
            self.sam2_convs = deepcopy(self.convs)

    def forward(
        self, tensor_list: List[torch.Tensor]
    ) -> Tuple[
        List[torch.Tensor],
        List[torch.Tensor],
        Optional[List[torch.Tensor]],
        Optional[List[torch.Tensor]],
    ]:
        xs = self.trunk(tensor_list)
        if len(xs) != len(self.convs):
            raise ValueError(
                f"Expected {len(self.convs)} intermediate ViT features, got {len(xs)}."
            )
        sam3_out, sam3_pos = [], []
        sam2_out, sam2_pos = None, None
        if self.sam2_convs is not None:
            sam2_out, sam2_pos = [], []
        for i, (x, scale) in enumerate(zip(xs, self.scale_factors)):
            sam3_x_out = self.convs[i](x)
            if scale != 1.0:
                output_size = (
                    round(sam3_x_out.shape[-2] * scale),
                    round(sam3_x_out.shape[-1] * scale),
                )
                sam3_x_out = F.interpolate(
                    sam3_x_out,
                    size=output_size,
                    mode="bilinear",
                    align_corners=False,
                )
            sam3_pos_out = self.position_encoding(sam3_x_out).to(sam3_x_out.dtype)
            sam3_out.append(sam3_x_out)
            sam3_pos.append(sam3_pos_out)

            if self.sam2_convs is not None:
                sam2_x_out = self.sam2_convs[i](x)
                if scale != 1.0:
                    sam2_x_out = F.interpolate(
                        sam2_x_out,
                        size=output_size,
                        mode="bilinear",
                        align_corners=False,
                    )
                sam2_pos_out = self.position_encoding(sam2_x_out).to(sam2_x_out.dtype)
                sam2_out.append(sam2_x_out)
                sam2_pos.append(sam2_pos_out)
        return sam3_out, sam3_pos, sam2_out, sam2_pos
