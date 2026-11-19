# 工作台 UI 结构与色彩链路

> 范围：`/` 工作台（不含管理端 `/admin/**`）。
> 扫描日期：2026-11-19。组件清单见 [frontend-component-library.md](frontend-component-library.md)，设计规则见 [docs/rules/ui-style.md](../rules/ui-style.md)。

## 一、布局骨架

```
┌──────────────────────────────────────────────────────────────┐
│ 顶栏 52px：品牌 + 链路状态 + 侧栏开关 + 管理控制台 + 登录        │
├──────────────┬──────────────────────────────┬────────────────┤
│ 左栏 210px   │ 地图（flex:1）                │ 右栏 294px     │
│ 最小 182     │                              │ 最小 126       │
│ 最大 560     │                              │ 最大 720       │
├──────────────┴──────────────────────────────┴────────────────┤
│ 左右栏可拖拽（resizer 6px），可整栏收起                         │
└──────────────────────────────────────────────────────────────┘
```

组件：`MainLayout.vue` → `LeftSidebar.vue` / `MapPanel.vue` / `ChatPanel.vue`，外加 `LoginDialog.vue`、`ImagerySearchDialog.vue`。

## 二、左 → 右组件清单

### 2.1 顶栏

| 区域 | 内容 | 组件 |
|---|---|---|
| 左 | 品牌图 + 「国土智察 自然资源遥感智能监测系统」 | `MainLayout` + `Icon` |
| 右 | 左右栏开关、会话号、管理控制台入口、登录 | `MainLayout`、`Icon`、`LoginDialog` |

### 2.2 左栏

入口组件：`LeftSidebar.vue` → `ConvSidebar.vue`。

| 顺序 | 内容 | 实现 |
|---|---|---|
| 1 | 新建任务 | 原生 `<button class="new-task-btn">` |
| 2 | 专家·技能·连接器 | 原生 `<button class="tool-btn">` + `el-icon` → `ExtensionsDialog` |
| 3 | 资料库 | 原生 `<button class="tool-btn">` → `LibraryDialog` |
| 4 | 影像检索 | 原生 `<button class="tool-btn">` → `ImagerySearchDialog` |
| 5 | 搜索图层 | 原生 `<button class="tool-btn">` + Teleport 自绘搜索遮罩 |
| 6 | 项目空间 | `.project-row` 列表；hover 显示新建任务/重命名/删除 |
| 7 | 会话记录 | `.conversation-card` 列表；hover 显示重命名/删除 |
| 8 | 新建项目 | `.new-project-inline` |

弹窗：`PromptDialog`（新建项目 / 重命名项目 / 重命名对话）、`ExtensionsDialog` / `LibraryDialog`（`PanelDialog` + `BaseDialog`）。

### 2.3 中栏（地图）

入口组件：`MapPanel.vue`。

| 区域 | 内容 |
|---|---|
| 左上工具条 | `图层` → `卷帘` → `样本制作` → `地物提取` → `变化检测`；样本模式追加 `绘制 / 顶点 / 框选`，同时隐藏图层/卷帘/地物/变化 |
| 右上 | 放大 / 缩小 |
| 左下 | 清除影像预览（存在预览时） |
| 右下 | 经纬度 + 比例尺白底卡片 |
| 浮层 | `LayerPanel`、卷帘面板、`SamplePanel`、框选提示、卷帘分隔线、图例 `LegendOverlay` |
| 弹窗 | `SamplePhaseDialog`、`CaptureInterpretDialog`、`ChangeDetectDialog`、`ImagerySearchDialog` |
| 地图内容 | 底图、WMS/WFS、影像预览、样本影像、标注图层、红框工作范围 |

### 2.4 右栏（Copilot）

入口组件：`ChatPanel.vue`。

| 顺序 | 内容 | 组件 |
|---|---|---|
| 1 | 项目/会话上下文 + 任务日志入口 | `ProjectSelector` |
| 2 | 消息滚动区 | `MessageBubble`、`AssistantSteps` |
| 3 | 工具执行进度 | `TaskProgressBar` |
| 4 | 输入区 | 原生 textarea、附件条、图层 @ 弹层 |
| 5 | 底部操作 | 上传、发送（黑圆按钮）/ 停止（红图标） |
| 6 | 弹窗 | `TaskMonitorDialog`、`PromptDialog`（编辑消息 / 重新生成） |

## 三、色彩体系

### 3.1 表面层级（由浅到深）

| 令牌 | 颜色 | 工作台用途 |
|---|---|---|
| `--bg-base` | `#f5f5f3` | 页面底色、地图栏外层、输入区容器 |
| `--bg-panel` | `#fcfcfb` | 顶栏、左右栏、浮层、弹窗、工具条默认底 |
| `--bg-hover` | `#efefec` | 悬停：项目行、会话卡、工具按钮、弹窗按钮 |
| `--bg-inset` | `#f0f0ed` | 输入框、次级按钮、用户气泡、工具卡 |
| `--bg-sunken` | `#ecece8` | 禁用按钮、下沉块 |
| 选中态 | `#ececec` | 项目行、会话卡、样本实例行 |
| 地图舞台 | `--bg-inset` | `.wms-stage` 内底色 |

### 3.2 文字与边框

| 令牌 | 颜色 | 用途 |
|---|---|---|
| `--text-primary` | `#1a1a18` | 标题、正文、激活文字 |
| `--text-secondary` | `#5f5f59` | 次要文字、按钮、说明 |
| `--text-muted` | `#8d8d86` | 弱提示、占位、时间 |
| `--border-faint` | `rgba(24,24,20,.04)` | 发丝分隔 |
| `--border` | `rgba(24,24,20,.07)` | 常规边框 |
| `--border-strong` | `rgba(24,24,20,.12)` | 悬停/激活边框 |

### 3.3 状态变化

| 元素 | 默认 | 悬停 | 选中/激活 |
|---|---|---|---|
| 项目行 / 会话卡 | 透明 | `--bg-hover` | `#ececec` |
| 左栏工具按钮 | 透明 | `--bg-hover` | — |
| 新建任务 | `--bg-panel` + 0.5px 内描边 | `--bg-hover` + 强描边 | — |
| 地图工具条按钮 | `color-mix(--bg-panel 92%, transparent)` + 12px 模糊 | `--bg-panel` + 强描边 + 浮阴影 | 同悬停 |
| 地图缩放按钮 | 透明 | `--bg-hover` | — |
| 输入框 / 选择器 | `--bg-inset` + 0.5px 内描边 | 不变 | 聚焦同样式，无外发光 |
| 主按钮 / 发送 | `#1a1a1a` 白字 | `#111110` | — |
| 次按钮 | `--bg-inset` + `--text-secondary` | `--bg-hover` + `--text-primary` | — |
| 危险操作 | `--accent-red` | 红色文字 / 10% 红底 | — |
| 禁用 | `--bg-sunken` + `--text-muted` | — | — |

### 3.4 消息区

| 元素 | 背景 | 文字 |
|---|---|---|
| 用户消息 | `--bg-inset`（`design-v2.css` 覆盖组件内 `#f2f3f5`） | `--text-primary` |
| AI 消息 | 透明 | `--text-primary` |
| 工具消息 | `--bg-inset`，无左边框 | `--text-primary` |
| 工具轨迹卡 | `--bg-base` + 0.5px `--border-faint` | `--text-secondary` |
| 工具调用卡 | `--bg-panel` + 0.5px 边框 | `--text-secondary` |
| 内联结果 | `--bg-inset` | `--text-secondary` |
| 表格 | 表头 `--bg-base`，正文 `--bg-panel`，0.5px 下边框 | `--text-primary` |

### 3.5 语义色

| 令牌 | 颜色 | 用途 |
|---|---|---|
| `--accent` | `#4b5fd6` | 品牌标识、少量链接/装饰；不用于选中底 |
| `--accent-green` | `#3f8f5f` | 链路正常、成功 |
| `--accent-amber` | `#a8751f` | 连接中、未保存 |
| `--accent-red` | `#b54a4a` | 断链、错误、危险操作、停止 |
| `--accent-blue` | `#5f79d9` | 少量信息提示 |

### 3.6 动效

- 悬停/背景：`120ms` / `180ms`，`--ease-out`。
- 左右栏宽度：`180ms`。
- 拖拽分隔线：跟手，无回弹。
- 弹窗/浮层：`--shadow-float`，不使用纯黑阴影。

## 四、当前不一致

| 位置 | 问题 |
|---|---|
| `MessageBubble.vue` | 组件内仍保留 `#eef1ff`、`rgba(241,247,252,.72)`、`rgba(233,242,249,.68)`、`rgba(157,188,213,.45)` 等浅蓝；工作台主要消息/轨迹已被 `design-v2.css` 覆盖，但未覆盖的分支仍会出现浅蓝。 |
| `MainLayout.vue` | 顶栏底边硬编码 `#f0f0f0`；收起侧栏按钮阴影 `rgba(0,0,0,.06)` 使用纯黑。 |
| `ConvSidebar.vue` | `#ececec`、`#8d97a5`、`#d03050`、`#f59e0b` 硬编码；与令牌值重复但未走变量。 |
| `MapPanel.vue` | 样本工具条按钮 `.sample-tool` 未纳入 `design-v2.css` 的地图按钮选择器清单；默认/激活态由通用 `.map-tools-bar button` 与组件 scoped 样式叠加。 |
| 输入框 | 统一为 `--bg-inset` + 0.5px 内描边；聚焦不加外发光，符合规则。 |
| 选中态 | 左栏项目/会话与样本实例均为 `#ececec`，无浅蓝；地图要素选中用加粗描边 + 白色外圈。 |

## 五、组件使用总表

| 层 | 组件 | 工作台使用位置 |
|---|---|---|
| 壳层 | `MainLayout`、`LeftSidebar`、`WorkbenchView` | 三栏、顶栏、拖拽 |
| 基础 | `Icon`、`BaseButton`、`BaseDialog`、`PromptDialog`、`PanelDialog` | 全局 |
| 左栏 | `ConvSidebar`、`ExtensionsDialog`、`LibraryDialog` | 项目/会话、扩展、资料库 |
| 地图 | `MapPanel`、`LayerPanel`、`ImagerySearchDialog`、`CaptureInterpretDialog`、`ChangeDetectDialog`、`LegendOverlay`、`SamplePanel`、`SamplePhaseDialog` | 地图与三类业务入口 |
| 右栏 | `ChatPanel`、`ProjectSelector`、`MessageBubble`、`AssistantSteps`、`TaskProgressBar`、`TaskMonitorDialog` | 会话与执行轨迹 |
| 认证 | `LoginDialog`、`AuthPage` | 登录 |
