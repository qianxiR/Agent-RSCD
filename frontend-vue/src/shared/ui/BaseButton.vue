<!-- 通用按钮：统一 primary / ghost / icon / quick 四种外观，尺寸与内边距可配。 -->
<script setup>
defineProps({
  // primary：深色实心主操作；ghost：描边次操作；icon：纯图标；quick：胶囊快捷项
  variant: { type: String, default: 'primary' },
  // 尺寸：md = 32px（主/次按钮）/ 28px（图标），lg = 34px，sm = 24px（图标）
  size: { type: String, default: 'md' },
  // 水平内边距，缺省按尺寸取 14px；调用方需要精确对齐时显式传入
  px: { type: [Number, String], default: null },
  // 主按钮加粗（600）
  bold: { type: Boolean, default: false },
  // 图标按钮使用更浅的次要文字色
  muted: { type: Boolean, default: false },
})
</script>

<template>
  <button
    type="button"
    class="ui-btn"
    :class="[
      `ui-btn--${variant}`,
      `ui-btn--${size}`,
      { 'ui-btn--bold': bold, 'ui-btn--muted': muted },
    ]"
    :style="px !== null ? { '--ui-btn-px': `${px}px` } : undefined"
  >
    <slot />
  </button>
</template>

<style scoped>
.ui-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}

.ui-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* ---------- 外观 ---------- */
.ui-btn--primary {
  color: #fff;
  background: #1a1a1a;
  border: 1px solid #1a1a1a;
  border-radius: var(--r-md);
}

.ui-btn--primary:hover:not(:disabled) {
  background: #111110;
  border-color: #111110;
}

.ui-btn--ghost {
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}

.ui-btn--ghost:hover:not(:disabled) {
  color: var(--text-primary);
  border-color: var(--border-strong);
}

.ui-btn--icon {
  padding: 0;
  color: var(--text-secondary);
  background: transparent;
  border: 0;
}

.ui-btn--icon:hover:not(:disabled) {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.ui-btn--quick {
  padding: 2px 8px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 999px;
}

.ui-btn--quick:hover:not(:disabled) {
  color: var(--text-primary);
}

/* ---------- 尺寸 ---------- */
.ui-btn--md:not(.ui-btn--quick) {
  height: 32px;
  padding: 0 var(--ui-btn-px, 14px);
  font-size: var(--fs-md);
}

.ui-btn--lg:not(.ui-btn--quick) {
  height: 34px;
  padding: 0 var(--ui-btn-px, 14px);
  font-size: var(--fs-lg);
}

.ui-btn--md.ui-btn--icon {
  width: 28px;
  height: 28px;
  border-radius: var(--r-sm);
}

.ui-btn--sm.ui-btn--icon {
  width: 24px;
  height: 24px;
  border-radius: var(--r-xs);
}

/* ---------- 修饰 ---------- */
.ui-btn--bold {
  font-weight: 600;
}

.ui-btn--muted.ui-btn--icon {
  color: var(--text-muted);
}
</style>
