#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
双时相影像渔网分割命令行入口。

默认行为与当前 `resources/cdcd` 数据集一致：
- 不重叠
- 随机种子 42
- 默认保留坐标系
- 输出目录结构为 train/val/test × t1/t2
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.model.SamSeg.fishnet import main


if __name__ == "__main__":
    raise SystemExit(main())