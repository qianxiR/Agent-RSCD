<script setup>
// ==================== 样本制作：实例列表与保存导出 ====================
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import Icon from '@/shared/ui/Icon.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import { useSampleStudio } from '@/composables/useSampleStudio'
import { useSampleEditor } from '@/composables/useSampleEditor'

defineEmits(['exit'])

// 折叠状态：标注时收起面板腾出地图视野，工具条上仍可切换绘制/顶点/框选
const collapsed = ref(false)

const {
  taskDir,
  project,
  sourceFrame,
  frame,
  phases,
  classes,
  instances,
  selectedId,
  selectedIds,
  mode,
  dirty,
  saving,
  status,
  savedAt,
  polygonArea,
  selectShape,
  removeShape,
  removeSelected,
  changeClassName,
  classNameOf,
  classColorHex,
  setClassColor,
  save,
  exportUrl,
} = useSampleStudio()
const { setMode } = useSampleEditor()

const phaseText = computed(() => (
  phases.value.length
    ? phases.value.map((item) => item.date).join(' ↔ ')
    : '未载入'
))
// 分辨率：框选范围决定实际取到的瓦片层级，粗于 1 m/px 时提示
const coarse = computed(() => Number(frame.value?.gsd || 0) > 1)
const resolutionText = computed(() => {
  const current = frame.value
  if (!current?.gsd) return ''
  const gsd = Number(current.gsd)
  const text = `z${current.zoom} · ${gsd < 1 ? gsd.toFixed(2) : gsd.toFixed(1)} m/px`
  return coarse.value ? `${text} · 影像偏粗` : text
})
const canExport = computed(() => Boolean(savedAt.value) && !dirty.value)

function isSelected(id) {
  return id === selectedId.value || selectedIds.value.includes(id)
}

// 入参: shape, 输入事件 | 方法: 自由输入类别名，空值回退 | 出参: 无
function onClassNameChange(shape, event) {
  const text = String(event.target.value || '').trim()
  if (!text) {
    event.target.value = classNameOf(shape)
    return
  }
  changeClassName(shape.id, text)
  event.target.value = classNameOf(shape)
}

// 入参: 模式 | 方法: 再次点击当前模式时回到浏览 | 出参: 无
function toggleMode(next) {
  setMode(mode.value === next ? 'browse' : next)
}

// 入参: 'coco'|'shp'|'dataset' | 方法: 让用户选择保存位置后写入导出文件 | 出参: 无
async function download(format) {
  const url = exportUrl(format)
  if (!url) {
    ElMessage.warning('请先保存标注')
    return
  }
  const name = taskDir.value || 'sample'
  const suggestedName = format === 'coco'
    ? `coco_${name}.json`
    : format === 'dataset'
      ? `${name}_dataset.zip`
      : `${name}_shp.zip`
  const types = format === 'coco'
    ? [{ description: 'JSON', accept: { 'application/json': ['.json'] } }]
    : [{ description: 'ZIP', accept: { 'application/zip': ['.zip'] } }]

  try {
    if (window.showSaveFilePicker) {
      const handle = await window.showSaveFilePicker({ suggestedName, types })
      const writable = await handle.createWritable()
      const response = await fetch(url)
      if (!response.ok) throw new Error(`导出失败 (${response.status})`)
      await writable.write(await response.blob())
      await writable.close()
      ElMessage.success('已导出到所选位置')
      return
    }
    const link = document.createElement('a')
    link.href = url
    link.download = suggestedName
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  } catch (error) {
    if (error.name === 'AbortError') return
    ElMessage.error(error.message || '导出失败')
  }
}

// 入参: 无 | 方法: 保存并提示结果 | 出参: 无
async function onSave() {
  const result = await save()
  if (result.ok) ElMessage.success(`已保存 ${result.result.instance_count} 个标注`)
  else ElMessage.error(result.error || '保存失败')
}

// 入参: 无 | 方法: 删除当前单选或多选 | 出参: 无
function onDeleteSelected() {
  const count = removeSelected()
  if (!count) ElMessage.info('请先选择标注')
}
</script>

<template>
  <div class="sample-panel" :class="{ collapsed }">
    <div class="sp-head">
      <div class="sp-title">
        <Icon name="audit" :size="14" />
        <span>样本制作</span>
        <span v-if="collapsed && frame" class="sp-title-count">{{ instances.length }}</span>
        <span v-if="collapsed && dirty" class="sp-title-dirty" title="有未保存修改">·</span>
      </div>
      <div class="sp-head-actions">
        <BaseButton
          variant="icon"
          size="sm"
          :title="collapsed ? '展开面板' : '折叠面板'"
          @click="collapsed = !collapsed"
        >
          <Icon :name="collapsed ? 'chevronRight' : 'chevronDown'" :size="14" />
        </BaseButton>
        <BaseButton variant="icon" size="sm" title="退出样本制作" @click="$emit('exit')">
          <Icon name="close" :size="14" />
        </BaseButton>
      </div>
    </div>

    <template v-if="!collapsed">
      <div class="sp-info">
        <label class="sp-field">
          <span>任务名</span>
          <input v-model="taskDir" class="sp-input" :disabled="saving || Boolean(savedAt)" />
        </label>
      <div class="sp-meta">
        <span>项目 {{ project }}</span>
        <span v-if="frame">{{ frame.width }} × {{ frame.height }} px</span>
        <span v-else-if="sourceFrame">{{ sourceFrame.width }} × {{ sourceFrame.height }} px</span>
      </div>
      <div v-if="resolutionText" class="sp-resolution" :class="{ coarse: coarse }">
        {{ resolutionText }}
      </div>
      <div class="sp-phase">{{ phaseText }}</div>
      </div>

      <div v-if="frame" class="sp-modes">
        <button
          type="button"
          :class="{ active: mode === 'draw' }"
          @click="toggleMode('draw')"
        >绘制</button>
        <button
          type="button"
          :class="{ active: mode === 'edit' }"
          @click="toggleMode('edit')"
        >顶点</button>
        <button
          type="button"
          :class="{ active: mode === 'select' }"
          @click="toggleMode('select')"
        >框选</button>
        <button
          type="button"
          class="sp-delete"
          :disabled="!selectedId && !selectedIds.length"
          title="删除选中的标注"
          @click="onDeleteSelected"
        >
          <Icon name="trash" :size="13" />
        </button>
      </div>

      <div v-if="frame" class="sp-count">
        标注 <span class="sp-num">{{ instances.length }}</span>
        <span v-if="dirty" class="sp-dirty">未保存</span>
      </div>

      <div v-if="frame" class="sp-list">
        <div v-if="!instances.length" class="sp-empty">尚未绘制标注</div>
        <div
          v-for="shape in instances"
          :key="shape.id"
          class="sp-item"
          :class="{ sel: isSelected(shape.id) }"
          @click="selectShape(shape.id)"
        >
          <div class="sp-item-top">
            <input
              type="color"
              class="sp-color"
              :value="classColorHex(shape.classId)"
              title="选择类别颜色"
              @click.stop
              @input="setClassColor(shape.classId, $event.target.value)"
            />
            <input
              class="sp-class"
              :value="classNameOf(shape)"
              list="sp-class-options"
              placeholder="输入类别"
              @click.stop
              @change="onClassNameChange(shape, $event)"
            />
            <span class="sp-area">{{ Math.round(polygonArea(shape.points)) }} px</span>
            <BaseButton
              variant="icon"
              size="sm"
              title="删除标注"
              @click.stop="removeShape(shape.id)"
            >
              <Icon name="trash" :size="13" />
            </BaseButton>
          </div>
          <div class="sp-item-sub">{{ shape.points.length }} 个顶点</div>
        </div>
        <datalist id="sp-class-options">
          <option v-for="item in classes" :key="item.id" :value="item.name" />
        </datalist>
      </div>

      <div v-if="frame" class="sp-status">{{ status || '左键绘制；F2/Enter 闭合；Shift 点顶点删除；框选后 Delete 批删' }}</div>

      <div v-if="frame" class="sp-actions">
        <BaseButton variant="primary" bold :disabled="saving || !frame" @click="onSave">
          {{ saving ? '保存中…' : '保存标注' }}
        </BaseButton>
        <BaseButton variant="ghost" :disabled="!canExport" @click="download('dataset')">
          导出数据集
        </BaseButton>
      </div>
      <div v-if="frame" class="sp-actions sp-actions-sub">
        <BaseButton variant="ghost" :disabled="!canExport" @click="download('coco')">COCO</BaseButton>
        <BaseButton variant="ghost" :disabled="!canExport" @click="download('shp')">SHP</BaseButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sample-panel {
  position: absolute;
  top: 44px;
  left: 10px;
  z-index: 26;
  width: 320px;
  max-height: calc(100% - 58px);
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-float);
}
/* 折叠后只留标题行，宽度收窄，不遮挡地图 */
.sample-panel.collapsed {
  width: auto;
  padding: 6px 8px;
  gap: 0;
}
.sp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.sp-head-actions { display: flex; align-items: center; gap: 2px; }
.sp-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text-primary);
}
.sample-panel.collapsed .sp-title { font-size: var(--fs-md); }
.sp-title-count {
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.sp-title-dirty {
  font-size: var(--fs-sm);
  line-height: 1;
  color: var(--accent-amber);
}
.sp-info { display: flex; flex-direction: column; gap: 5px; }
.sp-field { display: flex; flex-direction: column; gap: 4px; font-size: var(--fs-sm); color: var(--text-secondary); }
.sp-input {
  width: 100%;
  box-sizing: border-box;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg-inset);
  color: var(--text-primary);
  font-family: inherit;
  font-size: var(--fs-md);
  outline: none;
}
.sp-meta {
  display: flex;
  justify-content: space-between;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.sp-phase { font-size: var(--fs-sm); color: var(--text-secondary); }
.sp-resolution {
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.sp-resolution.coarse { color: var(--accent-amber); }

.sp-modes { display: flex; gap: 4px; }
.sp-modes button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 26px;
  padding: 0 9px;
  font-family: inherit;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  cursor: pointer;
}
.sp-modes button:hover { background: var(--bg-hover); color: var(--text-primary); }
.sp-modes button.active { background: var(--bg-hover); color: var(--text-primary); border-color: var(--border-strong); }
.sp-modes .sp-delete { margin-left: auto; padding: 0 7px; }
.sp-modes .sp-delete:disabled { opacity: 0.45; cursor: default; }

.sp-count {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.sp-num { color: var(--text-primary); font-variant-numeric: tabular-nums; }
.sp-dirty { margin-left: auto; color: var(--accent-amber); }

.sp-list {
  flex: 1;
  min-height: 80px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.sp-empty {
  padding: 14px 0;
  text-align: center;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.sp-item {
  padding: 6px 7px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg-base);
  cursor: pointer;
}
.sp-item.sel { background: #ececec; border-color: var(--border-strong); }
.sp-item-top { display: flex; align-items: center; gap: 6px; }
.sp-color {
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--r-xs);
  background: var(--bg-panel);
  cursor: pointer;
}
.sp-color::-webkit-color-swatch-wrapper { padding: 1px; }
.sp-color::-webkit-color-swatch { border: 0; border-radius: 2px; }
.sp-class {
  min-width: 0;
  flex: 1;
  height: 24px;
  padding: 0 5px;
  font-family: inherit;
  font-size: var(--fs-sm);
  color: var(--text-primary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-xs);
  outline: none;
}
.sp-class:focus {
  border-color: var(--border-strong);
  box-shadow: 0 0 0 2px var(--bg-inset);
}
.sp-area { margin-left: auto; font-size: var(--fs-sm); color: var(--text-muted); font-variant-numeric: tabular-nums; }
.sp-item-sub { margin-top: 3px; font-size: var(--fs-sm); color: var(--text-muted); }

.sp-status {
  min-height: 28px;
  padding: 5px 7px;
  border-radius: var(--r-sm);
  background: var(--bg-inset);
  color: var(--text-muted);
  font-size: var(--fs-sm);
  line-height: 1.5;
}
.sp-actions { display: flex; gap: 6px; }
.sp-actions > :first-child { flex: 1; }
.sp-actions-sub > * { flex: 1; }
</style>
