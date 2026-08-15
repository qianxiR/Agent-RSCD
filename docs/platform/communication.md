# 前后端通信机制文档

> 核心模式：**WebSocket 事件驱动 + 回调注入 + `request_id` 闭环**。架构总览见 [architecture.md](../architecture.md)。

---

## 一、通信结构

WebSocket 全双工通道（`/api/v1/agent/ws/chat`），以 `request_id` 配对完成「智能体发指令→前端执行渲染→回传结果」闭环，支持同一连接多对话并行。

```
前端 (index.html + ws-chat.js)          后端 (main.py + chat_service.py)
  │                                          │
  │── chat_request ─────────────────────────▶│  用户消息
  │                                          │  (LLM 推理 + 工具编排)
  │◀── thinking (流式) ──────────────────────│  思考链
  │◀── tool_call (通知) ────────────────────│  工具调用
  │◀── frontend_action (★) ─────────────────│  前端执行指令
  │── tool_result (★, 按 request_id 配对) ──▶│  执行回执
  │◀── content (最终回复) ───────────────────│
  │◀── done ─────────────────────────────────│  本轮结束
```

★ 标记的两条消息构成前后端双向交互的核心闭环。

---

## 二、消息协议

### 2.1 下行（后端→前端，10 种 type）

| type | 含义 | 触发时机 |
|---|---|---|
| `session_started` | 会话 id | 连接建立/新会话创建 |
| `thinking` | 思考流（流式） | LLM 推理中 |
| `tool_call` | 工具调用通知 | LLM 决定调工具 |
| `frontend_action` ★ | 前端执行指令 | 工具返回 `type=frontend_action` |
| `content` | 最终回复 | LLM 无 tool_calls 时 |
| `done` | 本轮结束 | 对话完成 |
| `error` | 错误 | 异常 |
| `chat_stopped` | 已停止 | 用户点停止 |
| `active_tasks_list` | 活跃任务 | 查询/任务状态变更 |
| `ping` | 心跳 | 周期性 |

### 2.2 上行（前端→后端，5 种 type）

| type | 含义 |
|---|---|
| `chat_request` | 用户消息（含 conversation_id/model/project_id） |
| `tool_result` ★ | 前端执行回执（按 request_id 配对，wait_for_result=True 时必须） |
| `stop_chat` | 停止（含 conversation_id） |
| `get_active_tasks` | 查询活跃任务 |
| `pong` | 心跳响应 |

---

## 三、`frontend_action` 的 event_type 清单

> ★ **阶段 14** 曾移除 Leaflet 地图模式（前端改为卡片墙）；**阶段 21** 又重新引入地图引擎 **OpenLayers 9**，前端统一为 **地图容器**（影像/掩膜/矢量作为图层叠加）。

| event_type | 含义 | 前端执行函数 | 阻塞? |
|---|---|---|---|
| `render_image` | 🛰️ 遥感影像（原图层 + 掩膜叠加层 + 矢量图层 + 图例浮层） | `renderImage()` → `showImageInPanel()`（加 OL 图层）+ `showVectorFile()`（矢量） | 是（等图层加载） |
| `layer_control` / `layer_group_control` | 图层显隐/切换 | `renderLayerControl()` → `showWmsInPanel()` | 是（等 WMS 加载） |
| `layer_display` / `layer_locate` | 图层加载/定位 | `renderLayerDisplay()` → `showWmsInPanel()` | 是（等 WMS 加载） |
| `download` | 下载文件（报表/矢量/Excel） | `renderDownload()` | 否 |

> 注：矢量文件（GeoJSON/Shapefile）不单独占用 event_type，由 `render_image` 的 `params.vector_url` 触发前端 `showVectorFile()` 叠加为地图矢量图层（`.shp` 经 `/api/v1/vector/shp-to-geojson` 端点转换）。

表格类结果（`render_table`）由消息渲染层（`msg-ui.js`）直接处理，不经过 `executeFrontendAction` 路由。

---

## 四、关键设计点

### 4.1 `request_id` —— 下行/上行的配对钥匙

每个 `frontend_action` 带唯一 `request_id`。前端执行完后用相同 `request_id` 发 `tool_result`。后端 `wait_for_result=True` 时用 `asyncio.Future` 阻塞等待对应回执，超时（`ws_frontend_result_timeout`=120s）自动放行。

### 4.2 `wait_for_result` —— 阻塞 vs 发射即忘

| 场景 | wait_for_result | 行为 |
|---|---|---|
| 图层控制（需确认加载成功） | **True** | 后端阻塞等前端回执 |
| 渲染/下载/遥感/报表 | **False** | 发射即忘，不阻塞 |

### 4.3 多对话并行（v2.1）

同一 WS 连接支持多对话并行。每条下行消息带 `conversation_id`，前端 `conversationCallbacks` Map 按 id 路由到对应会话的回调。

### 4.4 为什么用 WebSocket 而非 SSE

- 双向：`tool_result` 回执必须前端→后端
- 多对话：单连接复用，按 conversation_id 路由
- 阻塞语义：`wait_for_result=True` 需要 future 配对

---

## 五、消息分支端点（v2.5 Git-like DAG）

> ★ 阶段 16（v2.5）：新增 3 个 HTTP 端点支持消息分支。消息表加 `node_id`/`parent_id`/`is_active`，停止回滚改为软删除。

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/v1/conversations/{id}/fork` | POST | 从指定 node_id 分叉（隐藏后续兄弟，更新 active_leaf_node） |
| `/api/v1/conversations/{id}/regenerate` | POST | fork + 返回状态（前端再通过 WS 发 chat_request 重发） |
| `/api/v1/conversations/{id}/branches` | GET | 分支信息（active/hidden 消息计数） |

**前端交互**：human 消息气泡 hover 显示 `↻ 重新生成` 按钮 → `handleRegenerate` → fork → 重载 UI → 用原 prompt 重新发对话。所有消息气泡挂 `data-node-id`。
