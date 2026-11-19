# 前端组件规范与要求

> 适用范围：`frontend-vue/` 用户端工作台（`/`）。管理端 `/admin/**` 暂不纳入。
> 相关文档：[ui-style.md](ui-style.md)（视觉规则）、[frontend-component-library.md](../platform/frontend-component-library.md)（现有组件清单）、[workbench-ui-map.md](../platform/workbench-ui-map.md)（工作台左→右结构与色彩链路）。
> 扫描脚本：全局技能 `frontend-design-detail/scripts/check_ui_rules.py`。

## 一、分层

```
L0 设计令牌        styles/theme.css
L1 基础组件        shared/ui/
L2 布局组件        壳层、栏、工具条、浮层
L3 业务组件        components/{sidebar,map,chat}/
L4 功能层          composables/、services/、stores/
```

约束：

- `shared/ui/` 组件不得调用 API、不得读 Pinia store、不得依赖业务常量。
- 业务组件不得重复实现按钮、弹窗、空态、加载态；不得硬编码颜色/字体/圆角。
- 组件使用 ≥2 个业务域才进入 `shared/ui/`，否则留在对应业务目录。
- 依赖方向：业务组件 → 基础组件；功能层可被业务组件调用；基础组件不反向依赖。

## 二、目录与命名

```
shared/ui/           PascalCase.vue，组件名即文件名
components/sidebar/  左栏容器与行组件
components/map/      地图容器、工具条、浮层、业务弹窗
components/chat/     会话容器、消息、输入区、轨迹
composables/         useXxx.js，一个文件一个职责
services/            xxx.js，纯请求与契约
stores/              xxx.js，Pinia store
```

命名：

- 容器组件以业务域命名：`ConvSidebar`、`MapPanel`、`ChatPanel`。
- 行/卡片：`ProjectRow`、`ConversationCard`、`LayerRow`、`MessageBubble`。
- 浮层/弹窗：`XxxPanel`、`XxxDialog`。
- 一个文件只导出一个组件；一个组件只承担一个视觉职责。

## 三、组件 API 约定

1. Props 只进不出；Emits 只出不进。
2. `v-model` 只用于开关、输入值、选中值；不要用 `v-model` 传复杂业务对象。
3. 基础组件的 Props 必须给默认值；布尔默认 `false`；数组/对象用工厂函数。
4. 业务组件向基础组件传值，不向基础组件传回调（用 Emit）。
5. 插槽命名：默认插槽为主内容；`actions` 为行操作；`header` / `footer` 为壳层区域。
6. 事件命名：`update:modelValue`、`select`、`create`、`confirm`、`cancel`、`close`、`refresh`。
7. 组件内不写 `!important`；全局覆盖只允许在 `styles/overlays.css` 与 `styles/design-v2.css`。
8. 组件内不出现 `font-family` 硬编码；统一继承 `--sans` 或 `--mono`。

## 四、色彩与状态

### 4.1 表面阶梯

| 令牌 | 值 | 用途 |
|---|---|---|
| `--bg-base` | `#f5f5f3` | 页面底、地图栏外层 |
| `--bg-panel` | `#fcfcfb` | 顶栏、左右栏、浮层、弹窗 |
| `--bg-hover` | `#efefec` | 悬停 |
| `--bg-inset` | `#f0f0ed` | 输入框、次级按钮、用户气泡 |
| 选中 | `#ececec` | 列表选中、模式激活 |
| `--bg-sunken` | `#ecece8` | 禁用、下沉 |

### 4.2 状态

| 元素 | 默认 | 悬停 | 选中/激活 |
|---|---|---|---|
| 列表行 | 透明 | `--bg-hover` | `#ececec` |
| 工具条按钮 | 面板白 + 0.5px 内描边 | `--bg-hover` + 强描边 | 同悬停 |
| 输入框 | `--bg-inset` + 0.5px 内描边 | 不变 | 聚焦同样式 |
| 主按钮 | `#1a1a1a` 白字 | `#111110` | — |
| 次按钮 | `--bg-inset` | `--bg-hover` | — |
| 危险 | `--accent-red` | 红字/10% 红底 | — |

### 4.3 禁止

- 选中/激活不得使用浅蓝、强调色底或彩色描边。
- 不得出现 `#eef1ff`、`#f2f3f5`、`rgba(241,247,252)`、`rgba(233,242,249)`、`rgba(157,188,213)`。
- 不得使用纯黑 `#000` 或 `rgba(0,0,0,...)` 阴影。
- 不得在组件内硬编码字体族、圆角、阴影、颜色。
- 不得卡片套卡片；分组用间距与 0.5px 分隔线。
- 每屏只保留一个黑色主按钮。

## 五、字体与排版

| 项 | 规则 |
|---|---|
| 字体 | `--sans` = Times New Roman + SimSun；代码/日志 `--mono` |
| 字号 | 12 / 13 / 16 / 20 四级；正文 13 |
| 字重 | 400 / 500 / 600 |
| 行高 | 正文 1.55–1.72；紧凑列表 1.4 |
| 数字 | `tabular-nums` |
| 孤字 | 正文 `text-wrap: pretty` |

## 六、基础组件清单与 API

| 组件 | Props | Emits / Model | 职责 |
|---|---|---|---|
| `BaseButton` | `variant, size, px, bold, muted` | — | primary / ghost / icon / quick |
| `BaseDialog` | `width, title, showClose, closeOnClickModal, closeOnPressEscape, appendToBody, alignCenter` | `v-model` | `el-dialog` 外壳 |
| `PromptDialog` | `title, label, placeholder, multiline, prefill, confirmText, cancelText, emptyHint, width` | `confirm(text), cancel`；`v-model` | 文本输入 |
| `ConfirmDialog` | `title, description, confirmText, cancelText, danger, requireReason, reasonLabel, reasonPlaceholder, busy` | `confirm(payload), cancel`；`v-model` | 普通/危险确认 |
| `PanelDialog` | `title, count, width, refreshable` | `refresh`；`v-model` | 面板型弹窗 |
| `FormField` | `label, hint, error, required` | — | 字段容器 |
| `SearchInput` | `placeholder, disabled` | `clear`；`v-model` | 搜索输入 |
| `ColorField` | `title, disabled` | `v-model` | 颜色选择 |
| `ClassCombo` | `options, placeholder, disabled` | `update:modelValue, create` | 类别自由输入 |
| `ToolbarButton` | `active, disabled, title, compact` | — | 工具条按钮 |
| `FloatingPanel` | `title, width, top, left, closable, closeTitle` | `close` | 地图浮层外壳 |
| `ListRow` | `active, title, subtitle, meta, titleAttr` | `select` | 列表行 |
| `SectionHeader` | `title, count` | — | 分组标题 |
| `LoadingState` | `text, inline` | — | 加载态 |
| `DialogActions` | — | — | 弹窗底部按钮 |
| `EmptyState` | `icon, title, description` | — | 空态 |
| `Icon` | `name, size, strokeWidth` | — | 图标 |
| `StatCard` | `label, value, unit, foot, tone` | — | 指标卡 |
| `PageHeader` | `title, subtitle` | — | 页头 |
| `PermissionGate` | `permission` | — | 权限门 |
| `DangerConfirm` | `open, title, description, confirmText, requireReason, busy` | `close, confirm` | 过渡组件，新代码用 `ConfirmDialog` |
| `AppModal` | `open, title, wide` | `close` | 过渡组件，新代码用 `ConfirmDialog` |

## 七、组合模式

### 7.1 弹窗

```vue
<BaseDialog v-model="open" width="520px" align-center :show-close="false">
  <template #header>
    <div class="head">
      <div class="title"><Icon name="..." /> 标题</div>
      <BaseButton variant="icon" size="sm" @click="open = false"><Icon name="close" /></BaseButton>
    </div>
  </template>
  <div class="body">
    <FormField label="名称" :error="error"><input class="input" /></FormField>
    <DialogActions>
      <BaseButton variant="ghost" @click="open = false">取消</BaseButton>
      <BaseButton variant="primary" bold @click="submit">确定</BaseButton>
    </DialogActions>
  </div>
</BaseDialog>
```

- 头部高度 36px，底部内边距 14/18/16。
- 主按钮在右，次按钮在左。
- 文本输入用 `PromptDialog`；危险操作 use `ConfirmDialog danger`。

### 7.2 地图浮层

```vue
<FloatingPanel title="卷帘" width="300px" @close="open = false">
  <FormField label="左侧时相">...</FormField>
  <template #footer>
    <BaseButton variant="primary" bold>开启卷帘</BaseButton>
  </template>
</FloatingPanel>
```

- 工具条统一 `ToolbarButton`；顺序：`图层 | 卷帘 | 样本制作 | 地物提取 | 变化检测`。
- 样本模式追加 `绘制 | 顶点 | 框选`，同时禁用图层/卷帘。
- 选中态中性灰，不用强调色。

### 7.3 列表

```vue
<SectionHeader title="项目空间" :count="projects.length" />
<ListRow
  v-for="p in projects"
  :key="p.id"
  :active="p.id === activeId"
  :title="p.name"
  @select="select(p)"
>
  <template #actions><Icon name="plus" :size="13" /></template>
</ListRow>
```

- 行高 32–36px；标题 13px；副标题/时间 12px muted。
- 操作区 hover 出现，不做常驻。

### 7.4 会话

- 用户消息 `--bg-inset`；AI 消息透明；工具消息 `--bg-inset`。
- 轨迹卡 `--bg-base` + 0.5px 边框；工具调用卡 `--bg-panel`。
- 输入区 `--bg-base` + 0.5px 内描边；发送按钮黑色圆形；停止按钮红色图标。
- 禁止使用浅蓝底、蓝色左边框。

## 八、迁移阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| A | 规范、令牌、扫描脚本 | 脚本可运行；构建通过 |
| B | L1 基础组件 | 组件 API 齐全、无业务依赖 |
| C | 左栏拆分 | 外观不变；项目/会话/搜索行为不变 |
| D | 地图拆分 | 工具顺序、框选、卷帘、样本模式不变 |
| E | 会话拆分 | 发送/停止/编辑/重新生成不变 |
| F | 业务弹窗统一 | 全部走 BaseDialog/FormField/DialogActions |
| G | 技能落盘与验收集成 | 技能可加载；扫描通过 |

拆分阶段只搬代码，不改视觉；配色修正单独提交。

## 九、验收清单

- [ ] `npm --prefix frontend-vue run build` 通过。
- [ ] 修改区域无 console / pageerror。
- [ ] 无浅蓝、无纯黑阴影、无硬编码字体。
- [ ] 选中态 `#ececec`，悬停 `--bg-hover`。
- [ ] 主按钮黑色，每屏最多一个。
- [ ] 弹窗走 `BaseDialog`；文本输入走 `PromptDialog`；危险确认走 `ConfirmDialog`。
- [ ] 新增组件已登记到 `frontend-component-library.md`。
- [ ] `check_ui_rules.py` 无新增违规。

## 十、反例

- 在组件内写 `background: #eef1ff` 表示选中。
- 在业务组件里复制一套 `.button` / `.dialog` 样式。
- 用 `!important` 覆盖基础组件来改业务样式。
- 用强调色描边表示地图要素选中。
- 列表行常驻显示操作图标，挤压标题。
- 同一屏出现两个黑色主按钮。
