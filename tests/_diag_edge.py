# -*- coding: utf-8 -*-
"""诊断: distance 算子边缘占比 + 叠加后变色情况"""
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image
from backend.model.SamSeg.edge_utils import extract_edge, overlay_edge_on_image

MASK = str(ROOT / "tests" / "data" / "8fb75bc3_1782029898655_r000_c006_建筑_seg_t1cd.png")
SRC = str(ROOT / "tests" / "data" / "r000_c006_t1.tif")

m = np.array(Image.open(MASK).convert("L"))
seg = (m > 10).astype(np.int64)
binary = (seg == 1).astype(np.uint8) * 255
print(f"前景像素占比: {(seg==1).sum()/seg.size*100:.1f}%")

# 读原图
orig = np.array(Image.open(SRC).convert("RGB"))
print(f"原图 shape: {orig.shape}, 均值RGB: {orig.reshape(-1,3).mean(axis=0).round(1)}")

for method in ["distance", "canny", "sobel"]:
    e = extract_edge(binary, method=method)
    ratio = (e > 0).sum() / e.size * 100
    print(f"\n[{method}] 边缘像素占比: {ratio:.2f}%")

    # 叠加后, 非边缘像素是否真的不变?
    overlaid = overlay_edge_on_image(orig, e, color=(255, 0, 0), thickness=2)
    edge_bool = e > 0
    # 膨胀后的边缘 mask 难精确还原, 用"叠加图与原图的差异像素数"近似
    diff = np.any(overlaid != orig, axis=-1)
    print(f"  叠加后与原图差异像素占比: {diff.sum()/diff.size*100:.2f}% (应接近边缘占比)")

    # 测试 distance 不同 threshold
    if method == "distance":
        from backend.model.SamSeg.edge_utils import distance_transform_boundary
        for th in [0.5, 1.0, 1.5, 2.0]:
            b = distance_transform_boundary(binary, threshold=th)
            er = (b > 0).astype(np.uint8) * 255
            print(f"  distance threshold={th}: 边缘占比={(er>0).sum()/er.size*100:.2f}%")

# 保存当前默认 distance 叠加图, 直观看变色程度
e_default = extract_edge(binary, method="distance")
out = overlay_edge_on_image(orig, e_default, color=(255, 0, 0), thickness=2)
Image.fromarray(out).save(str(ROOT / "tests" / "_diag_edge_default.png"))
print(f"\n已保存诊断图: tests/_diag_edge_default.png")
