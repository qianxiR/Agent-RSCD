<!-- 对话框外壳：统一 el-dialog 的尺寸、遮罩行为与头部/底部插槽，业务内容由调用方提供。 -->
<script setup>
const open = defineModel({ type: Boolean, default: false })

defineProps({
  // 对话框宽度，需带单位；调用方按内容给固定值或响应式表达式
  width: { type: String, default: '420px' },
  // 默认标题；提供 header 插槽时忽略
  title: { type: String, default: '' },
  showClose: { type: Boolean, default: true },
  closeOnClickModal: { type: Boolean, default: true },
  closeOnPressEscape: { type: Boolean, default: true },
  appendToBody: { type: Boolean, default: true },
  alignCenter: { type: Boolean, default: false },
})
</script>

<template>
  <el-dialog
    v-model="open"
    :width="width"
    :title="title || undefined"
    :show-close="showClose"
    :close-on-click-modal="closeOnClickModal"
    :close-on-press-escape="closeOnPressEscape"
    :append-to-body="appendToBody"
    :align-center="alignCenter"
  >
    <template v-if="$slots.header" #header>
      <slot name="header" />
    </template>
    <slot />
    <template v-if="$slots.footer" #footer>
      <slot name="footer" />
    </template>
  </el-dialog>
</template>
