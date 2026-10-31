<!-- 空态：图标 / 标题 / 说明 / 操作 -->
<script setup>
import Icon from './Icon.vue'

defineProps({
  icon: { type: String, default: 'folder' },
  title: { type: String, default: '暂无数据' },
  description: { type: String, default: '' },
})
</script>

<template>
  <div class="empty">
    <span class="empty-icon"><Icon :name="icon" :size="30" :stroke-width="1.4" /></span>
    <p class="empty-title">{{ title }}</p>
    <p v-if="description" class="empty-desc">{{ description }}</p>
    <div v-if="$slots.actions" class="empty-actions">
      <slot name="actions" />
    </div>
  </div>
</template>
