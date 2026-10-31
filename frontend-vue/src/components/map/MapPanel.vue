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
import { ElMessage } from 'element-plus'
import {
  setMap, clearMap, loadDefaultLayers, setupFeatureInfo, getBasemapType,
  clearImageryLayers, listImageryLayers, getImageryVersion,
  addFootprintLayer, removeLayer,
} from '@/composables/useMap'
import { useImagerySearch } from '@/composables/useImagerySearch'
import { useCaptureInterpret } from '@/composables/useCaptureInterpret'
import { useSwipeCompare } from '@/composables/useSwipeCompare'
import { restoreBasemap } from '@/composables/useBasemap'
import { captureWaybackImage } from '@/composables/useWaybackCapture'
import { captureImagery, georeferenceImagery, getEsriWaybackReleases } from '@/services/imagery'
import LayerPanel from '@/components/map/LayerPanel.vue'
import ChangeDetectDialog from '@/components/map/ChangeDetectDialog.vue'
import Icon from '@/shared/ui/Icon.vue'
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

const drawHint = computed(() => {
  if (captureLoading.value) return '正在抓取 Esri 最高分辨率影像…'
  if (changeDialogLoading.value) return '正在获取两期影像…'
  if (pendingBBox.value) return '已选择区域 · Enter 确定 · Esc 取消'
  if (drawMode.value === 'change') return '拖动选择变化检测范围 · Esc 取消'
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

// 入参: 无 | 方法: 打开/关闭卷帘面板，首次打开时懒加载版本清单 | 出参: 无
function toggleComparePanel() {
  comparePanelOpen.value = !comparePanelOpen.value
  if (comparePanelOpen.value) {
    layerPanelOpen.value = false
    if (!waybackReleases.value.length) loadWaybackReleases()
  }
}

// 入参: 无 | 方法: 叠加左右时相并开启卷帘；先清掉会遮挡对比的检索预览层 | 出参: 无
function applyCompare() {
  const left = compareLeftRelease.value
  const right = compareRightRelease.value
  if (!left || !right) {
    ElMessage.warning('请选择两个时相')
    return
  }
  if (left.id === right.id) {
    ElMessage.warning('左右时相不能相同')
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
  comparePanelOpen.value = false
  ElMessage.success(`卷帘对比：${left.date} ↔ ${right.date}`)
}

// 入参: 无 | 方法: 退出卷帘对比并移除两个 Wayback 图层 | 出参: 无
function stopCompare() {
  stopCompareSwipe()
  comparePanelOpen.value = false
}

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

// 入参: mode 'imagery'|'capture' | 方法: 进入拖拽框选，结束后按模式回传 bbox | 出参: 无
function startDraw(mode) {
  if (!map || dragBox) return
  // 重新发起地物提取时，丢弃上一次待确认的框
  if (pendingBBox.value) {
    pendingBBox.value = null
    removeLayer('outline:capture-bbox', 'imagery-outline')
  }
  drawMode.value = mode
  dragBox = new DragBox({ condition: always, className: 'ol-dragbox imagery-box' })
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
          @click="toggleLayerPanel"
        >
          <Icon name="layers" :size="14" />
          <span>图层</span>
        </button>
        <button
          type="button"
          class="map-compare-toggle"
          :class="{ active: compareActive || comparePanelOpen }"
          title="卷帘对比（Esri Wayback 历史影像）"
          @click="toggleComparePanel"
        >
          <Icon name="image" :size="14" />
          <span>卷帘</span>
        </button>
        <button
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
            <span>卷帘对比</span>
          </div>
          <button type="button" class="compare-icon-btn" title="关闭" @click="comparePanelOpen = false">
            <Icon name="close" :size="14" />
          </button>
        </div>

        <label class="compare-field">
          <span>左侧时相</span>
          <el-select
            v-model="compareLeftId"
            size="small"
            filterable
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
            filterable
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
        <div class="compare-note">
          当前 Esri 底图是最新影像镶嵌，没有单一时相日期。这里使用 Wayback 发布版本：
          日期为版本发布日期，不是单景成像日期；不同地区可用时相与分辨率不同。
          灰色区域表示该时相在此处没有影像（不是加载失败）；相邻年份可能共用同一景，
          Wayback 只发布全球版本，局部没更新时两侧看起来会一样。
        </div>
        <div v-if="waybackError" class="compare-error">
          <span>{{ waybackError }}</span>
          <button type="button" @click="loadWaybackReleases(true)">重试</button>
        </div>
        <div v-if="compareError" class="compare-error">
          <span>{{ compareError }}</span>
        </div>

        <div class="compare-actions">
          <button
            type="button"
            class="compare-primary"
            :disabled="!canApplyCompare || waybackLoading"
            @click="applyCompare"
          >{{ compareActive ? '更新卷帘' : '开启卷帘' }}</button>
          <button v-if="compareActive" type="button" class="compare-ghost" @click="stopCompare">关闭</button>
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
        v-if="pendingBBox || changeDrawRequested || changeDialogLoading || imagery.drawRequested.value || capture.drawRequested.value"
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
  background: var(--accent-soft);
  color: var(--accent);
}
.ol-map-host :deep(.ol-attribution) {
  bottom: 4px;
  left: 4px;
  font-size: var(--fs-xs);
}
/* ===== 底图切换按钮组（右上角，紧凑白底卡片，风格对齐 .ol-info-bar）===== */
/* 入参: 无 | 方法: absolute 定位右上角，按钮 flex 横排；激活态用主题色填充 */
/* 出参: 一组紧凑按钮，激活项高亮区分当前底图 */
.basemap-switcher {
  position: absolute;
  top: 10px;
  right: 10px;
  display: flex;
  gap: 2px;
  background: #fff;
  padding: 2px;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-shell);
  z-index: 10;
}
.basemap-btn {
  padding: 3px 10px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: transparent;
  border: none;
  border-radius: var(--r-xs);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.basemap-btn:hover {
  background: var(--accent-soft);
  color: var(--accent);
}
.basemap-btn.active {
  background: var(--accent);
  color: #fff;
  font-weight: 600;
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
  gap: 10px;
  background: #fff;
  padding: 4px 10px;
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
  font-family: var(--mono);
  color: var(--accent);
}
.ol-map-host :deep(.ol-scale-line) {
  position: static;
  background: transparent;
  padding: 0;
  color: var(--accent);
}
.ol-map-host :deep(.ol-scale-line-inner) {
  border-color: var(--accent);
  color: var(--accent);
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
  color: var(--accent);
  background: var(--accent-soft);
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
.map-extract-toggle,
.map-change-toggle {
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
.map-change-toggle.active {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
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
.compare-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--text-secondary);
  background: transparent;
  border: 0;
  border-radius: var(--r-xs);
  cursor: pointer;
}
.compare-icon-btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
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
.compare-note {
  padding: 7px 8px;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--text-muted);
  background: var(--bg-inset);
  border-radius: var(--r-sm);
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
.compare-primary {
  flex: 1;
  height: 32px;
  font-size: var(--fs-md);
  font-weight: 600;
  color: #fff;
  background: var(--accent);
  border: none;
  border-radius: var(--r-md);
  cursor: pointer;
}
.compare-primary:hover {
  background: var(--accent-hover);
}
.compare-primary:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.compare-ghost {
  height: 32px;
  padding: 0 12px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  cursor: pointer;
}
.compare-ghost:hover {
  color: var(--text-primary);
  border-color: var(--border-strong);
}

/* 卷帘分隔线：白色细线 + 中央圆柄 */
.swipe-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
  z-index: 18;
  cursor: ew-resize;
  touch-action: none;
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
  color: var(--accent-soft);
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
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
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
  color: var(--accent);
  border-color: var(--accent);
  background: #fff;
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

/* ===== 框选提示（顶部居中） ===== */
.draw-hint {
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
