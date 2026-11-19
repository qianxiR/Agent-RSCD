<!-- 加载态：中性旋转指示 + 文案。 -->
<script setup>
defineProps({
  text: { type: String, default: '加载中…' },
  inline: { type: Boolean, default: false },
})
</script>

<template>
  <div class="ui-loading" :class="{ inline }">
    <span class="ui-loading-spinner" />
    <span v-if="text" class="ui-loading-text">{{ text }}</span>
  </div>
</template>

<style scoped>
.ui-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px 0;
  color: var(--text-muted);
  font-size: var(--fs-sm);
}
.ui-loading.inline {
  justify-content: flex-start;
  padding: 0;
}
.ui-loading-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--border-strong);
  border-top-color: var(--text-secondary);
  border-radius: 50%;
  animation: ui-loading-spin 0.8s linear infinite;
}
@keyframes ui-loading-spin {
  to { transform: rotate(360deg); }
}
</style>
