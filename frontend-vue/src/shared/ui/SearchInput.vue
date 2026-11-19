<!-- 搜索输入：输入框 + 清除按钮，焦点态保持中性，不出现蓝色高亮。 -->
<script setup>
import Icon from '@/shared/ui/Icon.vue'

const model = defineModel({ type: String, default: '' })

defineProps({
  placeholder: { type: String, default: '搜索' },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['clear'])

function clear() {
  model.value = ''
  emit('clear')
}
</script>

<template>
  <div class="ui-search" :class="{ disabled }">
    <Icon name="search" :size="13" class="ui-search-icon" />
    <input
      v-model="model"
      class="ui-search-input"
      type="text"
      :placeholder="placeholder"
      :disabled="disabled"
    />
    <button
      v-if="model"
      type="button"
      class="ui-search-clear"
      title="清除"
      @click="clear"
    >
      <Icon name="close" :size="12" />
    </button>
  </div>
</template>

<style scoped>
.ui-search {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 8px;
  background: var(--bg-inset);
  border-radius: var(--r-sm);
  box-shadow: inset 0 0 0 var(--hairline) var(--border-faint);
}
.ui-search:focus-within {
  box-shadow: inset 0 0 0 var(--hairline) var(--border-strong);
}
.ui-search.disabled { opacity: 0.6; }
.ui-search-icon { flex: 0 0 auto; color: var(--text-muted); }
.ui-search-input {
  flex: 1;
  min-width: 0;
  height: 100%;
  padding: 0;
  font-family: inherit;
  font-size: var(--fs-sm);
  color: var(--text-primary);
  background: transparent;
  border: 0;
  outline: none;
}
.ui-search-input::placeholder { color: var(--text-muted); }
.ui-search-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: var(--text-muted);
  background: transparent;
  border: 0;
  border-radius: var(--r-xs);
  cursor: pointer;
}
.ui-search-clear:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}
</style>
