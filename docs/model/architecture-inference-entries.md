# 三类推理入口与模型绑定

> 状态：2026-11-19。样本标注入口已上线人工标注；原生 SAM3 候选待接入。地物提取、变化检测已分别绑定开放词汇分割与专用变化检测模型。
> 本文只定义入口、模型、调用链与边界，不改变各模型的推理口径。

## 一、总表

| 序号 | 功能入口 | 当前状态 | 绑定模型 | 代码路径 | 调用链 |
|---|---|---|---|---|---|
| 1 | 样本标注 | 人工标注已上线；模型候选待接入 | 原生 SAM3（接地头 + 交互头） | `backend/model/sam3native/` | 待接入：`/api/v1/samples/candidates` → `run_classes`；`/api/v1/samples/interactive` → 交互头 |
| 2 | 地物提取 | 已接入 | 开放词汇分割（SamSeg 本体，SegEarth-OV3） | `backend/model/SamSeg/` | 地图框选 → `/api/v1/imagery/capture` → Agent `segment_image` → `official.py` |
| 3 | 变化检测 | 已接入 | 训练好的变化检测模型（change_qwen ONNX） | `backend/model/change_detection/change_qwen_runtime/` | 地图框选 + 两期时相 → `georeference` → Agent `detect_change` → ONNX 推理 |

## 二、入口 1：样本标注 → 原生 SAM3

### 2.1 当前

- 前端：地图左上角「样本制作」→ 选时相 → 红框工作范围 → 绘制/顶点/框选 → 保存 → 导出数据集。
- 后端：`backend/model/sample/` 只做标注契约、任务存储、COCO/SHP/数据集导出，**不加载模型**。
- 原生 SAM3 权重在 `backend/model/sam3native/weights/sam3/sam3.pt`，本地副本，`.gitignore` 排除。

### 2.2 待接入

- 接地头批量候选：`NativeInstanceService.run_classes`，输入抓图 + 类别表，输出候选实例（类别、分数、像素框、多边形）与语义图。
- 交互头精修：`InteractiveSegmentService.predict`，输入点 / 框，输出掩码与多边形。
- 新增 `backend/model/sample/engine.py`：加载原生 SAM3 前释放 SamSeg processor，避免 6GB 显存 OOM。
- 新增 REST：

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/api/v1/samples/status` | — | `{available, device, model_loaded, samseg_loaded}` |
| POST | `/api/v1/samples/candidates` | `{image_path, classes[], prompts{}, postprocess{min_area,max_hole_area}}` | `{instances[], classes, palette, width, height, seconds}` |
| POST | `/api/v1/samples/interactive` | `{image_path, points[], point_labels[], box}` | `{masks, best, geojson}` |

- 前端新增「生成候选」「点框精修」；候选作为标注初稿，人工仍可增删改。
- 候选与人工标注共用 `annotations.json`，`source` 字段区分 `grounding` / `interactive` / `manual`。

### 2.3 验收

- 同一张抓图可生成候选、可点框精修、可保存回读。
- 候选类别使用当前任务类别表（默认 6 类，可自由输入自定义类别）；`MAX_INSTANCES=255`、`min_area` 过滤生效。
- 与 SamSeg 互斥加载，两个引擎交替不 OOM。

## 三、入口 2：地物提取 → 开放词汇分割

### 3.1 当前

- 前端：地图左上角「地物提取」→ DragBox 框选 → `/api/v1/imagery/capture` 抓 Esri/天地图带 CRS 影像 → 当前会话附件。
- Agent：`segment_image` → `backend/model/SamSeg/` → `official.py` 单图 / 512 滑窗 + 双头融合 + `prob_thd`。
- 类别：英文类别 + 英文同义词，由 Agent 把中文自然语言转换为模型提示词。
- 产物：整幅类别图、矢量、图例；结果登记 `image_metadata` / `vector_layer`。

### 3.2 口径

- 只有 SamSeg 本体一条路径，`SAMSEG_INFERENCE_MODE` 已删除，无开关、无原生 SAM3 兜底。
- 不做实例级编辑；实例级标注归样本标注入口。

### 3.3 后续

- 类别表模板化与同义词维护。
- 大图滑窗与分块推理参数化。
- 结果矢量化与面积统计保持现有链路。

## 四、入口 3：变化检测 → 训练好的变化检测模型

### 4.1 当前

- 前端：地图左上角「变化检测」→ DragBox 框选 → 选 T1/T2 Esri Wayback 版本 → 浏览器拼接两期 PNG → `/api/v1/imagery/georeference` 写 GeoTIFF。
- Agent：`detect_change` → `backend/model/change_detection/change_qwen_runtime/` → 训练好的 ONNX 权重。
- 权重：九寨沟滑坡（`change_qwen_jzg.onnx`）、成都建筑（`change_qwen_cdcd.onnx`），336×336 分块推理。
- 产物：概率 GeoTIFF、变化图斑矢量、面积统计。

### 4.2 口径

- 只走 change_qwen 专用 ONNX，唯一路径；`sam3_native_change` 已删除。
- 不借 SamSeg 或原生 SAM3 兜底。

### 4.3 后续

- 权重版本写入 `meta.json`，便于结果追溯。
- 阈值与分块参数按数据集校准。
- 新增区域权重时只增加权重与配置，不新增第二套推理链。

## 五、边界

- 三类入口各自独立：地物提取只出整幅语义图，样本标注只出实例级标注，变化检测只出两期变化图斑；互不兜底。
- 样本标注的人工链路当前不加载模型；原生 SAM3 权重不进入 Git 历史。
- 地物提取与变化检测的模型路径、权重、后处理均按本文固定，不在入口层做切换。
- 显存：SamSeg 常驻约 3.3GB，原生 SAM3 约 3.3GB；样本候选接入时必须先释放 SamSeg。
