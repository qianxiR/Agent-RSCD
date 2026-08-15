> 🗄️ **已归档 · 2026-07-09**：Vue3 改造方案已落地完成，不再作为活跃规划。
> - P0–P5 + §7.1–7.6 全部落地，`frontend-vue/` 为现役前端（原生 `frontend/` 已停用）。
> - §7.7 待办中「P6 后端挂载切换」不适用——`main.py` 无前端静态挂载，前后端本就分离部署。
> - 现行前端实现以 `frontend-vue/src` 代码为准，平台层架构见 [`architecture-platform.md`](../platform/architecture-platform.md)。
> - 本文档仅作改造历程留存。

# 前端 Vue3 改造方案（frontend-vue）

> 本文记录从原生 JS 前端（`frontend/`）迁移到 **Vue3 + Vite + Pinia + Element Plus** 的工程级改造方案。
>
> 关联文档：原生前端总纲 [architecture.md](../architecture.md)、`map/` 模块拆分 [frontend-map-geoserver.md](./frontend-map-geoserver.md)、Agent 内核 [design-overview.md](../agent/core/design-overview.md)。

---

## 一、改造背景与核心决策

原生前端是零构建的原生 JS + CDN 依赖，全局变量驱动、跨文件函数调用、DOM 直接操作。随着对话状态机、多对话路由、地图容器、工具结果渲染等逻辑复杂化，可维护性已到瓶颈。

### 三个关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| **迁移策略** | 并行双轨 | 新工程独立目录，与原生版并存，后端挂载点暂不变，零侵入切换 |
| **技术栈** | Vue3 + Vite + Pinia + Element Plus | 响应式状态收敛、组合式 API、组件化、生态成熟 |
| **实施顺序** | 先做 WS + 状态层 | 这是所有 UI 的依赖，先做扎实再叠 UI |

---

## 二、工程位置与双轨切换

新工程独立目录，与原生版并存：

```
agent-flow-study/
├── frontend/          # 原生版（保持不动，后端继续挂载）
└── frontend-vue/      # 新 Vue 工程开发中
```

**开发期**：Vite dev server 的 `proxy` 转发 `/api` 和 `/ws` 到后端（默认 `:8020`），前后端独立热更新。

**生产切换**：改造完成后，把后端 `StaticFiles` 挂载目标从 `frontend/` 改为 `frontend-vue/dist/`，一行代码切换，原生版保留作回滚备份。

---

## 三、目录结构

```
frontend-vue/
├── index.html
├── package.json
├── vite.config.js          # dev proxy → backend:8020
├── src/
│   ├── main.js             # createApp + ElementPlus + Pinia
│   ├── App.vue             # 根
│   ├── layouts/
│   │   └── MainLayout.vue  # 三栏 + 拖拽分隔（el-container）
│   ├── composables/        # ★ 逻辑层（优先实现）
│   │   ├── useWebSocket.js # WsChatClient → composable
│   │   └── useChat.js      # 发送/停止/新建对话 编排
│   ├── stores/             # ★ 状态层（优先实现）
│   │   ├── conversation.js # convStates → Pinia
│   │   ├── project.js
│   │   └── map.js
│   ├── services/
│   │   └── api.js          # REST（对话列表/图层/上传）
│   └── components/
│       ├── chat/           # ChatPanel, MessageBubble...
│       ├── map/            # MapPanel (OpenLayers), LayerTree
│       └── sidebar/        # ConvSidebar, WorkspaceTree
```

---

## 四、模块映射（原生 → Vue）

| 原生模块 | Vue 对应 | 说明 |
|---|---|---|
| `core/state.js` 的 `convStates` 全局变量 | `stores/conversation.js`（Pinia） | 多对话状态收敛到 store |
| `core/ws-chat.js` 的 `WsChatClient` 类 | `composables/useWebSocket.js` | 多对话回调路由机制原样保留 |
| `core/ws-send.js` 的 `initWsClient`/`sendMessage` | `useChat.js` + store action | 发送编排 |
| `chat/conv-core.js` 对话切换/历史 | store action + composable | |
| `chat/msg-ui.js` DOM 渲染 | `MessageBubble.vue`（响应式） | 数据驱动替代手动 DOM |
| `map/wms-render.js` OL 地图 | `MapPanel.vue` `onMounted` 挂载 OL | 地图引擎核心保留 |

---

## 五、第一步：WebSocket + 状态层（优先实现）

这是所有 UI 的依赖，先把它做扎实。核心设计如下。

### 5.1 状态层 `stores/conversation.js` —— 替代全局 `convStates`

```js
{
  // 每个对话的独立状态（替代 convStates[convId]）
  convStates: {
    [convId]: {
      isSending,         // 是否正在等待回复
      currentContent,    // 当前回复流式内容
      thinkingBuffer,    // 思考过程缓冲
      title,             // 对话标题
      messages           // 消息列表
    }
  },
  activeConvId,           // 当前激活对话
  // actions: getConvState, switchConv, appendMessage, setSending...
}
```

### 5.2 逻辑层 `composables/useWebSocket.js` —— 封装 `WsChatClient`

- **保留多对话回调路由**（按 `conversation_id` 分发），这是原生版的核心机制，不能丢
- 暴露接口：`connect` / `sendChat` / `sendStop` / `sendToolResult`
- 消息回调写入 Pinia store（替代原来的直接 DOM 操作）

### 5.3 设计收益

UI 组件（后续做的 `chat` / `map` / `sidebar`）只需：

```js
import { useConversationStore } from '@/stores/conversation'
import { useWebSocket } from '@/composables/useWebSocket'
```

不再有全局变量和跨文件函数调用。

### 5.4 验收方式

这一步产物是可独立验证的基础设施——写一个极简测试页面，确认：
1. WS 能连上后端
2. 能收消息
3. 状态正确写入 store

验证通过后再往上叠 UI。

---

## 六、实施顺序

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **P0** | Vite 工程 + proxy 配置 + Pinia/ElementPlus 装配 | 无 |
| **P1** | `stores/conversation.js` + `composables/useWebSocket.js` + 极简测试页 | P0 |
| **P2** | `composables/useChat.js` 发送/停止/新建编排 | P1 |
| **P3** | ChatPanel / MessageBubble / ConvSidebar | P1、P2 |
| **P4** | MapPanel（OpenLayers）+ LayerTree | P1 |
| **P5** | MainLayout 三栏整合 + 拖拽分隔 | P3、P4 |
| **P6** | 后端 StaticFiles 挂载切换，原生版下线 | P5 验收通过 |

---

## 七、P5 之后的工程进展（实测落地）

> 以下为 P5（三栏布局）完成后，逐步补齐的核心交互闭环。均经前后端联调验证，后端端点全部复用、未改动。

### 7.1 frontend_action 执行器（7 事件分离架构）

**问题**：P1 的 `useWebSocket.js` 把 `frontend_action` 仅当作 `tool_call` 记录，从不回传 `tool_result`。后果——4 个图层类请求（`wait_for_result=True`）会让后端 `wait_for_frontend_result` 阻塞 120s 超时；`render_image`/`download` 也无法真正执行。

**架构（事件分离）**：

```
composables/
├── useWebSocket.js          # frontend_action 分支改为调分发器（动态 import 打破循环依赖）
├── useFrontendAction.js     # 分发器：注册表 + 统一回传 tool_result + 错误兜底
└── frontend-actions/        # 7 个事件各一文件，零耦合
    ├── index.js             # 注册表 { event_type → handler }，加文件即生效
    ├── layerControl.js      # layer_control (show/hide/toggle)
    ├── layerGroupControl.js # layer_group_control (按 group_name 前缀批量)
    ├── layerDisplay.js      # layer_display
    ├── layerLocate.js       # layer_locate (叠加 + bbox 定位)
    ├── renderImage.js       # render_image (完整版: 底图+矢量面/线+图例)
    ├── download.js          # download (触发浏览器 <a> 下载)
    └── skipVisual.js        # render_table/chart/ui_panel_control (静默回传)
```

- **Handler 统一契约**：`async (data, ctx) => ({status, ...})`，互不依赖；`ctx.map` 注入 `useMap()` 能力
- **分发器职责单一**：只做「分发 + 回传 + 兜底」，不碰 DOM、不含业务逻辑
- **后端协议事实**：7 个 event_type，图层类 `wait_for_result=True`（必须回传否则阻塞），渲染/下载类 `=False`

### 7.2 地图能力补全（WFS / ImageStatic / 图例）

**`useMap.js` 新增**（296 行，≤300 约束）：

| 能力 | 函数 | 对应原生 |
|---|---|---|
| WFS 矢量叠加 | `addWfsLayer({name,workspace,bbox,forceFit})` | `showWfsInPanel` |
| 本地影像静态叠加 | `addStaticImageLayer({path})` | `showImageInPanel` |
| 图例浮层状态 | `getLegendState/setLegend/clearLegend` | `_renderLegendOverlay` |

- WFS 走后端代理 `/api/v1/geoserver/wms?service=WFS`，VectorLayer + GeoJSON，边界线样式（描边 `#2c6fbd` 2px）
- ImageStatic：`getImageMeta` 验证 CRS → 判投影（>180→3857）→ `/upload/?preview=1` 转码 → 先 `fit` 视图（懒加载必须）→ 监听 `imageloadend`
- 图例：响应式 `ref` 数据驱动 `LegendOverlay.vue`，`clearBusinessLayers` 联动清理（替代原生 `createElement` 反模式）

**`renderImage.js` 完整版**（4 步，对齐原生）：底图 await（决定 status）→ 变化面/边缘线 fire-and-forget（forceFit:false）→ 图例覆盖

### 7.3 影像上传→加载→分割 流程闭环

**问题**：`ChatPanel.vue` 上传按钮是 stub（只 console.log），整条"上传→加载→分割"链路断裂。

**流程（已打通）**：

```
点上传 → await /samseg/upload（等服务器落盘 + CRS 校验）
       → 拿 paths
       ├── 线A(fire-and-forget): ImageStatic 加载显示到地图（与分割互不阻塞）
       └── 线B(同步): 路径注入输入框 → 用户补"分割这张图"发送
```

- `api.js`：`uploadSamsegImages`（FormData 绕开 JSON header）、`getImageMeta`（验证工具）
- 分割结果渲染（segment_image 自动 shp→发GeoServer→render_image）后端已就绪，无需改

### 7.4 对话 fork / regenerate（分支）

**设计决策**：AI 消息气泡上挂"↻ 重新生成"按钮（ChatGPT 风格），支持编辑 prompt。

| 文件 | 改动 |
|---|---|
| `api.js` | `regenerateConversation`、`getConvBranches` |
| `useChat.js` | `regenerate` 编排 + `resolveForkNode`（定位 AI 的 parent human 作分叉点） |
| `MessageBubble.vue` | AI 消息 hover 显示按钮，emit regenerate |
| `ChatPanel.vue` | `onRegenerate` 弹编辑框（ElMessageBox.prompt 预填原 prompt）→ fork → 重发 |

**流程**：reload 历史拿完整 nodeId → 定位 parent human → `POST /regenerate`（fork + 回显 prompt）→ `loadHistory` 刷新 → WS 重发编辑后 prompt。

**关键约束**：后端 `done` 事件不回传 node_id，实时生成的 AI 消息需先 reload 历史补 nodeId。

### 7.5 任务进度条（最轻方案）

**设计决策**：不做独立任务面板，在对话流内展示重工具进度。

| 文件 | 改动 |
|---|---|
| `conversation.js` | convState 加 `activeTask` + `setActiveTask/clearActiveTask` |
| `useWebSocket.js` | 收到重工具 tool_call 启动 3s 轮询 `/tasks`；done/error 停止 |
| `TaskProgressBar.vue`（新） | 对话内进度条（状态色标 + tool_name + 进度 + 错误） |

- **重工具判断**：工具名正则 `/segment|detect_change|report|pyramid|cog|topology|component|shapefile|overlay_edge|visualize_vector/`（后端仅这些类工具创建 ai_task）
- **轮询自停止**：命中终态（done/failed/cancelled）自动停轮询

#### 7.5.1 任务日志监控弹窗（复刻原生 task-monitor.js）

**定位**：与 7.5 的对话内进度条并存——进度条管"当前进行中的实时反馈"，弹窗管"全局任务历史 + 详情日志查看"。

**核心事实（与原生前端一致）**：弹窗是**纯只读的 REST 快照查看器**，不轮询、不订阅 WS、**不回写对话状态**。对话状态（分割结果渲染等）由独立的 WS `frontend_action` 链路驱动，两条链路零耦合。

| 文件 | 改动 |
|---|---|
| `api.js` | `getTaskDetail(taskId)`（详情，含 input/output/error）、`getTaskLogs(taskId)`（执行日志，含 level/elapsed_ms） |
| `TaskMonitorDialog.vue`（新） | Teleport Modal，双栏（左任务列表 + 右详情/日志），手动刷新 |
| `ChatPanel.vue` | `.ai-header` 加 `.ai-actions` 按钮（📋 任务日志监控），挂载弹窗 |

**复刻要点**：
- 双栏布局：左 42% 任务卡片列表（状态徽标 + `#id 工具名` + 耗时/创建时间 + error 预览），右详情（属性表 + output 摘要 + 执行日志列表）
- 状态徽标 5 态配色对齐原生：pending 灰 / running 橙 / done 绿 / failed 红 / cancelled 灰
- 打开时默认按当前会话过滤（`activeConvId` 预填筛选框），支持改状态/会话筛选 + 手动刷新
- 日志 level 配色：error 浅红 / warning 浅橙 / info 默认
- 点击遮罩空白处或"关闭"按钮关闭

#### 7.5.2 聊天消息轨迹展示（思考 / 工具调用 / 正文）

**定位**：聊天流只承载用户可读的执行轨迹和最终回复，工具原始结果归入任务日志，不在消息气泡内展开原始 JSON。

| 文件 | 改动 |
|---|---|
| `MessageBubble.vue` | 将 AI 消息拆为思考过程、工具调用、回复内容三类 UI；思考和工具调用默认折叠 |
| `conversation.js` | 历史消息规范化；后端 `tool` role 合并到上一条 AI 的 `toolCalls[].result` |

**展示规则**：

- 思考过程和工具调用使用独立展开状态，避免打开工具调用时同步展开思考过程。
- 工具调用折叠态显示工具名和调用数量；展开态显示参数和工具结果摘要。
- 工具结果显示完整摘要文本，保留换行，不截断；原始 JSON 只在任务日志详情中查看。
- 工具结果不再生成独立消息气泡，避免切换会话重放历史时工具结果与正文错位。
- 工具调用配色与思考过程统一使用 `accent` 色系。

**后端消息兼容**：

- `human`、`ai`、`tool` 三类角色进入前端规范化流程。
- `system` 或未知角色不进入聊天 UI。
- `tool_call_id` 优先用于结果归并；缺失时按工具名和未挂载调用顺序回退。

### 7.6 联调验证（已完成）

**环境**：后端用 conda `sam3` 环境（FastAPI 0.135.3 + Starlette 0.52.1 兼容）；前端 Vite dev proxy。

> 注意：后端在 anaconda 全局环境起不来（FastAPI/Starlette 版本错配），必须用 `sam3` 环境：`conda run -n sam3 python -m uvicorn backend.main:app --host 127.0.0.1 --port 8020`

**curl 实测结果**：

| 验证项 | 结果 |
|---|---|
| `vite build` | ✅ 1928 modules 通过 |
| 后端启动（sam3） | ✅ :8020，DB 已连接 |
| `/conversations` `/projects` `/geoserver/services` | ✅ 返回真实数据 |
| `/tasks` | ✅ `{status:success,count:0,tasks:[]}` 结构匹配前端 |
| `/branches` | ✅ `{active_messages,hidden_messages,has_branches}` |
| 历史消息 `node_id` | ✅ 每条都带（fork 定位依据就绪） |
| **POST `/regenerate`** | ✅ 返回结构完全匹配前端契约；fork 后 hidden_messages 正确递增 |
| Vite proxy 转发 | ✅ localhost:5173/api → :8020 |
| WS 端点 | ✅ 路由存在（400 握手拒绝为 curl 伪握手所致，浏览器可用） |

### 7.7 待办

- **P6 后端挂载切换**（上线动作，一行代码）：`main.py` 的 `FRONTEND_DIR` 从 `frontend/` 改为 `frontend-vue/dist/`
- **工作区目录浏览**（`/api/v1/workspace/list`，原生有 Vue 缺）
- **分支切换 UI**（左右箭头恢复旧分支，原生也没有，后端 `/branches` 已就绪）
