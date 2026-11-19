<!-- 颜色字段：原生颜色选择器，统一为 20×20 色块。 -->
<script setup>
const model = defineModel({ type: String, default: '#1a1a18' })

defineProps({
  title: { type: String, default: '选择颜色' },
  disabled: { type: Boolean, default: false },
})
</script>

<template>
  <input
    v-model="model"
    class="ui-color"
    type="color"
    :title="title"
    :disabled="disabled"
  />
</template>

<style scoped>
.ui-color {
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
  padding: 0;
  background: var(--bg-panel);
  border: var(--hairline) solid var(--border);
  border-radius: var(--r-xs);
  cursor: pointer;
}
.ui-color:disabled { opacity: 0.5; cursor: default; }
.ui-color::-webkit-color-swatch-wrapper { padding: 1px; }
.ui-color::-webkit-color-swatch { border: 0; border-radius: 2px; }
</style>
