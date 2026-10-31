<script setup>
// ==================== 变化检测：选择两期 Esri 时相 ====================
// 流程: 地图拖框 → 本弹窗选 T1/T2（Esri Wayback 发布版本）→ 浏览器拼接两期高清影像
//       → 上传配准 → 加入聊天附件
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import Icon from '@/shared/ui/Icon.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  bbox: { type: Array, default: null },
  releases: { type: Array, default: () => [] },
  releasesLoading: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'cancel'])

const t1Id = ref('')
const t2Id = ref('')

const bboxText = computed(() => (
  props.bbox ? props.bbox.map((v) => Number(v).toFixed(4)).join(', ') : '未选择'
))
const t1Release = computed(() => props.releases.find((item) => item.id === t1Id.value) || null)
const t2Release = computed(() => props.releases.find((item) => item.id === t2Id.value) || null)

function pickDefaults() {
  const list = props.releases
  if (!list.length) return
  const exists = (id) => list.some((item) => item.id === id)
  const targetYear = Number(list[0].date.slice(0, 4)) - 5
  const older = list.find((item) => Number(item.date.slice(0, 4)) === targetYear)
    || list[list.length - 1]
    || list[0]
  // T1 必须是较早时相，T2 是较新时相；默认 T1≈5 年前、T2=最新
  if (!exists(t1Id.value)) t1Id.value = older.id
  if (!exists(t2Id.value)) t2Id.value = list[0].id
}

watch(
  [() => props.modelValue, () => props.releases],
  () => {
    if (props.modelValue) pickDefaults()
  },
  { immediate: true },
)

function cancel() {
  emit('update:modelValue', false)
  emit('cancel')
}

function confirm() {
  if (!t1Release.value || !t2Release.value) {
    ElMessage.warning('请选择 T1、T2 两个 Esri 时相')
    return
  }
  if (t1Release.value.date >= t2Release.value.date) {
    ElMessage.warning('T1 必须早于 T2')
    return
  }
  // 用 window 事件保证到达地图面板：el-dialog 根节点会吞掉同名组件事件
  window.dispatchEvent(new CustomEvent('rsiis:change-submit', {
    detail: { t1: t1Id.value, t2: t2Id.value },
  }))
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    width="440px"
    top="12vh"
    class="change-dialog"
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    append-to-body
  >
    <template #header>
      <div class="change-head">
        <div class="change-title">
          <Icon name="calendar" :size="17" />
          <span>变化检测 · 选择时相</span>
        </div>
      </div>
    </template>

    <div class="change-body">
      <div class="change-bbox">
        <span class="change-bbox-label">范围</span>
        <span class="change-bbox-value mono">{{ bboxText }}</span>
      </div>

      <label class="change-field">
        <span>T1 · 前一时相</span>
        <el-select
          v-model="t1Id"
          size="small"
          filterable
          :loading="releasesLoading"
          placeholder="选择 Esri 历史版本"
          class="change-control"
          :disabled="loading || releasesLoading"
        >
          <el-option
            v-for="release in releases"
            :key="release.id"
            :label="release.date"
            :value="release.id"
          />
        </el-select>
      </label>
      <label class="change-field">
        <span>T2 · 后一时相</span>
        <el-select
          v-model="t2Id"
          size="small"
          filterable
          :loading="releasesLoading"
          placeholder="选择 Esri 历史版本"
          class="change-control"
          :disabled="loading || releasesLoading"
        >
          <el-option
            v-for="release in releases"
            :key="release.id"
            :label="release.date"
            :value="release.id"
          />
        </el-select>
      </label>

      <div class="change-note">
        使用 Esri Wayback 高分辨率影像（浏览器拼接原始瓦片）；日期为 Esri 发布版本日期，
        两期影像会按同一 AOI/同一像素网格裁剪后再送入分割模型。
      </div>
      <div v-if="error" class="change-error">{{ error }}</div>
      <div v-if="loading" class="change-loading">
        <span class="change-spinner"></span>
        正在拼接并上传两期 Esri 影像，通常需要 10–40 秒…
      </div>
    </div>

    <template #footer>
      <div class="change-actions">
        <button type="button" class="change-ghost" :disabled="loading" @click="cancel">取消</button>
        <button type="button" class="change-primary" :disabled="loading" @click="confirm">
          {{ loading ? '取图中…' : '获取两期影像' }}
        </button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.change-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
}
.change-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-hero);
  font-weight: 600;
  color: var(--text-primary);
}
.change-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: var(--bg-panel);
}
.change-bbox {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: var(--bg-inset);
  border-radius: var(--r-sm);
}
.change-bbox-label {
  flex-shrink: 0;
  color: var(--text-muted);
}
.change-bbox-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.change-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
}
.change-control {
  width: 100%;
}
.change-note {
  padding: 8px 9px;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--text-muted);
  background: var(--bg-inset);
  border-radius: var(--r-sm);
}
.change-error {
  padding: 7px 9px;
  font-size: var(--fs-sm);
  line-height: 1.5;
  color: var(--accent-red);
  background: #fdf1f1;
  border-radius: var(--r-sm);
}
.change-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-sm);
  color: var(--accent);
}
.change-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--accent-soft);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: change-spin 0.8s linear infinite;
}
@keyframes change-spin {
  to { transform: rotate(360deg); }
}
.change-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 16px 16px;
  background: var(--bg-panel);
}
.change-ghost,
.change-primary {
  height: 32px;
  padding: 0 16px;
  font-size: var(--fs-md);
  border-radius: var(--r-md);
  cursor: pointer;
}
.change-ghost {
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
}
.change-ghost:hover {
  color: var(--text-primary);
  border-color: var(--border-strong);
}
.change-primary {
  color: #fff;
  background: var(--accent);
  border: none;
}
.change-primary:hover {
  background: var(--accent-hover);
}
.change-ghost:disabled,
.change-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.change-dialog :deep(.el-dialog__header) {
  padding: 0;
  margin: 0;
}
.change-dialog :deep(.el-dialog__body) {
  padding: 0;
}
.change-dialog :deep(.el-dialog__footer) {
  padding: 0;
}
.change-dialog :deep(.el-input__wrapper),
.change-dialog :deep(.el-select__wrapper) {
  min-height: 30px;
}
</style>

<style>
/* 弹窗外壳 Teleport 到 body，需全局选择器（scoped 无法命中 el-dialog 根节点） */
.change-dialog.el-dialog {
  padding: 0;
  border-radius: 14px;
  overflow: hidden;
  background: var(--bg-panel);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18);
}
.change-dialog .el-dialog__header {
  padding: 0;
  margin: 0;
}
.change-dialog .el-dialog__body {
  padding: 0;
}
.change-dialog .el-dialog__footer {
  padding: 0;
}
</style>
