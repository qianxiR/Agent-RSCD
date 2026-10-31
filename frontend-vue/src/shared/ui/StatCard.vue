<!-- 指标卡：标签 / 数值 / 脚注 -->
<script setup>
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '—' },
  unit: { type: String, default: '' },
  foot: { type: String, default: '' },
  // normal | up | warn
  tone: { type: String, default: 'normal' },
})
</script>

<template>
  <div class="kpi">
    <div class="kpi-label">
      <slot name="icon" />
      <span>{{ label }}</span>
    </div>
    <div class="kpi-value">
      {{ value }}<small v-if="unit">{{ unit }}</small>
    </div>
    <div v-if="foot || $slots.foot" class="kpi-foot" :class="tone !== 'normal' ? tone : ''">
      <slot name="foot">{{ foot }}</slot>
    </div>
  </div>
</template>
