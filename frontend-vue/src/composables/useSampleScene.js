// ==================== 样本制作场景：抓图、铺图、双期卷帘 ====================
// 单期：一张 ImageStatic 直接铺图；双期：两张同范围同缩放的 ImageStatic，B 按分隔线右侧裁剪。
import { ref } from 'vue'
import ImageLayer from 'ol/layer/Image'
import VectorLayer from 'ol/layer/Vector'
import ImageStatic from 'ol/source/ImageStatic'
import VectorSource from 'ol/source/Vector'
import Feature from 'ol/Feature'
import Polygon from 'ol/geom/Polygon'
import Style from 'ol/style/Style'
import Stroke from 'ol/style/Stroke'
import Fill from 'ol/style/Fill'
import DragBox from 'ol/interaction/DragBox'
import { always } from 'ol/events/condition'
import { getRenderPixel } from 'ol/render'
import { transformExtent } from 'ol/proj'
import { captureWaybackImage } from '@/composables/useWaybackCapture'
import { georeferenceImagery } from '@/services/imagery'
import { getMap } from '@/composables/useMap'

const RANGE_COLOR = '#b54a4a'
const MIN_RANGE_PX = 32

const capturing = ref(false)
const progress = ref('')
const error = ref('')
const swipeActive = ref(false)
const ratio = ref(0.5)
const rangeMode = ref(false)
const rangeError = ref('')
const rangeRect = ref(null)

let layerA = null
let layerB = null
let divider = null
let clipped = false
let dragging = false
let previousView = null
let scene = null
let rangeBox = null
let rangeLayer = null
let rangeSource = null
let rangeOnConfirm = null
let pendingRange = null
const cleanups = []

function clearLayers() {
  cleanups.splice(0).forEach((fn) => fn())
  const map = getMap()
  if (map) {
    if (layerA) map.removeLayer(layerA)
    if (layerB) map.removeLayer(layerB)
    if (rangeLayer) map.removeLayer(rangeLayer)
  }
  layerA = null
  layerB = null
  rangeLayer = null
  rangeSource = null
}

function removeDivider() {
  if (divider?.parentElement) divider.parentElement.removeChild(divider)
  divider = null
  dragging = false
}

function createImageLayer(result, zIndex) {
  const extent = transformExtent(result.bbox, 'EPSG:4326', 'EPSG:3857')
  return new ImageLayer({
    source: new ImageStatic({
      url: result.preview_url,
      imageExtent: extent,
      projection: 'EPSG:3857',
    }),
    opacity: 1,
    zIndex,
  })
}

function placeDivider() {
  if (!divider) return
  const map = getMap()
  const size = map?.getSize() || [1000, 800]
  divider.style.left = `${Math.round(size[0] * ratio.value)}px`
}

function onDividerPointerDown(event) {
  if (!swipeActive.value) return
  event.preventDefault()
  dragging = true
  divider.setPointerCapture?.(event.pointerId)
}

function onDividerPointerMove(event) {
  if (!dragging) return
  const map = getMap()
  const size = map?.getSize() || [1000, 800]
  const viewport = map?.getViewport()
  if (!viewport) return
  const rect = viewport.getBoundingClientRect()
  ratio.value = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
  placeDivider()
  map.render()
}

function onDividerPointerUp(event) {
  dragging = false
  try {
    divider?.releasePointerCapture?.(event.pointerId)
  } catch (e) {
    // 指针已释放时忽略
  }
}

function buildDivider() {
  const map = getMap()
  const viewport = map?.getViewport()
  if (!viewport) return
  divider = document.createElement('div')
  divider.className = 'sample-swipe-divider'
  divider.title = '拖动分隔线对比两个时相'
  divider.style.cssText = [
    'position:absolute',
    'top:0',
    'bottom:0',
    'width:2px',
    'margin-left:-1px',
    'background:rgba(255,255,255,0.95)',
    'box-shadow:0 0 0 1px rgba(0,0,0,0.35)',
    'z-index:18',
    'cursor:ew-resize',
    'touch-action:none',
  ].join(';')
  const handle = document.createElement('span')
  handle.textContent = '↔'
  handle.style.cssText = [
    'position:absolute',
    'top:50%',
    'left:50%',
    'transform:translate(-50%,-50%)',
    'width:34px',
    'height:34px',
    'display:flex',
    'align-items:center',
    'justify-content:center',
    'font-size:16px',
    'color:#1a1a18',
    'background:#fff',
    'border:1px solid rgba(24,24,20,0.12)',
    'border-radius:50%',
    'box-shadow:0 18px 48px rgba(24,24,20,0.12)',
    'pointer-events:none',
  ].join(';')
  divider.appendChild(handle)
  divider.addEventListener('pointerdown', onDividerPointerDown)
  divider.addEventListener('pointermove', onDividerPointerMove)
  divider.addEventListener('pointerup', onDividerPointerUp)
  divider.addEventListener('pointercancel', onDividerPointerUp)
  viewport.appendChild(divider)
  cleanups.push(() => {
    divider?.removeEventListener('pointerdown', onDividerPointerDown)
    divider?.removeEventListener('pointermove', onDividerPointerMove)
    divider?.removeEventListener('pointerup', onDividerPointerUp)
    divider?.removeEventListener('pointercancel', onDividerPointerUp)
  })
  placeDivider()
}

function clipLayerB() {
  if (!layerB) return
  const map = getMap()
  const onPre = (event) => {
    if (!swipeActive.value || !divider) return
    const size = map.getSize()
    if (!size) return
    const viewportRect = map.getViewport().getBoundingClientRect()
    const rect = divider.getBoundingClientRect()
    let x = size[0] * ratio.value
    if (rect.width && viewportRect.width) {
      x = rect.left + rect.width / 2 - viewportRect.left
    }
    x = Math.max(0, Math.min(size[0], x))
    const corners = [
      [x, 0],
      [size[0], 0],
      [size[0], size[1]],
      [x, size[1]],
    ].map((pixel) => getRenderPixel(event, pixel))
    const ctx = event.context
    ctx.save()
    ctx.beginPath()
    ctx.moveTo(corners[0][0], corners[0][1])
    ctx.lineTo(corners[1][0], corners[1][1])
    ctx.lineTo(corners[2][0], corners[2][1])
    ctx.lineTo(corners[3][0], corners[3][1])
    ctx.closePath()
    ctx.clip()
    clipped = true
  }
  const onPost = (event) => {
    if (!clipped) return
    event.context.restore()
    clipped = false
  }
  layerB.on('prerender', onPre)
  layerB.on('postrender', onPost)
  cleanups.push(() => {
    layerB?.un('prerender', onPre)
    layerB?.un('postrender', onPost)
  })
}

// ==================== 工作范围红框 ====================

function sceneCoordToPixel(coordinate) {
  const [minx, miny, maxx, maxy] = scene.extent
  return [
    ((coordinate[0] - minx) / (maxx - minx)) * scene.width,
    ((maxy - coordinate[1]) / (maxy - miny)) * scene.height,
  ]
}

function scenePixelToCoord(x, y) {
  const [minx, miny, maxx, maxy] = scene.extent
  return [
    minx + (x / scene.width) * (maxx - minx),
    maxy - (y / scene.height) * (maxy - miny),
  ]
}

function ensureRangeLayer(map) {
  if (rangeLayer) return
  rangeSource = new VectorSource()
  rangeLayer = new VectorLayer({
    source: rangeSource,
    zIndex: 55,
    style: new Style({
      stroke: new Stroke({ color: RANGE_COLOR, width: 2 }),
    }),
  })
  map.addLayer(rangeLayer)
}

function drawRangeRect(rect) {
  if (!rangeSource) return
  rangeSource.clear()
  if (!rect) return
  const [x0, y0, x1, y1] = rect
  const ring = [
    scenePixelToCoord(x0, y0),
    scenePixelToCoord(x1, y0),
    scenePixelToCoord(x1, y1),
    scenePixelToCoord(x0, y1),
    scenePixelToCoord(x0, y0),
  ]
  rangeSource.addFeature(new Feature({ geometry: new Polygon([ring]) }))
}

function onRangeBoxEnd() {
  if (!scene || !rangeBox) return
  const extent = rangeBox.getGeometry().getExtent()
  const minx = Math.max(extent[0], scene.extent[0])
  const miny = Math.max(extent[1], scene.extent[1])
  const maxx = Math.min(extent[2], scene.extent[2])
  const maxy = Math.min(extent[3], scene.extent[3])
  if (minx >= maxx || miny >= maxy) {
    rangeError.value = '框选范围不在影像内'
    pendingRange = null
    drawRangeRect(null)
    return
  }
  const [x0, y0] = sceneCoordToPixel([minx, maxy])
  const [x1, y1] = sceneCoordToPixel([maxx, miny])
  const rect = [
    Math.round(Math.max(0, Math.min(x0, x1))),
    Math.round(Math.max(0, Math.min(y0, y1))),
    Math.round(Math.min(scene.width, Math.max(x0, x1))),
    Math.round(Math.min(scene.height, Math.max(y0, y1))),
  ]
  if (rect[2] - rect[0] < MIN_RANGE_PX || rect[3] - rect[1] < MIN_RANGE_PX) {
    rangeError.value = `工作范围至少 ${MIN_RANGE_PX}×${MIN_RANGE_PX} 像素`
    pendingRange = null
    drawRangeRect(null)
    return
  }
  pendingRange = rect
  rangeRect.value = rect
  rangeError.value = ''
  drawRangeRect(rect)
}

function onRangeDblClick() {
  if (!rangeMode.value) return
  rangeOnConfirm?.()
}

function stopRangeInteraction() {
  const map = getMap()
  if (!map) return
  if (rangeBox) map.removeInteraction(rangeBox)
  map.un('dblclick', onRangeDblClick)
  rangeBox = null
  const target = map.getTargetElement()
  if (target) target.style.cursor = ''
}

// 入参: { onConfirm } | 方法: 进入红框模式，等待拖框 | 出参: 是否启动成功
export function startRange({ onConfirm } = {}) {
  const map = getMap()
  if (!map || !scene) return false
  rangeOnConfirm = onConfirm || null
  clearRange()
  ensureRangeLayer(map)
  stopRangeInteraction()
  rangeBox = new DragBox({
    condition: always,
    style: new Style({
      stroke: new Stroke({ color: RANGE_COLOR, width: 1.5, lineDash: [6, 4] }),
      fill: new Fill({ color: 'rgba(181,74,74,0.08)' }),
    }),
  })
  rangeBox.on('boxend', onRangeBoxEnd)
  map.addInteraction(rangeBox)
  map.on('dblclick', onRangeDblClick)
  rangeMode.value = true
  rangeError.value = ''
  const target = map.getTargetElement()
  if (target) target.style.cursor = 'crosshair'
  return true
}

// 入参: 无 | 方法: 清除当前红框，留在红框模式 | 出参: 无
export function clearRange() {
  pendingRange = null
  rangeRect.value = null
  rangeError.value = ''
  drawRangeRect(null)
}

// 入参: 无 | 方法: Enter/双击确认红框，切到框内视图并返回工作帧 | 出参: workFrame | null
export function confirmRange() {
  if (!scene) return null
  if (!pendingRange) {
    rangeError.value = '请先框选工作范围'
    return null
  }
  const [x0, y0, x1, y1] = pendingRange
  const [minx, maxy] = scenePixelToCoord(x0, y0)
  const [maxx, miny] = scenePixelToCoord(x1, y1)
  const workExtent = [minx, miny, maxx, maxy]
  layerA?.setExtent(workExtent)
  layerB?.setExtent(workExtent)
  const map = getMap()
  // 左侧给样本面板留出空间，避免红框工作区被面板遮住
  map?.getView().fit(workExtent, { padding: [24, 24, 24, 340], duration: 180 })
  stopRangeInteraction()
  rangeMode.value = false
  rangeRect.value = [...pendingRange]
  return {
    width: x1 - x0,
    height: y1 - y0,
    bounds: transformExtent(workExtent, 'EPSG:3857', 'EPSG:4326'),
    extent: workExtent,
    cropPx: [...pendingRange],
    sourceWidth: scene.width,
    sourceHeight: scene.height,
  }
}

// 入参: 无 | 方法: 撤销红框，恢复整张影像视图 | 出参: 无
export function resetRange() {
  clearRange()
  stopRangeInteraction()
  rangeMode.value = false
  layerA?.setExtent(undefined)
  layerB?.setExtent(undefined)
  const map = getMap()
  if (map && scene) map.getView().fit(scene.extent, { padding: [40, 40, 40, 40], duration: 0 })
}


export async function prepareScene({ releaseA, releaseB, bbox, taskName }) {
  const map = getMap()
  if (!map) throw new Error('地图尚未就绪')
  if (!releaseA?.m) throw new Error('请选择时相 A')
  capturing.value = true
  error.value = ''
  progress.value = '正在抓取时相 A…'
  try {
    const capturedA = await captureWaybackImage({
      release: releaseA,
      bbox,
      exactBbox: true,
    })
    progress.value = '正在写入时相 A…'
    const imageA = await georeferenceImagery({
      blob: capturedA.blob,
      bbox: capturedA.bbox,
      name: `sample_${taskName}_a`,
      date: releaseA.date,
    })

    let capturedB = null
    let imageB = null
    if (releaseB?.m) {
      progress.value = '正在抓取时相 B…'
      capturedB = await captureWaybackImage({
        release: releaseB,
        bbox: capturedA.bbox,
        exactBbox: true,
        zoom: capturedA.zoom,
      })
      progress.value = '正在写入时相 B…'
      imageB = await georeferenceImagery({
        blob: capturedB.blob,
        bbox: capturedB.bbox,
        name: `sample_${taskName}_b`,
        date: releaseB.date,
      })
    }

    clearLayers()
    removeDivider()
    stopRangeInteraction()
    rangeMode.value = false
    pendingRange = null
    rangeRect.value = null

    const view = map.getView()
    previousView = { center: view.getCenter(), zoom: view.getZoom() }

    layerA = createImageLayer(imageA, 20)
    map.addLayer(layerA)
    if (imageB) {
      layerB = createImageLayer(imageB, 20.5)
      map.addLayer(layerB)
    }

    const extent = transformExtent(imageA.bbox, 'EPSG:4326', 'EPSG:3857')
    scene = {
      extent,
      width: imageA.width,
      height: imageA.height,
      bounds: imageA.bbox,
      path: imageA.file_path,
    }
    view.fit(extent, { padding: [40, 40, 40, 40], duration: 0 })

    swipeActive.value = Boolean(imageB)
    ratio.value = 0.5
    if (swipeActive.value) {
      buildDivider()
      clipLayerB()
    }

    const frame = {
      width: imageA.width,
      height: imageA.height,
      bounds: imageA.bbox,
      extent,
      crs: 'EPSG:4326',
      path: imageA.file_path,
    }
    const phases = [
      { date: releaseA.date, path: imageA.file_path, preview_url: imageA.preview_url },
    ]
    if (imageB) {
      phases.push({
        date: releaseB.date,
        path: imageB.file_path,
        preview_url: imageB.preview_url,
      })
    }
    progress.value = ''
    return { frame, phases }
  } catch (e) {
    error.value = e.message || '影像抓取失败'
    throw e
  } finally {
    capturing.value = false
  }
}

// 入参: 无 | 方法: 移除样本影像/卷帘并回到进入前视图 | 出参: 无
export function exitScene({ restoreView = true } = {}) {
  stopRangeInteraction()
  clearLayers()
  removeDivider()
  swipeActive.value = false
  ratio.value = 0.5
  rangeMode.value = false
  rangeError.value = ''
  rangeRect.value = null
  pendingRange = null
  scene = null
  const map = getMap()
  if (restoreView && map && previousView?.center) {
    map.getView().animate({ center: previousView.center, zoom: previousView.zoom, duration: 220 })
  }
  previousView = null
}

export function useSampleScene() {
  return {
    capturing,
    progress,
    error,
    swipeActive,
    ratio,
    rangeMode,
    rangeError,
    rangeRect,
    prepareScene,
    startRange,
    clearRange,
    confirmRange,
    resetRange,
    exitScene,
  }
}
