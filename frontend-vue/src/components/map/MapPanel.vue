<script setup>
// ==================== 地图面板（OpenLayers 容器）====================
// ★ 对应原生 map/wms-render.js 的 initWmsMap
//   样式对齐原生 .wms-panel / .wms-stage / .ol-map-host
import { onMounted, onBeforeUnmount, nextTick, useTemplateRef, watch, computed, ref } from 'vue'
import Map from 'ol/Map'
import View from 'ol/View'
import DragBox from 'ol/interaction/DragBox'
import { always } from 'ol/events/condition'
import { defaults as defaultControls, ScaleLine, MousePosition } from 'ol/control'
import { createStringXY } from 'ol/coordinate'
import { fromLonLat, transformExtent } from 'ol/proj'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  setMap, clearMap, loadDefaultLayers, setupFeatureInfo, getBasemapType,
  clearImageryLayers, listImageryLayers, getImageryVersion,
  addFootprintLayer, removeLayer, getViewBbox, setFeatureInfoEnabled,
} from '@/composables/useMap'
import { useImagerySearch } from '@/composables/useImagerySearch'
import { useCaptureInterpret } from '@/composables/useCaptureInterpret'
import { useSwipeCompare } from '@/composables/useSwipeCompare'
import { useSampleStudio } from '@/composables/useSampleStudio'
import { useSampleScene } from '@/composables/useSampleScene'
import { useSampleEditor } from '@/composables/useSampleEditor'
import { showRegionGrid, clearRegionGrid } from '@/composables/useRegionGrid'
import { restoreBasemap } from '@/composables/useBasemap'
import { captureWaybackImage } from '@/composables/useWaybackCapture'
import { captureImagery, georeferenceImagery, getEsriWaybackReleases } from '@/services/imagery'
import { loadSampleAnnotations } from '@/services/samples'
import LayerPanel from '@/components/map/LayerPanel.vue'
import ChangeDetectDialog from '@/components/map/ChangeDetectDialog.vue'
import SamplePhaseDialog from '@/components/map/SamplePhaseDialog.vue'
import SamplePanel from '@/components/map/SamplePanel.vue'
import Icon from '@/shared/ui/Icon.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import 'ol/ol.css'

const mapRef = useTemplateRef('mapRef')
const popupRef = useTemplateRef('popupRef')
let map = null
let resizeObserver = null

// 当前底图类型（固定 Esri，不再提供切换 UI）
const currentBasemap = getBasemapType()

// 入参: delta 缩放级数 | 方法: 右上角自定义放大/缩小 | 出参: 无
function zoomBy(delta) {
  const view = map?.getView()
  if (!view) return
  const current = view.getZoom()
  if (current == null) return
  const min = view.getMinZoom() ?? 0
  const max = view.getMaxZoom() ?? 28
  view.animate({
    zoom: Math.max(min, Math.min(max, current + delta)),
    duration: 180,
  })
}

// ==================== 地物提取：入口 + 框选范围（影像检索入口统一在左栏） ====================
const imagery = useImagerySearch()
const capture = useCaptureInterpret()
const {
  active: compareActive,
  leftDate: compareLeftDate,
  rightDate: compareRightDate,
  error: compareError,
  startSwipe: startCompareSwipe,
  stopSwipe: stopCompareSwipe,
  setRatio: setCompareRatio,
} = useSwipeCompare()
const layerPanelOpen = ref(false)
const comparePanelOpen = ref(false)
const waybackReleases = ref([])
const waybackLoading = ref(false)
const waybackError = ref('')
const waybackFallback = ref(false)
const compareLeftId = ref('')
const compareRightId = ref('')
const captureLoading = ref(false)
const changeDrawRequested = ref(false)
const changeDialogOpen = ref(false)
const changeDialogLoading = ref(false)
const changeDialogError = ref('')
const changeBBox = ref(null)
const drawMode = ref(null)
const pendingBBox = ref(null)
let dragBox = null
let compareDragging = false

// ==================== 样本制作 ====================
const {
  active: sampleActive,
  phaseDialogOpen: samplePhaseOpen,
  mode: sampleMode,
  dirty: sampleDirty,
  beginSession,
  restoreSession,
  resetSession,
} = useSampleStudio()
const {
  capturing: sampleCapturing,
  progress: sampleProgress,
  tilePercent: sampleTilePercent,
  prepareScene,
  loadStoredScene,
  exitScene,
} = useSampleScene()
const sampleEditor = useSampleEditor()
const sampleBBox = ref(null)
const sampleEntering = ref(false)

const drawHint = computed(() => {
  if (captureLoading.value) return '正在抓取 Esri 最高分辨率影像…'
  if (changeDialogLoading.value) return '正在获取两期影像…'
  if (pendingBBox.value) return '已选择区域 · Enter 确定 · Esc 取消'
  if (drawMode.value === 'change') return '拖动选择变化检测范围 · Esc 取消'
  if (drawMode.value === 'sample') return '拖动选择样本区域 · Esc 取消'
  return drawMode.value === 'capture'
    ? '拖动选择地物提取范围 · Esc 取消'
    : '拖动鼠标框选检索范围 · Esc 取消'
})

// 每年只保留最新一个版本，避免 197 个发布版本把下拉框撑得过长；卷帘对比按年选择即可
const compareReleaseOptions = computed(() => {
  const seenYears = new Set()
  return waybackReleases.value.filter((item) => {
    const year = item.date.slice(0, 4)
    if (seenYears.has(year)) return false
    seenYears.add(year)
    return true
  })
})
const compareLeftRelease = computed(
  () => compareReleaseOptions.value.find((item) => item.id === compareLeftId.value) || null,
)
const compareRightRelease = computed(
  () => compareReleaseOptions.value.find((item) => item.id === compareRightId.value) || null,
)
const canApplyCompare = computed(
  () => Boolean(compareLeftRelease.value && compareRightRelease.value)
    && compareLeftId.value !== compareRightId.value,
)

// 当前地图上的影像预览/轮廓数量（版本号触发重算）
const imageryCount = computed(() => {
  getImageryVersion().value
  // 同一景可能同时有快视图/瓦片两层，按景去重，避免显示“清除影像 2”
  const ids = new Set(listImageryLayers().map((layer) => String(layer.name).replace(/^thumb:/, '')))
  return ids.size
})

// 入参: 无 | 方法: 清除全部影像预览图层与轮廓 | 出参: 无
function clearImageryPreview() {
  clearImageryLayers()
  imagery.clearOverlaid()
  ElMessage.success('已清除影像预览')
}

// ==================== 变化检测：区域 + 两个时相 ====================
// 入参: 无 | 方法: 进入地物提取拖框，关闭其他左上角浮层 | 出参: 无
function startCaptureDraw() {
  layerPanelOpen.value = false
  comparePanelOpen.value = false
  changeDialogOpen.value = false
  capture.requestDraw()
}

// 入参: 无 | 方法: 进入变化检测拖框，关闭其他左上角浮层 | 出参: 无
function startChangeDraw() {
  layerPanelOpen.value = false
  comparePanelOpen.value = false
  changeDialogOpen.value = false
  changeDialogError.value = ''
  if (!waybackReleases.value.length) loadWaybackReleases()
  changeDrawRequested.value = true
}

// 入参: 无 | 方法: 取消时相选择，清除待选范围与虚线框 | 出参: 无
function cancelChangeDetect() {
  changeDialogOpen.value = false
  changeBBox.value = null
  changeDialogError.value = ''
  changeDialogLoading.value = false
  changeDrawRequested.value = false
  removeLayer('outline:capture-bbox', 'imagery-outline')
}

// 入参: { t1, t2 } 两个 Wayback 版本 ID | 方法: 弹窗确认入口（日志便于链路排查） | 出参: 无
function onChangeConfirm(payload) {
  runChangeCapture(payload)
}

// 入参: event window 事件 | 方法: 兜底接收弹窗提交（避免 el-dialog 根事件透传冲突） | 出参: 无
function onChangeSubmitEvent(event) {
  if (!event.detail) return
  onChangeConfirm(event.detail)
}

// 入参: { t1, t2 } 两个 Wayback 版本 ID | 方法: 浏览器拼接两期 Esri 高清图 → 配准 → 加入附件
async function runChangeCapture({ t1, t2 }) {
  if (!changeBBox.value || changeDialogLoading.value) return
  const left = compareReleaseOptions.value.find((item) => item.id === t1)
  const right = compareReleaseOptions.value.find((item) => item.id === t2)
  if (!left || !right) {
    changeDialogError.value = '请选择两个 Esri 时相'
    return
  }
  changeDialogLoading.value = true
  changeDialogError.value = ''
  try {
    const bbox = changeBBox.value
    const captureOf = async (release, phase) => {
      const capture = await captureWaybackImage({ release, bbox })
      const geo = await georeferenceImagery({
        blob: capture.blob,
        bbox: capture.bbox,
        name: `change_${phase}_${release.date}`,
        date: release.date,
      })
      return {
        name: geo.file_name,
        path: geo.file_path,
        preview_url: geo.preview_url,
        source: 'change',
        intent: 'change',
        phase,
        datetime: release.date,
        layer_name: geo.layer_name || '',
        zoom: capture.zoom,
      }
    }
    const first = await captureOf(left, 't1')
    const second = await captureOf(right, 't2')
    window.dispatchEvent(new CustomEvent('rsiis:change-capture', {
      detail: {
        images: [first, second],
        bbox,
        source: 'wayback',
        source_label: 'Esri Wayback',
        t1_date: left.date,
        t2_date: right.date,
      },
    }))
    changeDialogOpen.value = false
    changeBBox.value = null
  } catch (e) {
    changeDialogError.value = e.message || '两期 Esri 影像获取失败'
  } finally {
    changeDialogLoading.value = false
  }
}

// ==================== 卷帘对比（Esri Wayback）====================
// 入参: 无（读取 compareReleaseOptions） | 方法: 选择默认两个时相（最新版 + 约 5 年前） | 出参: 无
function pickCompareDefaults() {
  const options = compareReleaseOptions.value
  if (!options.length) return
  const exists = (id) => options.some((item) => item.id === id)
  if (!exists(compareLeftId.value)) compareLeftId.value = options[0].id
  if (exists(compareRightId.value)) return
  const targetYear = Number(options[0].date.slice(0, 4)) - 5
  const sameYear = options.find((item) => Number(item.date.slice(0, 4)) === targetYear)
  const target = new Date(`${options[0].date}T00:00:00Z`)
  target.setUTCFullYear(target.getUTCFullYear() - 5)
  const targetDate = target.toISOString().slice(0, 10)
  const older = sameYear
    || options.find((item) => item.date <= targetDate)
    || options[options.length - 1]
  compareRightId.value = older.id
}

// 入参: force 是否强制刷新 | 方法: 拉取 Wayback 版本清单并设置默认值 | 出参: 无
async function loadWaybackReleases(force = false) {
  if (waybackLoading.value) return
  if (!force && waybackReleases.value.length) return
  waybackLoading.value = true
  waybackError.value = ''
  try {
    const result = await getEsriWaybackReleases({ force })
    waybackReleases.value = result.releases
    waybackFallback.value = result.source === 'fallback'
    if (waybackFallback.value) waybackError.value = ''
    pickCompareDefaults()
  } catch (e) {
    waybackError.value = e.message || 'Wayback 版本清单加载失败'
  } finally {
    waybackLoading.value = false
  }
}

// 入参: 无 | 方法: 打开/关闭图层面板；两个左上角浮层互斥，避免重叠 | 出参: 无
function toggleLayerPanel() {
  layerPanelOpen.value = !layerPanelOpen.value
  if (layerPanelOpen.value) comparePanelOpen.value = false
}

// 入参: 无 | 方法: 样本制作入口，按当前视图范围选择时相 | 出参: 无
function onSampleClick() {
  if (sampleActive.value || sampleEntering.value) return
  layerPanelOpen.value = false
  comparePanelOpen.value = false
  changeDialogOpen.value = false
  if (compareActive.value) stopCompareSwipe()
  if (dragBox) stopDraw()
  if (pendingBBox.value) {
    pendingBBox.value = null
    removeLayer('outline:capture-bbox', 'imagery-outline')
  }
  capture.cancelDraw()
  imagery.drawRequested.value = false
  changeDrawRequested.value = false
  if (!waybackReleases.value.length) loadWaybackReleases()
  sampleBBox.value = getViewBbox()
  // 未框选时先用当前视图范围铺红框格网，框选后按新范围重画
  if (sampleBBox.value?.length) showRegionGrid(sampleBBox.value)
  samplePhaseOpen.value = true
}

// 入参: 无 | 方法: 关闭时相弹窗，回到地图框选样本区域；松开后带新范围重开弹窗 | 出参: 无
function startSampleRegionDraw() {
  if (!map) return
  samplePhaseOpen.value = false
  layerPanelOpen.value = false
  comparePanelOpen.value = false
  changeDialogOpen.value = false
  if (compareActive.value) stopCompareSwipe()
  if (dragBox) stopDraw()
  // 重新框选时先收掉旧范围的红框格网，避免与拖拽框叠在一起
  clearRegionGrid()
  startDraw('sample')
}

// 入参: { taskName, releaseA, releaseB, bbox, projectId }
// 方法: 抓取所选时相影像 → 铺图/卷帘 → 进入标注会话
// 出参: 无
async function onSampleConfirm(payload) {
  if (sampleEntering.value) return
  samplePhaseOpen.value = false
  sampleEntering.value = true
  try {
    const scene = await prepareScene({
      releaseA: payload.releaseA,
      releaseB: payload.releaseB,
      bbox: payload.bbox,
      taskName: payload.taskName,
    })
    beginSession({
      projectId: payload.projectId,
      taskName: payload.taskName,
      frameData: scene.frame,
      phaseList: scene.phases,
    })
    sampleEditor.enter(map)
    setFeatureInfoEnabled(false)
    if (popupRef.value) popupRef.value.innerHTML = ''
    sampleEditor.setMode('draw')
    ElMessage.success('影像已载入，可直接标注')
  } catch (e) {
    ElMessage.error(e.message || '影像抓取失败')
    samplePhaseOpen.value = true
  } finally {
    sampleEntering.value = false
  }
}

// 入参: { taskName, projectId }
// 方法: 打开历史任务：读回标注 → 用任务目录源影像铺图 → 进入标注会话
// 出参: 无
async function onSampleRestore(payload) {
  if (sampleEntering.value) return
  sampleEntering.value = true
  try {
    const data = await loadSampleAnnotations({
      project: payload.projectId,
      taskDir: payload.taskName,
    })
    const annotations = data.annotations || {}
    samplePhaseOpen.value = false
    loadStoredScene({
      phasePreviews: data.phase_previews,
      bounds: annotations.image?.bounds,
    })
    const count = restoreSession({
      projectId: payload.projectId,
      taskName: payload.taskName,
      annotations,
      previews: data.phase_previews,
    })
    sampleEditor.enter(map)
    setFeatureInfoEnabled(false)
    if (popupRef.value) popupRef.value.innerHTML = ''
    sampleEditor.setMode('draw')
    // 历史任务恢复后同样铺回红框格网，保持版面一致
    if (annotations.image?.bounds) showRegionGrid(annotations.image.bounds)
    ElMessage.success(`已打开历史任务，载入 ${count} 个标注`)
  } catch (e) {
    ElMessage.error(e.message || '打开历史任务失败')
    samplePhaseOpen.value = true
  } finally {
    sampleEntering.value = false
  }
}

// 入参: 模式 draw | edit | select | 方法: 切换标注模式，再次点击回到浏览 | 出参: 无
function toggleSampleMode(next) {
  if (!sampleActive.value) return
  sampleEditor.setMode(sampleMode.value === next ? 'browse' : next)
}

// 入参: 无 | 方法: 退出样本制作，未保存时二次确认 | 出参: 无
async function requestExitSample() {
  if (sampleDirty.value) {
    try {
      await ElMessageBox.confirm(
        '当前标注尚未保存，退出将丢失修改。',
        '退出样本制作',
        { confirmButtonText: '退出', cancelButtonText: '继续编辑', type: 'warning' },
      )
    } catch (e) {
      return
    }
  }
  sampleEditor.exit()
  exitScene()
  resetSession()
  clearRegionGrid()
  setFeatureInfoEnabled(true)
}

// 入参: 无 | 方法: 打开/关闭卷帘面板，首次打开时懒加载版本清单 | 出参: 无
function toggleComparePanel() {
  comparePanelOpen.value = !comparePanelOpen.value
  if (comparePanelOpen.value) {
    layerPanelOpen.value = false
    if (!waybackReleases.value.length) loadWaybackReleases()
  }
}

// 入参: { closePanel, notify } | 方法: 叠加左右时相；卷帘已开启时按新时相热更新并保留分隔线位置
// 出参: 无
function renderCompare({ closePanel = false, notify = false } = {}) {
  const left = compareLeftRelease.value
  const right = compareRightRelease.value
  if (!left || !right) {
    if (notify) ElMessage.warning('请选择两个时相')
    return
  }
  if (left.id === right.id) {
    if (notify) ElMessage.warning('左右时相不能相同')
    return
  }
  if (imageryCount.value) {
    clearImageryLayers()
    imagery.clearOverlaid()
  }
  const ok = startCompareSwipe({
    leftUrl: left.tileUrl,
    rightUrl: right.tileUrl,
    leftDate: left.date,
    rightDate: right.date,
  })
  if (!ok) {
    ElMessage.error('地图尚未就绪，无法开启卷帘')
    return
  }
  if (closePanel) comparePanelOpen.value = false
  if (notify) ElMessage.success(`卷帘：${left.date} ↔ ${right.date}`)
}

// 入参: 无 | 方法: 面板「开启/更新卷帘」按钮 | 出参: 无
function applyCompare() {
  renderCompare({ closePanel: true, notify: true })
}

// 入参: 无 | 方法: 退出卷帘对比并移除两个 Wayback 图层 | 出参: 无
function stopCompare() {
  stopCompareSwipe()
  comparePanelOpen.value = false
}

// 卷帘已开启时，改选时相立即热更新左右图层，不必再点「更新卷帘」
watch([compareLeftId, compareRightId], () => {
  if (!compareActive.value || !canApplyCompare.value) return
  renderCompare()
})

// 入参: pointerdown | 方法: 开始拖动卷帘分隔线 | 出参: 无
function onComparePointerDown(event) {
  if (!compareActive.value) return
  event.preventDefault()
  compareDragging = true
  window.addEventListener('pointermove', onComparePointerMove)
  window.addEventListener('pointerup', stopCompareDrag, { once: true })
  window.addEventListener('pointercancel', stopCompareDrag, { once: true })
}

// 入参: pointermove | 方法: 按指针在地图内的横向比例更新裁剪位置 | 出参: 无
function onComparePointerMove(event) {
  if (!compareDragging || !mapRef.value) return
  const rect = mapRef.value.getBoundingClientRect()
  if (!rect.width) return
  setCompareRatio((event.clientX - rect.left) / rect.width)
}

// 入参: 无 | 方法: 结束拖动并移除全局监听 | 出参: 无
function stopCompareDrag() {
  compareDragging = false
  window.removeEventListener('pointermove', onComparePointerMove)
  window.removeEventListener('pointerup', stopCompareDrag)
  window.removeEventListener('pointercancel', stopCompareDrag)
}

// 入参: bbox | 方法: 保留框选范围虚线框（新框替换旧框） | 出参: 无
function showCaptureBBox(bbox) {
  const [minx, miny, maxx, maxy] = bbox
  const geometry = {
    type: 'Polygon',
    coordinates: [[
      [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
    ]],
  }
  removeLayer('outline:capture-bbox', 'imagery-outline')
  addFootprintLayer({ name: 'capture-bbox', geometry, bbox })
}

// 入参: bbox 框选范围 | 方法: 直接按 Esri 最高分辨率抓图并加入当前会话附件（不弹窗） | 出参: 无
async function captureDirect(bbox) {
  captureLoading.value = true
  try {
    const res = await captureImagery({ source: 'esri', bbox, target_gsd: 0.3 })
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
        prompt: '',
        autoSend: false,
        capture: res,
      },
    }))
    if (res.fallback) {
      ElMessage.warning('Esri 暂不可达，已用天地图影像，请输入解译类别')
    }
  } catch (e) {
    ElMessage.error(e.message || '取图失败')
  } finally {
    captureLoading.value = false
    capture.cancelDraw()
  }
}

// 入参: mode 'imagery'|'capture'|'change'|'sample' | 方法: 进入拖拽框选，结束后按模式回传 bbox | 出参: 无
function startDraw(mode) {
  if (!map || dragBox) return
  // 重新发起地物提取时，丢弃上一次待确认的框
  if (pendingBBox.value) {
    pendingBBox.value = null
    removeLayer('outline:capture-bbox', 'imagery-outline')
  }
  drawMode.value = mode
  dragBox = new DragBox({
    condition: always,
    className: mode === 'sample' ? 'ol-dragbox imagery-box sample-box' : 'ol-dragbox imagery-box',
  })
  dragBox.on('boxend', () => {
    const extent = dragBox.getGeometry().getExtent()
    // 视图是 3857，框选范围统一转回 4326 再交给检索/取图/变化检测（后端与弹窗都按经纬度）
    const bbox = transformExtent(extent, map.getView().getProjection(), 'EPSG:4326')
      .map((value) => Number(value.toFixed(6)))
    stopDraw()
    if (mode === 'change') {
      // 变化检测：先保留范围，再弹出时相选择；选完两期才取图
      changeDrawRequested.value = false
      changeBBox.value = bbox
      showCaptureBBox(bbox)
      changeDialogError.value = ''
      changeDialogOpen.value = true
    } else if (mode === 'capture') {
      // 松开鼠标只进入「待确认」：保留虚线框，Enter 确认后才抓图；Esc 取消
      capture.cancelDraw()
      pendingBBox.value = bbox
      showCaptureBBox(bbox)
    } else if (mode === 'sample') {
      sampleBBox.value = bbox
      // 红框 + 正方形格网一直保留到退出标注，作为范围与比例参考
      showRegionGrid(bbox)
      samplePhaseOpen.value = true
    } else {
      imagery.finishDraw(bbox)
    }
  })
  map.addInteraction(dragBox)
  const target = map.getTargetElement()
  if (target) target.style.cursor = 'crosshair'
}

// 入参: 无 | 方法: 退出框选并清理交互/光标 | 出参: 无
function stopDraw() {
  if (dragBox && map) map.removeInteraction(dragBox)
  dragBox = null
  drawMode.value = null
  const target = map?.getTargetElement()
  if (target) target.style.cursor = ''
}

// 入参: 无 | 方法: 回车确认待选区 → 开始抓图（待确认框保留到抓图完成） | 出参: 无
function confirmPendingBBox() {
  const bbox = pendingBBox.value
  if (!bbox || captureLoading.value) return
  pendingBBox.value = null
  captureDirect(bbox)
}

// 入参: 无 | 方法: Esc 取消待选区 → 清除虚线框并回到空闲状态 | 出参: 无
function cancelPendingBBox() {
  if (!pendingBBox.value) return
  pendingBBox.value = null
  removeLayer('outline:capture-bbox', 'imagery-outline')
  capture.cancelDraw()
  stopDraw()
  ElMessage.info('已取消地物提取，可重新框选')
}

// 入参: target 键盘事件目标 | 方法: 判断是否正在输入框/可编辑区域中，避免抢 Enter | 出参: boolean
function isTypingTarget(target) {
  const tag = target?.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable === true
}

// 入参: event 键盘事件 | 方法: Enter 确认待选区；Esc 取消待选区或退出框选模式 | 出参: 无
function onKeydown(event) {
  if (sampleActive.value && sampleEditor.onKeydown(event)) return
  if (event.key === 'Enter' && pendingBBox.value) {
    if (isTypingTarget(event.target)) return
    event.preventDefault()
    event.stopPropagation()
    confirmPendingBBox()
    return
  }
  if (event.key !== 'Escape') return
  if (pendingBBox.value) {
    event.preventDefault()
    cancelPendingBBox()
    return
  }
  if (drawMode.value === 'sample') {
    event.preventDefault()
    stopDraw()
    samplePhaseOpen.value = true
    return
  }
  if (changeDrawRequested.value) {
    stopDraw()
    changeDrawRequested.value = false
    changeDialogOpen.value = false
  }
  if (imagery.drawRequested.value) {
    stopDraw()
    imagery.cancelDraw()
  }
  if (capture.drawRequested.value) {
    stopDraw()
    capture.cancelDraw()
  }
}

watch([imagery.drawRequested, capture.drawRequested, changeDrawRequested], ([wantImagery, wantCapture, wantChange]) => {
  if (wantChange || wantCapture || wantImagery) {
    startDraw(wantChange ? 'change' : wantCapture ? 'capture' : 'imagery')
  } else {
    stopDraw()
  }
})

// 入参: 卷帘瓦片错误信息 | 方法: 首次出现时提示一次，避免每个瓦片弹一条 | 出参: 无
watch(compareError, (msg) => {
  if (msg) ElMessage.warning(msg)
})

// 弹窗取消而非进入标注时收起红框格网；框选中/抓图中保持
watch(samplePhaseOpen, (open) => {
  if (open || sampleActive.value || sampleEntering.value) return
  if (drawMode.value === 'sample') return
  clearRegionGrid()
})

// 入参: event window 事件 | 方法: 底图/历史底图瓦片全失败时提示（5 秒内去重） | 出参: 无
let lastBasemapErrorAt = 0
function onBasemapErrorEvent(event) {
  const message = event.detail?.message
  if (!message) return
  if (Date.now() - lastBasemapErrorAt < 5000) return
  lastBasemapErrorAt = Date.now()
  ElMessage.warning(message)
}

onMounted(() => {
  // 入参: 无（target 取 ref DOM）
  // 方法: 初始化 OL Map，视图用 EPSG:3857 + ScaleLine/MousePosition；底图由 useMap.loadDefaultLayers() 注入
  //   ★ 视图与底图/影像预览瓦片同为 3857：避免 OL 对每张 3857 瓦片做 4326 重投影（每帧逐瓦片 canvas 重采样，
  //     是底图渲染慢的主因）。经纬度展示仍由 MousePosition 转 4326，业务 bbox 在边界处统一转换。
  //   layers 留空：底图（Esri/天地图）通过 loadDefaultLayers 注入，业务图层在其上
  // 出参: 无（map 实例供后续图层操作）
  map = new Map({
    target: mapRef.value,
    layers: [],
    view: new View({
      projection: 'EPSG:3857',
      center: fromLonLat([103.943905, 30.764575]),
      zoom: 9,
    }),
    controls: defaultControls({ attribution: false, zoom: false }).extend([
      new ScaleLine(),
      new MousePosition({
        coordinateFormat: createStringXY(5),
        projection: 'EPSG:4326',
        undefinedHTML: '0, 0',
      }),
    ]),
  })

  setMap(map)
  // 默认加载矢量图层已禁用, 改为按需从图层面板手动加载
  loadDefaultLayers()
  // 恢复上次选择的底图：默认最新 Esri 影像；用户在图层面板选过历史时相则按日期恢复
  restoreBasemap()
  setupFeatureInfo(popupRef.value)
  resizeObserver = new ResizeObserver(() => map?.updateSize())
  resizeObserver.observe(mapRef.value)

  // 入参: 无（操作 mapRef 内的 OL 控件 DOM）
  // 方法: 将经纬度(MousePosition)与比例尺(ScaleLine)两个 control 元素移入统一白底卡片 .ol-info-bar，
  //   由 flex 横向自动拼接；避免原先 right:150px 硬编码偏移在比例尺变宽时产生缝隙/重叠
  //   为什么用 nextTick + requestAnimationFrame: OL 控件在 setTarget 后异步渲染进 DOM，
  //   需等其挂载完成才能 querySelector 到 .ol-mouse-position/.ol-scale-line
  // 出参: 无
  nextTick(() => {
    requestAnimationFrame(() => {
      const host = mapRef.value
      const mousePos = host.querySelector('.ol-mouse-position')
      const scaleLine = host.querySelector('.ol-scale-line')
      if (!mousePos || !scaleLine) return
      const bar = document.createElement('div')
      bar.className = 'ol-info-bar'
      bar.appendChild(mousePos)
      bar.appendChild(scaleLine)
      host.appendChild(bar)
    })
  })

  window.addEventListener('keydown', onKeydown)
  window.addEventListener('rsiis:change-submit', onChangeSubmitEvent)
  window.addEventListener('rsiis:basemap-error', onBasemapErrorEvent)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('rsiis:change-submit', onChangeSubmitEvent)
  window.removeEventListener('rsiis:basemap-error', onBasemapErrorEvent)
  sampleEditor.exit()
  exitScene({ restoreView: false })
  resetSession()
  clearRegionGrid()
  setFeatureInfoEnabled(true)
  stopCompareDrag()
  stopCompareSwipe()
  stopDraw()
  resizeObserver?.disconnect()
  clearMap()
  map?.setTarget(undefined)
  map = null
})
</script>

<template>
  <div class="wms-panel shell-fade-in">
    <!-- 地图舞台（对齐原生 .wms-stage） -->
    <div class="wms-stage">
      <div ref="mapRef" class="ol-map-host"></div>
      <div ref="popupRef" class="wms-popup"></div>

      <!-- 放大/缩小：右上角（底图固定 Esri，不再显示底图切换框） -->
      <div class="map-zoom-control">
        <button type="button" title="放大" @click="zoomBy(1)">+</button>
        <button type="button" title="缩小" @click="zoomBy(-1)">−</button>
      </div>

      <!-- 地图工具条：统一左上角，从左到右追加；新增地图功能入口放这里 -->
      <div class="map-tools-bar">
        <button
          type="button"
          class="map-layer-toggle"
          :class="{ active: layerPanelOpen }"
          title="打开图层管理"
          :disabled="sampleActive"
          @click="toggleLayerPanel"
        >
          <Icon name="layers" :size="14" />
          <span>图层</span>
        </button>
        <button
          type="button"
          class="map-compare-toggle"
          :class="{ active: compareActive || comparePanelOpen }"
          title="卷帘（Esri Wayback 历史影像）"
          :disabled="sampleActive"
          @click="toggleComparePanel"
        >
          <Icon name="image" :size="14" />
          <span>卷帘</span>
        </button>
        <button
          type="button"
          class="map-sample-toggle"
          :class="{ active: sampleActive || samplePhaseOpen }"
          title="样本制作"
          :disabled="sampleActive || sampleEntering"
          @click="onSampleClick"
        >
          <Icon name="audit" :size="14" />
          <span>样本制作</span>
        </button>
        <template v-if="sampleActive">
          <button
            type="button"
            class="sample-tool"
            :class="{ active: sampleMode === 'draw' }"
            title="画多边形：左键落点，F2/Enter 闭合，Backspace 撤销，Esc 取消"
            @click="toggleSampleMode('draw')"
          >绘制</button>
          <button
            type="button"
            class="sample-tool"
            :class="{ active: sampleMode === 'edit' }"
            title="编辑顶点：拖动、点边加点、Shift 点顶点删除"
            @click="toggleSampleMode('edit')"
          >顶点</button>
          <button
            type="button"
            class="sample-tool"
            :class="{ active: sampleMode === 'select' }"
            title="框选标注：拖框选中，Delete 批量删除"
            @click="toggleSampleMode('select')"
          >框选</button>
        </template>
        <button
          v-if="!sampleActive"
          type="button"
          class="map-extract-toggle"
          :class="{ active: capture.drawRequested.value || pendingBBox || captureLoading }"
          title="在地图上选择区域，抓取当前最高分辨率影像并送入当前会话"
          :disabled="captureLoading"
          @click="startCaptureDraw"
        >
          <Icon name="map" :size="14" />
          <span>地物提取</span>
        </button>
        <button
          v-if="!sampleActive"
          type="button"
          class="map-change-toggle"
          :class="{ active: changeDrawRequested || changeDialogOpen }"
          title="框选区域并选择两个时相，做变化检测"
          @click="startChangeDraw"
        >
          <Icon name="calendar" :size="14" />
          <span>变化检测</span>
        </button>
      </div>
      <div v-if="comparePanelOpen" class="compare-panel">
        <div class="compare-panel-head">
          <div class="compare-panel-title">
            <Icon name="image" :size="14" />
            <span>卷帘</span>
          </div>
          <BaseButton variant="icon" size="sm" title="关闭" @click="comparePanelOpen = false">
            <Icon name="close" :size="14" />
          </BaseButton>
        </div>

        <label class="compare-field">
          <span>左侧时相</span>
          <el-select
            v-model="compareLeftId"
            size="small"
            class="compare-date-select"
            popper-class="basemap-date-popper"
            :loading="waybackLoading"
            placeholder="选择发布日期"
          >
            <el-option
              v-for="release in compareReleaseOptions"
              :key="release.id"
              :label="release.date"
              :value="release.id"
            />
          </el-select>
        </label>
        <label class="compare-field">
          <span>右侧时相</span>
          <el-select
            v-model="compareRightId"
            size="small"
            class="compare-date-select"
            popper-class="basemap-date-popper"
            :loading="waybackLoading"
            placeholder="选择发布日期"
          >
            <el-option
              v-for="release in compareReleaseOptions"
              :key="release.id"
              :label="release.date"
              :value="release.id"
            />
          </el-select>
        </label>

        <div v-if="waybackLoading && !waybackReleases.length" class="compare-loading">正在加载历史版本…</div>
        <div v-if="waybackFallback" class="compare-warn">
          版本清单接口暂不可达，已使用内置年度版本快照；仍然可以对比，瓦片加载可能较慢。
        </div>
        <div v-if="waybackError" class="compare-error">
          <span>{{ waybackError }}</span>
          <button type="button" @click="loadWaybackReleases(true)">重试</button>
        </div>
        <div v-if="compareError" class="compare-error">
          <span>{{ compareError }}</span>
        </div>

        <div class="compare-actions">
          <BaseButton
            class="compare-grow"
            variant="primary"
            bold
            :disabled="!canApplyCompare || waybackLoading"
            @click="applyCompare"
          >{{ compareActive ? '更新卷帘' : '开启卷帘' }}</BaseButton>
          <BaseButton v-if="compareActive" variant="ghost" :px="12" @click="stopCompare">关闭</BaseButton>
        </div>
      </div>

      <!-- 卷帘分隔线：仅对比开启时显示，可拖动 -->
      <div
        v-if="compareActive"
        class="swipe-divider"
        style="left: 50%"
        title="拖动分隔线对比两个时相"
        @pointerdown="onComparePointerDown"
      >
        <span class="swipe-handle">↔</span>
      </div>
      <div v-if="compareActive" class="compare-bar">
        <span class="compare-date">左 {{ compareLeftDate || '时相 A' }}</span>
        <span class="compare-arrow">↔</span>
        <span class="compare-date">右 {{ compareRightDate || '时相 B' }}</span>
        <span class="compare-hint">灰色 = 该时相无影像</span>
        <button type="button" class="compare-exit" @click="stopCompare">退出卷帘</button>
      </div>

      <!-- 图层管理：左上角工具条下方浮层（从 Agent 左栏移出） -->
      <div v-if="layerPanelOpen" class="map-layer-panel">
        <LayerPanel floating @back="layerPanelOpen = false" />
      </div>

      <!-- 样本制作：实例列表 / 保存导出 -->
      <SamplePanel
        v-if="sampleActive"
        @exit="requestExitSample"
      />

      <!-- 样本影像抓取进度：逐格抓图时胶囊底部显示完成比例 -->
      <div v-if="sampleEntering || sampleCapturing" class="sample-capturing">
        <span class="sample-spinner" />
        <span>{{ sampleProgress || '正在准备影像…' }}</span>
        <span v-if="sampleTilePercent" class="sample-capturing-pct">
          <span class="sample-capturing-bar" :style="{ width: `${sampleTilePercent}%` }" />
        </span>
      </div>

      <!-- 影像预览清除（左下角，仅在存在预览图层时出现） -->
      <div v-if="imageryCount" class="map-actions">
        <button
          type="button"
          class="imagery-entry imagery-clear"
          title="清除地图上的影像预览图层"
          @click="clearImageryPreview"
        >
          <Icon name="close" :size="13" />
          <span>清除影像 {{ imageryCount }}</span>
        </button>
      </div>

      <!-- 底图署名（Esri 时显示，满足署名要求） -->
      <div v-if="currentBasemap === 'esri'" class="basemap-attribution">
        Esri, Maxar, Earthstar Geographics
      </div>

      <!-- 框选提示（仅框选模式显示） -->
      <div
        v-if="pendingBBox || drawMode === 'sample' || changeDrawRequested || changeDialogLoading || imagery.drawRequested.value || capture.drawRequested.value"
        class="draw-hint"
      >
        <Icon name="map" :size="13" />
        <span>{{ drawHint }}</span>
      </div>
    </div>

    <ChangeDetectDialog
      v-model="changeDialogOpen"
      :bbox="changeBBox"
      :releases="compareReleaseOptions"
      :releases-loading="waybackLoading"
      :loading="changeDialogLoading"
      :error="changeDialogError"
      @cancel="cancelChangeDetect"
    />

    <SamplePhaseDialog
      v-model="samplePhaseOpen"
      :releases="compareReleaseOptions"
      :loading="waybackLoading"
      :bbox="sampleBBox"
      @confirm="onSampleConfirm"
      @restore="onSampleRestore"
      @request-region="startSampleRegionDraw"
    />
  </div>
</template>

<style scoped>
/* ===== 地图面板（对齐原生 .wms-panel） ===== */
.wms-panel {
  flex: 1;
  min-width: 0;
  background: var(--bg-panel);
  padding: 10px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: 100%;
}

/* ===== 地图舞台（对齐原生 .wms-stage） ===== */
.wms-stage {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
  background: #fff;
  border-radius: var(--r-shell);
  box-shadow: var(--shadow-shell);
}

/* ===== OL 容器（对齐原生 .ol-map-host） ===== */
.ol-map-host {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.ol-map-host :deep(.ol-zoom) {
  top: 10px;
  left: 10px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  padding: 2px;
  backdrop-filter: blur(8px);
}
.ol-map-host :deep(.ol-zoom button) {
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-xl);
  font-weight: 600;
  border-radius: var(--r-xs);
}
.ol-map-host :deep(.ol-zoom button:hover) {
  background: var(--bg-hover);
  color: var(--text-primary);

}
.ol-map-host :deep(.ol-attribution) {
  bottom: 4px;
  left: 4px;
  font-size: var(--fs-xs);
}
/* ===== 经纬度 + 比例尺 统一白底卡片（flex 横向自动拼接）===== */
/* 入参: 无 | 方法: 父容器 absolute 定位右下角 + flex 子项并排；子控件去掉各自定位与背景回归文档流 */
/* 出参: 单张白底圆角卡片，宽度随比例尺/经纬度自适应，永不产生缝隙 */
.ol-map-host :deep(.ol-info-bar) {
  position: absolute;
  right: 8px;
  bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  background: #fff;
  padding: 2px 6px;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-shell);
  z-index: 10;
}
.ol-map-host :deep(.ol-mouse-position) {
  position: static;
  background: transparent;
  padding: 0;
  font-size: var(--fs-sm);
  font-family: var(--sans);
  color: var(--text-secondary);
}
.ol-map-host :deep(.ol-scale-line) {
  position: static;
  background: transparent;
  border: none;
  border-radius: 0;
  box-shadow: none;
  overflow: visible;
}
.ol-map-host :deep(.ol-scale-line-inner) {
  border: 1px solid var(--text-secondary);
  border-top: none;
  border-radius: 0 0 3px 3px;
  margin: 0;
  color: var(--text-muted);
  font-size: var(--fs-sm);
  font-family: var(--sans);
  line-height: 1.2;
  text-align: center;
  padding: 1px 6px 0;
  min-width: 48px;
}

/* ===== 地物提取（左上角工具条） / 清除影像（左下角） ===== */
/* ===== 右上角缩放 ===== */
.map-zoom-control {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 12;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-shell);
  overflow: hidden;
}
.map-zoom-control button {
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  background: #fff;
  color: var(--text-secondary);
  font-size: 17px;
  line-height: 1;
  cursor: pointer;
}
.map-zoom-control button + button {
  border-top: 1px solid var(--border);
}
.map-zoom-control button:hover {
  color: var(--text-primary);

  background: var(--bg-hover);
}

/* ===== 地图左上角工具条：新增地图功能按钮统一追加到此，从左到右排列 ===== */
.map-tools-bar {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 25;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  max-width: calc(100% - 60px);
}

/* ===== 卷帘对比（左上角工具条，图层右侧） ===== */
.map-compare-toggle,
.map-sample-toggle,
.map-extract-toggle,
.map-change-toggle,
.sample-tool {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 9px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: #fff;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-shell);
  cursor: pointer;
}
.map-compare-toggle:hover,
.map-compare-toggle.active,
.map-extract-toggle:hover,
.map-extract-toggle.active,
.map-change-toggle:hover,
.map-change-toggle.active,
.sample-tool:hover,
.sample-tool.active {
  color: var(--text-primary);

  border-color: var(--border-strong);
  background: var(--bg-hover);
}
.map-compare-toggle:disabled,
.map-sample-toggle:disabled,
.sample-tool:disabled {
  opacity: 0.55;
  cursor: default;
}
.compare-panel {
  position: absolute;
  top: 44px;
  left: 10px;
  z-index: 26;
  width: 300px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-float);
}
.compare-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.compare-panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text-primary);
}
.compare-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.compare-field :deep(.el-select) {
  width: 100%;
}
.compare-field :deep(.el-select__wrapper) {
  min-height: 30px;
  font-size: var(--fs-md);
}
.compare-loading {
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.compare-warn {
  padding: 7px 8px;
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: var(--accent-amber);
  background: #fdf6e7;
  border-radius: var(--r-sm);
}
.compare-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: var(--accent-red);
}
.compare-error button {
  flex-shrink: 0;
  padding: 0;
  color: var(--accent);

  background: none;
  border: 0;
  cursor: pointer;
  text-decoration: underline;
}
.compare-actions {
  display: flex;
  gap: 8px;
}
.compare-grow {
  flex: 1;
}

/* 卷帘分隔线：白色细线 + 中央圆柄 */
.swipe-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 0 0 1px rgba(24, 24, 20, 0.35);
  z-index: 18;
  cursor: ew-resize;
  touch-action: none;
  transition: left 80ms var(--spring-drag);
}
.swipe-divider::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -9px;
  width: 18px;
}
.swipe-handle {
  position: absolute;
  top: 50%;
  left: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  transform: translate(-50%, -50%);
  font-size: 16px;
  color: var(--accent);

  background: #fff;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  box-shadow: var(--shadow-float);
  pointer-events: none;
}
.compare-bar {
  position: absolute;
  left: 50%;
  bottom: 50px;
  transform: translateX(-50%);
  z-index: 21;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 11px;
  font-size: var(--fs-sm);
  color: #fff;
  background: rgba(26, 26, 26, 0.82);
  border-radius: 999px;
  box-shadow: var(--shadow-float);
  white-space: nowrap;
}
.compare-arrow {
  opacity: 0.7;
}
/* 灰色垫层说明：避免用户把“无影像缺口”误认为加载失败/过渡错位 */
.compare-hint {
  padding-left: 8px;
  border-left: 1px solid rgba(255, 255, 255, 0.22);
  font-size: var(--fs-xs);
  color: rgba(255, 255, 255, 0.72);
}
.compare-exit {
  padding: 0 0 0 8px;
  font-size: var(--fs-sm);
  color: #fff;
  background: none;
  border: 0;
  border-left: 1px solid rgba(255, 255, 255, 0.3);
  cursor: pointer;
}
.compare-exit:hover {
  color: var(--text-primary);
}

/* ===== 图层管理（左上角工具条第一项） ===== */
.map-layer-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 9px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: #fff;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-shell);
  cursor: pointer;
}
.map-layer-toggle:hover,
.map-layer-toggle.active {
  color: var(--text-primary);

  border-color: var(--border-strong);
  background: var(--bg-hover);
}
.map-layer-toggle:disabled {
  opacity: 0.55;
  cursor: default;
}
.map-layer-panel {
  position: absolute;
  top: 44px;
  left: 10px;
  z-index: 26;
  width: 320px;
  max-height: calc(100% - 58px);
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-float);
  overflow: hidden;
}
/* 浮层空间有限：三项操作改为横排，给图层树留高度 */
.map-layer-panel :deep(.layer-actions) {
  flex-direction: row;
  gap: 4px;
  padding: 6px;
}
.map-layer-panel :deep(.layer-action) {
  flex: 1;
  width: auto;
  justify-content: center;
  padding: 4px 6px;
  font-size: var(--fs-sm);
}

.map-actions {
  position: absolute;
  left: 10px;
  bottom: 10px;
  display: flex;
  gap: 6px;
  z-index: 10;
}

/* ===== 样本影像抓取进度 ===== */
.sample-capturing {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 30;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  font-size: var(--fs-md);
  color: #fff;
  background: rgba(26, 26, 26, 0.86);
  border-radius: 999px;
  box-shadow: var(--shadow-float);
  font-variant-numeric: tabular-nums;
}
/* 逐格抓图进度条：贴在胶囊底部，宽度按瓦片完成比例增长 */
.sample-capturing-pct {
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 4px;
  height: 2px;
  background: rgba(255, 255, 255, 0.24);
  border-radius: 999px;
  overflow: hidden;
}
.sample-capturing-bar {
  display: block;
  height: 100%;
  background: #fff;
  transition: width var(--t-fast) linear;
}
.sample-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.32);
  border-top-color: #fff;
  border-radius: 50%;
  animation: sample-spin 0.8s linear infinite;
}
@keyframes sample-spin {
  to { transform: rotate(360deg); }
}
.imagery-entry {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-shell);
  cursor: pointer;
  backdrop-filter: blur(8px);
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.imagery-entry:hover,
.imagery-entry.capture-entry:hover {
  color: var(--text-primary);

  border-color: var(--border-strong);
  background: var(--bg-panel);
}
.imagery-clear {
  color: var(--accent-red);
  border-color: rgba(220, 76, 76, 0.35);
}
.imagery-clear:hover {
  color: var(--accent-red);
  border-color: var(--accent-red);
}

/* ===== Esri 底图署名 ===== */
.basemap-attribution {
  position: absolute;
  right: 10px;
  bottom: 42px;
  padding: 2px 6px;
  font-size: var(--fs-xs);
  font-family: var(--sans);
  color: var(--text-muted);
  background: rgba(255, 255, 255, 0.82);
  border-radius: var(--r-xs);
  z-index: 9;
  pointer-events: none;
}

/* ===== 框选拖拽框 ===== */
.ol-map-host :deep(.imagery-box) {
  border: 1.5px dashed var(--accent);
  background: rgba(77, 107, 254, 0.08);
  border-radius: 2px;
}
/* 样本区域：拖拽中即是最终红色边界，与落框后的格网同一颜色 */
.ol-map-host :deep(.sample-box) {
  border: 2px solid rgba(181, 74, 74, 0.95);
  background: rgba(181, 74, 74, 0.035);
  border-radius: 0;
}

/* ===== 框选提示（顶部居中）；样本工作范围红框提示复用同一外观 ===== */
.draw-hint,
.sample-range-hint {
  position: absolute;
  top: 48px;
  left: 50%;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: var(--fs-md);
  color: #fff;
  background: rgba(26, 26, 26, 0.82);
  border-radius: 999px;
  box-shadow: var(--shadow-float);
  z-index: 20;
  pointer-events: none;
}

</style>
