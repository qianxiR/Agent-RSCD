# 标注编辑沙盒（独立进程，不进系统）

只做标注编辑：**服务端默认影像 → 画多边形 / 顶点增删改 → 保存 annotations.json（带经纬度）→ 浏览器下载 COCO / SHP**。
不加载任何模型，不改 `backend/`、`frontend-vue/`。

## 启动

```powershell
cd E:\1代码\系统\Agent-RSCD
D:\anaconda3\envs\sam3\python.exe sandbox\sample-ui\server.py
# 打开 http://127.0.0.1:8765/
```

## 默认影像

服务端按顺序取：`SAMPLE_IMAGE` 环境变量 → `agent-files/imagery/captures/` 最新且带 CRS 的 GeoTIFF → `work/uploads/` 最新文件。
影像以 `ol.source.ImageStatic` 铺在地图上，坐标范围取自 GeoTIFF 的 transform（不是文件名或请求 bbox）。

## 操作（对齐 X-AnyLabeling）

顶部地图工具条（复刻主系统风格，左上角）：**图层 | 卷帘 | 时相 | 绘制 | 顶点 | 框选**。

| 操作 | 方式 |
|---|---|
| 画多边形 | 「画多边形」→ 左键落点；点回起点 / **F2** / Enter 闭合；**Backspace** 撤销上一点；**Esc** 取消 |
| 改顶点 | 「编辑顶点」→ 直接拖动 |
| 加顶点 | 编辑模式下**单击多边形的边** |
| 删顶点 | **Shift + 点顶点**，或选中顶点后按 **F2**；最少 3 点 |
| 框选批量选择 | 「框选」→ 地图拖框，选中范围内所有标注（列表高亮 + **每个选中形状的顶点都会显示**） |
| 批量删除 | 框选后按 **Delete** |
| 删形状 | 浏览模式选中后 Delete |
| 改类别 | 列表里下拉选择（building / road / water / bareland / vegetation / farmland） |
| 双击 | 已由 **F2** 替代（闭合 / 删顶点） |

## 工具条与多时相 / 卷帘

- **图层**：显示/隐藏左侧标注面板（隐藏后地图自动重算尺寸）；
- **卷帘**：开启后出现白色分隔线（带 ⇔ 手柄），可拖动；B 时相只在分隔线右侧渲染；
- **时相**：弹出面板，内含 A/B 两个 Wayback 年度版本下拉（13 个年份）、时相透明度、影像透明度、显示抓图影像开关；
- 工具条按钮选中态为强调色描边（与主系统一致）。

## 产物

保存后写入 `sandbox/sample-ui/work/<任务目录>/annotations.json`，每个实例同时含：

- `points`：图像像素坐标（编辑用，不重复闭合首点）
- `coordinates`：经纬度坐标（EPSG:4326，标注与 SHP 用）
- `bbox_px` / `area_px` / `label` / `class_id` / `score` / `shape_type`

下载（浏览器直接另存）：

- **COCO JSON**：`/api/download/coco?task_dir=...`（像素多边形）
- **SHP zip**：`/api/download/shp?task_dir=...`，压缩包内含 `annotations.shp/.shx/.dbf/.prj/.cpg` 与 `.geojson`，坐标系 WGS84（`.prj` 为 GCS_WGS_1984）

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/scene` | 默认影像信息（尺寸、真实经纬度范围、预览地址） |
| GET | `/api/preview?path=` | 影像转 PNG 预览 |
| POST | `/api/annotations` | 保存标注 |
| GET | `/api/annotations?task_dir=` | 读回标注 |
| GET | `/api/download/coco` | 下载 COCO JSON |
| GET | `/api/download/shp` | 下载 SHP 压缩包 |
| GET | `/api/health` | 健康检查 |

## 已实测（无模型）

默认影像 `capture_esri_20261119_104141.tif`（1024×457）自动载入；画两个 4 点多边形均用 **F2** 闭合；「框选标注」拖框选中 2 个并 Delete 批量删除（2→0）；编辑模式选中顶点后按 **F2** 顶点 4→3；时相下拉 13 个年份可选，卷帘分隔线显示、透明度 55% / 影像透明度 70% 生效；保存与 COCO/SHP 下载此前已验证（`.prj` 为 `GCS_WGS_1984`）；页面无 JS 报错。

## 已知限制

- 默认影像无 CRS 时只能画 COCO（SHP 下载会提示缺少坐标系）。
- 类别固定 6 类（building / road / water / bareland / vegetation / farmland），页面不提供类别编辑。
- 未做交互头点/框精修、遮挡/重叠组管理、LabelMe JSON 直接导入导出。
