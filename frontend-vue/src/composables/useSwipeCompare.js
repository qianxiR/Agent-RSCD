// ==================== 双时相卷帘（Esri Wayback 两个发布版本）====================
// 方法: 叠加两张 3857 XYZ 瓦片，在 prerender 中按分隔线的 CSS 像素位置裁剪左右图层；
//   裁剪前必须经 getRenderPixel 把 CSS 像素换算成画布设备像素（高 DPI 下两者不同）。
// 说明: 只操作 OpenLayers 图层与 canvas 裁剪，不改动业务 WMS/WFS 图层。
//   ★ 灰色垫层: 卷帘开启时在两张瓦片下铺一层浅灰，某个时相在该区域没有瓦片时显示灰色，
//     而不是透出「最新底图」造成“过渡位置不在分隔线”的错觉。
import { ref } from 'vue'
import TileLayer from 'ol/layer/Tile'
import XYZ from 'ol/source/XYZ'
import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import Feature from 'ol/Feature'
import Polygon from 'ol/geom/Polygon'
import Style from 'ol/style/Style'
import Fill from 'ol/style/Fill'
import { getRenderPixel } from 'ol/render'
import { getMap } from './useMap'
import { createRetryingTileLoad } from '@/utils/tileLoader'

const active = ref(false)
const ratio = ref(0.5)
const leftDate = ref('')
const rightDate = ref('')
// 瓦片加载结果：区分「完全没加载到影像」和「局部无覆盖」，避免一个 404 就报红
const error = ref('')
const leftStats = { ok: 0, fail: 0 }
const rightStats = { ok: 0, fail: 0 }
let leftLayer = null
let rightLayer = null
let backdropLayer = null
let coverageTimer = null
const cleanups = []

// 入参: layer 瓦片层, side 'left'|'right'
// 方法: prerender 读取分隔线的 CSS 像素中心，经 getRenderPixel 换算成画布设备像素后裁剪；
//   postrender 恢复。DOM 分隔线与 canvas 裁剪共用同一个 CSS 像素，DPR>1 时也不会偏。
// 出参: 无
function clipLayer(layer, side) {
  let clipped = false
  const onPre = (event) => {
    if (!active.value) return
    const map = getMap()
    const size = map?.getSize()
    if (!size) return
    const width = size[0]
    // 以 DOM 分隔线的实际像素位置为准：先写线、再渲染，裁剪永远追着线走，
    // 不再出现 CSS 与 canvas 两个通道各算一份 x 的错位
    const viewportRect = map.getViewport().getBoundingClientRect()
    const divider = document.querySelector('.swipe-divider')
    let x = Math.round(width * ratio.value)
    if (divider && viewportRect.width) {
      const rect = divider.getBoundingClientRect()
      x = Math.round(rect.left + rect.width / 2 - viewportRect.left)
    }
    x = Math.max(0, Math.min(width, x))
    const ctx = event.context
    // 画布坐标系是设备像素（CSS 像素 × devicePixelRatio），CSS 像素必须经 getRenderPixel
    // 换算后再裁剪；否则高 DPI（Windows 缩放非 100%）下接缝整条偏左、与 DOM 分隔线错位
    const left = side === 'left' ? 0 : x
    const right = side === 'left' ? x : width
    const corners = [
      [left, 0],
      [right, 0],
      [right, size[1]],
      [left, size[1]],
    ].map((pixel) => getRenderPixel(event, pixel))
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
  layer.on('prerender', onPre)
  layer.on('postrender', onPost)
  cleanups.push(() => {
    layer.un('prerender', onPre)
    layer.un('postrender', onPost)
  })
}

// 入参: 无 | 方法: 覆盖整个投影范围的浅灰垫层（zIndex 5.5，位于底图之上、两个时相之下） | 出参: VectorLayer
function createBackdrop() {
  const map = getMap()
  const projection = map.getView().getProjection()
  // 3857/4326 都有全局 extent；自定义投影缺失时退回 Web Mercator 范围
  const [minx, miny, maxx, maxy] = projection.getExtent()
    || [-20037508.34, -20037508.34, 20037508.34, 20037508.34]
  const ring = [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
  const layer = new VectorLayer({
    source: new VectorSource({ features: [new Feature(new Polygon([ring]))] }),
    style: new Style({ fill: new Fill({ color: '#dfe3e8' }) }),
    zIndex: 5.5,
  })
  layer.set('compareBackdrop', true)
  return layer
}

// 入参: 无 | 方法: 2 秒后按左右两侧的加载结果给出**只针对真失败**的提示 | 出参: 无
function scheduleCoverageCheck() {
  if (coverageTimer) return
  coverageTimer = setTimeout(() => {
    coverageTimer = null
    evaluateCoverage()
  }, 2000)
}

// 入参: 无 | 方法: 一侧完全没加载到瓦片才提示；局部无覆盖交给灰色垫层表达 | 出参: 无
function evaluateCoverage() {
  if (!active.value) return
  const leftNone = leftStats.ok === 0 && leftStats.fail > 0
  const rightNone = rightStats.ok === 0 && rightStats.fail > 0
  if (leftNone && rightNone) {
    error.value = '两侧历史影像都没有加载到瓦片：Wayback 不可达或该区域无影像'
  } else if (leftNone) {
    error.value = `左侧 ${leftDate.value || '时相 A'} 在该区域没有可用影像（灰色为无覆盖）`
  } else if (rightNone) {
    error.value = `右侧 ${rightDate.value || '时相 B'} 在该区域没有可用影像（灰色为无覆盖）`
  } else {
    error.value = ''
  }
}

// 入参: url 瓦片模板, zIndex, label, side
// 方法: 创建 EPSG:3857 瓦片图层（与视图同投影，无需重投影）；失败退避重试并统计加载结果
// 出参: TileLayer
function createLayer(url, zIndex, label, side) {
  const stats = side === 'left' ? leftStats : rightStats
  const source = new XYZ({
    url,
    projection: 'EPSG:3857',
    crossOrigin: 'anonymous',
    maxZoom: 19,
    cacheSize: 1024,
    transition: 0,
    // Wayback 经代理容易抖动：失败退避重试 1 次
    tileLoadFunction: createRetryingTileLoad({ retries: 1 }),
  })
  source.on('tileloadend', () => {
    stats.ok += 1
    // 有瓦片成功后再复核，之前“无影像”的误报会被清掉
    if (error.value) evaluateCoverage()
  })
  source.on('tileloaderror', () => {
    stats.fail += 1
    scheduleCoverageCheck()
  })
  const layer = new TileLayer({ source, zIndex, useInterimTilesOnError: true })
  layer.set('layerName', label)
  layer.set('workspace', 'wayback')
  layer.set('compare', true)
  return layer
}

// 入参: { leftUrl, rightUrl, leftDate, rightDate }
// 方法: 移除旧对比层 → 灰色垫层 → 两张瓦片 → 挂裁剪 → 入图 → 重置分隔线
// 出参: boolean 是否成功
function startSwipe({ leftUrl, rightUrl, leftDate: ld, rightDate: rd }) {
  stopSwipe()
  const map = getMap()
  if (!map || !leftUrl || !rightUrl) return false
  error.value = ''
  leftStats.ok = 0
  leftStats.fail = 0
  rightStats.ok = 0
  rightStats.fail = 0
  backdropLayer = createBackdrop()
  leftLayer = createLayer(leftUrl, 7, 'wayback:left', 'left')
  rightLayer = createLayer(rightUrl, 6, 'wayback:right', 'right')
  clipLayer(leftLayer, 'left')
  clipLayer(rightLayer, 'right')
  map.addLayer(backdropLayer)
  map.addLayer(rightLayer)
  map.addLayer(leftLayer)
  leftDate.value = ld || ''
  rightDate.value = rd || ''
  ratio.value = 0.5
  active.value = true
  map.render()
  return true
}

// 入参: 无 | 方法: 移除对比层/垫层、解绑渲染事件、重置状态 | 出参: 无
function stopSwipe() {
  const map = getMap()
  if (map) {
    if (leftLayer) map.removeLayer(leftLayer)
    if (rightLayer) map.removeLayer(rightLayer)
    if (backdropLayer) map.removeLayer(backdropLayer)
  }
  leftLayer = null
  rightLayer = null
  backdropLayer = null
  cleanups.splice(0).forEach((fn) => fn())
  if (coverageTimer) {
    clearTimeout(coverageTimer)
    coverageTimer = null
  }
  leftStats.ok = 0
  leftStats.fail = 0
  rightStats.ok = 0
  rightStats.fail = 0
  if (ratioFrame) {
    cancelAnimationFrame(ratioFrame)
    ratioFrame = 0
  }
  active.value = false
  leftDate.value = ''
  rightDate.value = ''
  ratio.value = 0.5
  error.value = ''
  map?.render()
}

// 入参: value 0–1 | 方法: 设置卷帘比例；同一帧内合并多次 pointermove，并同步重绘，
//   避免 CSS 分隔线先动、canvas 裁剪晚一帧造成“手柄与影像缝隙错位”
// 出参: 无
let ratioFrame = 0
function setRatio(value) {
  const next = Number(value)
  if (!Number.isFinite(next)) return
  if (ratioFrame) cancelAnimationFrame(ratioFrame)
  ratioFrame = requestAnimationFrame(() => {
    ratioFrame = 0
    ratio.value = Math.min(0.95, Math.max(0.05, next))
    // 先写分隔线 DOM，再 renderSync：clipLayer 在 prerender 里读取这条线的实际像素
    const divider = document.querySelector('.swipe-divider')
    if (divider) divider.style.left = `${ratio.value * 100}%`
    getMap()?.renderSync()
  })
}

export function useSwipeCompare() {
  return {
    active,
    ratio,
    leftDate,
    rightDate,
    error,
    startSwipe,
    stopSwipe,
    setRatio,
  }
}
