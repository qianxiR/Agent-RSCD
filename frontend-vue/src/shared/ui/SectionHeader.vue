<!-- 分组标题：标题 + 计数 + 右侧操作插槽。 -->
<script setup>
defineProps({
  title: { type: String, default: '' },
  count: { type: [Number, String], default: null },
})
</script>

<template>
  <div class="ui-section">
    <span class="ui-section-title">{{ title }}</span>
    <span v-if="count !== null" class="ui-section-count">{{ count }}</span>
    <span v-if="$slots.actions" class="ui-section-actions">
      <slot name="actions" />
    </span>
  </div>
</template>

<style scoped>
.ui-section {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 2px;
}
.ui-section-title {
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.ui-section-count {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.ui-section-actions {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
</style>
