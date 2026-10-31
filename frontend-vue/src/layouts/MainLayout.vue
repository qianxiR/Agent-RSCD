<script setup>
// ==================== 三栏主布局 + 拖拽分隔 ====================
// ★ 对应原生 index.html 的 .header + .main 三栏结构
//   header：logo + 系统标题 + WS 状态点（与原生前端一比一）
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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

// 顶栏时间：30s 刷新一次，避免秒级重渲染
const clock = ref('')
let clockTimer = null
function tickClock() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  clock.value = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
onMounted(() => {
  tickClock()
  clockTimer = setInterval(tickClock, 30000)
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
})

// 左右栏宽度（px），中间栏 flex:1
const leftWidth = ref(300)
const rightWidth = ref(420)

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
    min: isLeft ? 260 : 180,
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
        <span class="status-item">
          <span class="status-dot" :class="wsStatus"></span>
          <span class="status-text">{{ wsLabel }}</span>
        </span>
        <span v-if="sessionId" class="status-item mono">会话 {{ sessionId.slice(0, 8) }}</span>
        <span class="status-item mono clock">{{ clock }}</span>
        <RouterLink class="admin-entry" to="/admin" title="进入管理控制台（需要管理员 MFA）">
          <Icon name="shield" :size="13" />
          <span>管理控制台</span>
        </RouterLink>
        <LoginDialog />
      </div>
    </header>

    <!-- 三栏主体（对齐原生 .main） -->
    <div class="main">
      <div class="pane pane-left" :style="{ width: leftWidth + 'px' }">
        <LeftSidebar />
      </div>
      <div class="resizer resizer-v" @pointerdown="startResize('left', $event)"></div>

      <div class="pane pane-center">
        <MapPanel />
      </div>

      <div class="resizer resizer-v" @pointerdown="startResize('right', $event)"></div>
      <div class="pane pane-right" :style="{ width: rightWidth + 'px' }">
        <ChatPanel />
      </div>
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
  border-bottom: 1px solid #f0f0f0;
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
  font-weight: 400;
  letter-spacing: 0.2px;
  color: var(--text-muted);
  white-space: nowrap;
}
.header-brand-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}
.header-status {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  font-size: var(--fs-md);
  color: var(--text-muted);
}
.status-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.status-item.clock {
  color: var(--text-muted);
  font-size: var(--fs-sm);
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
  background: var(--bg-panel);
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
  color: var(--text-secondary);
}
.app-header :deep(.auth-entry .auth-user) {
  color: var(--text-secondary);
  font-size: var(--fs-sm);
}
.app-header :deep(.auth-entry .el-button.is-link) {
  color: var(--accent);
  font-size: var(--fs-sm);
}
.app-header :deep(.auth-entry .el-button.is-link:hover) {
  color: var(--accent-hover);
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
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
  background: var(--bg-base);
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
  background: var(--border);
  position: relative;
  z-index: 5;
  transition: background-color var(--t-base) var(--ease-out);
}
.resizer-v {
  width: 4px;
  cursor: col-resize;
  align-self: stretch;
}
.resizer:hover {
  background: var(--accent);
}
</style>
