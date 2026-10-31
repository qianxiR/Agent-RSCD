<!-- 通用弹窗：标题 / 内容插槽 / 底部插槽。Esc 关闭，点击遮罩不关闭（防误触）。 -->
<script setup>
import { onBeforeUnmount, watch } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  wide: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}

watch(
  () => props.open,
  (open) => {
    if (open) window.addEventListener('keydown', onKey)
    else window.removeEventListener('keydown', onKey)
  },
)

onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-mask" role="dialog" aria-modal="true">
      <div class="modal" :class="{ 'modal-wide': wide }">
        <div class="modal-hd">
          <h3>{{ title }}</h3>
          <button class="icon-btn modal-x" aria-label="关闭" @click="emit('close')">
            <Icon name="close" :size="15" />
          </button>
        </div>
        <div class="modal-bd">
          <slot />
        </div>
        <div v-if="$slots.footer" class="modal-ft">
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>
