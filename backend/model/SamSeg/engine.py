# -*- coding: utf-8 -*-
"""
SAM3 官方引擎加载（Model 层 / SamSeg）

背景:
  `backend/model/SamSeg/SamSeg/sam3` 是随 SamSeg 子项目拷贝进来的旧版模型代码，
  与官方 `sam3.pt` 权重的键名/输入分辨率/neck 结构不一致（加载后检测头全空）。
  环境 `sam3` conda 里已安装官方 `sam3` 包，本模块负责：
    1. 清掉本地 `sam3` 的 sys.modules / sys.path 优先级，确保 import 到 site-packages；
    2. 用官方 `build_sam3_image_model` + `Sam3Processor(resolution=1008)` 加载权重；
    3. 返回 processor（processor.model 即官方模型）。

依赖方向: 仅依赖 torch + 已安装的 sam3 包；不反向依赖 tools/agent。
"""
import importlib
import logging
import sys
from pathlib import Path
from typing import Tuple

import torch

logger = logging.getLogger(__name__)

# 本地旧版 sam3 目录（不能被 import）
_LOCAL_SAM3_DIR = (Path(__file__).resolve().parent / "SamSeg" / "sam3").resolve()


def ensure_installed_sam3():
    """
    入参: 无。
    方法: 若 `sam3` 已被解析到本地旧目录，清空相关 sys.modules 并从 sys.path 去掉该目录；
          然后 import 官方 site-packages 版本。
    出参: 官方 sam3 模块对象。
    """
    # 1) 清掉已加载的本地 sam3（含子模块）
    loaded = sys.modules.get("sam3")
    loaded_file = Path(getattr(loaded, "__file__", "") or "").resolve() if loaded else None
    if loaded is not None and loaded_file is not None and _LOCAL_SAM3_DIR in loaded_file.parents:
        for name in [n for n in sys.modules if n == "sam3" or n.startswith("sam3.")]:
            sys.modules.pop(name, None)
        logger.info("[SamSeg-Engine] 已卸载本地旧版 sam3，改用 site-packages 官方包")

    # 2) 把本地 SamSeg 目录从 sys.path 前部移除（避免 `sam3` 被本地目录遮蔽）
    local_project_dir = _LOCAL_SAM3_DIR.parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != local_project_dir]

    sam3 = importlib.import_module("sam3")
    sam3_file = Path(getattr(sam3, "__file__", "") or "").resolve()
    if _LOCAL_SAM3_DIR in sam3_file.parents:
        raise ImportError(
            f"sam3 仍解析到本地旧目录: {sam3_file}; 请检查 sys.path 或卸载本地 sam3 目录"
        )
    logger.info(f"[SamSeg-Engine] 使用官方 sam3: {sam3_file}")
    return sam3


def load_model(ckpt: str, bpe: str, conf: float, device: str) -> Tuple[object, torch.device]:
    """
    入参: 权重/BPE 路径、实例置信度阈值、设备字符串。
    方法: 官方 `build_sam3_image_model` + `Sam3Processor(resolution=1008)`。
    出参: (processor, torch.device)；processor.model 为官方模型。
    """
    sam3 = ensure_installed_sam3()
    from sam3.model.sam3_image_processor import Sam3Processor

    dev = torch.device(device if device else ("cuda" if torch.cuda.is_available() else "cpu"))
    logger.info(f"[SamSeg-Engine] 加载官方 SAM3: ckpt={Path(ckpt).name}, device={dev}")
    model = sam3.build_sam3_image_model(
        bpe_path=bpe,
        checkpoint_path=ckpt,
        device=str(dev),
        load_from_HF=False,
    )
    # ★ 官方 SAM3 输入分辨率 1008；处理器默认值各版本不一致，显式指定。
    processor = Sam3Processor(
        model,
        resolution=1008,
        confidence_threshold=conf,
        device=str(dev),
    )
    logger.info("[SamSeg-Engine] 模型加载完成")
    return processor, dev
