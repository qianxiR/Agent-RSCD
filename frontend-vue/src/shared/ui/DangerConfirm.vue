<!-- 危险操作确认：必须填写原因（写入审计），二次确认后才提交。 -->
<script setup>
import { ref, watch } from 'vue'
import AppModal from './AppModal.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '确认执行该操作？' },
  description: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  // 是否强制填写原因
  requireReason: { type: Boolean, default: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'confirm'])

const reason = ref('')
const error = ref('')

watch(
  () => props.open,
  (open) => {
    if (open) {
      reason.value = ''
      error.value = ''
    }
  },
)

function submit() {
  if (props.requireReason && reason.value.trim().length < 4) {
    error.value = '请填写至少 4 个字的原因，便于审计追溯'
    return
  }
  emit('confirm', reason.value.trim())
}
</script>

<template>
  <AppModal :open="open" :title="title" @close="emit('close')">
    <p v-if="description" class="secondary" style="line-height: 1.65; margin-bottom: 12px">
      {{ description }}
    </p>
    <div v-if="requireReason" class="field">
      <label>操作原因<span class="required">*</span></label>
      <textarea
        v-model="reason"
        class="input"
        placeholder="例如：该用户已离职，回收全部访问权限"
        :class="{ 'is-invalid': !!error }"
        maxlength="200"
      />
      <p v-if="error" class="hint" style="color: var(--accent-red)">{{ error }}</p>
      <p v-else class="hint">原因会写入审计日志，用于事后追溯。</p>
    </div>
    <div v-if="!requireReason" class="alert alert-warn">
      <slot name="warning">该操作不可撤销，请确认后继续。</slot>
    </div>
    <template #footer>
      <button class="btn" :disabled="busy" @click="emit('close')">取消</button>
      <button class="btn btn-danger" :disabled="busy" @click="submit">
        {{ busy ? '处理中…' : confirmText }}
      </button>
    </template>
  </AppModal>
</template>
