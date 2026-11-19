<!-- 确认弹窗：普通/危险操作共用，可要求填写原因。 -->
<script setup>
import { ref, watch } from 'vue'
import BaseDialog from '@/shared/ui/BaseDialog.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import Icon from '@/shared/ui/Icon.vue'

const open = defineModel({ type: Boolean, default: false })

const props = defineProps({
  title: { type: String, default: '确认' },
  description: { type: String, default: '' },
  confirmText: { type: String, default: '确定' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
  requireReason: { type: Boolean, default: false },
  reasonLabel: { type: String, default: '操作原因' },
  reasonPlaceholder: { type: String, default: '请填写原因' },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])

const reason = ref('')
const error = ref('')

watch(open, (value) => {
  if (!value) return
  reason.value = ''
  error.value = ''
})

function cancel() {
  if (props.busy) return
  open.value = false
  emit('cancel')
}

function confirm() {
  if (props.busy) return
  if (props.requireReason && !reason.value.trim()) {
    error.value = '请填写原因'
    return
  }
  open.value = false
  emit('confirm', props.requireReason ? { reason: reason.value.trim() } : undefined)
}
</script>

<template>
  <BaseDialog
    v-model="open"
    width="420px"
    align-center
    :show-close="false"
    :close-on-click-modal="!busy"
  >
    <template #header>
      <div class="ui-confirm-head">
        <span class="ui-confirm-title">{{ title }}</span>
        <BaseButton variant="icon" size="sm" title="关闭" @click="cancel">
          <Icon name="close" :size="14" />
        </BaseButton>
      </div>
    </template>

    <div class="ui-confirm-body">
      <p v-if="description" class="ui-confirm-desc">{{ description }}</p>
      <label v-if="requireReason" class="ui-confirm-field">
        <span>{{ reasonLabel }}</span>
        <textarea
          v-model="reason"
          class="ui-confirm-reason"
          rows="3"
          :placeholder="reasonPlaceholder"
          :disabled="busy"
        />
      </label>
      <div v-if="error" class="ui-confirm-error">{{ error }}</div>
      <div class="ui-confirm-actions">
        <BaseButton variant="ghost" :disabled="busy" @click="cancel">{{ cancelText }}</BaseButton>
        <BaseButton
          variant="primary"
          bold
          class="ui-confirm-ok"
          :class="{ 'is-danger': danger }"
          :disabled="busy"
          @click="confirm"
        >{{ busy ? '处理中…' : confirmText }}</BaseButton>
      </div>
    </div>
  </BaseDialog>
</template>

<style scoped>
.ui-confirm-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 36px;
  padding: 0 18px;
  border-bottom: var(--hairline) solid var(--border);
}
.ui-confirm-title {
  font-size: var(--fs-lg);
  font-weight: var(--fw-strong);
  color: var(--text-primary);
}
.ui-confirm-body { padding: 14px 18px 16px; }
.ui-confirm-desc {
  margin: 0 0 10px;
  font-size: var(--fs-md);
  line-height: 1.6;
  color: var(--text-secondary);
}
.ui-confirm-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.ui-confirm-reason {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  min-height: 64px;
  padding: 7px 9px;
  font-family: inherit;
  font-size: var(--fs-md);
  color: var(--text-primary);
  background: var(--bg-inset);
  border: 0;
  border-radius: var(--r-sm);
  box-shadow: inset 0 0 0 var(--hairline) var(--border-faint);
  outline: none;
}
.ui-confirm-reason:focus {
  box-shadow: inset 0 0 0 var(--hairline) var(--border-strong);
}
.ui-confirm-error {
  margin-top: 6px;
  font-size: var(--fs-sm);
  color: var(--accent-red);
}
.ui-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.ui-confirm-ok.is-danger.ui-btn {
  background: var(--accent-red);
  border-color: var(--accent-red);
}
.ui-confirm-ok.is-danger.ui-btn:hover:not(:disabled) {
  background: #9d3f3f;
  border-color: #9d3f3f;
}
</style>
