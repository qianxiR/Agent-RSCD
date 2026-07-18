<script setup>
// ==================== 三栏主布局 + 拖拽分隔 ====================
// ★ 对应原生 index.html 的 .header + .main 三栏结构
//   header：logo + 系统标题 + WS 状态点（与原生前端一比一）
import { ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useConversationStore } from '@/stores/conversation'
import { useResize } from '@/composables/useResize'
import LeftSidebar from '@/components/sidebar/LeftSidebar.vue'
import MapPanel from '@/components/map/MapPanel.vue'
import ChatPanel from '@/components/chat/ChatPanel.vue'

const store = useConversationStore()
const { wsStatus, sessionId } = storeToRefs(store)

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
    <!-- 顶部：logo + 系统标题 + 状态（对齐原生 .header） -->
    <header class="app-header">
      <h1>
        <img src="/image.svg" alt="logo" class="header-logo">
        <span class="header-accent">国土智察</span> 自然资源遥感智能监测系统
      </h1>
      <div class="header-status">
        <span class="status-dot" :class="wsStatus"></span>
        <span v-if="sessionId" class="session-id">{{ sessionId.slice(0, 8) }}…</span>
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
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-base);
}

/* ===== 顶部 header（一比一对齐原生 .header） ===== */
.app-header {
  background: var(--bg-panel);
  padding: 8px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  box-shadow: inset 0 -1px 0 rgba(255, 255, 255, 0.5);
}
.app-header h1 {
  font-size: var(--fs-hero);
  color: var(--accent);
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-logo {
  height: 34px;
  width: auto;
  border-radius: var(--r-sm);
  flex-shrink: 0;
  object-fit: contain;
}
.header-accent {
  color: var(--accent);
  margin-right: 6px;
}
.header-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
  font-family: var(--mono);
}
.session-id {
  color: var(--text-muted);
}
.status-dot {
  width: 7px;
  height: 7px;
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
  box-shadow: -4px 0 16px rgba(15, 35, 58, 0.04);
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
