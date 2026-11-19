<!-- 列表行：项目 / 会话 / 实例共用；选中态中性灰，操作区 hover 出现。 -->
<script setup>
defineProps({
  active: { type: Boolean, default: false },
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  meta: { type: String, default: '' },
  titleAttr: { type: String, default: '' },
})

const emit = defineEmits(['select'])
</script>

<template>
  <div
    class="ui-row"
    :class="{ active }"
    @click="emit('select')"
  >
    <div class="ui-row-main">
      <span class="ui-row-title" :title="titleAttr || title">{{ title }}</span>
      <span v-if="subtitle" class="ui-row-sub">{{ subtitle }}</span>
    </div>
    <span v-if="meta" class="ui-row-meta">{{ meta }}</span>
    <span v-if="$slots.actions" class="ui-row-actions" @click.stop>
      <slot name="actions" />
    </span>
  </div>
</template>

<style scoped>
.ui-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  border-radius: var(--r-md);
  background: transparent;
  cursor: pointer;
}
.ui-row:hover { background: var(--bg-hover); }
.ui-row.active { background: #ececec; }
.ui-row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ui-row-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-md);
  color: var(--text-primary);
}
.ui-row-sub {
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.ui-row-meta {
  flex: 0 0 auto;
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.ui-row-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  opacity: 0;
  transition: opacity var(--t-fast) var(--ease-out);
}
.ui-row:hover .ui-row-actions,
.ui-row.active .ui-row-actions { opacity: 1; }
</style>
