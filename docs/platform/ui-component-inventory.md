# 用户端 UI 资产盘点与组件库规划

> 盘点日期：2026-11-15
> 范围：`frontend-vue/` 用户端（路由 `/`，工作台：会话 + 地图 + Copilot）。
> 不在范围：管理端 `/admin/**`（其实现与设计不纳入本文结论）、旧版原生前端 `frontend/`、后端接口。
> 方法：静态扫描 `frontend-vue/src` 的 Vue 单文件组件、全局样式、composables/stores/services；统计值为扫描结果，非估计。
> 增量（2026-11-19）：样本制作新增 `SamplePanel.vue`、`SamplePhaseDialog.vue`，用户端 Vue 文件 21 个；其余统计未重扫。

---

## 一、结论

**仓库内没有可复用的组件库。**

用户端 UI 由三部分构成：

1. 全局设计令牌 `styles/theme.css`（CSS 变量）；
2. 三个全局样式文件：`content.css`（Markdown 排版）、`overlays.css`（Element Plus 浮层覆盖）、`design-v2.css`（工作台壳层与地图控件）；
3. 17 个业务组件，每个组件在 `<style scoped>` 内自带按钮、弹层、表单、列表样式。

因此新增系统或新增页面时，按钮、弹窗、表单、空态、加载态都需要重新实现，复用只能靠复制粘贴。

事实统计：

| 指标 | 数值 |
|---|---|
| 用户端 Vue 文件 | 19（17 个业务组件 + `MainLayout.vue` + `WorkbenchView.vue`） |
| 用户端 Vue 代码行数 | 9588 |
| scoped 样式内唯一类名 | 433 |
| 自定义按钮类名 | 21（其中 2 个已无引用） |
| 原生 `<button>` / `<el-button>` | 35 / 3 |
| 用户端 `:deep()` 写 EP 内部结构 | 41 处，分布在 9 个组件 |
| 用户端 `.el-*` 选择器 | 44 个 |
| 全局样式行数 | `theme.css` 366 / `content.css` 243 / `overlays.css` 228 / `design-v2.css` 589 |

---

## 二、页面与布局

| 路由 | 文件 | 结构 |
|---|---|---|
| `/` | `views/WorkbenchView.vue` → `layouts/MainLayout.vue` | 顶栏（品牌 + 系统名 + WS 状态 + 时钟 + 账号）→ 三栏：`LeftSidebar`（会话/图层/扩展）、`MapPanel`、`ChatPanel`；两栏可拖拽调整宽度（`useResize`） |

`MainLayout.vue`（312 行）承担：三栏骨架、拖拽分隔、WS 连接状态、时钟、登录弹窗与影像检索弹窗的挂载。

`design-v2.css` 的壳层选择器（`.layout`、`.app-header`、`.main`、`.pane-left`、`.pane-center`、`.pane-right`、`.resizer-v`）只服务这一套布局，和管理端 `shared/styles/shell.css` 的 shell 体系互不复用。

---

## 三、组件清单

| 域 | 文件 | 行数 | 职责 | 使用的 EP 组件 |
|---|---|---|---|---|
| 布局 | `layouts/MainLayout.vue` | 312 | 三栏骨架、拖拽、WS 状态、弹窗挂载 | — |
| 认证 | `components/auth/LoginDialog.vue` | 108 | 工作台登录 | `el-dialog` `el-form` `el-form-item` `el-input` `el-button` |
| 会话 | `components/chat/ChatPanel.vue` | 851 | 输入框、附件、@图层、发送/停止 | `ElMessage` |
| 会话 | `components/chat/MessageBubble.vue` | 816 | 消息气泡、Markdown、复制、操作栏 | `el-image` |
| 会话 | `components/chat/AssistantSteps.vue` | 623 | ReAct 步骤与工具调用轨迹卡 | `el-image` |
| 会话 | `components/chat/ProjectSelector.vue` | 112 | 项目/会话上下文条 | `el-select` `el-option` `el-icon` |
| 会话 | `components/chat/TaskProgressBar.vue` | 102 | 任务进度条 | — |
| 会话 | `components/chat/TaskMonitorDialog.vue` | 273 | 任务日志弹窗（自绘遮罩） | `el-select` `el-input` `el-button` |
| 地图 | `components/map/MapPanel.vue` | 1356 | OL 地图、底图、绘制、卷帘、截图、检索入口 | `el-select` `el-option` |
| 地图 | `components/map/LayerPanel.vue` | 1172 | 图层树、可见性、透明度、删除、检索 | `el-select` `el-option` `el-icon` |
| 地图 | `components/map/ImagerySearchDialog.vue` | 1401 | 影像检索（源/时相/云量/预览/裁剪） | `el-dialog` `el-select` `el-date-picker` `el-slider` |
| 地图 | `components/map/CaptureInterpretDialog.vue` | 362 | 框选解译 | `el-dialog` `el-select` |
| 地图 | `components/map/ChangeDetectDialog.vue` | 318 | 变化检测参数与结果 | `el-dialog` `el-select` |
| 地图 | `components/map/LegendOverlay.vue` | 56 | 图例浮层 | — |
| 侧栏 | `components/sidebar/ConvSidebar.vue` | 1042 | 会话列表、搜索、重命名/删除 | `el-icon` |
| 侧栏 | `components/sidebar/ExtensionsDialog.vue` | 306 | 专家/技能/连接器面板 | `el-dialog` `el-icon` |
| 侧栏 | `components/sidebar/LibraryDialog.vue` | 199 | 技能/插件库面板 | `el-dialog` `el-icon` |
| 侧栏 | `components/sidebar/LeftSidebar.vue` | 48 | 侧栏容器 | — |
| 视图 | `views/WorkbenchView.vue` | 18 | 工作台挂载点 + WS 生命周期 | — |

---

## 四、控件层盘点

### 4.1 按钮

用户端有 21 个自定义按钮类名，其中 4 组是同一件东西的重复实现：

| 语义 | 实现 | 所在组件 |
|---|---|---|
| 主按钮 | `cap-primary` / `change-primary` / `im-primary` / `compare-primary` | 框选解译 / 变化检测 / 影像检索 / 地图卷帘 |
| 次按钮 | `cap-ghost` / `change-ghost` / `im-ghost` / `compare-ghost` | 同上 |
| 图标按钮 | `cap-icon-btn` / `im-icon-btn` / `compare-icon-btn` / `layer-icon-btn` / `pnl-icon-btn` / `ai-action-btn` | 6 个组件 |
| 业务按钮 | `new-task-btn` / `tool-btn` / `send-btn` / `stop-btn` / `input-upload-btn` / `message-action-btn` / `layer-search-clear-btn` / `search-clear-btn` / `ws-pick-btn` | 5 个组件 |

- 事实：`basemap-btn`、`msg-op-btn` 只有样式定义，无任何引用。
- 事实：`LoginDialog`、`TaskMonitorDialog` 另用原生 `<el-button>`，全站 3 处。
- 事实：35 处原生 `<button>` 的样式全部由所在组件的 scoped 样式提供，无公共按钮组件。

### 4.2 弹层

用户端存在 5 种弹层实现方式：

| 方式 | 实现位置 | 特征 |
|---|---|---|
| `el-dialog` + 全局样式 | `LoginDialog.vue` | 依赖 `theme.css` / `overlays.css` 的全局覆盖 |
| `el-dialog` + `.panel-dialog` 公共类 | `ExtensionsDialog.vue`、`LibraryDialog.vue` | 公共类定义在 `design-v2.css:574-589` |
| `el-dialog` + 组件私有类 | `CaptureInterpretDialog.vue`（`.capture-dialog`）、`ChangeDetectDialog.vue`（`.change-dialog`）、`ImagerySearchDialog.vue`（`.imagery-dialog`） | 各自重写 header/body/footer 内边距与边框 |
| Teleport + 自绘遮罩 | `TaskMonitorDialog.vue`（`.task-monitor-mask`）、`LayerPanel.vue`（`.layer-search-overlay`）、`ConvSidebar.vue`（`.search-overlay`） | 各自实现 Esc、点击遮罩关闭、焦点语义 |
| 轻量浮层 | `MapPanel.vue`、`LayerPanel.vue` 内的下拉/提示 | 直接使用 EP popper 或绝对定位 |

- 事实：`ChangeDetectDialog.vue:283-318` 同时写 `:deep(.el-dialog__*）` 与 `.change-dialog .el-dialog__*` 两套选择器，属于同一目标的双份规则。
- 事实：3 个地图弹窗、2 个面板弹窗、1 个登录弹窗的头部/底部结构高度相似，但没有共享组件。

### 4.3 表单

- 事实：用户端直接使用 EP 控件（`el-input`、`el-select`、`el-date-picker`、`el-slider`），没有统一封装。
- 事实：`el-form` 只在 `LoginDialog.vue` 出现一次；其余表单为手写 label + 控件 + 错误文案。
- 事实：`.field` / `.hint` / `.is-invalid` 规范只定义在 `shared/styles/shell.css`（管理端样式表），用户端 `ChangeDetectDialog`、`ImagerySearchDialog`、`MapPanel` 各自复制了写法，未复用实现。

### 4.4 数据展示

| 控件 | 现状 |
|---|---|
| 空态 | `EmptyState.vue` 仅管理端使用；用户端 8 个组件各自写 `empty` 结构 |
| 状态/标签 | `.badge-*` 定义在管理端样式表；用户端各组件自绘 `status-*`、`chip`、`tag`、`active` 等 |
| 加载态 | 各组件自写 spinner / skeleton；无统一组件 |
| 表格 | 用户端 `TaskMonitorDialog.vue` 用裸 `<table>`；Markdown 表格走 `.rich-table`（`design-v2.css`） |
| 消息/轨迹 | `MessageBubble.vue`（816 行）、`AssistantSteps.vue`（623 行）承载气泡、Markdown、工具调用、复制、折叠，未拆分子组件 |
| 提示 | 统一用 `ElMessage`（9 个文件），无自有 toast |

### 4.5 图标

- 事实：`shared/ui/Icon.vue` 内置约 40 个 Lucide 路径，用户端 7 个文件引用。
- 事实：`@element-plus/icons-vue` 的 `el-icon` 在 `ProjectSelector`、`LayerPanel`、`ConvSidebar`、`ExtensionsDialog`、`LibraryDialog` 中使用。
- 结论：两套图标体系并存，同一界面内图标风格与尺寸不统一。

### 4.6 地图领域控件

`MapPanel.vue` 内的底图切换、日期选择、绘制工具、卷帘、截图按钮，`LayerPanel.vue` 内的图层树与检索框，均为组件内模板 + scoped 样式，无独立控件封装。

---

## 五、样式层

### 5.1 加载顺序与职责

`main.js` 的引入顺序决定覆盖关系：

```text
theme.css → content.css → overlays.css → shared/styles/shell.css → design-v2.css
```

| 文件 | 行数 | 职责 | 是否用户端专属 |
|---|---|---|---|
| `styles/theme.css` | 366 | 设计令牌、基础重置、EP 变量映射（80-88 行）、段落动画 | 否，全站 |
| `styles/content.css` | 243 | Markdown 正文排版（`.content-text`） | 否 |
| `styles/overlays.css` | 228 | EP 浮层玻璃态（`.el-overlay`、`.el-dialog`、popper） | 否 |
| `shared/styles/shell.css` | 1103 | 管理端壳层与组件类 | 否，但全局加载 |
| `styles/design-v2.css` | 589 | 工作台壳层、消息、输入区、Markdown、地图控件、`.panel-dialog` | 是 |

### 5.2 重复定义与覆盖

| 目标 | 重复位置 |
|---|---|
| `.el-dialog` 圆角/边框/阴影 | `theme.css:308-364`、`overlays.css:22-60`、`design-v2.css:423-445` |
| `.el-button` / `.el-button--primary` 基础态 | `theme.css:334-356`、`design-v2.css:446-463` |
| `.content-text` 正文排版 | `content.css:94-170`、`design-v2.css:310-380` |
| 弹窗壳层 | `.panel-dialog`（design-v2）+ 三个地图弹窗私有类 + 自绘遮罩 |

后果：同一控件的行为由三层全局规则叠加决定，`overlays.css` 大量使用 `!important`，局部调整需要提高选择器权重，升级 Element Plus 版本时需逐处核对。

### 5.3 `:deep()` 依赖

用户端 41 处 `:deep()`、44 个 `.el-*` 选择器，分布在 9 个组件：

`AssistantSteps`、`MessageBubble`、`ProjectSelector`、`TaskMonitorDialog`、`ChangeDetectDialog`、`ImagerySearchDialog`、`LayerPanel`、`MapPanel`、`MainLayout`。

这些规则直接依赖 EP 内部 DOM 结构，属于升级风险点；应集中到组件库的主题层，而不是散落在业务组件。

---

## 六、可复用的非 UI 层

| 文件 | 性质 | 是否随组件库 |
|---|---|---|
| `composables/useResize.js` | 通用（拖拽尺寸，带 `invert`/`min`/`max`） | 可 |
| `utils/markdown.js` + `content.css` | 通用（Markdown 渲染 + 富表格） | 可（作为 Markdown 组件） |
| `utils/tileCache.js`、`tileLoader.js` | 地图领域 | 否 |
| `composables/useMap.js` | 地图领域（OL 单例、WMS/WFS、GetFeatureInfo） | 否 |
| `composables/useBasemap.js`、`useSwipeCompare.js`、`useWaybackCapture.js`、`useImagerySearch.js` | 遥感/地图领域 | 否 |
| `composables/useWebSocket.js`、`useChat.js`、`useFrontendAction.js`、`stores/*`、`services/*` | 业务协议 | 否 |

---

## 七、复用障碍（事实）

1. 没有独立包：`frontend-vue/package.json` 为 `private`，无 lib 构建入口，组件全部通过 `@/` 别名引用，无法被其他项目安装。
2. 没有组件契约：现有“组件库”是 CSS 类（`.btn`、`.card`、`.field`），只提供视觉，不提供 props/slots/事件/状态。
3. 样式与业务耦合：按钮、弹层、表单样式写在业务组件 scoped 中，提取需同时迁移模板结构与样式。
4. 全局覆盖无收敛：EP 覆盖分散在 3 个文件 + 9 个组件，组件库必须接管主题层，否则复用后仍会出现样式冲突。
5. 图标双体系：`Icon.vue` 与 `el-icon` 并存，组件库必须先定一套。
6. 地图组件与 OL 实例、WS 协议、后端接口强绑定（`useMap` 单例、`frontend_action`），不能直接进通用库。

---

## 八、组件库规划

### 8.1 边界

**进库：**

- 设计令牌与 EP 主题层（替代 `theme.css` 的 EP 映射、`overlays.css` 的浮层覆盖、`design-v2.css` 的 `.el-*` 覆盖）；
- 与业务无关的原子/复合控件（按钮、弹层、表单行、表格、状态、空态、加载、图标、Markdown）；
- 通用工具（`useResize`、`markdown.js`）。

**不进库：**

- `MapPanel`、`LayerPanel`、`ImagerySearchDialog`、`ChangeDetectDialog`、`CaptureInterpretDialog` 整体；
- `useMap`、`useBasemap`、`useSwipeCompare`、`useWaybackCapture`、`useImagerySearch`；
- `ChatPanel`、`MessageBubble`、`AssistantSteps` 的消息协议联动逻辑；
- 会话/项目 stores 与 API services。

地图与对话组件只把其中可复用碎片（工具栏、筛选行、结果缩略图网格、键值行、状态点）下沉为通用控件。

### 8.2 P0：基础控件（先做，直接消除重复）

| 组件 | 替代对象 | 关键接口 |
|---|---|---|
| `BaseButton` | 21 个按钮类 + 3 处 `el-button` | 已落地 `frontend-vue/src/shared/ui/BaseButton.vue`：`variant: primary/ghost/icon/quick`、`size: md/lg/sm`、`px`、`bold`、`muted`；`disabled`/`title`/`type` 走属性透传 |
| `BaseDialog` | 5 种弹层方式 | 已落地 `frontend-vue/src/shared/ui/BaseDialog.vue`：`width`、`title`、`showClose`、`closeOnClickModal`、`closeOnPressEscape`、`appendToBody`、`alignCenter`；header/footer 插槽。`PanelDialog.vue` 在其上固化「左分类 + 右列表」外壳 |
| `BaseField` | 手写 label + 控件 + 错误 | `label`、`required`、`hint`、`error`、`inline`、默认插槽 |
| `BaseTable` | 裸 `<table>` + `.table` 类 | `columns`、`data`、`loading`、`empty` 插槽、`actions` 插槽 |
| `BaseStatus` | `.badge-*` + 各组件 `status-*` | `tone: neutral/success/warn/danger/info`、`dot`、默认插槽 |
| `BaseEmpty` | 8 处自绘空态 | `icon`、`title`、`description`、`actions` 插槽 |
| `BaseLoading` | 各组件 spinner/skeleton | `variant: line/skeleton/spinner`、`rows` |
| `BaseIcon` | `Icon.vue` + `el-icon` | `name`、`size`、`strokeWidth`；内部统一到一套路径集 |
| `BaseMarkdown` | `content.css` + `markdown.js` + `.rich-table` | `content`、`streaming`、`copyable`、表格增强开关 |

### 8.3 P1：复合控件（P0 稳定后再做）

| 组件 | 来源共性 |
|---|---|
| `PanelShell` | `.panel-dialog` + 三个地图弹窗壳层 + 自绘遮罩（统一头部、滚动区、底部操作区） |
| `DialogForm` | `LoginDialog` 的 `el-form` 用法 + 手写校验 + footer 按钮 |
| `FilterBar` | 影像检索/变化检测/任务监控的筛选行（select + date + 按钮组） |
| `ListPanel` | `ConvSidebar` 与 `LayerPanel` 的搜索 + 列表 + 选中/悬停态 |
| `ToolbarGroup` | `MapPanel` 底图/日期/卷帘/截图按钮组 |
| `KeyValueList` | 任务详情、元信息展示 |
| `TraceCard` | `AssistantSteps` 的步骤/工具调用卡（只抽展示层，不含协议） |

### 8.4 P2：留在应用内

`MapPanel`、`LayerPanel`、`ImagerySearchDialog`、`ChangeDetectDialog`、`CaptureInterpretDialog`、`ChatPanel`、`MessageBubble`、`AssistantSteps` 保业务结构，仅替换 P0/P1 控件。

---

## 九、落地顺序与验收

建议顺序（每步可独立验收）：

1. 建 `packages/ui`：令牌 + EP 主题层 + P0 组件；`frontend-vue` 通过本地依赖引用。（包形态未定，当前 P0 组件先落在 `frontend-vue/src/shared/ui/`）
2. 先替换 4 组重复按钮与 5 种弹层——这是重复量最大、改动最机械的部分。（按钮已完成：cap / change / im / compare 四组已改用 `BaseButton`，同时删除 `basemap-btn`、`msg-op-btn` 两处死样式；弹层已完成 4 组：登录、扩展、资料库、3 个地图弹窗统一走 `BaseDialog`/`PanelDialog`，剩余 3 个 Teleport 自绘遮罩未改）
3. 再替换空态、状态、加载态、Markdown。
4. 移除 `design-v2.css` 中的 `.el-*` 覆盖与 `:deep()` 规则，全部收口到库的主题层。
5. P1 按实际复用次数排序，至少两个调用点才抽。

验收标准：

- 用户端不再新增组件内按钮/弹层/表单样式；
- `design-v2.css` 不再包含 `.el-*` 选择器；
- 用户端 `:deep()` 数量降为 0；
- 同一控件在三个地图弹窗与两个面板弹窗中的外观与交互一致；
- EP 升级时只改库内主题层。

---

## 十、未决事项

1. 包形态：`packages/ui` 作为 monorepo 子包，还是独立 Git 仓库 + npm 私有源。
2. 图标基线：统一到 Lucide（`Icon.vue`）还是 EP 图标；统一后才能做 `BaseIcon`。
3. 主题范围：只保留当前浅色单主题，还是同时准备深色令牌（影响令牌结构）。
4. 管理端是否后续迁入同一套组件（本文不涉及，但影响令牌命名与包边界）。
5. 地图碎片下沉范围：`ToolbarGroup`、`FilterBar` 是否值得抽，取决于后续是否还有第二个地图类系统。
