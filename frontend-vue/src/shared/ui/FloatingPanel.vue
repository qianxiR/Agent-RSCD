<!-- 地图浮层外壳：标题 + 关闭 + 内容 + 可选底部，位置与宽度可配。 -->
<script setup>
import BaseButton from '@/shared/ui/BaseButton.vue'
import Icon from '@/shared/ui/Icon.vue'

defineProps({
  title: { type: String, default: '' },
  width: { type: String, default: '320px' },
  top: { type: Number, default: 44 },
  left: { type: Number, default: 10 },
  closable: { type: Boolean, default: true },
  closeTitle: { type: String, default: '关闭' },
})

const emit = defineEmits(['close'])
</script>

<template>
  <div
    class="ui-float"
    :style="{ width, top: `${top}px`, left: `${left}px` }"
  >
    <div v-if="title || closable || $slots.header" class="ui-float-head">
      <slot name="header">
        <span class="ui-float-title">{{ title }}</span>
      </slot>
      <BaseButton
        v-if="closable"
        variant="icon"
        size="sm"
        :title="closeTitle"
        @click="emit('close')"
      >
        <Icon name="close" :size="14" />
      </BaseButton>
    </div>
    <div class="ui-float-body">
      <slot />
    </div>
    <div v-if="$slots.footer" class="ui-float-foot">
      <slot name="footer" />
    </div>
  </div>
</template>

<style scoped>
.ui-float {
  position: absolute;
  z-index: 26;
  max-height: calc(100% - 58px);
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  background: var(--bg-panel);
  border: var(--hairline) solid var(--border-strong);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-float);
}
.ui-float-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.ui-float-title {
  font-size: var(--fs-lg);
  font-weight: var(--fw-strong);
  color: var(--text-primary);
}
.ui-float-body {
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ui-float-foot {
  display: flex;
  gap: 6px;
}
</style>
