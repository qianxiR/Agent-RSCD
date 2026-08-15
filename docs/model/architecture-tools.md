# 工具系统（注册 / 分类 / 47 工具 / 统一 Schema）

> 本文是 [architecture.md](../architecture.md) 的子文档。系统总览见总纲。

---

## 3. 工具系统

> **一句话**：47 个工具 + 装饰器注册机制，LLM 通过 `bind_tools()` 调用，工具返回 `frontend_action` 指令或普通数据结果。

**关键文件**：
- [`backend/model/tools/tool_registry.py`](../../backend/model/tools/tool_registry.py) — 注册表
- [`backend/model/tools/data_tools.py`](../../backend/model/tools/data_tools.py) — GeoServer + 数据库工具
- [`backend/model/tools/layer_tools.py`](../../backend/model/tools/layer_tools.py) — 图层控制工具
- [`backend/model/tools/analysis_tools.py`](../../backend/model/tools/analysis_tools.py) — GeoServer 下载工具
- [`backend/model/tools/samseg_tools.py`](../../backend/model/tools/samseg_tools.py) — 🛰️ SamSeg 遥感分析工具（语义分割/变化检测）
- [`backend/model/tools/vector_tools.py`](../../backend/model/tools/vector_tools.py) — GeoJSON 矢量可视化工具（矢量图斑 PNG 渲染 + Shapefile 导出）
- [`backend/model/tools/skill_tools.py`](../../backend/model/tools/skill_tools.py) — 技能检索工具
- [`backend/agent/tools/memory_tools.py`](../../backend/agent/tools/memory_tools.py) — 记忆工具（agent 层）

### 3.1 注册机制

**装饰器顺序**（关键）：`@register_tool("category")` 在上，`@tool` 在下。

```python
@register_tool("layer_control")   # ← 先执行（外层），把 @tool 包装后的 StructuredTool 注册
@tool                             # ← 后执行（内层），把函数包装为 LangChain StructuredTool
def toggle_layer_visibility(layer_name, action, workspace=""):
    ...
```

**注册表结构**：`_tool_registry[category][tool_name] = StructuredTool`，另有反向映射 `_tool_category_map[tool_name] = category` 供 prompt 分组用。

**查询接口**：

```python
get_all_tools() -> Dict[str, Callable]       # 扁平 dict
get_tools_for_llm() -> list                  # 供 bind_tools 用的工具列表
get_tool_by_name(name) -> Optional[Callable] # 按名查
get_tool_category_map() -> Dict[str, str]    # 名 → 分类（供 prompt 分组）
```

**自动注册**：[`backend/model/tools/__init__.py`](../../backend/model/tools/__init__.py) 通配导入 5 个工具模块（`samseg_tools` 用 try/except 包裹，torch 缺失时跳过），`main.py` 只需 `import backend.model.tools` 即触发全部注册。记忆工具在 agent 层单独导入（避免 model→agent 反向依赖）。

### 3.2 工具分类与标签

System Prompt 中工具目录按以下分类展示（[`system_prompt.py::_TOOL_CATEGORY_LABELS`](../../backend/agent/prompt/system_prompt.py)）：

| category | 中文标签 | 说明 |
|----------|---------|------|
| `samseg` | 遥感解译分析 | SamSeg 语义分割 / 多时相变化检测（指定地物类别，含业务类型 VLM 判读） |
| `remote_sensing` | 遥感影像获取 | 从 GeoServer 下载栅格影像为 GeoTIFF |
| `geoserver` | 图层服务操作 | 栅格/矢量图层多格式上传下载 + 服务查询 |
| `layer_control` | 地图图层控制 | 前端地图图层的显示/隐藏/定位（通过 frontend_action） |
| `preprocess` | 数据预处理 | 影像金字塔构建 / COG 转换 / 矢量拓扑检查（阶段 12 新增） |
| `report` | 报表生成 | 变化统计图表 / PDF Word 监测报告 / 矢量与表格导出（阶段 12 新增） |
| `analysis` | 掩膜/矢量分析 | 掩膜连通域标记 + 几何特征提取 + 可选 GeoJSON 导出（阶段 14 新增）；边缘算子叠加可视化（阶段 16 新增）；GeoJSON 图斑可视化 PNG + Shapefile 导出（阶段 19 新增） |
| `database` | 数据库查询 | PostgreSQL 表的查询、统计、增删改 + 影像元数据时空检索 |
| `sandbox` | 沙盒代码与文件 | 沙盒内执行 Python/Shell 代码；导入文件；沙盒图片渲染；下载沙盒文件到宿主机目录 |
| `skill` | 长任务技能 | 检索标准化长任务编排方案 |
| `memory` | 记忆管理 | 查看/清空长期记忆与会话摘要 |

### 3.3 工具完整清单（47 个，10 大分类）

> ★ 工具规模演进：阶段 8（28 个）→ 阶段 12（+11：database 元数据检索 / preprocess / report）→ 阶段 14（+1：analysis 类 connect_components）→ 阶段 16（+2：delete_geoserver_layer 补齐图层删除 + overlay_edge_on_image 边缘算子叠加）→ 阶段 19（+1：visualize_vector GeoJSON 可视化进工作区）→ 当前 47 个 10 大类。

#### 遥感解译主线（samseg 4 + remote_sensing 1）

| # | 工具名 | 分类 | 签名 | 返回类型 |
|---|--------|------|------|---------|
| 1 | `segment_image` | samseg | `(image_path, classes="") -> Dict` | frontend_action(`render_image`) |
| 2 | `detect_change` | samseg | `(t1_path, t2_path, classes="") -> Dict` | frontend_action(`render_image`) + VLM 业务归类 |
| 3 | `understand_image` | samseg | `(image_path, question="") -> Dict` | direct（VLM 视觉理解文本） |
| 4 | `prepare_cd_dataset_by_fishnet` | samseg | `(t1_path, t2_path, output_dir, tile_size=512) -> Dict` | direct（CD 训练集渔网切片） |
| 5 | `download_geoserver_layer` | geoserver | `(layer_name) -> Dict` | frontend_action(`download`) |

#### GeoServer 图层服务（geoserver 8）

| # | 工具名 | 签名 | 返回类型 |
|---|--------|------|---------|
| 6 | `list_geoserver_services` | `() -> Dict` | frontend_action(`render_table`) |
| 7 | `download_raster_layer` | `(layer_name, workspace="") -> Dict` | frontend_action(`download`) |
| 8 | `upload_raster_layer` | `(file_path, layer_name="", workspace="") -> Dict` | direct（自动登记 image_metadata） |
| 9 | `upload_shapefile_layer` | `(file_path, layer_name="", workspace="") -> Dict` | direct（自动登记 vector_layer） |
| 10 | `download_shapefile_layer` | `(layer_name, workspace="") -> Dict` | frontend_action(`download`) |
| 11 | `download_geoserver_layer` | `(layer_name, file_type="auto", workspace="") -> Dict` | frontend_action(`download`)（自动检测格式） |
| 12 | `publish_geojson_layer` | `(geojson_path, layer_name="", workspace="", show_immediately=True) -> Dict` | direct（可选附带 frontend_action） |
| 13 | `delete_geoserver_layer` ★ | `(layer_name, workspace="", delete_store=True) -> Dict` | direct（★ v2.5 补齐删除，幂等） |

#### 数据库查询（database 10，含元数据检索）

| # | 工具名 | 签名 | 返回类型 |
|---|--------|------|---------|
| 11 | `list_database_tables` | `() -> Dict` | frontend_action(`render_table`) |
| 12 | `read_table_statistics` | `(table_name) -> Dict` | frontend_action(`render_table`) |
| 13 | `query_and_render_table` | `(table_name, limit=20) -> Dict` | frontend_action(`render_table`) |
| 14 | `insert_data` | `(table_name, data) -> Dict` | direct |
| 15 | `update_data` | `(table_name, data, condition="") -> Dict` | direct |
| 16 | `delete_data` | `(table_name, condition="") -> Dict` | direct |
| 17 | `search_images_by_time` | `(start_date, end_date, limit=50) -> Dict` | direct（时间检索 #4） |
| 18 | `search_images_by_region` | `(minx, miny, maxx, maxy, limit=50) -> Dict` | direct（空间检索 #4） |
| 19 | `list_image_catalog` | `(limit=100) -> Dict` | direct（影像目录 #4） |
| 20 | `list_vector_catalog` | `(kind="", limit=100) -> Dict` | direct（矢量目录 #2） |

#### 数据预处理（preprocess 3，阶段 12 新增）

| # | 工具名 | 签名 | 返回类型 |
|---|--------|------|---------|
| 21 | `build_pyramid` | `(image_path, overview_levels="2,4,8,16") -> Dict` | direct（#3） |
| 22 | `convert_to_cog` | `(image_path, output_path="") -> Dict` | direct（#3，沙盒优先宿主兜底） |
| 23 | `check_topology` | `(vector_path, fix=False) -> Dict` | direct（#3） |

#### 报表生成（report 4，阶段 12 新增）

| # | 工具名 | 签名 | 返回类型 |
|---|--------|------|---------|
| 24 | `generate_change_stats_chart` | `(change_geojson_path, chart_type="both") -> Dict` | frontend_action(`render_image`)（#15） |
| 25 | `generate_monitor_report` | `(change_geojson_path, output_format="pdf", ...) -> Dict` | frontend_action(`download`)（#16） |
| 26 | `export_change_vector` | `(change_geojson_path, output_format="shapefile") -> Dict` | frontend_action(`download`)（#17） |
| 27 | `export_stats_table` | `(change_geojson_path, output_format="excel") -> Dict` | frontend_action(`download`)（#17） |

#### 掩膜/矢量分析（analysis 3，阶段 14+16+19+21）

| # | 工具名 | 签名 | 返回类型 |
|---|--------|------|---------|
| 28 | `analyze_connected_components` | `(mask_path, min_area=64, export_geojson=False, source_image_path="") -> Dict` | frontend_action(`render_image`)（连通域标记 + 可选 GeoJSON 导出） |
| 29 | `overlay_edge_on_image` ★ | `(source_image_path, mask_path, method="distance", edge_color="255,0,0", edge_thickness=2, mask_alpha=0.0) -> Dict` | frontend_action(`render_image`)（★ 边缘算子叠加，3 种算子：距离变换/Canny/Sobel。**阶段 21 后仅手动调用**：segment/detect_change 不再自动生成 edge 产物，前端用掩膜图层透明度叠加实现等效效果） |
| 30 | `visualize_vector` ★ | `(geojson_path, background_image_path="", alpha=0.5, export_shp=True) -> Dict` | frontend_action(`render_image`)（★ GeoJSON 图斑 → PNG + Shapefile。**阶段 21 后**矢量原生渲染到地图容器 `showVectorFile`，本工具主要用于报告 PNG 插图 + Shapefile 导出） |

#### 地图图层控制（layer_control 5）

| # | 工具名 | 签名 | 返回类型 | `wait_for_result` |
|---|--------|------|---------|-------------------|
| 29 | `toggle_layer_visibility` | `(layer_name, action, workspace="") -> Dict` | frontend_action(`layer_control`) | **True** |
| 30 | `hide_layer` | `(layer_name) -> Dict` | frontend_action(`layer_control`) | **True** |
| 31 | `toggle_layer_group_visibility` | `(group_name, action) -> Dict` | frontend_action(`layer_group_control`) | **True** |
| 32 | `load_geoserver_layer` | `(layer_id, layer_name, workspace="", layer_type="raster", bbox=None) -> Dict` | frontend_action(`layer_display`) | **True** |
| 33 | `locate_to_layer_bounds` | `(layer_name, layer_id="", bbox=None) -> Dict` | frontend_action(`layer_locate`) | **True** |

#### 沙盒代码执行（sandbox 5）

| # | 工具名 | 签名 | 返回类型 |
|---|--------|------|---------|
| 34 | `run_python_code` | `(code) -> Dict` | direct（async，Docker 容器执行） |
| 35 | `run_shell_command` | `(command) -> Dict` | direct（async） |
| 36 | `render_sandbox_image` | `(filename, caption="") -> Dict` | frontend_action(`render_image`) |
| 37 | `import_file_to_sandbox` | `(file_path) -> Dict` | direct |
| 38 | `download_file_from_sandbox` | `(file_path, dest_dir) -> Dict` | direct（async，宿主机执行复制到任意目录） |

> ★ **v2.5 沙盒挂载扩展**：沙盒容器除 `/workspace`（RW 写区）外，按当前会话 `(proj短, conv短)` 动态 RW 挂载本会话产物到容器内 `/files/{source}`（默认 `samseg/generate` → `/files/samseg_generate`，可配置 `SANDBOX_RW_MOUNT_SOURCES` 加 vector/analysis 等）。不同会话容器天然隔离。`download_file_from_sandbox` 把沙盒可访问的文件（支持 `/workspace/`、`/files/{source}/`、`agent-files` 相对路径三种写法）复制到用户指定的**任意宿主机绝对路径**，由宿主机后端执行（沙盒 Linux 容器无法直接写 Windows 盘符）。路径映射以结构化字段 `data.path_map` 注入（替代旧版纯文本 PATH HINT），供 AI 精确解析。前端卡片下载按钮（⬇）经 `POST /api/v1/sandbox/download-to-host` 复用同一逻辑。

#### 长任务技能 + 记忆管理（skill 2 + memory 4）

| # | 工具名 | 分类 | 签名 | 返回类型 |
|---|--------|------|------|---------|
| 38 | `lookup_skill` | skill | `(query) -> Dict` | direct |
| 39 | `list_available_skills` | skill | `() -> Dict` | direct |
| — | `view_user_memory` | memory | `() -> Dict` | direct |
| — | `view_conversation_summary` | memory | `() -> Dict` | direct |
| — | `clear_user_memory` | memory | `(confirm=False) -> Dict` | direct |
| — | `clear_conversation_summary` | memory | `(confirm=False) -> Dict` | direct |

> 注：memory 工具在 `agent.tools`（不是 `model.tools`），共 4 个，未计入 39 个总数。

> **三种结果类型**：
> - **`frontend_action`**（约 18 个工具）：工具返回特殊结构，后端识别后转成 WS 指令发前端。图层控制类必须 `wait_for_result=True`；渲染/下载/遥感/报表类 `False`（发射即忘）。
> - **direct**：返回 `{type:"success"/"error", data:...}`，直接回灌 LLM。
> - **重工具白名单**：`HEAVY_TOOL_CATEGORIES = {samseg, rule, report, preprocess, analysis}`，调用会触发 `ai_task` 持久化（状态/进度/IO/耗时）。
>
> **★ 优雅降级**：samseg 工具在 torch/权重缺失时返回 error；sandbox 工具在 Docker/镜像缺失时返回 error；都不影响其他工具。

### 3.4 工具返回的标准结构（v2.5 统一 schema）

> ★ **阶段 16（v2.5）**：新建 `backend/model/tools/_result.py`，定义标准 ToolResult schema + 构造 helper（`build_success`/`build_error`/`build_frontend_action`/`build_download_action`/`build_render_image_action`）。新工具强制用 helper，旧工具向后兼容（返回的 dict 继续工作）。

**统一 type 取值**（全代码库只有 3 种）：

| type | 含义 | 出现工具 |
|---|---|---|
| `frontend_action` | 触发前端渲染/下载/图层控制 | 18 个工具 |
| `success` | 纯数据/文本结果 | 21 个工具 |
| `error` | 失败 | 所有工具的错误路径 |

**标准 frontend_action 结构**（v2.5 helper 保证 `instruction.type == action`，修复历史不一致）：

```python
{
    "type": "frontend_action",
    "action": "render_image",              # ★ v2.5: action 字段 (helper 保证 == instruction.type)
    "instruction": {
        "type": "render_image",            # ★ == action (helper 保证一致)
        "action": "render_image",
        "params": {
            "image_url": "/api/v1/upload/{conv}/xxx.tif",      # ★ 原图 (地图底图层, 阶段21改)
            "mask_tif_url": "/api/v1/download/.../xxx.tif",    # ★ 掩膜 GeoTIFF (半透明叠加层, 阶段21新增, 替代旧 overlay_url)
            "input_image_path": "/abs/path/x.tif",             # ★ 宿主机路径 (前端查 /api/v1/image/meta 定位)
            "vector_url": "/api/v1/download/.../xxx.geojson",  # ★ 矢量 GeoJSON (地图矢量层, 可选)
            "legend": [{"name": "建筑", "hex": "#ff0000"}, ...],
            "has_crs": True
        }
    },
    "wait_for_result": False,
    "description": "正在渲染分割结果",
    "summary": "...",                      # ← 喂给 LLM 的统计摘要
    "data": {"stats": {...}, "mask_tif_path": "/abs/x.tif", "shp_dir_path": "/abs/x_shp/"},  # ★ v2.5+阶段21: 掩膜/shp目录 (overlay_path/edge_* 已移除)
    "verification": {"ok": True, "size_bytes": 12345, ...}       # ★ 成功契约校验 (推荐)
}
```

**direct 结构**：

```python
{"type": "success", "summary": "...", "data": {...}}
# 或
{"type": "error", "msg": "原因"}
```

**v2.5 路径字段规范化**（`_result.extract_artifact_path` 兼容新旧 15 种命名）：
- 新规范：`data.mask_tif_path`（掩膜磁盘绝对）+ `params.mask_tif_url`（前端叠加层 URL）+ `data.shp_dir_path`（shp 目录）
- ★ **阶段 21 移除**：`overlay_path` / `overlay_url`（混色叠加图，改前端图层透明度）、`edge_tif_path` / `edge_mask_tif_path`（边缘自动产物，保留手动工具 `overlay_edge_on_image`）
- 旧命名保留兼容：`output_path` / `geojson_path` / `local_path` / `shp_path` / `output_url` / `download_url` / `vector_url` 等
- `chat_service` 的 verification 兜底 + `_summarize_task_output` 已扩展识别全部新旧键名

**chat_service 消费逻辑**（向后兼容）：
- `_summarize_task_output`：抽 `type`/`summary`/`stats`/`artifact_path`（兼容新旧命名）
- verification 兜底：识别 `artifact_path`/`output_path`/`geojson_path`/`local_path`/`shp_path`，自动补 `verify_by_path`

---
