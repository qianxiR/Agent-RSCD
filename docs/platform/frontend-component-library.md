# 前端组件库清单

> 扫描日期：2026-11-19。范围：`frontend-vue/src`，共 48 个 `.vue` 文件。
> 工作台左→右的结构与配色链路见 [workbench-ui-map.md](workbench-ui-map.md)；设计约束见 [docs/rules/ui-style.md](../rules/ui-style.md)；令牌来源 `frontend-vue/src/styles/theme.css`。

## 一、分层

| 层 | 位置 | 内容 |
|---|---|---|
| L0 令牌与全局样式 | `styles/`、`shared/styles/` | `theme.css` 设计令牌；`design-v2.css` 工作台壳层；`content.css` Markdown；`overlays.css` EP 浮层覆盖；`shared/styles/shell.css` 管理端壳层 |
| L1 基础组件 | `shared/ui/` | 按钮、弹窗、输入弹窗、面板弹窗、图标、空态、统计卡、页头、权限门、危险确认、通用模态 |
| L2 业务组件 | `components/`、`layouts/`、`views/`、`admin/` | 会话、地图、侧栏、认证、管理端页面 |
| L3 功能层 | `composables/`、`services/`、`stores/` | 地图、影像、样本、会话、认证、WS、持久状态 |

## 二、基础组件（`shared/ui/`，11 个）

| 组件 | 行 | 用途 | Props | Emits / Model | 直接引用 |
|---|---:|---|---|---|---:|
| `BaseButton.vue` | 130 | 统一按钮外观：primary / ghost / icon / quick | `variant, size, px, bold, muted` | — | 8 |
| `BaseDialog.vue` | 38 | `el-dialog` 外壳，统一尺寸/遮罩/头尾插槽 | `width, title, showClose, closeOnClickModal, closeOnPressEscape, appendToBody, alignCenter` | `v-model` | 7 |
| `PromptDialog.vue` | 199 | 单行/多行文本输入弹窗，非空校验 | `title, label, placeholder, multiline, prefill, confirmText, cancelText, emptyHint, width` | `confirm(text), cancel`；`v-model` | 2 |
| `PanelDialog.vue` | 141 | 面板型弹窗（标题 + 计数 + 刷新） | `title, count, width, refreshable` | `refresh`；`v-model` | 2 |
| `AppModal.vue` | 48 | 管理端简洁模态 | `open, title, wide` | `close` | 2 |
| `DangerConfirm.vue` | 67 | 危险操作确认，可要求填写原因 | `open, title, description, confirmText, requireReason, busy` | `close, confirm` | 1 |
| `Icon.vue` | 121 | 内联 SVG 图标，Lucide 子集 | `name, size, strokeWidth` | — | 17 |
| `EmptyState.vue` | 22 | 空态：图标 + 标题 + 说明 | `icon, title, description` | — | 5 |
| `StatCard.vue` | 27 | 指标卡 | `label, value, unit, foot, tone` | — | 3 |
| `PageHeader.vue` | 23 | 页面标题 + 副标题 | `title, subtitle` | — | 6 |
| `PermissionGate.vue` | 21 | 按权限显隐插槽 | `permission` | — | 0 |

## 三、布局与页面

| 组件 | 行 | 用途 |
|---|---:|---|
| `App.vue` | 10 | 根组件 |
| `views/WorkbenchView.vue` | 19 | 工作台挂载点 + WS 生命周期 |
| `layouts/MainLayout.vue` | 405 | 顶栏 + 三栏（会话 / 地图 / Copilot）+ 拖拽分隔 |
| `admin/layouts/AdminShell.vue` | 198 | 管理端壳层与路由出口 |
| `admin/pages/*.vue` | 21–471 | 13 个管理页：总览、用户、角色、项目、数据、图层、任务、存储、审计、设置、待办、登录、403 |

## 四、业务组件

### 4.1 认证

| 组件 | 行 | 用途 |
|---|---:|---|
| `components/auth/LoginDialog.vue` | 110 | 工作台登录弹窗 |
| `shared/auth/AuthPage.vue` | 743 | 登录 / 注册 / 找回密码页面 |
| `shared/ui/PermissionGate.vue` | 21 | 权限门（当前无引用） |

### 4.2 会话

| 组件 | 行 | 用途 |
|---|---:|---|
| `components/chat/ChatPanel.vue` | 896 | 输入框、附件、@图层、发送/停止、编辑/重新生成 |
| `components/chat/MessageBubble.vue` | 800 | 消息气泡、Markdown、复制、操作栏 |
| `components/chat/AssistantSteps.vue` | 624 | ReAct 步骤与工具调用轨迹 |
| `components/chat/ProjectSelector.vue` | 113 | 项目/会话上下文选择 |
| `components/chat/TaskProgressBar.vue` | 103 | 任务进度条 |
| `components/chat/TaskMonitorDialog.vue` | 274 | 任务日志监控弹窗 |
| `components/sidebar/ConvSidebar.vue` | 1124 | 会话/项目列表、搜索、新建/重命名/删除 |
| `components/sidebar/LeftSidebar.vue` | 49 | 左栏容器（会话/图层/扩展切换） |

### 4.3 地图

| 组件 | 行 | 用途 |
|---|---:|---|
| `components/map/MapPanel.vue` | 1551 | OL 地图、底图、卷帘、框选、样本/地物/变化入口 |
| `components/map/LayerPanel.vue` | 1120 | 图层树、可见性、透明度、删除、检索 |
| `components/map/ImagerySearchDialog.vue` | 1337 | 多源影像检索、预览、裁剪下载 |
| `components/map/CaptureInterpretDialog.vue` | 331 | 框选解译确认 |
| `components/map/ChangeDetectDialog.vue` | 291 | 变化检测时相与结果入口 |
| `components/map/LegendOverlay.vue` | 57 | 图例浮层 |
| `components/map/SamplePanel.vue` | 423 | 样本实例列表、类别输入、颜色选择、保存/导出 |
| `components/map/SamplePhaseDialog.vue` | 280 | 样本时相与样本区域选择 |

### 4.4 侧栏弹层

| 组件 | 行 | 用途 |
|---|---:|---|
| `components/sidebar/ExtensionsDialog.vue` | 307 | 专家 / 技能 / 连接器 |
| `components/sidebar/LibraryDialog.vue` | 84 | 技能 / 插件库 |

## 五、功能层

### 5.1 Composables（13 个）

| 文件 | 行 | 职责 |
|---|---:|---|
| `useMap.js` | 927 | 地图单例、WMS/WFS/XYZ/ImageStatic、图例、属性查询、视图 bbox |
| `useSampleStudio.js` | 401 | 样本会话：类别/实例/工作帧/保存/导出 |
| `useSampleScene.js` | 521 | 样本抓图、双期卷帘、红框工作范围 |
| `useSampleEditor.js` | 549 | 样本绘制/顶点/框选与图层样式 |
| `useSwipeCompare.js` | 274 | 双时相卷帘裁剪与灰色垫层 |
| `useWaybackCapture.js` | 220 | Esri Wayback 瓦片拼接（支持精确 bbox / 固定缩放） |
| `useBasemap.js` | 206 | 底图持久化与恢复 |
| `useWebSocket.js` | 388 | 推理 WS、重连、事件分发 |
| `useChat.js` | 170 | 发送/停止/分叉/重发 |
| `useImagerySearch.js` | 85 | 影像检索面板状态 |
| `useCaptureInterpret.js` | 33 | 框选解译状态 |
| `useFrontendAction.js` | 59 | 前端指令执行 |
| `useResize.js` | 104 | 三栏拖拽宽度 |

### 5.2 Services（3 个）

| 文件 | 行 | 职责 |
|---|---:|---|
| `api.js` | 358 | 认证、会话、项目、任务、图层、上传下载 |
| `imagery.js` | 219 | 影像源、检索、下载、抓图、配准、Wayback 清单 |
| `samples.js` | 58 | 样本保存/回读/任务/导出 URL、默认类别与调色板 |

### 5.3 Stores（2 个）

| 文件 | 行 | 职责 |
|---|---:|---|
| `stores/conversation.js` | 568 | 会话、消息、流式状态、分支 |
| `stores/project.js` | 96 | 项目分组、激活项目、折叠状态 |

## 六、现状与缺口

已收口：

- 按钮：`BaseButton` 覆盖 8 处；primary 黑底、ghost 描边、icon 透明。
- 弹窗：`BaseDialog` 覆盖 7 处；文本输入已统一为 `PromptDialog`（ChatPanel / ConvSidebar）。
- 图标：`Icon` 覆盖 17 处。
- 空态：管理端统一 `EmptyState`。

未收口：

- 用户端仍有大量原生 `<button>` 与组件私有按钮类名（地图、侧栏、会话）。
- 弹层存在 `BaseDialog`、`AppModal`、`Teleport + 自绘遮罩` 三套；`TaskMonitorDialog`、`LayerPanel`、`ConvSidebar` 仍自绘遮罩。
- 用户端空态 / 加载态各自实现；`EmptyState` 未在用户端使用。
- 表单直接使用 EP 控件；无统一 `FormField`（label + 控件 + 错误文案）。
- 地图工具条按钮、面板外壳、列表行在 `MapPanel` / `LayerPanel` / `SamplePanel` 各写一份。
- `PermissionGate` 无引用。

## 七、建议的组件库分层

| 层 | 组件 | 优先级 |
|---|---|---|
| 已有基础 | BaseButton、BaseDialog、PromptDialog、PanelDialog、Icon、EmptyState、StatCard、PageHeader、DangerConfirm、AppModal、PermissionGate | 保持 |
| 表单 | FormField（label + 控件 + hint + error）、SearchInput、ColorField、ClassCombo | P0 |
| 地图 | MapToolbarButton、MapFloatingPanel、MapSwipeDivider、MapDrawHint | P1 |
| 列表 | ListRow、ListPanel、Tag/Badge、LoadingState | P1 |
| 弹层 | 统一 `ConfirmDialog`（替代 AppModal/DangerConfirm 两套）、统一 Teleport 遮罩壳 | P1 |
| 反馈 | Toast/Message 封装、InlineError、StatusDot | P2 |

## 八、维护规则

- 新增 UI 先查 `shared/ui/`，没有则先补基础组件，不在业务组件里复制按钮/弹窗/空态。
- 颜色、字体、圆角、阴影一律走 `theme.css` 令牌，不硬编码。
- 状态样式按 `docs/rules/ui-style.md`：选中/悬停用中性灰，不用浅蓝高亮；主按钮黑底，次按钮浅灰。
- 弹窗统一 `BaseDialog` 外壳；文本输入统一 `PromptDialog`。
- 新组件必须在本文补一行：文件、行数、用途、Props、Emits/Model、引用数。
