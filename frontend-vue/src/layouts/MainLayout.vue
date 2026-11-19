<script setup>
// ==================== 三栏主布局 + 拖拽分隔 ====================
// ★ 对应原生 index.html 的 .header + .main 三栏结构
//   header：logo + 系统标题 + WS 状态点（与原生前端一比一）
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useConversationStore } from '@/stores/conversation'
import { useResize } from '@/composables/useResize'
import LeftSidebar from '@/components/sidebar/LeftSidebar.vue'
import MapPanel from '@/components/map/MapPanel.vue'
import ChatPanel from '@/components/chat/ChatPanel.vue'
import LoginDialog from '@/components/auth/LoginDialog.vue'
import ImagerySearchDialog from '@/components/map/ImagerySearchDialog.vue'
import Icon from '@/shared/ui/Icon.vue'

const store = useConversationStore()
const { wsStatus, sessionId } = storeToRefs(store)

const WS_LABELS = {
  connected: '数据链路正常',
  connecting: '链路连接中',
  disconnected: '链路已断开',
  error: '链路异常',
}
const wsLabel = computed(() => WS_LABELS[wsStatus.value] || '链路状态未知')

// 左右栏宽度（px），中间栏 flex:1
const leftWidth = ref(210)
const rightWidth = ref(294)
// 左右栏收起状态（收起后宽度归零，内容保持挂载）
const leftCollapsed = ref(false)
const rightCollapsed = ref(false)

// 复用统一拖拽 composable（与 ChatPanel 输入框拖拽同一套逻辑）
const { startResize: startResizeRaw } = useResize()

// 入参: which 'left' | 'right'; ev pointerdown 事件
// 方法: 区分左右栏方向(右栏 invert)与钳位区间 → 交 composable 实时更新对应栏宽
// 出参: 无（拖拽中通过 onUpdate 写入 leftWidth/rightWidth）
function startResize(which, ev) {
  const isLeft = which === 'left'
  startResizeRaw(ev, {
    axis: 'x',
    // 左栏往右拖增宽(正)；右栏往左拖(clientX 减小)才增宽(invert)
    invert: !isLeft,
    min: isLeft ? 182 : 126,
    max: isLeft ? 560 : 720,
    start: isLeft ? leftWidth.value : rightWidth.value,
    onUpdate: (next) => { (isLeft ? leftWidth : rightWidth).value = next },
  })
}
</script>

<template>
  <div class="layout shell-fade-in">
    <!-- 顶部：机构标识 + 系统名 + 链路/时间/账号 -->
    <header class="app-header">
      <div class="header-brand">
        <img src="/image.svg" alt="国土智察" class="brand-logo-img" />
        <div class="header-titles">
          <h1>
            <span class="header-brand-name">国土智察</span>
            自然资源遥感智能监测系统
          </h1>
        </div>
      </div>

      <div class="header-status">
        <div class="pane-toggles">
          <button
            type="button"
            class="pane-toggle"
            :class="{ active: !leftCollapsed }"
            :title="leftCollapsed ? '展开左侧栏' : '收起左侧栏'"
            @click="leftCollapsed = !leftCollapsed"
          >
            <Icon name="panelLeft" :size="14" />
          </button>
          <button
            type="button"
            class="pane-toggle"
            :class="{ active: !rightCollapsed }"
            :title="rightCollapsed ? '展开右侧栏' : '收起右侧栏'"
            @click="rightCollapsed = !rightCollapsed"
          >
            <Icon name="panelRight" :size="14" />
          </button>
        </div>
        <span v-if="sessionId" class="status-item mono">会话 {{ sessionId.slice(0, 8) }}</span>
        <RouterLink class="admin-entry" to="/admin" title="进入管理控制台（需要管理员 MFA）">
          <Icon name="shield" :size="13" />
          <span>管理控制台</span>
        </RouterLink>
        <LoginDialog />
      </div>
    </header>

    <!-- 三栏主体（对齐原生 .main） -->
    <div class="main">
      <div
        class="pane pane-left"
        :class="{ collapsed: leftCollapsed }"
        :style="{ width: leftCollapsed ? '0px' : leftWidth + 'px' }"
      >
        <LeftSidebar />
      </div>
      <div v-if="!leftCollapsed" class="resizer resizer-v" @pointerdown="startResize('left', $event)"></div>

      <div class="pane pane-center">
        <MapPanel />
      </div>

      <div v-if="!rightCollapsed" class="resizer resizer-v" @pointerdown="startResize('right', $event)"></div>
      <div
        class="pane pane-right"
        :class="{ collapsed: rightCollapsed }"
        :style="{ width: rightCollapsed ? '0px' : rightWidth + 'px' }"
      >
        <ChatPanel />
      </div>

      <!-- 收起状态下的展开入口（贴在地图两侧边缘） -->
      <button
        v-if="leftCollapsed"
        type="button"
        class="pane-expand left"
        title="展开左侧栏"
        @click="leftCollapsed = false"
      >
        <Icon name="chevronRight" :size="13" />
      </button>
      <button
        v-if="rightCollapsed"
        type="button"
        class="pane-expand right"
        title="展开右侧栏"
        @click="rightCollapsed = false"
      >
        <Icon name="chevronLeft" :size="13" />
      </button>
    </div>

    <!-- 开源影像检索（Teleport 到 body，入口在左栏 / 地图左下角） -->
    <ImagerySearchDialog />
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-base);
}

/* ===== 顶部：极简品牌栏 ===== */
.app-header {
  height: 52px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 18px;
  background: var(--bg-panel);
  border-bottom: var(--hairline) solid var(--border);
  color: var(--text-primary);
  z-index: 10;
}
.header-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.brand-logo-img {
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  object-fit: contain;
  display: block;
}
.header-titles {
  min-width: 0;
}
.app-header h1 {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: var(--fw-body);
  letter-spacing: 0.2px;
  color: var(--text-muted);
  white-space: nowrap;
}
.header-brand-name {
  font-size: 15px;
  font-weight: var(--fw-strong);
  color: var(--text-primary);
}
.header-status {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  font-size: var(--fs-md);
  color: var(--text-muted);
  font-family: var(--sans);
}
.header-status :deep(*),
.header-status .mono,
.header-status .admin-entry {
  font-family: inherit;
}
.status-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.status-text {
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
/* 工作台 → 管理控制台的入口 */
.admin-entry {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg-inset);
  color: var(--text-secondary);

  font-family: var(--sans);
  font-size: var(--fs-md);
  text-decoration: none;
  transition: background-color var(--t-base) var(--ease-out), border-color var(--t-base) var(--ease-out);
}
.admin-entry:hover {
  color: var(--text-primary);
  border-color: var(--border-strong);
  background: var(--bg-hover);
}
.app-header :deep(.auth-entry) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg-inset);
  color: var(--text-secondary);
  font-family: var(--sans);
  font-size: var(--fs-md);
}
.app-header :deep(.auth-entry .auth-user) {
  color: var(--text-secondary);
  font-size: var(--fs-md);
}
.app-header :deep(.auth-entry .el-button.is-link) {
  color: var(--text-secondary);
  font-size: var(--fs-md);
  height: auto;
  padding: 0;
}
.app-header :deep(.auth-entry .el-button.is-link:hover) {
  color: var(--text-primary);
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.connected {
  background: var(--accent-green);
}
.status-dot.disconnected {
  background: var(--accent-red);
}
.status-dot.connecting {
  background: var(--accent-amber);
  animation: pulse 1.2s infinite;
}
.status-dot.error {
  background: var(--accent-red);
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

/* ===== 三栏主体（对齐原生 .main） ===== */
.main {
  position: relative;
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
  background: var(--bg-base);
}
.pane.collapsed {
  border-color: transparent;
}
.pane-toggles {
  display: flex;
  align-items: center;
  gap: 4px;
}
.pane-toggle {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg-inset);
  color: var(--text-muted);
  cursor: pointer;
  transition: background-color var(--t-base) var(--ease-out), border-color var(--t-base) var(--ease-out), color var(--t-base) var(--ease-out);
}
.pane-toggle:hover {
  color: var(--text-primary);
  border-color: var(--border-strong);
  background: var(--bg-hover);
}
.pane-toggle.active {
  color: var(--text-primary);
}
.pane-expand {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  z-index: 8;
  width: 18px;
  height: 52px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-muted);
  cursor: pointer;
}
.pane-expand:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}
.pane-expand.left {
  left: 0;
  border-left: none;
  border-radius: 0 8px 8px 0;
  box-shadow: 2px 0 6px rgba(24, 24, 20, 0.06);
}
.pane-expand.right {
  right: 0;
  border-right: none;
  border-radius: 8px 0 0 8px;
  box-shadow: -2px 0 6px rgba(24, 24, 20, 0.06);
}
.pane {
  min-width: 0;
  overflow: hidden;
  transition: width var(--t-base) var(--ease-out), box-shadow var(--t-base) var(--ease-out);
}
.pane-left {
  background: var(--bg-base);
  border-right: 1px solid var(--border);
  flex-shrink: 0;
}
.pane-center {
  flex: 1;
  background: var(--bg-panel);
}
.pane-right {
  background: var(--bg-base);
  border-left: 1px solid var(--border);
  flex-shrink: 0;
}

/* ===== 拖拽分隔条（对齐原生 .resizer） ===== */
.resizer {
  flex-shrink: 0;
  background: transparent;
  position: relative;
  z-index: 5;
  transition: background-color var(--t-base) var(--ease-out);
}
.resizer-v {
  width: 6px;
  cursor: col-resize;
  align-self: stretch;
}
.resizer:hover {
  background: var(--border-strong);
}
</style>
