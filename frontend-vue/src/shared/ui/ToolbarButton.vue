<!-- 工具条按钮：地图工具条与面板模式切换共用，选中用中性灰，不用强调色。 -->
<script setup>
defineProps({
  active: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  title: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})
</script>

<template>
  <button
    type="button"
    class="ui-tool"
    :class="{ active, compact }"
    :disabled="disabled"
    :title="title"
  >
    <slot />
  </button>
</template>

<style scoped>
.ui-tool {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 9px;
  font-family: inherit;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 0;
  border-radius: var(--r-sm);
  box-shadow:
    inset 0 0 0 var(--hairline) var(--border),
    0 1px 0 rgba(24, 24, 20, 0.035);
  cursor: pointer;
}
.ui-tool.compact {
  height: 26px;
  padding: 0 7px;
}
.ui-tool:hover:not(:disabled),
.ui-tool.active {
  color: var(--text-primary);
  background: var(--bg-hover);
  box-shadow: inset 0 0 0 var(--hairline) var(--border-strong);
}
.ui-tool:disabled {
  opacity: 0.55;
  cursor: default;
}
</style>
