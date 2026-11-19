# -*- coding: utf-8 -*-
"""原生 SAM3 感知引擎（工作站 G:/SAM/sam 实例分割口径移植）。

两个官方头共享一份权重：
- 交互头：点/框提示 → 单目标掩码（InteractiveSegmentService）；
- 文本接地头：类别提示 → 逐实例掩码/分数/框 + 语义图（NativeInstanceService.run_classes）。
不经过任何提示池/文本缓存机制，类别提示直达 Sam3Processor.set_text_prompt。
"""
