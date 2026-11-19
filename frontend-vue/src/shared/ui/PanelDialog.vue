<!-- 面板型对话框：左侧分类 + 右侧列表的固定外壳（扩展/资料库等），业务内容通过插槽注入。 -->
<script setup>
import BaseDialog from '@/shared/ui/BaseDialog.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import Icon from '@/shared/ui/Icon.vue'

const open = defineModel('open', { type: Boolean, default: false })

defineProps({
  title: { type: String, required: true },
  // 头部计数；null 时不显示
  count: { type: Number, default: null },
  width: { type: String, default: 'min(760px, calc(100vw - 24px))' },
  refreshable: { type: Boolean, default: true },
})

defineEmits(['refresh'])
</script>

<template>
  <BaseDialog
    v-model="open"
    class="panel-dialog"
    :width="width"
    align-center
    :show-close="false"
    :close-on-click-modal="false"
  >
    <template #header>
      <div class="pnl-head">
        <div class="pnl-head-title">
          <slot name="icon" />
          <span>{{ title }}</span>
        </div>
        <div class="pnl-head-right">
          <span v-if="count !== null" class="pnl-head-count">共 {{ count }} 项</span>
          <BaseButton v-if="refreshable" variant="icon" title="刷新" @click="$emit('refresh')">
            <Icon name="refresh" :size="16" />
          </BaseButton>
          <BaseButton variant="icon" title="关闭" @click="open = false">
            <Icon name="close" :size="16" />
          </BaseButton>
        </div>
      </div>
    </template>

    <div class="pnl-body">
      <aside class="pnl-side">
        <slot name="side" />
      </aside>
      <main class="pnl-main">
        <div class="pnl-toolbar">
          <span class="pnl-toolbar-title"><slot name="toolbar" /></span>
        </div>
        <div class="pnl-results">
          <slot />
        </div>
        <div v-if="$slots.hint" class="panel-hint">
          <slot name="hint" />
        </div>
      </main>
    </div>
  </BaseDialog>
</template>

<style scoped>
.pnl-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 36px;
  padding: 0 18px;
}
.pnl-head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-hero);
  font-weight: 600;
  color: var(--text-primary);
}
.pnl-head-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.pnl-head-count {
  font-size: var(--fs-md);
  color: var(--text-secondary);
}
.pnl-body {
  display: flex;
  height: min(600px, 74vh);
  background: var(--bg-panel);
}
.pnl-side {
  width: 168px;
  flex: 0 0 168px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 10px;
  border-right: 1px solid var(--border);
  background: #fcfcfc;
}
.pnl-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
}
.pnl-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px 8px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
}
.pnl-toolbar-title {
  font-weight: 600;
  color: var(--text-primary);
}
.pnl-results {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 16px 16px;
}
.panel-hint {
  margin: 0 16px 14px;
  padding: 8px 10px;
  border-radius: var(--r-md);
  background: var(--bg-inset);
  color: var(--text-muted);
  font-size: var(--fs-sm);
  line-height: 1.6;
}
</style>
