// ==================== 样本制作会话状态 ====================
// 模块级单例：MapPanel（地图交互）与 SamplePanel（实例列表）共用同一份会话状态。
import { computed, ref } from 'vue'
import { toLonLat } from 'ol/proj'
import {
  CUSTOM_CLASS_COLORS,
  SAMPLE_CLASSES,
  sampleExportUrl,
  saveSampleAnnotations,
} from '@/services/samples'

const active = ref(false)
const phaseDialogOpen = ref(false)
const project = ref('default')
const taskDir = ref('')
const sourceFrame = ref(null)
const frame = ref(null)
const rangeReady = ref(false)
const phases = ref([])
const classes = ref(SAMPLE_CLASSES.map((item) => ({ ...item, color: [...item.color] })))
const instances = ref([])
const selectedId = ref('')
const selectedIds = ref([])
const selectedVertex = ref(null)
const mode = ref('browse') // browse | draw | edit | select
const dirty = ref(false)
const saving = ref(false)
const status = ref('')
const savedAt = ref(0)

const selectedShape = computed(
  () => instances.value.find((item) => item.id === selectedId.value) || null,
)

function newId() {
  return `s${Date.now().toString(36)}${Math.floor(Math.random() * 90 + 10)}`
}

function classOf(classId) {
  return classes.value.find((item) => item.id === Number(classId))
    || classes.value[0]
    || SAMPLE_CLASSES[0]
}

// 入参: 类别名 | 方法: 不存在时按调色板新建类别 | 出参: 类别对象
function ensureClass(name) {
  const text = String(name || '').trim()
  if (!text) return classes.value[0] || SAMPLE_CLASSES[0]
  const found = classes.value.find((item) => item.name === text)
  if (found) return found
  const nextId = classes.value.reduce((max, item) => Math.max(max, item.id), 0) + 1
  const color = CUSTOM_CLASS_COLORS[classes.value.length % CUSTOM_CLASS_COLORS.length]
  const item = { id: nextId, name: text, color: [...color] }
  classes.value.push(item)
  return item
}

function classNameOf(shape) {
  return classOf(shape?.classId).name
}

function classColorHex(classId) {
  const [r, g, b] = classOf(classId).color || [0, 0, 0]
  return `#${[r, g, b].map((v) => Number(v).toString(16).padStart(2, '0')).join('')}`
}

function setClassColor(classId, hex) {
  const item = classes.value.find((entry) => entry.id === Number(classId))
  if (!item) return
  const text = String(hex || '').replace('#', '')
  if (!/^[0-9a-fA-F]{6}$/.test(text)) return
  item.color = [
    parseInt(text.slice(0, 2), 16),
    parseInt(text.slice(2, 4), 16),
    parseInt(text.slice(4, 6), 16),
  ]
  dirty.value = true
}

function changeClassName(id, name) {
  const shape = instances.value.find((item) => item.id === id)
  const text = String(name || '').trim()
  if (!shape || !text) return
  const item = ensureClass(text)
  shape.classId = item.id
  shape.edited = true
  dirty.value = true
}

// 入参: 无 | 方法: 开始新会话，记录整张抓图帧，等待红框确定工作范围 | 出参: 无
function beginSession({ projectId, taskName, frameData, phaseList }) {
  project.value = projectId || 'default'
  taskDir.value = taskName
  sourceFrame.value = frameData
  frame.value = null
  rangeReady.value = false
  phases.value = phaseList || []
  classes.value = SAMPLE_CLASSES.map((item) => ({ ...item, color: [...item.color] }))
  instances.value = []
  selectedId.value = ''
  selectedIds.value = []
  selectedVertex.value = null
  mode.value = 'range'
  dirty.value = false
  savedAt.value = 0
  status.value = ''
  active.value = true
}

// 入参: 红框换算后的工作帧 | 方法: 把参考帧切到红框范围，开始标注 | 出参: 无
function setWorkFrame(workFrame) {
  if (!workFrame) return
  frame.value = {
    ...(sourceFrame.value || {}),
    ...workFrame,
  }
  rangeReady.value = true
  mode.value = 'draw'
}

// 入参: 无 | 方法: 清空工作帧，回到红框选择状态（重选范围时使用） | 出参: 无
function beginRange() {
  frame.value = null
  rangeReady.value = false
  mode.value = 'range'
}

// 入参: 无 | 方法: 清空实例（重选工作范围时使用） | 出参: 无
function clearInstances() {
  instances.value = []
  selectedId.value = ''
  selectedIds.value = []
  selectedVertex.value = null
  dirty.value = false
  status.value = ''
}

// 入参: 无 | 方法: 清空会话状态（地图图层由 useSampleScene 负责移除） | 出参: 无
function resetSession() {
  active.value = false
  phaseDialogOpen.value = false
  sourceFrame.value = null
  frame.value = null
  rangeReady.value = false
  phases.value = []
  instances.value = []
  selectedId.value = ''
  selectedIds.value = []
  selectedVertex.value = null
  mode.value = 'browse'
  dirty.value = false
  saving.value = false
  status.value = ''
  savedAt.value = 0
}

// ==================== 像素 ↔ 地图坐标 ====================

// 入参: 影像像素坐标 | 方法: 按 ImageStatic 的 3857 extent 线性换算 | 出参: [mercX, mercY]
function pixelToCoord(x, y) {
  const current = frame.value
  if (!current) return [0, 0]
  const [minx, miny, maxx, maxy] = current.extent
  return [
    minx + (x / current.width) * (maxx - minx),
    maxy - (y / current.height) * (maxy - miny),
  ]
}

// 入参: 地图坐标 | 方法: 3857 extent → 影像像素 | 出参: [x, y]
function coordToPixel(coordinate) {
  const current = frame.value
  if (!current) return [0, 0]
  const [minx, miny, maxx, maxy] = current.extent
  const [cx, cy] = coordinate
  return [
    ((cx - minx) / (maxx - minx)) * current.width,
    ((maxy - cy) / (maxy - miny)) * current.height,
  ]
}

// 入参: 地图坐标 | 方法: 3857 → 4326 | 出参: [lon, lat]
function coordToLonLat(coordinate) {
  return toLonLat(coordinate)
}

function polygonArea(points) {
  let total = 0
  for (let index = 0; index < points.length; index += 1) {
    const [x1, y1] = points[index]
    const [x2, y2] = points[(index + 1) % points.length]
    total += x1 * y2 - x2 * y1
  }
  return Math.abs(total) / 2
}

function polygonBBox(points) {
  const xs = points.map((point) => point[0])
  const ys = points.map((point) => point[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}

// ==================== 实例操作 ====================

// 入参: 影像像素多边形 | 方法: 新建实例并选中；默认类别 building | 出参: 新实例
function addShape(points) {
  const shape = {
    id: newId(),
    classId: (classes.value[0] || SAMPLE_CLASSES[0]).id,
    points: points.map(([x, y]) => [Math.round(x * 10) / 10, Math.round(y * 10) / 10]),
    score: null,
    source: 'manual',
    edited: true,
  }
  instances.value.push(shape)
  selectShape(shape.id)
  dirty.value = true
  return shape
}

// 入参: id | 方法: 单选实例 | 出参: 无
function selectShape(id) {
  selectedId.value = id || ''
  selectedIds.value = []
  selectedVertex.value = null
}

// 入参: ids | 方法: 框选多选 | 出参: 无
function selectMany(ids) {
  selectedIds.value = [...ids]
  selectedId.value = ids.length === 1 ? ids[0] : ''
  selectedVertex.value = null
}

// 入参: id | 方法: 删除单个实例 | 出参: 无
function removeShape(id) {
  instances.value = instances.value.filter((item) => item.id !== id)
  if (selectedId.value === id) selectedId.value = ''
  selectedIds.value = selectedIds.value.filter((item) => item !== id)
  if (selectedVertex.value?.shapeId === id) selectedVertex.value = null
  dirty.value = true
}

// 入参: 无 | 方法: 删除当前单选或多选实例 | 出参: 删除数量
function removeSelected() {
  const ids = new Set(selectedIds.value)
  if (selectedId.value) ids.add(selectedId.value)
  if (!ids.size) return 0
  instances.value = instances.value.filter((item) => !ids.has(item.id))
  selectedId.value = ''
  selectedIds.value = []
  selectedVertex.value = null
  dirty.value = true
  return ids.size
}

// 入参: id, classId | 方法: 改类别 | 出参: 无
function changeClass(id, classId) {
  const shape = instances.value.find((item) => item.id === id)
  if (!shape) return
  shape.classId = Number(classId)
  shape.edited = true
  dirty.value = true
}

function markDirty() {
  dirty.value = true
}

// ==================== 保存 / 导出 ====================

// 入参: 无 | 方法: 组装 annotations.json（像素 + 经纬度双坐标） | 出参: payload
function buildAnnotations() {
  const current = frame.value
  if (!current) return null
  return {
    version: 1,
    task: { name: taskDir.value, project: project.value },
    image: {
      path: current.path,
      width: current.width,
      height: current.height,
      bounds: current.bounds,
      crs: 'EPSG:4326',
      crop_px: current.cropPx || [0, 0, current.width, current.height],
      source_width: current.sourceWidth || current.width,
      source_height: current.sourceHeight || current.height,
      phases: phases.value.map((item) => ({ date: item.date, path: item.path })),
    },
    classes: classes.value.map((item) => ({ ...item, color: [...item.color] })),
    instances: instances.value.map((shape) => {
      const cls = classOf(shape.classId)
      return {
        id: shape.id,
        label: cls.name,
        class_id: cls.id,
        class_name: cls.name,
        score: shape.score,
        source: shape.source,
        shape_type: 'polygon',
        points: shape.points,
        coordinates: shape.points.map(([x, y]) => {
          const [lon, lat] = coordToLonLat(pixelToCoord(x, y))
          return [Math.round(lon * 1e8) / 1e8, Math.round(lat * 1e8) / 1e8]
        }),
        bbox_px: polygonBBox(shape.points).map((value) => Math.round(value * 10) / 10),
        area_px: Math.round(polygonArea(shape.points)),
        edited: Boolean(shape.edited),
      }
    }),
  }
}

// 入参: 无 | 方法: 保存到后端任务目录 | 出参: { ok, error? }
async function save() {
  if (saving.value) return { ok: false, error: '正在保存' }
  const payload = buildAnnotations()
  if (!payload) return { ok: false, error: '影像未就绪' }
  if (!taskDir.value) return { ok: false, error: '任务名不能为空' }
  saving.value = true
  status.value = '正在保存…'
  try {
    const result = await saveSampleAnnotations({
      project: project.value,
      task_dir: taskDir.value,
      annotations: payload,
      source_images: phases.value.map((item) => item.path),
    })
    dirty.value = false
    savedAt.value = Date.now()
    status.value = `已保存 ${result.instance_count} 个标注`
    return { ok: true, result }
  } catch (error) {
    status.value = error.message || '保存失败'
    return { ok: false, error: status.value }
  } finally {
    saving.value = false
  }
}

// 入参: 'coco' | 'shp' | 方法: 生成下载地址；未保存时先返回空 | 出参: URL
function exportUrl(format) {
  if (!savedAt.value) return ''
  return sampleExportUrl({
    project: project.value,
    taskDir: taskDir.value,
    format,
  })
}

export function useSampleStudio() {
  return {
    active,
    phaseDialogOpen,
    project,
    taskDir,
    sourceFrame,
    frame,
    rangeReady,
    phases,
    classes,
    instances,
    selectedId,
    selectedIds,
    selectedVertex,
    selectedShape,
    mode,
    dirty,
    saving,
    status,
    savedAt,
    beginSession,
    resetSession,
    beginRange,
    setWorkFrame,
    clearInstances,
    addShape,
    selectShape,
    selectMany,
    removeShape,
    removeSelected,
    changeClass,
    changeClassName,
    ensureClass,
    classNameOf,
    classColorHex,
    setClassColor,
    markDirty,
    buildAnnotations,
    save,
    exportUrl,
    newId,
    classOf,
    pixelToCoord,
    coordToPixel,
    coordToLonLat,
    polygonArea,
    polygonBBox,
  }
}
