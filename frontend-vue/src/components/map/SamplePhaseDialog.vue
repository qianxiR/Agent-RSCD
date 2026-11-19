<script setup>
// ==================== 样本制作：进入前选择时相 ====================
// 单期：抓该期影像直接标注；双期：抓同期影像并进入卷帘，分隔线两侧都可用标注工具。
import { computed, ref, watch } from 'vue'
import Icon from '@/shared/ui/Icon.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseDialog from '@/shared/ui/BaseDialog.vue'
import { useProjectStore } from '@/stores/project'
import { useSampleStudio } from '@/composables/useSampleStudio'

const props = defineProps({
  releases: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  bbox: { type: Array, default: null },
})

const emit = defineEmits(['confirm', 'request-region'])
const open = defineModel({ type: Boolean, default: false })

const projectStore = useProjectStore()
const { taskDir } = useSampleStudio()

const phaseAId = ref('')
const phaseBId = ref('')
const taskName = ref('')
const error = ref('')

const projectId = computed(() => projectStore.activeProjectId || 'default')
const projectLabel = computed(() => {
  const item = projectStore.projects.find((p) => String(p.id) === String(projectId.value))
  return item?.name || projectId.value
})
const bboxText = computed(() => (
  props.bbox?.length ? props.bbox.map((v) => Number(v).toFixed(5)).join(', ') : '当前视图范围不可用'
))
const areaKm2 = computed(() => {
  if (!props.bbox?.length) return 0
  const [minx, miny, maxx, maxy] = props.bbox
  const lat = (miny + maxy) / 2
  return (maxx - minx) * (maxy - miny) * 111.32 * 111.32 * Math.cos((lat * Math.PI) / 180)
})
const releaseA = computed(() => props.releases.find((item) => item.id === phaseAId.value) || null)
const releaseB = computed(() => props.releases.find((item) => item.id === phaseBId.value) || null)

function defaultTaskName() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `sample_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
    + `_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

// 入参: 无 | 方法: 打开时补齐默认时相与任务名 | 出参: 无
function reset() {
  error.value = ''
  if (!props.releases.length) return
  const exists = (id) => props.releases.some((item) => item.id === id)
  if (!exists(phaseAId.value)) phaseAId.value = props.releases[0].id
  if (!exists(phaseBId.value)) phaseBId.value = ''
  if (!taskName.value) taskName.value = defaultTaskName()
}

watch(open, (value) => {
  if (value) reset()
})

watch(() => props.releases.length, () => {
  if (open.value) reset()
})

// 入参: 无 | 方法: 校验并提交时相选择 | 出参: 无
function submit() {
  if (!releaseA.value) {
    error.value = '请选择时相 A'
    return
  }
  if (releaseB.value && releaseB.value.id === releaseA.value.id) {
    error.value = '时相 A 与 B 不能相同'
    return
  }
  if (!props.bbox?.length) {
    error.value = '当前视图范围不可用，请缩放地图后重试'
    return
  }
  const name = taskName.value.trim() || defaultTaskName()
  taskDir.value = name
  error.value = ''
  emit('confirm', {
    taskName: name,
    releaseA: releaseA.value,
    releaseB: releaseB.value,
    bbox: props.bbox,
    projectId: projectId.value,
  })
}
</script>

<template>
  <BaseDialog
    v-model="open"
    width="520px"
    align-center
    class="sample-phase-dialog"
    :show-close="false"
    :close-on-click-modal="false"
  >
    <template #header>
      <div class="sp-head">
        <div class="sp-title">
          <Icon name="audit" :size="17" />
          <span>样本制作</span>
        </div>
        <BaseButton variant="icon" muted title="关闭" @click="open = false">
          <Icon name="close" :size="16" />
        </BaseButton>
      </div>
    </template>

    <div class="sp-body">
      <div class="sp-row">
        <div class="sp-label">任务名</div>
        <input v-model="taskName" class="sp-input" placeholder="sample_YYYYMMDD_HHMMSS" />
      </div>

      <div class="sp-meta">
        <span>项目：{{ projectLabel }}</span>
        <span>范围约 {{ areaKm2 < 0.01 ? areaKm2.toFixed(4) : areaKm2.toFixed(2) }} km²</span>
      </div>
      <div class="sp-region">
        <span>样本区域</span>
        <button type="button" @click="$emit('request-region')">在地图上框选</button>
      </div>
      <div class="sp-bbox">{{ bboxText }}</div>

      <div class="sp-grid">
        <label class="sp-field">
          <span>时相 A</span>
          <el-select v-model="phaseAId" size="small" :loading="loading" placeholder="选择发布日期">
            <el-option
              v-for="item in releases"
              :key="item.id"
              :label="item.date"
              :value="item.id"
            />
          </el-select>
        </label>
        <label class="sp-field">
          <span>时相 B（可选）</span>
          <el-select v-model="phaseBId" size="small" :loading="loading" placeholder="不启用">
            <el-option label="不启用" value="" />
            <el-option
              v-for="item in releases"
              :key="item.id"
              :label="item.date"
              :value="item.id"
            />
          </el-select>
        </label>
      </div>

      <div class="sp-hint">
        先在地图上框选样本区域；范围过大时抓图会失败，建议放大地图后框选。<br />
        只选时相 A：抓取该期影像直接标注；同时选 B：两期影像按同一范围抓取并进入卷帘。<br />
        进入标注后，还可在影像上框选更小的红框工作范围。
      </div>

      <div v-if="error" class="sp-error">{{ error }}</div>

      <div class="sp-actions">
        <BaseButton variant="ghost" @click="open = false">取消</BaseButton>
        <BaseButton variant="primary" bold :disabled="loading" @click="submit">进入标注</BaseButton>
      </div>
    </div>
  </BaseDialog>
</template>

<style>
.sample-phase-dialog.el-dialog {
  padding: 0;
  border-radius: 16px;
  overflow: hidden;
}
.sample-phase-dialog .el-dialog__header { padding: 0; margin: 0; }
.sample-phase-dialog .el-dialog__body { padding: 0; }
</style>

<style scoped>
.sp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 36px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border);
}
.sp-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}
.sp-body { padding: 14px 18px 16px; }
.sp-row { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
.sp-label { font-size: var(--fs-md); font-weight: 600; color: var(--text-primary); }
.sp-input {
  width: 100%;
  box-sizing: border-box;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-panel);
  color: var(--text-primary);
  font-family: inherit;
  font-size: var(--fs-md);
  outline: none;
}
.sp-input:focus {
  border-color: var(--border-strong);
  box-shadow: 0 0 0 2px var(--bg-inset);
}
.sp-meta {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.sp-region {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.sp-region button {
  padding: 0;
  font-family: inherit;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: none;
  border: 0;
  border-bottom: 1px solid var(--border-strong);
  cursor: pointer;
}
.sp-region button:hover { color: var(--text-primary); }
.sp-bbox {
  margin-bottom: 12px;
  padding: 7px 9px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-inset);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  word-break: break-all;
}
.sp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.sp-field { display: flex; flex-direction: column; gap: 5px; font-size: var(--fs-sm); color: var(--text-secondary); }
.sp-field :deep(.el-select) { width: 100%; }
.sp-hint {
  margin-top: 10px;
  font-size: var(--fs-sm);
  line-height: 1.6;
  color: var(--text-muted);
}
.sp-error {
  margin-top: 8px;
  font-size: var(--fs-sm);
  color: var(--accent-red);
}
.sp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
</style>
