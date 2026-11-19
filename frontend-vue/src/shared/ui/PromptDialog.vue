<!-- 输入对话框：沿用 BaseDialog 外壳，单行或多行文本输入，确认时做非空校验。 -->
<script setup>
import { nextTick, ref, watch } from 'vue'
import BaseDialog from '@/shared/ui/BaseDialog.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import Icon from '@/shared/ui/Icon.vue'

const open = defineModel({ type: Boolean, default: false })

const props = defineProps({
  // 头部标题
  title: { type: String, default: '' },
  // 输入框上方说明文字；为空时不占位
  label: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  // true 用多行输入（Enter 换行），false 单行（Enter 提交）
  multiline: { type: Boolean, default: false },
  // 打开时预填内容
  prefill: { type: String, default: '' },
  confirmText: { type: String, default: '确定' },
  cancelText: { type: String, default: '取消' },
  // 非空校验失败提示；传空字符串表示不做校验
  emptyHint: { type: String, default: '内容不能为空' },
  width: { type: String, default: '400px' },
})

const emit = defineEmits(['confirm', 'cancel'])

const value = ref('')
const error = ref('')
const inputRef = ref(null)

// 每次打开重置为预填值并聚焦，避免上一次输入残留
watch(open, (v) => {
  if (!v) return
  value.value = props.prefill || ''
  error.value = ''
  nextTick(() => inputRef.value?.focus())
})

function clearError() {
  error.value = ''
}

function cancel() {
  open.value = false
  emit('cancel')
}

function confirm() {
  const text = value.value.trim()
  if (!text && props.emptyHint) {
    error.value = props.emptyHint
    return
  }
  open.value = false
  emit('confirm', text)
}

// 单行输入 Enter 提交；输入法组合态下的 Enter 属于选词，不提交
function onEnter(e) {
  if (props.multiline || e.isComposing) return
  confirm()
}
</script>

<template>
  <BaseDialog
    v-model="open"
    class="prompt-dialog"
    :width="width"
    align-center
    :show-close="false"
    :close-on-click-modal="false"
  >
    <template #header>
      <div class="pd-head">
        <span class="pd-title">{{ title }}</span>
        <BaseButton variant="icon" :px="0" title="关闭" @click="cancel">
          <Icon name="close" :size="16" />
        </BaseButton>
      </div>
    </template>

    <div class="pd-body">
      <div v-if="label" class="pd-label">{{ label }}</div>
      <el-input
        ref="inputRef"
        v-model="value"
        class="pd-input"
        :type="multiline ? 'textarea' : 'text'"
        :autosize="multiline ? { minRows: 4, maxRows: 10 } : undefined"
        :placeholder="placeholder"
        @input="clearError"
        @keydown.enter="onEnter"
      />
      <div v-if="error" class="pd-error">{{ error }}</div>
    </div>

    <template #footer>
      <div class="pd-actions">
        <BaseButton variant="ghost" :px="16" @click="cancel">{{ cancelText }}</BaseButton>
        <BaseButton variant="primary" :px="16" @click="confirm">{{ confirmText }}</BaseButton>
      </div>
    </template>
  </BaseDialog>
</template>

<style scoped>
.pd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 34px;
  padding: 0 16px;
  border-bottom: var(--hairline) solid var(--border-faint);
}
.pd-title {
  font-size: var(--fs-hero);
  font-weight: 600;
  color: var(--text-primary);
}
.pd-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
}
.pd-label {
  font-size: var(--fs-md);
  color: var(--text-secondary);
}
.pd-input {
  width: 100%;
}
.pd-error {
  font-size: var(--fs-sm);
  line-height: 1.5;
  color: var(--accent-red);
}
.pd-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 16px 16px;
}

/* 输入框对齐会话输入区：底色 + 发丝级内环，聚焦与基态一致 */
.pd-input :deep(.el-input__wrapper) {
  min-height: 44px;
  padding: 0 12px;
  border: 0 !important;
  border-radius: var(--r-shell) !important;
  background: var(--bg-base) !important;
  box-shadow: inset 0 0 0 var(--hairline) var(--border-faint) !important;
}
.pd-input :deep(.el-input__wrapper.is-focus) {
  background: var(--bg-base) !important;
  box-shadow: inset 0 0 0 var(--hairline) var(--border-faint) !important;
}
.pd-input :deep(.el-input__inner) {
  background: transparent !important;
  box-shadow: none !important;
  border-radius: 0;
  font-size: var(--fs-lg);
  color: var(--text-primary);
}
.pd-input :deep(.el-textarea__inner) {
  padding: 10px 12px 8px;
  border: 0 !important;
  border-radius: var(--r-shell) !important;
  background: var(--bg-base) !important;
  box-shadow: inset 0 0 0 var(--hairline) var(--border-faint) !important;
  font-family: var(--sans);
  font-size: var(--fs-lg);
  line-height: 1.5;
  color: var(--text-primary);
  resize: none;
}
.pd-input :deep(.el-textarea__inner:focus) {
  background: var(--bg-base) !important;
  box-shadow: inset 0 0 0 var(--hairline) var(--border-faint) !important;
}
.pd-input :deep(.el-input__inner::placeholder),
.pd-input :deep(.el-textarea__inner::placeholder) {
  color: var(--text-muted);
}
.prompt-dialog :deep(.el-dialog__header) {
  padding: 0;
  margin: 0;
}
.prompt-dialog :deep(.el-dialog__body) {
  padding: 0;
}
.prompt-dialog :deep(.el-dialog__footer) {
  padding: 0;
}
</style>
