<script setup>
// ==================== Agent 能力占位面板（插件 / 多智能体） ====================
// 仅展示规划中的能力，不接后端；后续可替换为真实插件注册表/多智能体编排。
import { ChatLineRound, Plus, Connection } from '@element-plus/icons-vue'

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  items: { type: Array, default: () => [] },
})
const emit = defineEmits(['back'])
</script>

<template>
  <div class="agent-placeholder-panel">
    <div class="panel-actions">
      <button type="button" class="panel-action" disabled>
        <el-icon><Plus /></el-icon>
        <span>新建（规划中）</span>
      </button>
      <button type="button" class="panel-action" @click="emit('back')">
        <el-icon><ChatLineRound /></el-icon>
        <span>对话管理</span>
      </button>
    </div>

    <div class="panel-list">
      <div class="panel-section-header">{{ title }}</div>
      <div v-if="subtitle" class="panel-subtitle">{{ subtitle }}</div>

      <div v-for="item in items" :key="item.name" class="placeholder-card">
        <div class="placeholder-head">
          <el-icon class="placeholder-icon"><Connection /></el-icon>
          <span class="placeholder-title">{{ item.name }}</span>
          <span class="placeholder-tag">{{ item.status || '规划中' }}</span>
        </div>
        <div class="placeholder-desc">{{ item.desc }}</div>
      </div>

      <div class="panel-hint">
        当前为能力占位，尚未接入执行链路；不会影响现有 Agent、MCP、技能与解译功能。
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-placeholder-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.panel-actions {
  display: flex;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.panel-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-panel);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
}
.panel-action:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.panel-action:hover:not(:disabled) {
  background: var(--bg-hover);
}
.panel-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 10px;
}
.panel-section-header {
  font-size: var(--fs-md);
  color: var(--text-muted);
  padding: 8px 6px 6px;
}
.panel-subtitle {
  color: var(--text-muted);
  font-size: var(--fs-sm);
  line-height: 1.6;
  padding: 0 6px 10px;
}
.placeholder-card {
  border: 1px dashed var(--border);
  border-radius: var(--r-lg);
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--bg-base);
}
.placeholder-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.placeholder-icon {
  color: var(--accent);
}
.placeholder-title {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-lg);
  font-weight: 600;
}
.placeholder-tag {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  background: var(--bg-inset);
  border-radius: var(--r-sm);
  padding: 1px 6px;
}
.placeholder-desc {
  margin-top: 6px;
  color: var(--text-secondary);
  font-size: var(--fs-md);
  line-height: 1.55;
}
.panel-hint {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: var(--r-md);
  background: var(--bg-inset);
  color: var(--text-muted);
  font-size: var(--fs-sm);
  line-height: 1.6;
}
</style>
