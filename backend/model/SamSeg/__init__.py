# -*- coding: utf-8 -*-
"""
SAM 分割模型 (SegEarth-OV3) 包
基于 Meta SAM 3 的训练免开放词汇遥感分割与变化检测。

★ 本 __init__ 故意保持轻量, 不在包初始化时导入 SamSeg 子项目的符号。
  原因: 子项目内部用 `from infer import ...` / `from sam3 import ...` 裸导入
  (假定 CWD 是 SamSeg/SamSeg), 在本包结构下直接 import 会 ModuleNotFoundError。

  正确用法: from backend.model.SamSeg import runner
  runner 内部用 sys.path.insert 方案处理子项目的裸导入, 并提供:
    - samseg_available()     可用性检测
    - run_segment()          语义分割
    - run_change_detection() 变化检测
  子项目的原始函数 (load_model/multipass_inference 等) 由 runner 内部调用,
  不建议在包层直接 re-export, 避免触发导入错误。
"""
