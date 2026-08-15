> 🗄️ **已归档 · 2026-07-09**：本文针对原生 JS `frontend/map/`，该前端已被 `frontend-vue/` 取代。
> - 现行地图实现以 `frontend-vue/src/composables/useMap.js` 为准。
> - 「GeoServer → UI 加载流程」整体思路仍可参考，但代码示例对应已停用的旧前端。
> - 平台层架构以 [`architecture-platform.md`](../platform/architecture-platform.md) 为准。
> - 本文档仅作历史方案留存。

# 前端 map/ 模块拆分方案 + GeoServer → UI 加载流程

> 本文覆盖两件事：
> 1. [`frontend/map/`](../frontend/map/) 三个文件**按功能拆分**的建议方案；
> 2. 从 **GeoServer 加载数据 → 显示到前端 UI** 的完整流程与方法，作为可落地的对接方案。
>
> 关联文档：总纲 [architecture.md](./architecture.md)、平台层 [architecture-platform.md](./architecture-platform.md) §7（前端指令执行层 + OpenLayers 地图容器）。

---

## 一、现状盘点：三个文件各自在做什么

| 文件 | 行数 | 核心职责 | 依赖 |
|---|---|---|---|
| [`wms-render.js`](../frontend/map/wms-render.js) | ~1340 | **地图引擎核心**。OpenLayers 单例 `ol.Map` 初始化、图层注册表、WMS/影像/矢量三类图层叠加、视图状态持久化、GetFeatureInfo 点查弹窗、图例浮层。外加一堆**与地图无关的 Markdown 富表格/图表/面板 mock/下载卡片**（历史遗留）。 | `ol`（CDN）、`cs()`/`activeConvId`（core/state）、后端 `/api/v1/image/meta`、`/api/v1/vector/shp-to-geojson`、GeoServer WMS |
| [`layer-panel.js`](../frontend/map/layer-panel.js) | ~540 | **图层面板（图层管理 Tab）**。工作空间树渲染、显隐/定位/删除/上传发布、搜索过滤、地图 ↔ 面板勾选状态双向同步。 | `_getMap`/`showWmsInPanel`/`_unregisterWmsLayer`（wms-render）、后端 `/api/v1/geoserver/*` |
| [`task-monitor.js`](../frontend/map/task-monitor.js) | ~235 | **任务日志监控弹窗**。列出 AI 任务、查看任务详情/执行日志。自带 IIFE + 内联 CSS。 | 后端 `/api/v1/tasks/*` |

### 三个突出的问题

1. **`wms-render.js` 名不副实**：它叫 "wms-render"，实际混了「地图核心」+「Markdown 表格/图表/面板渲染」两类完全无关的职责（文件头注释还停留在 "Markdown 解析 + UI组件 + 代码高亮"）。这让它既是地图引擎又是渲染工具箱，1340 行里真正属于地图的只有一半左右。

2. **`task-monitor.js` 与地图无关**：它对接的是 `/api/v1/tasks`（任务日志），跟地图/影像/GIS 没有任何关系，放在 `map/` 下是历史归类错误（大概因为它是从 ai-actions 按钮触发的弹窗）。

3. **模块暴露方式不统一**：`task-monitor.js` 用 IIFE + `window.*` 挂载（局部隔离），`wms-render.js` / `layer-panel.js` 用全局函数直接挂 `window`（无隔离，靠 HTML `<script>` 顺序保证依赖）。

---

## 二、按功能拆分方案

原则：**一个文件一个职责**，文件名=职责；保留 `<script>` 全局函数风格（不引入构建工具），降低迁移成本。

### 推荐目录结构

```
frontend/
├── map/                          ← 只放「地图/GIS」相关
│   ├── map-core.js              ← 【新】OpenLayers 单例 + 图层注册表 + 视图状态 + GetFeatureInfo（从 wms-render.js 抽出）
│   ├── layer-loader.js          ← 【新】WMS / 影像 / 矢量三类图层加载入口（showWmsInPanel/showImageInPanel/showVectorFile）
│   └── layer-panel.js           ← 【保留】图层管理面板（仅微调：依赖从 wms-render 改成 map-core/layer-loader）
├── render/                       ← 【新目录】Markdown 富内容渲染（从 wms-render.js 抽出）
│   ├── rich-table.js            ← enhanceMarkdownTables/buildRichTable/copyTable（富表格）
│   └── chart-panel.js           ← renderChart/renderPanel（Canvas 图表/面板 mock，当前虽未接线但保留）
└── ...
```

> `task-monitor.js` **移出 `map/`**：它对接的是任务日志，建议放到 `chat/`（属于 AI 面板的辅助弹窗）或新建 `panel/` 目录。

### 拆分对照表（wms-render.js 1340 行 → 拆成 4 个文件）

| 来源（wms-render.js 的函数） | 去向 | 说明 |
|---|---|---|
| `enhanceMarkdownTables` / `buildRichTable` / `copyTable` / `renderRichTableBody` / `buildEmptyRichTable` | `render/rich-table.js` | 富表格，与地图完全无关 |
| `renderChart` / `renderPanel` / `_cssVar` | `render/chart-panel.js` | Canvas 图表/面板 mock（当前未接线，但被 `renderImage` 等引用，先迁出） |
| `renderImage` / `renderLayerControl` / `renderLayerDisplay` / `renderDownload` / `renderTable` | `render/action-renderers.js`（或并入对应模块） | 这些是 `event_type` 的执行函数（见 architecture-platform §7.1），本质是「frontend_action 渲染层」而非地图核心；它们**调用** `layer-loader.js` 的加载函数 |
| `_map` / `_layerRegistry` / `initWmsMap` / `_clearMapLayers` / `_fitMapToExtent` / `_syncCurrentConvMapView` / `_restoreMapViewForConv` / `_bindMapViewStateSync` / `_makeBoundarySLD` / `_getMap` / `setMapRestoreMode` / `restoreMapViewForConv` / `_unregisterWmsLayer` | **`map/map-core.js`** | 地图引擎本体：单例 + 注册表 + 视图状态 + 边界 SLD |
| `showWmsInPanel` / `buildWmsUrl` / `_bindFeatureInfoIfNeeded` / `_handleWmsFeatureInfo` / `closeWmsPopup` / `_fetchImageMeta` | **`map/layer-loader.js`** | WMS 图层加载 + 点查 |
| `showImageInPanel` / `_appendPreviewParam` | **`map/layer-loader.js`** | 影像图层加载 |
| `_addVectorLayer` / `showVectorFile` | **`map/layer-loader.js`** | 矢量图层加载 |
| `_renderLegendOverlay` | **`map/map-core.js`**（或 `layer-loader.js`） | 图例浮层，DOM 渲染 |
| `showWmsEmpty` / `clearWmsPanel` / `onWmsImageLoaded` | **`map/map-core.js`** | 清空/空状态 |
| `buildWmsErrorBlock` / `_fallbackCopy` / `addResultCard` / `openSandboxDownloadDialog` | `render/`（工具函数）或 `chat/msg-ui.js` | 错误块/下载对话框/回传卡片，是通用 UI 工具 |

### `index.html` 加载顺序调整

拆分后 `<script>` 顺序需保证依赖链（被依赖者先加载）：

```html
<!-- ════════ render: Markdown 富内容 (被 event_type 渲染函数依赖) ════════ -->
<script src="render/rich-table.js"></script>
<script src="render/chart-panel.js"></script>

<!-- ════════ map: 地图引擎 ════════ -->
<script src="map/map-core.js"></script>       <!-- 地图单例 + 注册表, 最先 -->
<script src="map/layer-loader.js"></script>   <!-- 依赖 map-core 的 _map/_registerLayer -->
<script src="map/layer-panel.js"></script>    <!-- 依赖 layer-loader 的 showWmsInPanel -->

<!-- ════════ chat: 任务日志 (从 map/ 迁入) ════════ -->
<script src="chat/task-monitor.js"></script>
```

### 迁移建议（落地步骤）

1. **第一步（低风险）**：把 `task-monitor.js` 从 `map/` 移到 `chat/`，改 `index.html` 的引用路径。功能零改动，只是归类。
2. **第二步**：从 `wms-render.js` 抽出富表格/图表到 `render/`。注意 `renderImage`/`renderLayerControl` 等仍引用它们，需保证 `render/*` 先加载。
3. **第三步（核心）**：把 `wms-render.js` 拆成 `map-core.js` + `layer-loader.js`。关键是分清「地图实例管理（map-core）」和「往实例上加图层（layer-loader）」——后者依赖前者的 `_map` / `_registerLayer` / `_fitMapToExtent`。
4. 每步拆完手动验证：打开地图 Tab、上传影像、图层管理面板定位/显隐、SamSeg 分割结果叠加、切换对话恢复图层。

> **务实提醒**：当前项目无构建工具、无模块系统（纯全局函数 + `<script>` 顺序依赖）。拆分的收益是「可读性 + 职责清晰」，**不会带来性能或功能提升**。若团队人手有限，**第一步 + 第三步**性价比最高，第二步（render 抽离）可暂缓。

---

## 三、GeoServer → UI 加载流程方案

### 3.1 总体架构

```
┌─────────────┐   REST/WMS    ┌──────────────┐    fetch     ┌──────────────────────────┐
│  GeoServer  │ ◄───────────► │  FastAPI 后端 │ ◄──────────► │     前端 (OpenLayers)     │
│ (8080)      │               │  main.py     │              │  map/layer-loader.js     │
│ - 工作空间   │               │ /api/v1/     │              │  map/layer-panel.js      │
│ - 图层(栅格/ │               │  geoserver/* │              │  ol.Map 单例             │
│   矢量)     │               │              │              │                          │
└─────────────┘               └──────────────┘              └──────────────────────────┘
```

**两条数据通路**（关键区分）：
- **REST 通路**：前端 ↔ 后端 ↔ GeoServer REST API。用于「列图层 / 查 bbox / 删图层 / 发布图层」。后端做鉴权、聚合、类型补全。
- **WMS 通路**：前端 → GeoServer WMS（**直连，不经后端**）。用于「出图」（瓦片渲染）。后端只提供 WMS URL，不代理图片流量。

### 3.2 两条触发路径

前端让 GeoServer 图层显示到地图上，有**两条入口**，最终都汇聚到同一个加载函数 `showWmsInPanel()`：

| 路径 | 触发者 | 入口函数 | 典型场景 |
|---|---|---|---|
| **A. AI 自然语言** | 用户在聊天框说话 → LLM 调工具 → 后端发 `frontend_action` | `renderLayerControl/Display()` → `showWmsInPanel()` | "显示 cd_shp:chengduqu" |
| **B. 图层面板手动** | 用户点击图层管理面板的勾选/定位 | `toggleLayerVisibilityFromPanel()` / `locateLayerFromPanel()` → `showWmsInPanel()` | 图层树勾选、🎯 定位按钮 |

> 两条路径复用同一个 `showWmsInPanel()`（`layer-loader.js`），保证加载行为、注册表登记、视图 fit、点查绑定完全一致——这是设计上的关键收敛点。

### 3.3 路径 A：AI 自然语言加载（完整时序）

```
用户输入"显示 cd_shp:chengduqu"
  │
  ▼
[chat/samseg.js] sendMessage()  ──WebSocket──►  [后端] LLM 决策调用 show_layer 工具
  │                                                   │ 工具查 GeoServer, 构造 frontend_action
  │                                                   ▼
  │  ◄────────WebSocket: frontend_action────────  {event_type:"layer_display",
  │                                                   payload:{layer_name, workspace, bbox, wms_url}}
  ▼
[chat/thinking.js] executeFrontendAction("layer_display", payload)
  │
  ▼
[layer-loader.js] renderLayerDisplay()  →  showWmsInPanel(layer_name, workspace, bbox, wms_url)
  │  (返回 Promise, 等瓦片 onload)
  ▼
[map-core.js] _registerLayer() + _map.addLayer() + _fitMapToExtent()
  │
  ▼  TileWMS 瓦片加载完成 (tileloadend)
  │
  ▼
[chat/thinking.js] wsClient.sendToolResult({status:"success", loaded:true})  ──► 后端 LLM 解除阻塞
```

**要点**：
- 后端工具负责「查 GeoServer 是否有这图层 + 拿到 bbox + 构造 wms_url」，把这三个关键参数塞进 `frontend_action.payload`。
- 前端**不等后端**渲染图片，而是直接拿 `wms_url` 让 OpenLayers `TileWMS` source 去直连 GeoServer 出图（`serverType:'geoserver'`）。
- `showWmsInPanel` 返回 Promise，首个瓦片 `tileloadend` 即 resolve；前端把「是否真的加载成功」回传后端，LLM 据此判断要不要重试/报错。

### 3.4 路径 B：图层面板手动加载

图层面板（`layer-panel.js`）打开时先拉工作空间树：

```
[切换到"图层管理"Tab] switchWorkbenchTab('layer')
  │
  ▼
refreshLayerPanel()  ──GET /api/v1/geoserver/services──►  后端聚合 GeoServer REST
  │                                                         (list_services + 给每个图层补 type)
  ▼
_renderLayerTree()  渲染工作空间树 (📂 workspace / 图层行 + 显隐checkbox + 🎯定位 + 🗑删除)
```

**显隐切换（勾选）**：
```
toggleLayerVisibilityFromPanel(checkbox)  [勾选]
  │
  ▼
showWmsInPanel(name, ws, null, null, ..., {forceFit:false})   ← 不传 bbox, 用全景兜底
  │  加载失败 → checkbox.checked = false (回滚)
  ▼
syncLayerPanelCheckboxes()  ← 地图图层变化后同步面板勾选
```

**定位（🎯 / 点图层名）**：
```
locateLayerFromPanel(ws, name)
  │
  ├─► switchWorkbenchTab('map')                              ← 先切回地图 Tab 让用户看到
  │
  ├─► GET /api/v1/geoserver/layers/{ws}/{name}/bbox          ← 单独查精确 bbox
  │       返回 {bbox:{minx,miny,maxx,maxy}}
  │
  ▼
showWmsInPanel(name, ws, bbox4326, null, ..., {forceFit:true})  ← 带精确 bbox + 强制定位
  │
  ▼
_setLayerCheckbox(ws, name, true)                            ← 同步面板勾选
```

> **勾选 vs 定位的区别**：勾选只叠加不挪视图（`forceFit:false`）；定位会先查 bbox 再把地图 fit 过去（`forceFit:true`）。两者最终都调 `showWmsInPanel`。

### 3.5 核心加载函数：`showWmsInPanel()` 内部流程

这是所有 WMS 图层显示的唯一收口（`map/layer-loader.js`）：

```javascript
function showWmsInPanel(layerName, workspace, bbox, wmsUrl, caption, record, viewOptions) {
  // 1. 确保 map 单例已初始化 (init.js 已调 initWmsMap, 这里是兜底)
  if (!_map) initWmsMap();

  // 2. bbox 4326 → 视图投影 (用于 fit; 无 bbox 则全球兜底, 但不强制 fit)
  const extent = bbox ? ol.proj.transformExtent(bbox, 'EPSG:4326', view.getProjection())
                      : [-180,-90,180,90];

  // 3. 构造 TileWMS source (★ 直连 GeoServer, 不经后端代理)
  //    - TILED:true       瓦片化, 按视口按需加载
  //    - TRANSPARENT:true 透明叠加, 不遮挡下层
  //    - 不设 crossOrigin 绕过 GeoServer 默认未开 CORS 的限制
  //    - viewOptions.sldBody 可选: 自定义 SLD (如仅边界线、透明填充)
  const source = new ol.source.TileWMS({
    url: wmsUrl || 'http://localhost:8080/geoserver/wms',
    params: { LAYERS: ws+':'+layerName, FORMAT:'image/png', TILED:true,
              TRANSPARENT:true, VERSION:'1.1.1', SRS:'EPSG:4326', ...(sldBody?{SLD_BODY}) },
    serverType: 'geoserver', transition: 0,
  });

  // 4. 创建 Tile 图层 (zIndex:10, opacity:0.8, 半透明叠加)
  //    挂 layerName/workspace/wmsBbox/wmsWidth/wmsHeight 到图层属性 (GetFeatureInfo 反算像素用)
  const layer = new ol.layer.Tile({ source, opacity:0.8, zIndex:10 });

  // 5. ★ 注册到图层注册表 (key = workspace:layerName) —— 统一增删查
  _registerLayer(ws+':'+layerName, layer, {type:'wms', workspace, layerName, bbox});

  // 6. addLayer + 绑定 GetFeatureInfo 点查 (单次绑定)
  _map.addLayer(layer);
  _bindFeatureInfoIfNeeded();
  syncLayerPanelCheckboxes();              // 同步图层面板勾选

  // 7. 视图 fit (仅当有真实 bbox 且 forceFit 时; 遵守"不打断用户视角"叠加语义)
  if (bbox) _fitMapToExtent(extent, !!(viewOptions && viewOptions.forceFit));

  // 8. 记录到对话 state (切换对话可恢复)
  if (record !== false) { cur.wmsImages.push({type:'wms', layerName, workspace, bbox, ...}); _persistConvWms(); }

  // 9. 返回 Promise: 首个 tileloadend → loaded:true; tileloaderror/10s超时 → loaded:false
  return new Promise((resolve) => {
    source.on('tileloadend',  () => resolve({loaded:true,  layer_name, workspace}));
    source.on('tileloaderror',() => resolve({loaded:false, error:'WMS 瓦片加载失败'}));
    setTimeout(() => resolve({loaded:false, error:'WMS 加载超时 (10s)'}), 10000);
  });
}
```

**设计要点**：
- **TileWMS 而非 ImageWMS**：瓦片化按需加载，支持透明叠加，多图层共存互不遮挡。
- **zIndex 分层**：底图影像 `5` < WMS `10` < 矢量描边 `15`，保证叠加顺序。
- **图层注册表 `_layerRegistry`**：统一管理所有业务图层（WMS/影像/矢量），`_clearMapLayers()` 切换对话时据此清理，替代脆弱的 `tag` 标签遍历。
- **fit 语义**：`_fitMapToExtent` 仅在「地图当前无业务图层」或「显式 forceFit」时才挪视图——避免每叠加一个图层就把用户视角拉走。

### 3.6 后端 REST 端点清单（前端直接调用）

| 方法 | 端点 | 用途 | 前端调用方 |
|---|---|---|---|
| GET | `/api/v1/geoserver/services` | 工作空间+图层树（含 type） | `refreshLayerPanel()` |
| GET | `/api/v1/geoserver/layers/{ws}/{name}/bbox` | 单图层 bbox（WMS GetCapabilities） | `locateLayerFromPanel()` |
| POST | `/api/v1/geoserver/layers` | 发布本地文件为图层 | `uploadLayerFromPanel()` |
| DELETE | `/api/v1/geoserver/layers/{ws}/{name}` | 删除图层（含数据存储） | `deleteLayerFromPanel()` |
| GET | `/api/v1/geoserver/feature-info?...` | GetFeatureInfo 属性查询 | `_handleWmsFeatureInfo()` |
| GET | `/api/v1/image/meta?path=...` | 影像地理元数据（CRS/bbox） | `_fetchImageMeta()` |
| GET | `/api/v1/vector/shp-to-geojson?shp_path=...` | SHP→GeoJSON 转换 | `showVectorFile()` |

> 这些端点**不走 LLM**（设计意图见 `main.py:548` 注释）：让前端面板直接拉数据/操作图层，省 token、即时响应。AI 自然语言路径则是另一条（LLM 调工具 → frontend_action）。

### 3.7 三类图层的加载差异

| 图层类型 | 加载函数 | OL Source | 数据来源 | 典型触发 |
|---|---|---|---|---|
| **WMS（GeoServer 已发布）** | `showWmsInPanel()` | `ol.source.TileWMS` | GeoServer WMS（直连） | 图层面板勾选/定位、AI `layer_display` |
| **本地影像（GeoTIFF/PNG）** | `showImageInPanel()` | `ol.source.ImageStatic` | 后端 `/api/v1/image/meta` 查 bbox+CRS，再按 URL 出图 | SamSeg 分割结果原图、上传的影像 |
| **本地矢量（SHP/GeoJSON）** | `showVectorFile()` | `ol.source.Vector` | SHP 走 `/api/v1/vector/shp-to-geojson` 转换；GeoJSON 直读 | 矢量降级叠加（GeoServer 不可用时） |

> SamSeg 分割结果（`renderImage`）会**组合**三者：原图走 `showImageInPanel`（底图），变化面优先走 `showWmsInPanel`（GeoServer WMS）、降级走 `showVectorFile`（本地矢量），边缘线同理。

### 3.8 视图状态与对话恢复

为保证「切换对话/刷新后地图回到离开前的状态」：

- **`_syncCurrentConvMapView()`**：地图视图 `change:center/resolution/rotation` 时触发，把 center/zoom/rotation 写入当前对话 `state.mapView` + 持久化 localStorage。
- **`_restoreMapViewForConv(convId)`**：切回某对话时，把该对话的 mapView 应用回地图视图。
- **`cur.wmsImages[]`**：每个加载的图层都记一条快照（type/layerName/bbox/url...），切对话时据此重建图层。
- **`setMapRestoreMode(true)`**：批量恢复期间关闭自动 fit，避免每个图层都抢一次视图。

### 3.9 默认中心点

地图初始化（`map-core.js::initWmsMap`）的默认视图写死为**成都市中心**，坐标取自 `tests/data/cd/chengduqu.shp`（WGS84/EPSG:4326）的几何中心：

```javascript
view: new ol.View({
  center: [103.943905, 30.764575],   // 成都市中心 (chengduqu.shp bbox 中点)
  zoom: 9,
  projection: 'EPSG:4326',
})
```

> bbox = `[102.991678, 30.091386, 104.896132, 31.437765]`，中心 = `((minx+maxx)/2, (miny+maxy)/2)`。
> 仅在地图首次初始化、无历史视图恢复时生效；后续各图层 `forceFit` 与对话恢复不受影响。

---

## 四、对接 GeoServer 的落地清单（给新接入者的 checklist）

若要把一个新的 GeoServer 图层接入并显示到 UI，按以下步骤：

1. **后端确认图层已发布**：GeoServer REST 或 Web UI 发布好图层（工作空间:图层名），能出图。
2. **路径 B（最快验证）**：打开前端「图层管理」Tab → 刷新 → 找到图层 → 勾选/🎯定位。能显示即说明 WMS 通路正常。
3. **路径 A（AI 接入）**：确认后端 `show_layer` 类工具能查到该图层并构造 `frontend_action`（含 layer_name/workspace/bbox/wms_url）。
4. **属性点查**：点击地图上的图层，看是否弹窗显示属性。WMS 图层依赖 `_bindFeatureInfoIfNeeded` + `/api/v1/geoserver/feature-info`；Vue 版 WFS `VectorLayer` 使用 OpenLayers `forEachFeatureAtPixel` 在客户端命中 feature 后复用同一个 popup。
5. **CRS 注意**：WMS 用 `EPSG:4326`；本地影像若 bbox 值 > 180 则按 `EPSG:3857` 投影米处理（`showImageInPanel` 内自动判定）。无 CRS 的影像会被**拒绝渲染**（系统只接受带坐标影像）。
6. **CORS**：GeoServer 默认不开 CORS，前端 `TileWMS` **不设 `crossOrigin`** 用普通 `<img>` 加载瓦片绕过（代价是不能跨域读像素，但出图够用）。

---

## 五、附：关键文件/函数索引

| 关注点 | 位置 |
|---|---|
| 地图单例初始化 | `map-core.js::initWmsMap()`（原 `wms-render.js:583`） |
| 默认中心点（成都） | `map-core.js::initWmsMap()` 的 `ol.View` |
| 图层注册表 | `map-core.js::_layerRegistry` + `_registerLayer/_unregisterLayer` |
| WMS 加载收口 | `layer-loader.js::showWmsInPanel()`（原 `wms-render.js:1008`） |
| 影像加载 | `layer-loader.js::showImageInPanel()`（原 `wms-render.js:716`） |
| 矢量加载 | `layer-loader.js::showVectorFile()`（原 `wms-render.js:885`） |
| 视图 fit 语义 | `map-core.js::_fitMapToExtent()`（原 `wms-render.js:517`） |
| GetFeatureInfo | `layer-loader.js::_handleWmsFeatureInfo()`（原 `wms-render.js:1135`） |
| 图层面板 | `layer-panel.js`（保留） |
| 工作空间树 | `layer-panel.js::refreshLayerPanel()` → GET `/api/v1/geoserver/services` |
| 后端 REST 聚合 | `backend/main.py:548` 起（`/api/v1/geoserver/*`） |
| GeoServer 客户端 | `backend/data/geoserver_client.py` |
| 启动入口 | `core/init.js:5` → `initWmsMap()` |

### 5. Vue 版当前差异（2026-07-07）

Vue 前端的地图能力集中在 `frontend-vue\src\composables\useMap.js`，与原生版的主要差异如下：

| 能力 | Vue 当前实现 | 说明 |
|---|---|---|
| WMS 图层 | `TileWMS` + `getFeatureInfoUrl` | 栅格和 GeoServer WMS 仍走服务端 GetFeatureInfo |
| WFS 矢量 | `VectorSource` + `GeoJSON` + `VectorLayer` | 图层面板和 frontend_action 加载矢量时走客户端渲染 |
| WFS 属性点查 | `forEachFeatureAtPixel` + `hitTolerance: 6` | 直接读取客户端 feature 属性，移除 `geometry` 后写入 popup |
| Popup 渲染 | `renderFeaturePopup` | WMS 和 WFS 共用同一套属性表 DOM |

修复边界：

- 点击建筑矢量不再依赖 `source.getFeatureInfoUrl`，因为 WFS `VectorSource` 没有该方法。
- 客户端矢量命中优先于 WMS 查询，避免同一点同时存在 WFS/WMS 时被底层 WMS 抢先处理。
- 未命中客户端矢量时保留原 WMS GetFeatureInfo 回退路径。
