<!-- 类别输入：自由文本 + 已有类别建议，输入新值时可触发创建。 -->
<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '输入类别' },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'create'])

const listId = `ui-class-${Math.random().toString(36).slice(2, 9)}`
const text = ref(props.modelValue || '')
const names = computed(() => props.options.map((item) => (
  typeof item === 'string' ? item : item?.name
)).filter(Boolean))

watch(() => props.modelValue, (value) => {
  text.value = value || ''
})

function onChange(event) {
  const value = String(event.target.value || '').trim()
  if (!value) {
    event.target.value = props.modelValue || ''
    return
  }
  text.value = value
  emit('update:modelValue', value)
  if (!names.value.includes(value)) emit('create', value)
}
</script>

<template>
  <input
    class="ui-class"
    :value="text"
    :list="listId"
    :placeholder="placeholder"
    :disabled="disabled"
    @change="onChange"
    @keydown.enter="$event.target.blur()"
  />
  <datalist :id="listId">
    <option v-for="name in names" :key="name" :value="name" />
  </datalist>
</template>

<style scoped>
.ui-class {
  min-width: 0;
  height: 24px;
  padding: 0 5px;
  font-family: inherit;
  font-size: var(--fs-sm);
  color: var(--text-primary);
  background: var(--bg-panel);
  border: var(--hairline) solid var(--border);
  border-radius: var(--r-xs);
  outline: none;
}
.ui-class:focus {
  border-color: var(--border-strong);
  box-shadow: 0 0 0 2px var(--bg-inset);
}
.ui-class::placeholder { color: var(--text-muted); }
.ui-class:disabled { opacity: 0.6; }
</style>
