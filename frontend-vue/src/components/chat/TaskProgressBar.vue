<script setup>
// ==================== 任务进度条 ====================
// ★ 对话内重工具进度展示（最轻方案，替代独立任务面板）
//   数据源: store.activeTask（useWebSocket 收到重工具 tool_call 后轮询 /tasks 写入）
//   生命周期: tool_call 触发 → done/error 清除；终态自动停止轮询
import { computed } from 'vue'

const props = defineProps({
  task: { type: Object, required: true }, // {id, tool_name, status, progress, error?}
})

// 状态色板（对齐原生 task-monitor: pending灰/running橙/done绿/failed红/cancelled灰）
const statusColor = computed(() => ({
  pending: 'var(--text-muted)',
  running: 'var(--accent-green)',
  done: 'var(--accent-green)',
  failed: 'var(--accent-red)',
  cancelled: 'var(--text-muted)',
}[props.task.status] || 'var(--text-muted)'))

const statusText = computed(() => ({
  pending: '等待中', running: '执行中', done: '已完成', failed: '失败', cancelled: '已取消',
}[props.task.status] || props.task.status))

const progress = computed(() => props.task.progress || 0)
</script>

<template>
  <div class="task-bar">
    <div class="task-bar-head">
      <span class="task-dot" :style="{ background: statusColor }"></span>
      <span class="task-tool">{{ task.tool_name || '任务' }}</span>
      <span class="task-status" :style="{ color: statusColor }">{{ statusText }}</span>
    </div>
    <div class="task-progress-track">
      <div class="task-progress-fill" :class="{ running: task.status === 'running' }" :style="{ width: progress + '%', background: statusColor }"></div>
    </div>
    <div v-if="task.error" class="task-error">{{ task.error }}</div>
  </div>
</template>

<style scoped>
.task-bar {
  margin: 2px 0;
  padding: 8px 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent-green);
  border-radius: var(--r-shell);
  font-size: var(--fs-sm);
  box-shadow: 0 1px 3px rgba(15, 35, 58, 0.04);
}
.task-bar-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.task-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.task-tool {
  flex: 1;
  font-family: var(--mono);
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-status {
  font-size: var(--fs-xs);
  font-weight: 600;
}
.task-progress-track {
  height: 4px;
  background: var(--bg-inset);
  border-radius: 2px;
  overflow: hidden;
}
.task-progress-fill {
  height: 100%;
  border-radius: 2px;
  transition: width var(--t-slow) var(--ease-out);
}
.task-progress-fill.running {
  animation: task-pulse 1.4s ease-in-out infinite;
}
@keyframes task-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
.task-error {
  margin-top: 4px;
  color: var(--accent-red);
  font-size: var(--fs-xs);
  font-family: var(--mono);
  word-break: break-all;
}
</style>
