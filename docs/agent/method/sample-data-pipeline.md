# 样本制作设计方案（AI 辅助单模式）

> 状态：标注编辑链路已实现（2026-11-19，阶段 79）；AI 候选与交互精修仍是设计。
> 相对 v1（2026-11-14，「手动 / AI 辅助 / AI 全自动」三模式）的变化：
> 1. **只保留 AI 辅助一条主线**。人工不需要从零画，AI 出候选后人工后修正——这是唯一工作流；
> 2. **人工编辑保留为流程内能力**（删实例、改类别、手绘补漏、顶点拖拽），不再单列为"手动模式"；
> 3. **取消 AI 全自动链**（证据提取 / RemoteCLIP 判别 / caption + VQA 汇编）。数据集生产等标注能力稳定后另立项，本设计不覆盖。
> 目标：地图框选 → 原生 SAM3 接地头批量出候选实例 → 人工增删改 → 落标注 JSON → 导出 COCO/SHP。
> 范围：实例分割口径，不含目标检测。

## 0. 三部分职责边界（各司其职，互不兜底）

| 部分 | 模型 / 权重 | 输入 | 产物 | 交互 |
|---|---|---|---|---|
| 地物提取 | SamSeg 本体（`backend/model/SamSeg/`，开放词汇语义分割） | 地图框选抓图 | 整幅类别图 + 矢量 + 图例 | 一次点击，Agent 调 `segment_image` |
| 变化检测 | change_qwen 专用 ONNX（`change_detection/change_qwen_runtime/`，九寨沟滑坡 / 成都建筑双权重） | 两期影像 | 变化图斑 + 概率图 + 面积统计 | `ChangeDetectDialog` |
| 样本制作 | 原生 SAM3（`backend/model/sam3native/`：接地头 + 交互头） | 抓图 + 类别表 + 人工提示 | 标注 JSON / 掩码 GeoTIFF / COCO / SHP | 弹窗 + 地图联动 + 实例编辑 |

硬边界：

- 地物提取只出"整幅语义图和平铺矢量"，不做实例级标注编辑；
- 样本制作只出"实例级标注"，不再产出业务用分类成果图；
- 原生 SAM3 不再参与地物提取（`SAMSEG_INFERENCE_MODE` 已废弃），变化检测也不借它兜底。

三类入口的当前状态、模型绑定与待接入接口见 [architecture-inference-entries.md](../../model/architecture-inference-entries.md)。

## 1. 主流程（唯一一条）

```
地图框选（复用 captureImagery，抓当前视域，带 CRS）
  → 选类别表（类别 + 同义词，可存模板）
  → SAM3 接地头 run_classes：框选范围 + 类别表 → 批量候选实例
  → 候选实例矢量上图（地图坐标 GeoJSON）
  → 人工后修正：删实例 / 改类别 / 手绘补漏 / 点框精修（二期）
  → 校验（空掩码、面积阈值、重叠、类别缺省）
  → 落 annotations.json（任务目录）
  → 栅格化回掩码 GeoTIFF（可选）+ 导出 COCO / SHP
```

**人工介入点**：候选生成之后的全量编辑。**AI 参与**：只出几何初稿（候选实例），不做类别裁决以外的推断。

## 2. 本期范围（阶段 79）

已实现：

- 时相选择：单期直接铺图，双期同范围同缩放抓图并进入卷帘；
- 实例列表 + 地图联动的增 / 删 / 改类别；
- 多边形手绘、顶点拖动/加点/删点、框选批删；
- `annotations.json` 落盘（像素 + 经纬度）+ COCO / SHP 导出。

未实现（阶段 80）：

- 接地头 `run_classes` 批量候选（类别同义词、实例合并、面积/孔洞后处理）；
- 交互头点 / 框精修；
- 掩码 GeoTIFF 栅格化；
- 与 SamSeg 的显存互斥加载。

不做（明确出界）：

- AI 全自动链（M4 证据 / M5 RemoteCLIP 判别 / M6 caption + VQA、referring 数据集汇编）；
- 训练调度、任务队列、多任务并发；
- 视频 / 多帧实例传播；
- 大图整幅滑窗推理（本期限定视域尺寸，见 §6 风险 4）。

## 3. 交互设计（地图弹窗）

入口：[MapPanel.vue](../../../frontend-vue/src/components/map/MapPanel.vue) 顶部「样本制作」按钮，与「卷帘」「地物提取」并列。点击后由 `SamplePhaseDialog.vue` 选时相与任务名，确认后进入工作台内标注模式。

弹窗与面板：

1. **时相**：任务名、项目、样本区域（点「在地图上框选」回地图拖框，松开后带新范围重开弹窗）、时相 A（必选）、时相 B（可选）。范围过大时抓图会拒绝，需先放大地图。框选范围以红色边界 + 正方形格网标出，长边固定等分 10 格、格宽随面积自动变化，一直保留到退出标注，作为范围与比例参考。
2. **影像**：单期抓一张 Esri Wayback GeoTIFF 直接铺图；双期以同一 bbox、同一缩放抓两张，进入卷帘，B 按分隔线右侧裁剪。
3. **标注**：影像载入后直接在整幅影像内标注，不再二次框选工作范围。地图左侧 `SamplePanel.vue` 提供绘制 / 顶点 / 框选、实例列表、类别自由输入与颜色选择、删除、保存、COCO/SHP/数据集下载；导出时弹出系统保存对话框选择保存位置。面板可折叠为一行标题，折叠后仍可在地图上绘制。
4. **操作**：左键落点，F2/Enter 闭合，Backspace 撤销，Esc 取消；拖动顶点、点边加点、Shift 点顶点或 F2 删点；框选后 Delete 批删；最少 3 点。

状态机：`idle → phase_selecting → capturing → editing → saved`。有未保存修改时退出二次确认。

## 4. 后端设计

### 4.1 模块

已实现（阶段 79）：

| 文件 | 职责 |
|---|---|
| `annotations.py` | `annotations.json` 契约校验、类别表、像素↔经纬度换算、bbox/面积、`crop_px` 校验 |
| `storage.py` | 任务目录、越界守卫、源影像复制、读写与任务列表 |
| `export.py` | COCO（复用 `sam3native/coco_export.write_coco`）/ SHP（复用 `vendor/spatial/export_utils`）/ 按 `crop_px` 裁剪 GeoTIFF 与 PNG / 数据集打包 |

未实现：

| 文件 | 职责 |
|---|---|
| `engine.py` | 显存仲裁：加载 native SAM3 前先释放 SamSeg processor |
| `candidates.py` | 包装 `inst_service.NativeInstanceService.run_classes` → 候选实例 |
| `interactive.py` | 交互头点 / 框精修（`InteractiveSegmentService.predict`） |

`backend/api/samples.py` 已挂 `/api/v1/samples`。

### 4.2 REST 契约

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/samples/annotations` | `{project, task_dir, annotations, source_images[]}` | `{status, path, instance_count, task_dir}` |
| GET | `/samples/annotations` | `?project=&task_dir=` | `{status, annotations}` |
| GET | `/samples/tasks` | `?project=` | `{status, tasks[{name,instance_count,saved_at,width,height}]}` |
| GET | `/samples/export` | `?project=&task_dir=&format=coco\|shp\|dataset` | 文件下载；`dataset` 为裁剪影像 + 标注 + 矢量的 zip |

注意：`source_images` 只接受 `agent-files/` 内路径，越界拒绝。

### 4.3 存储布局

```
agent-files/samples/{project}/{task}/
  source_a.tif         # 时相 A 抓图副本（带 CRS，整张）
  source_b.tif         # 双期时存在
  annotations.json     # 标注真值（影像像素坐标系）
  meta.json            # 任务摘要
  dataset/image/       # 数据集导出：按 crop_px 裁剪的 phase_a/b.tif + png
  dataset/vector/      # 数据集导出：SHP/GeoJSON
  coco.json            # 导出产物
  annotations.shp/.shx/.dbf/.prj/.cpg/.geojson
  annotations_shp.zip  # SHP 打包下载
  {task}_dataset.zip   # 数据集包：image/ + annotations.json + coco.json + vector/ + meta.json
```

### 4.4 `annotations.json` 契约

```json
{
  "version": 1,
  "task": {"name": "sample_20261119_122814", "project": "default", "created_at": "..."},
  "image": {
    "path": "source_a.tif", "width": 135, "height": 175,
    "bounds": [103.947, 30.756, 103.961, 30.771],
    "crs": "EPSG:4326",
    "crop_px": [0, 0, 135, 175],
    "source_width": 135, "source_height": 175,
    "phases": [{"date": "2026-11-18", "path": "source_a.tif"}]
  },
  "classes": [{"id": 1, "name": "building", "color": [230, 25, 75]}],
  "instances": [{
    "id": "s...", "label": "building", "class_id": 1, "class_name": "building",
    "score": null, "source": "manual", "shape_type": "polygon",
    "points": [[20.0, 30.0], [70.0, 30.0], [70.0, 100.0]],
    "coordinates": [[103.9495, 30.7685], ...],
    "bbox_px": [20.0, 30.0, 70.0, 100.0], "area_px": 1750,
    "edited": true
  }]
}
```

约定：`coordinates` 是 EPSG:4326 事实源，`points` 是影像像素坐标（COCO 用）；两者同时落盘。`image.width/height` 为影像尺寸，`image.bounds` 为影像经纬度范围；当前不二次框选，`image.crop_px` 固定为 `[0, 0, width, height]`，`source_width/source_height` 与影像尺寸一致，保留字段用于兼容旧任务。后端校验所有顶点落在影像内。类别默认 6 类，支持在面板内自由输入新类别并自选颜色；自定义类别写入 `classes` 与实例的 `class_name/class_id`。

## 5. 分阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| 79 | 人工标注闭环：时相选择 → 框选样本区域 → 抓图/卷帘 → 绘制/顶点/框选 → `annotations.json` → COCO/SHP/数据集（影像 + 标注 + 矢量） | ✅ 已实现 |
| 80 | AI 候选（接地头 `run_classes`）+ 交互头点/框精修 + 掩码 GeoTIFF | 待排期 |

数据集生产（证据 / CLIP / caption / VQA）不排期，等标注链路稳定后另写设计。

## 6. 风险与对策

1. **显存（6GB 卡，实测可用 5.8GB）**：SamSeg 常驻 3.3GB + 原生 SAM3 3.3GB 必 OOM。对策：`engine.py` 做互斥——进样本制作先 `runner.release_model()` 释放 SamSeg，退出后交给启动预热重载；native 侧支持 `release_all()`。
2. **加载时间 30–40s**：弹窗生命周期内复用同一 service（`set_image` 缓存编码，点选不重载）；弹窗关闭即释放。
3. **候选质量**：接地头对开放词汇敏感，同义词爆炸会撑大实例数。对策：`MAX_INSTANCES=255` 截断、`min_area` 过滤、同类同框 `_merge_same_box`、默认提示词收紧为 2–3 个/类。
4. **视域尺寸**：本期不做滑窗×实例组合，单次推理按抓图尺寸走（≤2048px 视域）；超限提示用户缩小范围。
5. **坐标一致性**：通过 CRS 同一的抓图 + rasterio transform 保证；像素域编辑禁止（沿用 v1 总原则）。
6. **类别口径**：样本制作类别表与 SamSeg 的 7 类默认集对齐，便于同一批数据既能喂分割也能喂样本，但**不共享权重**。

## 7. 与既有实现的关系

- 复用（不改）：`sam3native/interactive_segmentor.py`、`inst_service.py`、`mask_postprocess.py`、`palettes.py`、`coco_export.py`、`backend/integrations/vendor/spatial/export_utils.py`。
- 复用（口径）：`backend/model/SamSeg/geoio.py` 的读图/写掩码/矢量化，以及 `captureImagery` 抓图链路。
- 停用：`SAMSEG_INFERENCE_MODE`（已删）、`sam3native/service.py::segment`（不再被地物提取调用，保留给样本候选）。
- 待改名：`backend/model/segmentation/sam3_native/adapter.py`（实际代理 SamSeg，名字误导）。
