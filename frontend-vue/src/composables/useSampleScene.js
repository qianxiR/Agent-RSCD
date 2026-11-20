// ==================== 样本制作场景：抓图、铺图、双期卷帘 ====================
// 单期：一张 ImageStatic 直接铺图；双期：两张同范围同缩放的 ImageStatic，B 按分隔线右侧裁剪。
import { ref } from 'vue'
import ImageLayer from 'ol/layer/Image'
import ImageStatic from 'ol/source/ImageStatic'
import { getRenderPixel } from 'ol/render'
import { transformExtent } from 'ol/proj'
import { captureWaybackImage } from '@/composables/useWaybackCapture'
import { georeferenceImagery } from '@/services/imagery'
import { getMap } from '@/composables/useMap'


const capturing = ref(false)
const progress = ref('')
const error = ref('')
const swipeActive = ref(false)
const ratio = ref(0.5)
// 逐格抓图进度 0–100；0 表示不在抓图
const tilePercent = ref(0)

let layerA = null
let layerB = null
let divider = null
let clipped = false
let dragging = false
let previousView = null
const cleanups = []

// 入参: 抓图进度 { done, total } | 方法: 更新进度百分比 | 出参: 无
function setTileProgress(info) {
  const total = Number(info?.total) || 0
  const done = Number(info?.done) || 0
  tilePercent.value = total ? Math.round((done / total) * 100) : 0
}

function clearLayers() {
  cleanups.splice(0).forEach((fn) => fn())
  const map = getMap()
  if (map) {
    if (layerA) map.removeLayer(layerA)
    if (layerB) map.removeLayer(layerB)
  }
  layerA = null
  layerB = null
  tilePercent.value = 0
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

// 入参: 目标图层（默认当前 layerB） | 方法: 按分隔线右侧裁剪 B 层 | 出参: 无
function clipLayerB(target = layerB) {
  const layer = target
  if (!layer) return
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

export async function prepareScene({ releaseA, releaseB, bbox, taskName }) {
  const map = getMap()
  if (!map) throw new Error('地图尚未就绪')
  if (!releaseA?.m) throw new Error('请选择时相 A')
  capturing.value = true
  error.value = ''
  progress.value = '正在抓取时相 A…'
  tilePercent.value = 0

  const view = map.getView()
  previousView = { center: view.getCenter(), zoom: view.getZoom() }

  clearLayers()
  removeDivider()

  try {
    const capturedA = await captureWaybackImage({
      release: releaseA,
      bbox,
      exactBbox: true,
      onProgress: (info) => {
        setTileProgress(info)
        progress.value = `正在抓取时相 A… ${info.done}/${info.total}`
      },
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
      tilePercent.value = 0
      progress.value = '正在抓取时相 B…'
      capturedB = await captureWaybackImage({
        release: releaseB,
        bbox: capturedA.bbox,
        exactBbox: true,
        zoom: capturedA.zoom,
        onProgress: (info) => {
          setTileProgress(info)
          progress.value = `正在抓取时相 B… ${info.done}/${info.total}`
        },
      })
      progress.value = '正在写入时相 B…'
      imageB = await georeferenceImagery({
        blob: capturedB.blob,
        bbox: capturedB.bbox,
        name: `sample_${taskName}_b`,
        date: releaseB.date,
      })
    }

    const extent = transformExtent(imageA.bbox, 'EPSG:4326', 'EPSG:3857')
    layerA = createImageLayer(imageA, 20)
    map.addLayer(layerA)
    if (imageB) {
      layerB = createImageLayer(imageB, 20.5)
      map.addLayer(layerB)
    }
    map.getView().fit(extent, { padding: [40, 40, 40, 40], duration: 0 })

    swipeActive.value = Boolean(imageB)
    ratio.value = 0.5
    if (swipeActive.value) {
      buildDivider()
      clipLayerB()
    }

    // gsd 由后端按 3857 米算；这里换算成地面米，与时相弹窗的预测口径一致
    const centerLat = (imageA.bbox[1] + imageA.bbox[3]) / 2
    const groundGsd = imageA.gsd
      ? Number(imageA.gsd) * Math.cos((centerLat * Math.PI) / 180)
      : null
    const frame = {
      width: imageA.width,
      height: imageA.height,
      bounds: imageA.bbox,
      extent,
      crs: 'EPSG:4326',
      path: imageA.file_path,
      zoom: capturedA.zoom,
      gsd: groundGsd,
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
    tilePercent.value = 0
    capturing.value = false
  }
}

// 入参: { phasePreviews } | 方法: 直接用任务目录里的源影像铺图（不重新抓图） | 出参: 无
export function loadStoredScene({ phasePreviews, bounds }) {
  const map = getMap()
  if (!map) throw new Error('地图尚未就绪')
  const previews = (phasePreviews || []).filter((item) => item.preview_url)
  if (!previews.length) throw new Error('历史任务的源影像已不存在')
  const extent = transformExtent(bounds, 'EPSG:4326', 'EPSG:3857')

  clearLayers()
  removeDivider()
  const view = map.getView()
  previousView = { center: view.getCenter(), zoom: view.getZoom() }

  const create = (preview, zIndex) => new ImageLayer({
    source: new ImageStatic({
      url: preview.preview_url,
      imageExtent: extent,
      projection: 'EPSG:3857',
    }),
    opacity: 1,
    zIndex,
  })
  layerA = create(previews[0], 20)
  map.addLayer(layerA)
  if (previews[1]) {
    layerB = create(previews[1], 20.5)
    map.addLayer(layerB)
  }

  view.fit(extent, { padding: [40, 40, 40, 40], duration: 0 })
  swipeActive.value = Boolean(previews[1])
  ratio.value = 0.5
  if (swipeActive.value) {
    buildDivider()
    clipLayerB()
  }
}

// 入参: 无 | 方法: 移除样本影像/卷帘并回到进入前视图 | 出参: 无
export function exitScene({ restoreView = true } = {}) {
  clearLayers()
  removeDivider()
  swipeActive.value = false
  ratio.value = 0.5
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
    tilePercent,
    prepareScene,
    loadStoredScene,
    exitScene,
  }
}
