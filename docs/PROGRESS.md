# 项目进度文档

> 最后更新: 2026-07-09 · 架构详见 [architecture.md](./architecture.md)

---

## 一、项目定位

基于 WebSocket 的 Agent 系统。LLM（Qwen via DashScope）驱动 ReAct 推理 + 工具编排，用自然语言操作 **GeoServer 图层服务 + PostgreSQL 数据库 + SamSeg 遥感分析 + 沙盒代码执行**。

三层架构（agent → model → data），前端支持聊天 UI + **OpenLayers 地图容器**影像/矢量预览 + 项目会话管理。品牌名：**国土智察 自然资源遥感智能监测系统**。

---

## 二、演进历史（阶段 1-22 摘要）

| 阶段 | 主题 | 核心产出 |
|---|---|---|
| 1 | 脱离 qh 栅格影像数据库 | 清除湖泊/lake_catalog 绑定，变通用底座 |
| 2 | SamSeg 遥感分析接入 | segment_image + detect_change，SegEarth-OV3 推理 |
| 3 | 连通域统计增强 | 每类区域数、像素面积、占比 |
| 4 | 修复结果不显示 bug | 解耦 frontend_action 推送与等待；新增 `/api/v1/upload` |
| 5 | 原图即时显示 + 颜色图注 | 上传后立即显示；结果图加颜色→类别图注 |
| 6 | 完整流程梳理 | 端到端时序图 + 关键设计决策 |
| 7 | 变化检测误判优化 | iou/min_area 收紧；修复 prob 死代码 + 两期统一阈值 |
| 8 | 沙盒 + 统一文件存储 | Docker 沙盒子系统 + understand_image 视觉理解 |
| 9 | AI 任务持久化 | ai_task/task_log 表 + 重工具白名单埋点 |
| 10 | 渔网分割数据集 | XD2023+XD2025 切 1209 瓦片，7:1:2 划分 |
| 11 | 前端视觉统一 | 品牌名 + Logo + 蓝绿白配色 |
| 12 | 14 条核心功能对接 | 6 Phase 实施，新增 preprocess/report 工具类 |
| 13 | 沙盒启用 + 自纠学习 | self_correction.py 跨会话经验沉淀 |
| 14 | 架构强化 | 移除 Leaflet；连通域工具；GeoTIFF 掩码；卡片坐标 |
| 15-17 | 核心架构升级（v2.4-v2.5） | 目录顺序统一 `{proj}/{conv}`；统一 Tool Result schema；多级上下文压缩；消息分支 DAG；记忆策略调整 |
| 18-19 | 矢量可视化 + 表格富化 | `visualize_vector`（GeoJSON→PNG+shp）；富表格（排序/复制/分页） |
| 20 | 沙盒下载 + 错误治理 | 沙盒文件下载到宿主机；前端错误块一键复制 |
| **21** | **OpenLayers 地图容器（★重大回退决策）** | **撤销阶段 14"移除地图引擎"，重新引入 OpenLayers 9；卡片墙→地图图层容器；砍掉 overlay/edge 自动产物（改图层透明度叠加）；系统提示词加"表格优先"规则** |
| **22** | **矢量渲染增强 + 端到端测试** | **新增 shp-to-geojson 端点 + `showVectorFile`（geojson/shp 原生渲染）；后端脚本 `map_container_backend.py`（28 项验证）；Playwright `map-container.spec.ts`（4 项全过）** |

---

## 三、近期重大改造

| 维度 | 阶段 15 · 目录顺序统一 + 文件名精简（v2.4） | 阶段 16 · Agent 核心架构升级（v2.5） | 阶段 17 · 记忆策略调整 |
|---|---|---|---|
| **时间** | 2026-06 | 2026-06 | 2026-06 |
| **背景 / 问题** | 6 个工具文件目录顺序不一致（samseg/geoserver/report 是 `{conv}/{proj}`，sandbox 是 `{proj}/{conv}`）；文件名套娃过长 | 借鉴 APIX v2.1 升级三项核心能力（详见 architecture-tools.md §3.4 / architecture-agent.md §4/§4.6） | `extract_and_save_long_term_memory` 每轮 LLM 硬挖"用户偏好/事实"，21 条记忆 18 条（86%）低价值空话，污染 system prompt ~700 tokens |
| **核心产出 ①** | 新建 `backend/model/tools/_paths.py` 公共 helper，统一目录为 `{proj}/{conv}`、兜底常量 `_default`/`_anonymous` | **统一 Tool Result Schema（Phase 3）**：新建 `_result.py`（ToolResult + 5 helper：`build_success`/`build_error`/`build_frontend_action`/`build_download_action`/`build_render_image_action`）；保证 `instruction.type == action`；迁移 2 个异类工具（`import_file_to_sandbox` 的 `status`→`type`；`sandbox_tools` 的 stdout/stderr→data）；软约束旧工具不强制，`chat_service` 兼容新旧路径命名 | 新增开关 `enable_preference_extraction`（`config.py`，**默认 False**），关闭后 `extract_and_save_long_term_memory` 返回 0、`chat_service` 两处调用点成 no-op |
| **核心产出 ②** | 文件名精简为 `{会话id前8位}_{语义}_{4位随机}.{ext}`（去 stem 叠加 + 时间戳） | **多级上下文压缩（Phase 1）**：新增 `fold_tool_messages`（Level 0.5 可逆），trim 前折叠旧工具结果为 `[已完成·工具名·摘要]`；`maybe_summarize` 改两级：Level 1 fold（可逆）→ Level 2 LLM 摘要（threshold×1.5 触发，不可逆）；DB 加 `compress_level` 列；配置 `summary_level2_multiplier`/`tool_message_keep_tail` | `build_user_profile_text` 默认只注入 `lesson`/`workflow`/`envfact`，屏蔽 preference/entity/fact；新增 `envfact` 类别（环境事实，即"遇到过的问题+已知答案"） |
| **核心产出 ③** | 迁移脚本 `scripts/migrate_agent_files.py`（25 目录翻转 + 1 条 DB 更新） | **消息分支 Git-like DAG（Phase 2）**：message 表加 `node_id`/`parent_id`/`is_active`；conversation 表加 `active_leaf_node`；`fork_from_node`/`soft_delete_branch_after`（软删除，旧分支保留）；3 个新端点 `POST /fork`、`POST /regenerate`、`GET /branches`；前端 human 消息加 `↻ 重新生成` 按钮（hover 显示），48 条历史消息自动迁移为单链 DAG | 数据清理（`scripts/cleanup_user_memory.py`，带 DRY-RUN + 全量备份）：删 16 条废话、迁移 2 条字体 fact→envfact、保留 3 条 lesson；新增诊断脚本 `scripts/diag_user_memory.py` |
| **效果 / 验证** | sam3 环境 segment 端到端验证 ALL PASS | 8 个 Python 文件语法 + backend.main 加载 + fold/fork/迁移行为全部 ALL PASS | 注入 system prompt 的长期记忆从 ~2813 chars (~1406 tokens) 降至 ~2021 chars (~1010 tokens)，**100% 真信号**（错误案例+解决方案）；长期记忆唯一主动学习路径收敛为自纠捕捉器（规则、零 LLM 成本）；3 文件语法 OK + 开关默认值确认 + `build_user_profile_text` 实际输出核对 + `extract_and_save_long_term_memory` 返回 0 确认跳过 |

---

## 四、当前状态

### 4.1 文件存储（v2.4）

```
agent-files/
├── samseg/send/{conv}/                          ← 上传原图（单层）
├── samseg/generate/{proj}/{conv}/               ← 分割/变化检测结果
├── samseg/vector/{proj}/{conv}/                 ← 矢量 GeoJSON
├── geoserver/generate/{proj}/{conv}/            ← 图层下载
├── report/{proj}/{conv}/                        ← 报表/导出
├── analysis/{proj}/{conv}/                      ← 掩膜分析
├── preprocess/{proj}/{conv}/                    ← 预处理
└── sandbox/workspace/{proj}/{conv}/             ← 沙盒工作区
```

兜底：无项目→`_default`、无会话→`_anonymous`。文件名：`{conv8}_{语义}_{rand4}.{ext}`。

### 4.2 运行环境

- **sam3**（`D:\anaconda3\envs\sam3`）：torch + CUDA + pycocotools + rasterio/geopandas + reportlab，SamSeg/报表/预处理全可用
- **base**：无 torch，SamSeg 优雅降级
- **Docker**（可选）：`agent-sandbox:latest` 镜像，缺失则 4 个 sandbox 工具降级

### 4.3 启动

```powershell
conda activate sam3
python -m backend.main
# http://localhost:8020
```

### 4.4 工具箱

**47 个工具，10 大类**：samseg(4) / sandbox(4) / geoserver(8) / database(10) / layer_control(5) / preprocess(3) / report(4) / analysis(3) / memory(4) / skill(2)。重工具白名单：`{samseg, rule, report, preprocess, analysis}`。

**工作流（Skills）**：3 个已注册方案（`backend/model/skills/`）—— WF1 一键分割+可视化+报告、WF2 一键变化检测+可视化+报告、WF4 上传影像到 GeoServer+显示。agent 通过 `lookup_skill` 按需检索执行。

### 4.5 验证通过项

后端语法/工具注册/三层反伪装/samseg 降级与正常/端到端分割+变化检测+连通域/卡片坐标/沙盒 6 项/自纠学习 4 项/**v2.4 目录统一+文件名精简+迁移**/**v2.5 schema 统一+多级压缩+消息分支**/**v2.5.1 记忆策略收敛** — 全部 ✅。

---

## 五、待完成事项

**工程稳定性**：SAM3 权重预加载；大图超 120s WS 心跳；沙盒容器空闲回收。

**功能扩展（非阻塞）**：规则匹配模块；用户权限管理；变化检测方案 B（prompt-based CD）。

**UI 体验**：AI 对话面板思考/工具块自折叠（阶段 18，见下）。

---

## 六、阶段 18：AI 对话面板 UI 升级 — 思考/工具块自折叠

### 6.1 改动内容（做什么）

将 AI 对话面板中的 **思考面板（thinking-block）** 和 **调用工具面板（tool-block）**，从"全程展开"改为"**完成当前操作后自折叠**"，最终态为可点击展开/收起的折叠条。

| 块类型 | 流式进行中（当前） | 完成当前操作后（阶段 18 目标） | 用户再次操作 |
|---|---|---|---|
| thinking-block | 全展开 + 闪烁 cursor | 自动折叠为单行摘要（如 `💡 thinking（3 段，142 tokens）`） | 点击展开看完整思考；再点折叠 |
| tool-block | 全展开 `tool_name(args)` | 自动折叠为单行摘要（如 `🔧 segment_image ✓`） | 点击展开看完整 args/结果 |

**折叠/展开关系（统一规则）**：

- 每个 `iteration-group`（一轮 thinking + 配对工具）在**该轮结束时自折叠**为一行胶囊。
- 同一条 AI 消息内多个轮次 → 多个折叠胶囊纵向堆叠。
- 默认态：**最新一轮保持展开**（让用户看见正在发生什么），**历史轮次全部折叠**。
- 折叠态点击 → 展开看详情；展开态点击 → 折叠。最新轮次在下一轮开始时也自动收起。

**保留**：展开/折叠是纯前端状态，不写后端、不动 DB、不改 WS 协议；trace 弹窗（对话执行日志）继续走原聚合逻辑。

### 6.2 涉及文件

| 文件 | 改动点 |
|---|---|
| `frontend/chat/msg-ui.js` | `appendThinking` / `appendToolCall` / `appendThinkingTo` / `appendToolCallTo` / `finishMessageForConv` / `ensureIterationGroup`：增加折叠态 DOM 结构、点击切换、轮次切换时折叠旧组 |
| `frontend/styles.css` | 新增 `.iteration-group.collapsed`、`.iter-summary`、`.iter-summary-chevron` 等样式（复用现有 `--accent`/`--bg-inset`/`--border` 令牌） |
| `frontend/chat/samseg.js` | `_markBubbleStopped`：停止时把当前 thinking/tool 组折叠（与 onDone 行为对齐） |
| `frontend/chat/conv-core.js` | `rebuildConvUI`：历史消息重建时，**除最后一条 AI 消息的最后一轮外**，所有轮次默认折叠 |
| 后端 / DB / WS 协议 | **不改** |

### 6.3 实施方案（怎么做）

**步骤 1 · DOM 结构升级（msg-ui.js）**

每个 `iteration-group` 增加 summary 头部 + 详情容器：

```
.iteration-group                 ← 整组, 切换 .collapsed 类
├─ .iter-summary                 ← 折叠时唯一可见行 (点击切换)
│   ├─ .iter-summary-icon        ← ▼/▶ (旋转动画, 复用 .conv-group-header::before 思路)
│   ├─ .iter-summary-label       ← "thinking 汇总" / 工具名
│   └─ .iter-summary-meta        ← 段数/tokens/工具结果状态
└─ .iter-detail                  ← 原 thinking-block + tool-block 的父容器
    ├─ .thinking-block (原样)
    └─ .tool-block   (原样)
```

改造点：
- `ensureIterationGroup(st)`：建组时同步建 `.iter-summary` + `.iter-detail`，绑定 `onclick` → toggle `.collapsed`。
- `appendThinking` / `appendToolCall`：把块 append 到 `.iter-detail`（而非 group 本身），并**实时更新** `.iter-summary-meta`（thinking 段数/tokens、工具名列表）。
- `appendThinkingTo` / `appendToolCallTo`（重建路径）：同上，重建时一并填充 summary meta。

**步骤 2 · 折叠/展开逻辑（msg-ui.js）**

新增 helper：

```
function _collapseIterationGroup(group)   // group.classList.add('collapsed')
function _expandIterationGroup(group)     // group.classList.remove('collapsed')
function _toggleIterationGroup(group)     // 点击 summary 调用
function _syncIterationSummary(group)     // 根据 detail 内子元素生成 summary 文案
```

`_syncIterationSummary` 文案规则：
- 仅 thinking：`思考中（N 段，M tokens）` → 折叠后 `思考过程（N 段，M tokens）`
- thinking + 工具：`思考（N 段）→ 工具：toolA, toolB`
- 折叠态统一去掉 cursor，加 `✓` 或工具图标前缀

**步骤 3 · "完成当前操作后自折叠"触发时机（msg-ui.js + conv-core.js）**

触发折叠的 3 个时机，统一调用 `_collapseIterationGroup`：

| 时机 | 钩子位置 | 行为 |
|---|---|---|
| **新一轮 thinking 开始**（上一轮已完成） | `appendThinking` 顶部的 `if (st.currentGroupHasTool || !st.currentIterationGroup)` 分支 → 先 `_collapseIterationGroup(旧组)` 再 `ensureIterationGroup` | 上一轮立即折叠，新轮展开 |
| **消息结束**（onDone / onError / onChatStopped） | `finishMessageForConv` / `_markBubbleStopped` 末尾 | 当前最后一轮也折叠（保留点击展开） |
| **历史重建** | `rebuildConvUI` 末尾遍历所有 `.iteration-group`：最后一个保持展开（让用户看清最近结论），其余全折叠 | 与"最新轮展开、历史轮折叠"规则一致 |

**步骤 4 · 停止态对齐（samseg.js）**

`_markBubbleStopped` 在追加 `对话已停止` marker 前，先把 `st.currentIterationGroup` 折叠，避免半截展开的思考块残留。

**步骤 5 · 样式（styles.css）**

新增（复用现有令牌，不引入新色）：

```css
.iteration-group .iter-summary{
  display:flex; align-items:center; gap:8px;
  padding:6px 10px; cursor:pointer; user-select:none;
  background:var(--bg-inset); border:1px solid var(--border); border-radius:var(--r-sm);
  font-size:var(--fs-sm); color:var(--text-secondary); font-family:var(--mono);
}
.iteration-group .iter-summary:hover{ background:var(--bg-hover); color:var(--accent); }
.iteration-group .iter-summary-icon{ transition:transform .12s; color:var(--text-muted); }
.iteration-group.collapsed .iter-summary-icon{ transform:rotate(-90deg); }
.iteration-group .iter-detail{ margin-top:6px; }
.iteration-group.collapsed .iter-detail{ display:none; }
/* 折叠时整组收紧, 减少多轮堆叠的视觉占用 */
.iteration-group.collapsed{ margin-top:6px; }
```

**步骤 6 · 验证**

- 单轮（只 thinking 无工具）：thinking 流式 → onDone 折叠为 `思考过程（N 段，M tokens）`，点击展开/收起正常。
- 多轮（thinking→tool→thinking→tool）：第一轮在新轮开始时自动折叠；消息结束最后一轮折叠；历史多轮全部胶囊堆叠。
- 历史重建（切换会话/刷新）：旧消息全部折叠，仅最新一轮展开；点击可逐个展开。
- 停止态（用户点停止）：当前组折叠 + 红色 `对话已停止` marker。
- 回归：trace 弹窗、render_image 卡片墙、`↻ 重新生成` 按钮不受影响。

### 6.4 风险与边界

| 风险 | 处置 |
|---|---|
| 流式中频繁更新 summary 造成抖动 | summary 文案仅在追加新段/工具时更新（已有 throttle：thinking 走 300ms `flushThinkingLog`） |
| 折叠后用户找不到结果 | summary 始终可见且点击即可展开；最新一轮默认展开 |
| 重建路径与流式路径 DOM 不一致 | `appendXxx` 与 `appendXxxTo` 共用 `_syncIterationSummary`，逻辑单一来源 |
| 影响 v2.5 消息分支（fork/regenerate） | 仅在 `iteration-group` 这一层加折叠，不碰 `node_id`/`parent_id` 数据属性，分支按钮不受影响 |

### 6.5 预期收益

- 单条 AI 消息高度从"全展开多屏"压缩到"几个胶囊 + 最后结论"，长 ReAct 多轮对话可读性显著提升。
- 折叠/展开是纯前端，零后端改动、零协议改动，可独立上线、易回滚（删 `.collapsed` 类即恢复）。
- 与 trace 弹窗（执行日志全量）形成"日常轻量 / 调试全量"双层视图。

---

## 七、阶段 19：GeoJSON 矢量结果自动可视化进工作区（+ Shapefile + 封装工具）

### 7.1 背景与缺口

`segment_image` / `detect_change` 已自动产出彩色掩码 PNG + 叠加图（进工作区），且后端设了 `params.vector_url` 指向矢量 GeoJSON。但**前端 `renderImage` 只解构 `image_url/caption/legend/extra_images`，完全不读 `vector_url`** → 矢量结果从不进工作区。工作区是 `<img>` 卡片墙，渲染不了矢量。本阶段把 GeoJSON 栅格化为 PNG 进工作区，同时保存一份 `.shp`，并把方法封装成独立 Agent 工具。

### 7.2 改动内容（表格）

| 维度 | 说明 |
|---|---|
| **新增工具** | `visualize_vector`（`backend/model/tools/vector_tools.py`，注册类别 `analysis`，重工具白名单已含）：`(geojson_path, background_image_path="", alpha=0.5, export_shp=True)` → `frontend_action(render_image)` |
| **写法** | 严格遵循现有 Agent 工具模式：`@register_tool("analysis")` + `@tool` + `build_render_image_action` + `verify_by_path` 成功契约 |
| **核心纯函数** | `rasterize_vector_to_png()`：工具与 segment/detect 自动追加共用，单一来源；matplotlib Agg + CJK 字体；按 `class_name` 稳定哈希配色 + 右侧图例；有底图时按 rasterio bounds 对齐叠加 |
| **Shapefile** | `export_shp=True` 时导出 `.shp`（Fiona 0KB 拦截，ASCII stem），供 GIS；上游已产出 shp 时复用不重复导出 |
| **自动联动** | `samseg_tools._build_vector_visualization`：`segment_image`/`detect_change` 在 GeoJSON 真实生成时，自动生成矢量 PNG 并**追加为 `extra_images` 第二张卡片**，前端零改动 |
| **成功条件（契约）** | 返回 `type=frontend_action` / `action=render_image`；`verification.ok=True`；`data.artifact_path` 指向真实 PNG；`data.feature_count>0`；`export_shp=True` 时 `data.shp_path` 指向真实 `.shp` |
| **降级** | geopandas/matplotlib 缺失或 GeoJSON 为空 → 工具返回 `build_error`；segment/detect 自动追加失败只记 warning，**不阻断主结果**（分割/变化检测 PNG+统计照常返回） |
| **前端/DB/WS** | **不改** |

### 7.3 工作区最终顺序

```
segment_image  → 掩码叠加图 → 边缘叠加图 → GeoJSON 矢量图斑可视化
detect_change  → 变化结果图 → 叠加图 → GeoJSON 变化图斑可视化
```

### 7.4 涉及文件

| 文件 | 改动 |
|---|---|
| `backend/model/tools/vector_tools.py` | **新增**：`visualize_vector` 工具 + `rasterize_vector_to_png` 纯函数 |
| `backend/model/tools/samseg_tools.py` | `_build_vector_visualization` helper；`segment_image`/`detect_change` 把矢量 PNG 追加到 `extra_images`，shp/vis 路径并入 `data` |
| `backend/model/tools/__init__.py` | 自动导入 `vector_tools`（缺依赖 try/except 跳过） |
| `backend/model/skills/wf1-...md` / `wf2-...md` | 产出清单补矢量可视化 + Shapefile；新增成功条件；新增 `visualize_vector` 单独用法 |
| `docs/architecture.md` | 工具数 46→47；analysis 2→3；§3.2 标签；§3.3 清单新增 `visualize_vector`；新增 §10.8.1 产物链小节 |
| `docs/PROGRESS.md` | 本节（阶段 19） |
| 前端 / DB / WS | **不改** |

### 7.5 验证项

- **工具注册**：`import backend.model.tools` 后 `"visualize_vector" in get_all_tools()` 为真，分类为 `analysis`。
- **工具单独**：任意 segment 产出的 geojson → 生成带类别配色+图例的 PNG 进工作区，`verify_by_path.ok=True`；`export_shp=True` 时 `.shp` 真实落地。
- **segment_image**：执行后工作区出现矢量可视化卡片；`data.vector_vis_url` / `data.vector_vis_path` / `data.vector_shp_path` 齐全。
- **detect_change**：同上（变化图斑可视化）。
- **降级**：无矢量 / matplotlib 缺失 → 不追加 extra_images，不报错，主流程返回不变。
- **回归**：trace 弹窗、边缘叠加、`↻ 重新生成` 不受影响。

---

## 八、阶段 20：教训记忆结构化（2026-06）

**问题**：自纠捕捉器产出的 lesson 记忆是**纯规则拼接的流水账**——把 `error_msg + LLM 原始 thinking + 修复动作` 缝成一段，混着 markdown 符号、原始 JSON、报错原文。注入 system prompt 后是一坨，AI 无法直接照做复用。规则能记录"发生了什么"，提炼不出"正确做法是什么"。

**改动**（3 文件 + 数据清理，详见 [architecture-agent.md](./architecture-agent.md) §4.1.3）：

| 文件 | 改动 |
|------|------|
| `self_correction.py` | 新增 `_DISTILL_LESSON_SYSTEM` 提示词 + `async _distill_correction_to_structured()`（复用 deepseek-v4-flash + `_parse_json_items`，把 7 字段素材提炼成五段结构化经验）；`save_lesson_to_memory` 改 async，value 改由 LLM 提炼生成；★ **无降级**：提炼失败（超时 20s / 缺字段 / 解析错误）跳过不入库；`scan_and_save_corrections` 改 async，删 `save_workflow_to_memory` 双写（标 deprecated） |
| `chat_service.py` | `_async_self_correction_scan` 调用处加 `await` |
| `memory_context.py` | `build_user_profile_text` 注入端改多行缩进（value 换行保留 + 后续行 4 空格），五段在 system prompt 成块展示 |

**记忆 value 格式**（固定五段，不可缺）：
```
现象：失败时观察到的表象
根因：为什么会失败（本质归纳，非复述报错）
正确方法：① 具体步骤（工具名+参数）② ...
验证信号：如何确认修复成功
严重度：high|medium|low
```

**效果**：注入 system prompt 的教训从"流水账一坨"变成"可照做的五段结构化经验"，AI 下轮能直接复用。库纯净——只存结构化格式，旧流水账已清理。

**验证**：toggle_layer 场景实测 LLM 准确还原五段结构 + 字段校验对残缺/空值/非法严重度全部判失败跳过 + 注入端多行缩进正常 + 3 文件语法 OK + 数据清理备份 `backup_user_memory_structured_*.json`。

---

## 九、阶段 21：OpenLayers 地图容器（2026-06-24，★重大回退决策）

> ★ 本阶段**撤销阶段 14"移除 Leaflet、前端统一为卡片墙"的决定**，重新引入地图引擎。这不是简单的回退，而是基于"卡片墙无法实现地理叠加"这一认知的架构纠偏。

### 9.1 背景与问题

卡片墙（flex 堆叠 + CSS transform 缩放）的根本缺陷：
- **无法地理叠加**：原图与分割掩膜只能上下堆叠，看不到叠加效果；为此后端被迫预生成"overlay 混色图（原图70%+掩膜30%）"和"edge 边缘描线 GeoTIFF"，代价是额外产物 + 信息损失 + 5 类产物冗余
- **无法矢量渲染**：GeoJSON 矢量图斑前端渲染不了，阶段 19 不得不用 `visualize_vector` 把矢量栅格化成 PNG（绕路）
- **缩放非地理语义**：CSS transform 的缩放是"看图软件"式，多张图各自缩放，不按坐标联动

### 9.2 改动总览（前端 5 文件 + 后端 3 文件 + 2 skill）

**前端（卡片墙 → OpenLayers 地图容器）**：

| 文件 | 改动 |
|------|------|
| `frontend/index.html` | 引入 OpenLayers 9.2.4 CDN（ol.css + ol.js）；`wmsViewport` 改为 OL 挂载点；删自制缩放控件 |
| `frontend/styles.css` | `.wms-viewport` → `.ol-map-host`；删卡片墙样式（`.wms-item`/`.wms-zoom-*`）；保留 popup/legend |
| `frontend/map/wms-render.js` | **核心重写**为 OL 引擎：`initWmsMap`/`showImageInPanel`（ImageStatic 图层）/`showWmsInPanel`（ImageWMS）/`_clearMapLayers`/GetFeatureInfo Overlay；对外 API 签名不变，3 个调用方零改动 |
| `frontend/chat/conv-core.js` | `_restoreWmsPanelFromConv` 用 `_clearMapLayers()` 替代 `innerHTML=''` |
| `frontend/core/init.js` | 新增 `initWmsMap()` 启动调用 |

**后端（砍 overlay/edge 自动产物）**：

| 文件 | 改动 |
|------|------|
| `backend/model/SamSeg/runner.py` | 删 `_build_overlay_image`/`_build_edge_overlay_geotiff`/`_save_geotiff_singleband` 三函数 + 2 处调用块；保留 mask_tif/vector/shp/edge_utils |
| `backend/model/tools/samseg_tools.py` | segment/detect_change 清 overlay/edge 字段；`main_url` 改用原图；`artifact_path` 改用 mask_tif；summary 去三行声明；params 透传 `mask_tif_url`/`has_crs` |
| `backend/model/skills/wf1*.md`/`wf2*.md` | 删"步骤2 自动边缘叠加"，改为"图层透明度自动实现" |

**系统提示词**：`backend/agent/prompt/system_prompt.py` 新增"结构化呈现（表格优先）"规则（≥3 项同类结果强制 Markdown 表格）。

### 9.3 核心设计

- **图层叠加替代 overlay 混色**：原图层（底图，透明度 1.0）+ 掩膜 GeoTIFF 半透明叠加层（透明度 0.5）= 等效于旧 overlay，但零额外产物
- **无 CRS 影像虚拟坐标铺画布**：extent=[0,0,W,H]，4326 兜底，统一进地图容器不搞双模式
- **`edge_utils.py` 保留**：只砍自动产物，`overlay_edge_on_image` 手动工具仍可用
- **GetFeatureInfo 复用现有端点**：点击坐标反算像素 i/j 调 `/api/v1/geoserver/feature-info`，不新增后端接口

### 9.4 验证

- 后端 3 文件语法 OK + 工具注册 45 个正常 + 无遗留 overlay/edge 引用
- 前端 6 个 JS 语法 OK + 无旧卡片墙 API 残留
- 字段对齐确认：后端 params（mask_tif_url/input_image_path/legend/vector_url/has_crs）↔ 前端 renderImage 解构

---

## 十、阶段 22：矢量渲染增强 + 端到端测试（2026-06-24）

### 10.1 背景

阶段 21 的地图容器能渲染影像/掩膜/WMS 图层，但缺矢量能力（GeoJSON/Shapefile 直接渲染）。本阶段补齐，并建立完整的端到端测试体系。

### 10.2 改动（后端端点 + 前端矢量 + 2 个测试脚本）

| 文件 | 改动 |
|------|------|
| `backend/main.py` | 新增 `GET /api/v1/vector/shp-to-geojson`：geopandas 读 shp 目录（自动找 .dbf/.shx/.prj）→ 返回 GeoJSON；非 4326 自动转投影；feature >5000 采样保护 |
| `frontend/map/wms-render.js` | 新增 `showVectorFile(urlOrPath)` 统一入口：.geojson → OL Vector 图层；.shp → 调后端端点转换再渲染；`_addVectorLayer` 增强（支持 GeoJSON 对象）；`renderImage` 增 vector_url 处理 |
| `frontend/chat/conv-core.js` | `_restoreWmsPanelFromConv` 增加 `type='vector'` 分支 |
| `tests/map_container_backend.py` ★ | **后端产物验证脚本**（sam3 跑真实推理）：segment/detect_change/visualize_vector/export_change_vector/overlay_edge/shp 回读 6 大测试，28 项验证全过 |
| `tests/e2e/map-container.spec.ts` ★ | **Playwright 测试**：上传影像→地图 canvas、GeoJSON 矢量层、Shapefile（走端点转换）、segment 结果完整渲染链路，4 项全过 |

### 10.3 联调修复的 3 个 OL bug（关键经验）

| Bug | 根因 | 修复 |
|-----|------|------|
| `ol.control.defaults is not a function` | OL 9 里 `defaults` 是对象不是函数 | 改用 `ol.control.defaults.defaults()` |
| 有 CRS 影像加载超时 | `ImageStatic` 的 `imageExtent` 误用源 CRS 坐标当 4326 transform | bbox 值 >180 判投影，直接用源 CRS extent + projection |
| 图层不触发加载（imgReqs:0） | `view.fit(extent)` 的 extent 未转成 view projection，视图跑到无效坐标，图层一直在视图外（OL 懒加载） | fit 前先 `transformExtent(extent, sourceProj, viewProj)` |

### 10.4 验证证据

- 后端：28 项断言通过（segment 119 区域 / detect_change 10 变化区域 / 矢量 20 图斑 / shp 回读成功）
- Playwright：4 项全过，截图 `output/playwright/artifacts/map-0[1-4]-*.png` + 审计 `output/playwright/audit/map-*.json`
- 视觉确认：地图容器正确渲染遥感影像，OL 缩放控件 + 比例尺正常

### 10.5 运行方式

```bash
# 后端产物验证（必须 sam3 环境）
D:/anaconda3/envs/sam3/python.exe -m tests.map_container_backend

# 前端 Playwright（需后端运行，见 playwright-usage.md）
npx playwright test map-container --project=msedge
```

### 10.6 涉及文件清单

| 文件 | 用途 |
|------|------|
| `backend/main.py` | shp-to-geojson 端点 |
| `frontend/map/wms-render.js` | showVectorFile + OL 引擎 |
| `frontend/chat/conv-core.js` | 矢量层恢复 |
| `tests/map_container_backend.py` | 后端产物验证 |
| `tests/e2e/map-container.spec.ts` | 前端渲染验证 |
| `tests/data/r000_c006_t1.tif`/`t2.tif` | 双时相测试影像 |
| `tests/data/成都市_县.geojson` | 矢量测试数据 |

---

## 十一、阶段 23：Vue 聊天轨迹与 WFS 属性点查修复（2026-07-07）

### 11.1 背景

Vue 前端在切换历史会话后，AI 思考过程、工具调用、工具结果和最终回复混在同一个消息气泡中，工具结果还可能作为独立消息重新出现，导致聊天流布局混乱。同时，图层面板加载的 WFS 建筑矢量是客户端 `VectorLayer`，原点击属性面板只支持 WMS `GetFeatureInfo`，点击建筑矢量不会弹出属性信息。

### 11.2 改动

| 文件 | 改动 |
|------|------|
| `frontend-vue/src/components/chat/MessageBubble.vue` | AI 消息拆分为思考过程、工具调用、回复内容三段；思考和工具调用默认折叠；工具结果完整显示摘要，不展示原始 JSON |
| `frontend-vue/src/stores/conversation.js` | 历史消息按 `human` / `ai` / `tool` 规范化；后端 `tool` role 结果合并到上一条 AI 的 `toolCalls[].result` |
| `frontend-vue/src/composables/useMap.js` | 地图点击先命中 WFS 客户端矢量 feature，未命中再回退 WMS `GetFeatureInfo` |
| `docs/archive/frontend-vue-refactor.md` | 记录聊天轨迹展示规则和历史消息归并策略（已归档） |
| `docs/archive/frontend-map-geoserver.md` | 记录 Vue 版 WFS 客户端矢量属性点查差异（已归档） |

### 11.3 设计约束

- 聊天面板不展示工具原始 JSON，原始结果归入任务日志详情。
- 工具调用和思考过程使用独立展开状态，避免展开状态互相影响。
- 工具结果完整显示摘要文本并保留换行，避免 UI 摘要被前端截断。
- WFS `VectorSource` 没有 `getFeatureInfoUrl`，必须通过 OpenLayers 客户端 feature 命中检测读取属性。
- WFS 客户端命中优先，WMS 点查作为回退路径保留。

### 11.4 验证

```powershell
cd H:\西藏遥感Agent\Agent\frontend-vue; npm run build
```

验证结果：

- Vite build 通过。
- 聊天区不再出现 `查看原始结果`。
- 工具结果仍在工具调用内部，不生成独立工具结果气泡。
- 浏览器检查工具结果摘要完整显示，长摘要无省略号截断。
- 地图模块运行无前端 console error，WFS 客户端矢量点击路径已补齐。

---

