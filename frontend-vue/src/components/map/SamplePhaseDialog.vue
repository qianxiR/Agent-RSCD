<script setup>
// ==================== 样本制作：进入前选择时相 ====================
// 单期：抓该期影像直接标注；双期：抓同期影像并进入卷帘，分隔线两侧都可用标注工具。
import { computed, ref, watch } from 'vue'
import Icon from '@/shared/ui/Icon.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseDialog from '@/shared/ui/BaseDialog.vue'
import { useProjectStore } from '@/stores/project'
import { useSampleStudio } from '@/composables/useSampleStudio'
import { planWaybackResolution } from '@/composables/useWaybackCapture'
import { listSampleTasks } from '@/services/samples'

const props = defineProps({
  releases: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  bbox: { type: Array, default: null },
})

const emit = defineEmits(['confirm', 'request-region', 'restore'])
const open = defineModel({ type: Boolean, default: false })

const projectStore = useProjectStore()
const { taskDir } = useSampleStudio()

const phaseAId = ref('')
const phaseBId = ref('')
const taskName = ref('')
const error = ref('')
// new: 新建任务（抓图）；restore: 打开历史任务（直接读回标注）
const mode = ref('new')
const selectedTask = ref('')
const history = ref([])
const historyLoading = ref(false)

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

// 按当前范围预测抓图会落到哪一级瓦片：Wayback 最高 z19，范围越大层级越低
const resolution = computed(() => {
  if (!props.bbox?.length) return null
  try {
    return planWaybackResolution(props.bbox)
  } catch (e) {
    return null
  }
})
const resolutionText = computed(() => {
  if (!resolution.value) return ''
  const { zoom, gsd } = resolution.value
  const value = `z${zoom} · ${gsd < 1 ? gsd.toFixed(2) : gsd.toFixed(1)} m/px`
  return gsd > 1 ? `${value} · 影像偏粗，建议缩小范围` : value
})
const resolutionCoarse = computed(() => Number(resolution.value?.gsd || 0) > 1)

function defaultTaskName() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `sample_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
    + `_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

// 入参: 无 | 方法: 拉取当前项目的历史任务 | 出参: 无
async function loadHistory() {
  historyLoading.value = true
  try {
    const data = await listSampleTasks(projectId.value)
    history.value = (data.tasks || []).filter((item) => item.restorable)
  } catch (e) {
    history.value = []
  } finally {
    historyLoading.value = false
  }
}

// 入参: 无 | 方法: 切到新建任务：清空选择并给一个新任务名 | 出参: 无
function startNewTask() {
  mode.value = 'new'
  selectedTask.value = ''
  taskName.value = defaultTaskName()
  error.value = ''
}

// 入参: 任务名 | 方法: 选中历史任务后改为读回模式，时相与范围由任务自身决定 | 出参: 无
function pickHistory(name) {
  if (!name) {
    startNewTask()
    return
  }
  mode.value = 'restore'
  selectedTask.value = name
  taskName.value = name
  error.value = ''
}

// 入参: 历史任务 | 方法: 下拉项副标题（实例数 / 时相 / 保存时间） | 出参: 字符串
function historyMeta(item) {
  const parts = []
  if (item.instance_count) parts.push(`${item.instance_count} 个标注`)
  if (item.phase_dates?.length) parts.push(item.phase_dates.filter(Boolean).join(' ↔ '))
  if (item.saved_at) parts.push(item.saved_at.slice(5, 16))
  return parts.join(' · ')
}

// 入参: 无 | 方法: 打开时补齐默认时相与任务名；A 取最早一期、B 取最新一期 | 出参: 无
function reset() {
  error.value = ''
  loadHistory()
  if (!props.releases.length) return
  const exists = (id) => props.releases.some((item) => item.id === id)
  // releases 按日期倒序（最新在前）：B 用最新一期，A 用最早一期，默认跨度最大
  const latest = props.releases[0]
  const oldest = props.releases[props.releases.length - 1]
  // 只有一期时退回单期，避免 A/B 撞成同一期
  if (!exists(phaseBId.value)) phaseBId.value = oldest === latest ? '' : latest.id
  if (!exists(phaseAId.value)) phaseAId.value = oldest.id
  if (mode.value === 'new' && !taskName.value) taskName.value = defaultTaskName()
}

watch(open, (value) => {
  if (value) reset()
})

watch(() => props.releases.length, () => {
  if (open.value) reset()
})

// 入参: 无 | 方法: 校验并提交：恢复历史任务直接读回，新建任务按所选时相抓图 | 出参: 无
function submit() {
  if (mode.value === 'restore') {
    if (!selectedTask.value) {
      error.value = '请选择历史任务'
      return
    }
    taskDir.value = selectedTask.value
    error.value = ''
    emit('restore', { taskName: selectedTask.value, projectId: projectId.value })
    return
  }
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
  if (history.value.some((item) => item.name === name)) {
    error.value = `任务名 ${name} 已存在，请在下拉框选它或点加号新建`
    return
  }
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
        <div class="sp-label">任务</div>
        <div class="sp-task-row">
          <el-select
            v-model="selectedTask"
            class="sp-task-select"
            size="small"
            filterable
            :loading="historyLoading"
            placeholder="选择历史任务，或点右侧加号新建"
            @change="pickHistory"
          >
            <el-option v-if="!history.length" label="暂无历史任务" value="" disabled />
            <el-option
              v-for="item in history"
              :key="item.name"
              :label="item.name"
              :value="item.name"
            >
              <span class="sp-task-name">{{ item.name }}</span>
              <span class="sp-task-meta">{{ historyMeta(item) }}</span>
            </el-option>
          </el-select>
          <BaseButton
            variant="ghost"
            size="md"
            px="10"
            title="新建任务"
            @click="startNewTask"
          >
            <Icon name="plus" :size="14" />
          </BaseButton>
        </div>
      </div>

      <div v-if="mode === 'new'" class="sp-row">
        <div class="sp-label">任务名</div>
        <input v-model="taskName" class="sp-input" placeholder="sample_YYYYMMDD_HHMMSS" />
      </div>

      <div class="sp-meta">
        <span>项目：{{ projectLabel }}</span>
        <span>范围约 {{ areaKm2 < 0.01 ? areaKm2.toFixed(4) : areaKm2.toFixed(2) }} km²</span>
      </div>

      <template v-if="mode === 'new'">
        <div v-if="resolutionText" class="sp-resolution" :class="{ coarse: resolutionCoarse }">
          {{ resolutionText }}
        </div>
        <div class="sp-region">
          <span class="sp-region-label">样本区域</span>
          <button type="button" class="sp-region-btn" @click="$emit('request-region')">
            <Icon name="scan" :size="13" />
            <span>在地图上框选</span>
          </button>
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
          先点「在地图上框选」拖出样本区域，再选时相；范围过大时抓图会失败，建议放大地图后框选。<br />
          只选时相 A：抓取该期影像直接标注；同时选 B：两期影像按同一范围抓取并进入卷帘。
        </div>
      </template>

      <div v-else class="sp-hint">
        载入该任务已保存的影像与标注，退出后仍可再次从这里打开继续修改。
      </div>

      <div v-if="error" class="sp-error">{{ error }}</div>

      <div class="sp-actions">
        <BaseButton variant="ghost" @click="open = false">取消</BaseButton>
        <BaseButton variant="primary" bold :disabled="loading" @click="submit">
          {{ mode === 'restore' ? '打开任务' : '进入标注' }}
        </BaseButton>
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
.sp-task-row { display: flex; align-items: center; gap: 6px; }
.sp-task-select { flex: 1; min-width: 0; }
.sp-task-select :deep(.el-select__wrapper) { width: 100%; }
.sp-task-name { color: var(--text-primary); }
.sp-task-meta { margin-left: 10px; font-size: var(--fs-sm); color: var(--text-muted); }
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
.sp-resolution {
  margin-bottom: 6px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.sp-resolution.coarse { color: var(--accent-amber); }
.sp-region {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  margin-bottom: 6px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.sp-region-label { color: var(--text-secondary); }
/* 首次使用的主要动作：黑底实心 + 图标，避免被当成普通文字链接 */
.sp-region-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  font-family: inherit;
  font-size: var(--fs-sm);
  font-weight: 500;
  color: #fff;
  background: #1a1a1a;
  border: 1px solid #1a1a1a;
  border-radius: var(--r-sm);
  cursor: pointer;
  transition: background var(--t-fast) var(--ease-out);
}
.sp-region-btn:hover {
  color: #fff;
  background: #111110;
  border-color: #111110;
}
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
