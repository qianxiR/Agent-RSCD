<!-- 表单字段：label + 控件插槽 + hint/error，统一间距与文字层级。 -->
<script setup>
defineProps({
  label: { type: String, default: '' },
  hint: { type: String, default: '' },
  error: { type: String, default: '' },
  required: { type: Boolean, default: false },
})
</script>

<template>
  <div class="ui-field">
    <span v-if="label" class="ui-field-label">
      {{ label }}<em v-if="required" class="ui-field-required">*</em>
    </span>
    <slot />
    <span v-if="error" class="ui-field-error">{{ error }}</span>
    <span v-else-if="hint" class="ui-field-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.ui-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.ui-field-label {
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text-primary);
}
.ui-field-required {
  margin-left: 2px;
  font-style: normal;
  color: var(--accent-red);
}
.ui-field-hint {
  font-size: var(--fs-sm);
  line-height: 1.5;
  color: var(--text-muted);
}
.ui-field-error {
  font-size: var(--fs-sm);
  line-height: 1.5;
  color: var(--accent-red);
}
</style>
