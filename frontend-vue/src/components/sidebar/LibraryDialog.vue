<script setup>
// ==================== 资料库弹窗 ====================
// 用于管理检索与解译所用文档；后端尚未接入，当前仅布局占位。
// 布局对齐影像检索弹窗：左栏分类 + 右侧内容列表。
import { computed, ref } from 'vue'
import { Document } from '@element-plus/icons-vue'
import PanelDialog from '@/shared/ui/PanelDialog.vue'

const open = defineModel('open', { type: Boolean, default: false })

const sections = [
  { key: 'all', label: '全部文档', icon: Document },
]
const active = ref('all')

const activeLabel = computed(() => (
  sections.find((sec) => sec.key === active.value)?.label || ''
))
</script>

<template>
  <PanelDialog v-model:open="open" title="资料库" :refreshable="false">
    <template #icon>
      <el-icon><Document /></el-icon>
    </template>

    <template #side>
      <button
        v-for="sec in sections"
        :key="sec.key"
        type="button"
        class="pnl-side-item"
        :class="{ active: active === sec.key }"
        @click="active = sec.key"
      >
        <el-icon><component :is="sec.icon" /></el-icon>
        <span>{{ sec.label }}</span>
      </button>
    </template>

    <template #toolbar>{{ activeLabel }}</template>

    <div class="pnl-empty">暂无文档</div>

    <template #hint>
      资料库用于管理检索与解译所用文档；文档管理能力接入后在此展示。
    </template>
  </PanelDialog>
</template>

<style scoped>
.pnl-side-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  border: none;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-lg);
  cursor: pointer;
  text-align: left;
  width: 100%;
}
.pnl-side-item:hover {
  background: var(--bg-hover);
}
.pnl-side-item.active {
  background: #ececec;
  color: var(--text-primary);
  font-weight: var(--fw-medium);
}
.pnl-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  font-size: var(--fs-md);
  color: var(--text-muted);
}
</style>
