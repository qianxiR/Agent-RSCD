<script setup>
// ==================== 区域选择确认弹窗 ====================
// 流程：地图拖框 → 自动抓取 Esri 缩略图 → 用户用自然语言说明类别 → 发送给 Agent
// 类别不再在弹窗内固定选择，由 Agent 做意图识别并转换为英文类别/同义词。
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import Icon from '@/shared/ui/Icon.vue'
import { captureImagery } from '@/services/imagery'
import { useCaptureInterpret } from '@/composables/useCaptureInterpret'

const { dialogOpen, bbox, closeDialog } = useCaptureInterpret()

const targetGsd = ref(0.5)
const instruction = ref('')
const preview = ref(null)
const previewLoading = ref(false)
const previewError = ref('')
const loading = ref(false)
let previewSeq = 0

const gsdOptions = [
  { value: 0.3, label: '0.3m（最清晰）' },
  { value: 0.5, label: '0.5m（推荐）' },
  { value: 1, label: '1m' },
  { value: 2, label: '2m（更快）' },
]

const bboxText = computed(() => (
  bbox.value ? bbox.value.map((v) => Number(v).toFixed(5)).join(', ') : '未选择'
))
const areaDeg2 = computed(() => {
  if (!bbox.value?.length) return 0
  const [minx, miny, maxx, maxy] = bbox.value
  return Math.max(0, (maxx - minx) * (maxy - miny))
})
const areaKm2 = computed(() => {
  if (!bbox.value?.length) return 0
  const lat = (bbox.value[1] + bbox.value[3]) / 2
  return areaDeg2.value * 111.32 * 111.32 * Math.cos((lat * Math.PI) / 180)
})
const previewUrl = computed(() => preview.value?.preview_url || '')

// 入参: 无 | 方法: 按 bbox 以 2m 粗分辨率抓取一张用于预览的缩略图 | 出参: 无
async function loadPreview() {
  if (!dialogOpen.value || !bbox.value?.length) return
  const seq = ++previewSeq
  previewLoading.value = true
  previewError.value = ''
  preview.value = null
  try {
    const res = await captureImagery({
      source: 'esri',
      bbox: bbox.value,
      // 缩略图用较粗分辨率，避免大范围等 10s；最终提交再按用户选择精度抓图
      target_gsd: Math.max(2, targetGsd.value),
    })
    if (seq !== previewSeq) return
    if (res.status !== 'success') throw new Error(res.msg || '缩略图生成失败')
    preview.value = res
  } catch (e) {
    if (seq === previewSeq) {
      previewError.value = e.message || '缩略图生成失败'
    }
  } finally {
    if (seq === previewSeq) previewLoading.value = false
  }
}

// 打开弹窗或范围/分辨率变化时自动刷新缩略图
watch(
  () => [dialogOpen.value, bbox.value?.join(','), targetGsd.value],
  ([open]) => {
    if (open) {
      if (!instruction.value) instruction.value = ''
      loadPreview()
    }
  },
  { immediate: true },
)

// 入参: 无 | 方法: 按用户选定精度正式抓图，并把缩略图 + 原始描述送入当前会话 | 出参: 无
async function submit() {
  if (loading.value) return
  if (!bbox.value?.length) {
    ElMessage.warning('请先在地图上框选范围')
    return
  }
  const text = instruction.value.trim()
  if (!text) {
    ElMessage.warning('请描述要提取的类别，例如：建筑物、水体、植被')
    return
  }
  loading.value = true
  try {
    // 预览请求可能还在进行；等缩略图完成后再按用户精度正式抓图
    if (previewLoading.value) {
      while (previewLoading.value) await new Promise((r) => setTimeout(r, 120))
    }
    const res = await captureImagery({
      source: 'esri',
      bbox: bbox.value,
      target_gsd: targetGsd.value,
    })
    if (res.status !== 'success') throw new Error(res.msg || '取图失败')
    window.dispatchEvent(new CustomEvent('rsiis:capture-interpret', {
      detail: {
        image: {
          name: res.file_name,
          path: res.file_path,
          preview_url: res.preview_url,
          source: 'capture',
          intent: 'segment',
        },
        prompt: text,
        capture: res,
      },
    }))
    dialogOpen.value = false
    ElMessage.success(res.fallback ? 'Esri 暂不可达，已用天地图取图，正在送入解译…' : '已取图，正在送入当前会话解译…')
  } catch (e) {
    ElMessage.error(e.message || '取图失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="dialogOpen"
    width="560px"
    top="8vh"
    class="capture-dialog"
    :show-close="false"
    :close-on-click-modal="false"
    append-to-body
  >
    <template #header>
      <div class="cap-head">
        <div class="cap-title">
          <Icon name="map" :size="17" />
          <span>区域选择</span>
        </div>
        <button type="button" class="cap-icon-btn" title="关闭" @click="closeDialog">
          <Icon name="close" :size="16" />
        </button>
      </div>
    </template>

    <div class="cap-body">
      <!-- 范围缩略图：放在对话框最上方 -->
      <div class="cap-preview">
        <div v-if="previewLoading" class="cap-preview-empty">正在生成范围缩略图…</div>
        <img v-else-if="previewUrl" :src="previewUrl" class="cap-preview-img" alt="框选范围缩略图" />
        <div v-else class="cap-preview-empty">
          {{ previewError || '等待范围缩略图' }}
          <button v-if="previewError" type="button" class="cap-retry" @click="loadPreview">重试</button>
        </div>
        <div v-if="preview" class="cap-preview-meta">
          {{ preview.width }} × {{ preview.height }} px
          <span v-if="preview.fallback"> · 天地图兜底</span>
        </div>
      </div>

      <div class="cap-row cap-row-inline">
        <div class="cap-label">目标分辨率</div>
        <el-select v-model="targetGsd" size="small" style="width: 190px">
          <el-option
            v-for="item in gsdOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </div>

      <div class="cap-row">
        <div class="cap-label">告诉 Agent 要提取什么</div>
        <textarea
          v-model="instruction"
          class="cap-instruction"
          rows="3"
          placeholder="例如：提取建筑物和水体；或：找出所有温室大棚、光伏板"
        />
        <div class="cap-hint">
          Agent 会识别中文/自然语言类别，自动转换为英文类别 + 英文同义词，再传给 ProC-SAM3。
        </div>
      </div>

      <div class="cap-bbox mono">{{ bboxText }}</div>
      <div class="cap-note">
        范围约 {{ areaKm2 < 0.01 ? areaKm2.toFixed(4) : areaKm2.toFixed(2) }} km²
        （单次上限约 0.02°²）。默认使用 Esri 高清影像；取图后自动发布到图层管理并送入当前会话。
      </div>

      <div class="cap-actions">
        <button type="button" class="cap-ghost" :disabled="loading" @click="closeDialog">取消</button>
        <button type="button" class="cap-primary" :disabled="loading || previewLoading" @click="submit">
          {{ loading ? '发送中…' : '发送并解译' }}
        </button>
      </div>
    </div>
  </el-dialog>
</template>

<style>
.capture-dialog.el-dialog {
  padding: 0;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18);
}
.capture-dialog .el-dialog__header { padding: 0; margin: 0; }
.capture-dialog .el-dialog__body { padding: 0; }
</style>

<style scoped>
.cap-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border);
}
.cap-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}
.cap-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}
.cap-icon-btn:hover { background: var(--bg-hover); color: var(--text-primary); }

.cap-body { padding: 14px 18px 16px; }

.cap-preview {
  position: relative;
  min-height: 140px;
  margin-bottom: 12px;
  border-radius: 10px;
  overflow: hidden;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
}
.cap-preview-img {
  display: block;
  width: auto;
  max-width: 100%;
  max-height: 220px;
  margin: 0 auto;
  object-fit: contain;
  background: #fff;
}
.cap-preview-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: var(--fs-md);
}
.cap-preview-meta {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
}
.cap-retry {
  border: 1px solid var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 4px;
  padding: 2px 8px;
  cursor: pointer;
}

.cap-row { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.cap-row-inline {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
}
.cap-label { font-size: var(--fs-md); font-weight: 600; color: var(--text-primary); }
.cap-instruction {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  min-height: 68px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-panel);
  color: var(--text-primary);
  font-size: var(--fs-md);
  line-height: 1.55;
  outline: none;
}
.cap-instruction:focus { border-color: var(--accent); box-shadow: var(--shadow-focus); }
.cap-hint { font-size: var(--fs-sm); color: var(--text-muted); line-height: 1.5; }

.cap-bbox {
  margin-bottom: 8px;
  padding: 7px 9px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-inset);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  word-break: break-all;
}
.cap-note {
  margin-bottom: 14px;
  color: var(--text-muted);
  font-size: var(--fs-sm);
  line-height: 1.6;
}
.cap-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.cap-ghost,
.cap-primary {
  height: 32px;
  padding: 0 14px;
  border-radius: var(--r-md);
  font-size: var(--fs-md);
  cursor: pointer;
}
.cap-ghost {
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-secondary);
}
.cap-primary {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
}
.cap-primary:disabled,
.cap-ghost:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
