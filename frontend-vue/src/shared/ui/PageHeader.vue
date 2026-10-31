<!-- 页面标题区：标题 + 右侧操作（不使用副标题） -->
<script setup>
// subtitle 仅用于吸收调用方传参，不再渲染（全局不使用副标题）
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
})
</script>

<template>
  <header class="page-head">
    <div class="page-head-text">
      <h1 class="page-title">
        {{ title }}
        <slot name="badge" />
      </h1>
    </div>
    <div v-if="$slots.actions" class="page-actions">
      <slot name="actions" />
    </div>
  </header>
</template>
