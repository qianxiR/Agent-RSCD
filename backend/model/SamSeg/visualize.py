# -*- coding: utf-8 -*-
"""
SamSeg 可视化辅助层 (Visualization Layer)
==========================================

职责: 把推理产出的类别索引/调色板 转换为「人眼可看」的可视化产物
      (彩色 PNG 掩膜 + 前端颜色图注 legend)。

★ 与推理核心 (runner.py) 解耦: 本模块只负责"渲染外观", 不关心模型/算法。
★ 与产物落盘的地理坐标无关 (GeoTIFF/shp/矢量 在 geoio.py)。

依赖:
  - PIL.Image: PNG 落盘
  - numpy: 数组索引上色
无 torch / rasterio / geopandas 依赖 (轻量, 任何环境可用)。
"""
import logging
from typing import List

logger = logging.getLogger(__name__)


# 英文标准名 → 中文展示名 (图注用)。与 runner._CN_TO_EN_CLASS 互逆, 只覆盖常用类
# ★ 从 runner.py 迁入 (原 runner.py:755), _build_legend 依赖它
_EN_TO_CN_CLASS = {
    "background": "背景",
    "building": "建筑",
    "road": "道路",
    "water": "水",
    "bareland": "裸地",
    "vegetation": "植被",
    "farmland": "农田",
}


def build_legend(palette, class_lines: List[str], include_background: bool = False) -> list:
    """
    从 palette (num_cls, 3) + class_lines 导出前端图注用的颜色→类别列表。
    palette 第 0 行是 background (PNG 里是白色), 默认不导出 background。

    出参: [{name, hex}, ...]  其中 hex 形如 "#ff0000", 与 PNG 实际颜色严格一致。
    设计要点: 直接复用 build_palette 计算出的颜色 (PNG 上色用的就是它),
              不在前端另写颜色映射表, 避免颜色与图不一致。
    """
    legend = []
    for cid in range(len(class_lines)):
        if cid == 0 and not include_background:
            continue
        name = class_lines[cid].split(",")[0].strip()
        rgb = palette[cid]
        # 中文名映射: 让图注显示用户更熟悉的中文
        cn = _EN_TO_CN_CLASS.get(name, name)
        legend.append({"name": cn, "hex": "#{:02x}{:02x}{:02x}".format(*[int(v) for v in rgb])})
    return legend


def save_color_mask_png(color_mask, output_path: str) -> str:
    """
    彩色掩膜数组 → 存为 PNG。

    ★ 从 run_segment/run_change_detection 业务主流程中提取的可视化逻辑
      (原 inline 在 runner.py:325-326 / 438-440):
        color_mask = palette[seg]   # (H,W,3) uint8 索引上色 (调用方做)
        Image.fromarray(color_mask).save(output_path)  ← 本函数只做这一步
      调用方仍保留 color_mask 引用 (供 geoio.save_geotiff_mask 复用同一数组)。

    入参:
      - color_mask: (H,W,3) uint8 彩色掩膜 (调用方已 palette[seg] 上色)
      - output_path: 输出 PNG 路径
    出参: output_path (成功) / 抛异常 (失败, 由调用方捕获)
    """
    from PIL import Image
    Image.fromarray(color_mask).save(output_path)
    return output_path
