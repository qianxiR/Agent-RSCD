// ==================== 地图单例: WMS/WFS/ImageStatic 叠加 + 图例 + GetFeatureInfo ====================
// ★ 对应原生 map/wms-render.js 的 _map + showWmsInPanel/showWfsInPanel/showImageInPanel; 走后端代理非直连 GeoServer
import { ref } from 'vue'
import TileLayer from 'ol/layer/Tile'
import ImageLayer from 'ol/layer/Image'
import VectorLayer from 'ol/layer/Vector'
import WMTS from 'ol/source/WMTS'
import TileWMS from 'ol/source/TileWMS'
import ImageStatic from 'ol/source/ImageStatic'
import VectorSource from 'ol/source/Vector'
import GeoJSON from 'ol/format/GeoJSON'
import Style from 'ol/style/Style'
import Stroke from 'ol/style/Stroke'
import Fill from 'ol/style/Fill'
import Overlay from 'ol/Overlay'
import { transformExtent, get as getProjection } from 'ol/proj'
import { getWidth, getTopLeft } from 'ol/extent'
import WMTSTileGrid from 'ol/tilegrid/WMTS'
import { makeBoundarySLD } from '@/utils/sld'
import { getImageMeta } from '@/services/api'
import { debugLog } from '@/utils/debugLog'

const WMS_URL = '/api/v1/geoserver/wms' // 后端代理(同时承载 WFS)
const TDT_KEY = import.meta.env.VITE_TDT_KEY || ''
const TDT_PROJECTION = 'EPSG:3857'
const TDT_BASE_URL = 'https://t0.tianditu.gov.cn'
const TDT_LEVELS = 18
const DEFAULT_BOUNDARY = {
  name: 'chengdushi',
  workspace: 'cd_shp',
  fullName: 'cd_shp:chengdushi',
  strokeColor: '#2c6fbd',
}

// 矢量边界线样式(fill 透明 + stroke 主题蓝, 与 makeBoundarySLD 视觉一致)
const _vectorStyle = new Style({
  fill: new Fill({ color: 'rgba(0,0,0,0)' }),
  stroke: new Stroke({ color: '#2c6fbd', width: 2 }),
})

let _map = null // 模块级单例

// 图例浮层状态（响应式，供 MapPanel.vue 渲染 LegendOverlay）
const _legendState = ref({ title: '', items: [] })

export function setMap(map) {
  _map = map
}
export function getMap() {
  return _map
}
export function clearMap() {
  _map = null
}

// 入参: name, workspace
// 出参: 地图上是否存在该业务图层
export function hasLayer(name, workspace) {
  if (!_map) return false
  let found = false
  _map.getLayers().forEach((layer) => {
    if (layer.get('layerName') === name && (layer.get('workspace') || '') === (workspace || '')) {
      found = true
    }
  })
  return found
}

// 入参: { name, workspace, bbox?, sldBody?, layerType?, caption? }
// 方法: 叠加 TileWMS 图层; 由调用方决定是否传 sldBody (矢量图层应传 makeBoundarySLD, 与 loadDefaultLayers 统一)
//   bbox 提供时定位；同名去重（已存在只 fit）
// 出参: { loaded: boolean, error?: string }
export function addWmsLayer({ name, workspace, bbox = null, sldBody = null, layerType = 'unknown' }) {
  if (!_map) return { loaded: false, error: '地图未初始化' }
  const normalizedBbox = normalizeBbox(bbox)
  if (hasLayer(name, workspace)) {
    if (normalizedBbox) fitExtent(normalizedBbox)
    return { loaded: true }
  }
  // 矢量自动注入边界线 SLD（面透明+描边+name标注），栅格/unknown 不注入
  const finalSld = sldBody || null
  // ★ 对齐原生 _showTileWmsFallback 的 params: FORMAT/TRANSPARENT 保证 PNG 透明通道输出;
  //   STYLES='' 清空默认样式, 确保 SLD_BODY 生效 (否则 GeoServer 可能用内置 style 覆盖);
  //   SRS 显式声明坐标系, 避免 OL 默认行为在不同版本间的差异。
  const params = {
    LAYERS: `${workspace}:${name}`,
    FORMAT: 'image/png',
    TILED: true,
    TRANSPARENT: true,
    VERSION: '1.1.1',
    SRS: 'EPSG:4326',
  }
  if (finalSld) {
    params.SLD_BODY = finalSld
    params.STYLES = ''  // ★ 清空默认样式, 确保 SLD_BODY 生效
  }

  const source = new TileWMS({
    url: WMS_URL,
    params,
    serverType: 'geoserver',
    transition: 0,
  })
  const layer = new TileLayer({ source, zIndex: 10 })
  layer.set('layerName', name)
  layer.set('workspace', workspace)
  layer.set('business', true)

  // ★ 先 fit 视图到图层范围, 再 addLayer。
  //   为什么: addLayer 后 OL 立即按当前 view 计算瓦片坐标 → 若当前 view 未覆盖极小范围图层
  //   (如 0.01° 的分割结果), tileUrlFunction 因 tileCoord 无法映射到有效 bbox 而返回 undefined,
  //   所有瓦片直接进入 tileloaderror(url=undefined), 后续 fitExtent 不会让已失败的 tile 重生。
  //   先 fit 后 addLayer 确保 layer 入图时 view 已在目标范围, tileGrid 能正确生成瓦片坐标。
  if (normalizedBbox) fitExtent(normalizedBbox)
  _map.addLayer(layer)

  // ★ 诊断日志（写根目录 wms-debug.log）：定位「瓦片不显示」根因
  debugLog(`addWmsLayer name=${name} ws=${workspace} type=${layerType} bbox=${normalizedBbox ? normalizedBbox.join(',') : 'null'} params=${JSON.stringify(params)}`)
  let _firstUrl = ''
  source.on('tileloadstart', (e) => {
    if (!_firstUrl) {
      _firstUrl = e.tile?.getSrc?.() || '(无URL)'
      debugLog(`首个瓦片URL [${name}]: ${_firstUrl}`)
    }
  })
  source.on('tileloadend', () => debugLog(`瓦片加载成功 ✓ [${name}]`))
  source.on('tileloaderror', (e) => debugLog(`瓦片加载失败 ✗ [${name}] url=${e.tile?.getSrc?.()}`))

  return { loaded: true }
}

// 入参: { name, workspace, bbox?, forceFit? }
// 方法: 构造 WFS GetFeature URL（走代理 outputFormat=application/json）→ VectorSource(GeoJSON)
//   + VectorLayer（边界线样式，与 WMS makeBoundarySLD 视觉一致）→ addLayer
//   bbox && forceFit 时定位（polygon/edge 传 false 避免抢底图视角）
// 出参: Promise<{loaded, error?}> —— 监听 featuresloadend/error + 10s 超时
export function addWfsLayer({ name, workspace, bbox = null, forceFit = false }) {
  if (!_map) return Promise.resolve({ loaded: false, error: '地图未初始化' })
  const normalizedBbox = normalizeBbox(bbox)
  const typeName = workspace ? `${workspace}:${name}` : name
  const params = new URLSearchParams({
    service: 'WFS',
    version: '1.1.0',
    request: 'GetFeature',
    typeName,
    outputFormat: 'application/json',
    srsName: 'EPSG:4326',
  })
  const source = new VectorSource({ url: `${WMS_URL}?${params}`, format: new GeoJSON() })
  const layer = new VectorLayer({ source, style: _vectorStyle, zIndex: 15 }) // 矢量在最上层，不被影像遮盖
  layer.set('layerName', name)
  layer.set('workspace', workspace)
  layer.set('business', true)
  _map.addLayer(layer)

  // 异步等待 features 加载结果（featuresloadend/error + 10s 超时，对齐原生 showWfsInPanel）
  return new Promise((resolve) => {
    let settled = false
    const done = (loaded, error = null) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (loaded && normalizedBbox && forceFit) fitExtent(normalizedBbox)
      resolve({ loaded, error })
    }
    const timer = setTimeout(() => done(false, 'WFS 加载超时 (10s)'), 10000)
    source.on('featuresloadend', () => done(true))
    source.on('featuresloaderror', () => done(false, 'WFS 加载失败'))
  })
}

// 入参: { path } 影像绝对路径
// 方法: getImageMeta 验证取 bbox/CRS → 判定投影 → ImageStatic(源 CRS 坐标) → fit 视图(懒加载必须) → 监听结果
// 出参: Promise<{loaded, error?}> —— fire-and-forget, 加载与分割等操作互不阻塞
export async function addStaticImageLayer({ path }) {
  if (!_map) return { loaded: false, error: '地图未初始化' }
  const meta = await getImageMeta(path)
  if (!meta.has_crs || !meta.bbox) return { loaded: false, error: meta.msg || '影像无地理坐标' }
  // bbox 值>180 为投影坐标, 否则经纬度; imageExtent 必须用源 CRS 坐标配 projection
  const proj = Math.abs(meta.bbox[0]) > 180 ? 'EPSG:3857' : 'EPSG:4326'
  const extent = transformExtent(meta.bbox, proj, _map.getView().getProjection())
  const source = new ImageStatic({ url: buildImageUrl(path), imageExtent: meta.bbox, projection: proj })
  const layer = new ImageLayer({ source, zIndex: 5 }) // 备用：静态影像叠加层
  layer.set('layerName', path.split(/[\\/]/).pop())
  layer.set('business', true)
  _map.addLayer(layer)
  _map.getView().fit(extent, { padding: [40, 40, 40, 40], maxZoom: 16 }) // 先 fit 视图触发懒加载

  return new Promise((resolve) => {
    let settled = false
    const timer = setTimeout(() => { if (!settled) { settled = true; resolve({ loaded: false, error: '影像加载超时 (15s)' }) } }, 15000)
    source.on('imageloadend', () => { if (!settled) { settled = true; clearTimeout(timer); resolve({ loaded: true }) } })
    source.on('imageloaderror', () => { if (!settled) { settled = true; clearTimeout(timer); resolve({ loaded: false, error: '影像加载失败' }) } })
  })
}

// 入参: path 影像绝对路径
// 出参: 预览 URL —— samseg/send/ 下走 /upload/, 否则 /download/; 带 ?preview=1 触发 TIFF→PNG 转码
function buildImageUrl(path) {
  const norm = path.replace(/\\/g, '/')
  const idx = norm.indexOf('samseg/send/')
  const rel = idx >= 0 ? norm.slice(idx + 'samseg/send/'.length) : norm.split('agent-files/')[1] || norm
  return `${idx >= 0 ? '/api/v1/upload/' : '/api/v1/download/'}${rel}?preview=1`
}

// 入参: name, workspace
// 方法: 从地图移除指定业务图层
export function removeLayer(name, workspace) {
  if (!_map) return
  const toRemove = []
  _map.getLayers().forEach((layer) => {
    if (layer.get('layerName') === name && (layer.get('workspace') || '') === (workspace || '')) {
      toRemove.push(layer)
    }
  })
  toRemove.forEach((layer) => _map.removeLayer(layer))
}

// 清空所有业务图层（联动清图例，对齐原生 _clearMapLayers 的图例清理）
export function clearBusinessLayers() {
  if (!_map) return
  const toRemove = []
  _map.getLayers().forEach((layer) => {
    if (layer.get('business')) toRemove.push(layer)
  })
  toRemove.forEach((layer) => _map.removeLayer(layer))
  clearLegend()
}

// ==================== 图例浮层状态 ====================
// 入参: 无
// 出参: 图例响应式状态 ref（{ title, items:[{name,hex}] }），供 MapPanel.vue 渲染
export function getLegendState() {
  return _legendState
}

// 入参: title 图例标题, items 图例项数组 [{name, hex}]
// 方法: 覆盖写入图例状态（同会话只保留最新一个，对齐原生 _renderLegendOverlay 去重）
// 出参: 无（副作用：更新 _legendState）
export function setLegend(title = '图例', items = []) {
  _legendState.value = { title, items: Array.isArray(items) ? items : [] }
}

// 清空图例（clearBusinessLayers 联动调用，或在切换对话时显式调用）
export function clearLegend() {
  _legendState.value = { title: '', items: [] }
}

// 入参: bbox [minx,miny,maxx,maxy] 或 {minx,miny,maxx,maxy}, EPSG:4326
// 方法: 将后端对象形态和前端数组形态统一为 OpenLayers 需要的 extent 数组。
// 出参: number[4] 或 null。
function normalizeBbox(bbox) {
  if (!bbox) return null
  const values = Array.isArray(bbox)
    ? bbox
    : [bbox.minx, bbox.miny, bbox.maxx, bbox.maxy]
  if (values.length !== 4) return null
  const nums = values.map((v) => Number(v))
  return nums.every(Number.isFinite) ? nums : null
}

// 入参: bbox [minx,miny,maxx,maxy] 或 {minx,miny,maxx,maxy}, EPSG:4326
// 方法: 转换投影并 fit 视图
function fitExtent(bbox) {
  if (!_map || !bbox) return
  const normalizedBbox = normalizeBbox(bbox)
  if (!normalizedBbox) return
  const view = _map.getView()
  const extent = transformExtent(normalizedBbox, 'EPSG:4326', view.getProjection())
  view.fit(extent, { padding: [40, 40, 40, 40], maxZoom: 16 })
}

// 入参:
//   layerName: 天地图图层名, 例如 'img' 或 'cia'; tileMatrixSet: 分辨率矩阵集, 固定为 'w'。
// 方法:
//   构造天地图 WMTS 瓦片源与图层, 使用 3857 瓦片在当前 4326 视图中自动重投影;
//   这样既保留现有业务 WMS 的经纬度逻辑, 又能把影像底图挂成默认基底。
// 出参:
//   返回一个已配置好的 TileLayer; 若 key 缺失仍返回图层对象, 仅会在网络层加载失败。
function createTdtLayer(layerName, tileMatrixSet, opacity) {
  const projection = getProjection(TDT_PROJECTION)
  const projectionExtent = projection.getExtent()
  const tileGrid = new WMTSTileGrid({
    origin: getTopLeft(projectionExtent),
    resolutions: Array.from({ length: TDT_LEVELS }, (_, z) => getWidth(projectionExtent) / 256 / Math.pow(2, z)),
    matrixIds: Array.from({ length: TDT_LEVELS }, (_, z) => String(z)),
  })
  const source = new WMTS({
    url: `${TDT_BASE_URL}/${layerName}_${tileMatrixSet}/wmts?tk=${encodeURIComponent(TDT_KEY)}`,
    layer: layerName,
    matrixSet: tileMatrixSet,
    format: 'tiles',
    style: 'default',
    wrapX: true,
    crossOrigin: 'anonymous',
    projection: TDT_PROJECTION,
    tileGrid,
  })
  return new TileLayer({
    source,
    opacity,
  })
}

// 入参: showLabel 是否显示 name 注记
// 方法: 移除旧成都市边界 WMS，再按底图状态重新注入边界 SLD；底图为 none 时显示注记，其余底图隐藏注记
// 出参: 无
function refreshDefaultBoundary(showLabel = false) {
  if (!_map) return
  removeLayer(DEFAULT_BOUNDARY.name, DEFAULT_BOUNDARY.workspace)
  addWmsLayer({
    name: DEFAULT_BOUNDARY.name,
    workspace: DEFAULT_BOUNDARY.workspace,
    sldBody: makeBoundarySLD(DEFAULT_BOUNDARY.fullName, DEFAULT_BOUNDARY.strokeColor, showLabel),
  })
}

// ★ 默认加载图层：天地图影像底图 + 成都市边界（cd_shp:chengdushi，默认不显示 name 注记）
export function loadDefaultLayers() {
  // 入参: 无（依赖模块级 _map）
  // 方法: setBasemap('img') 注入影像底图，并由 setBasemap 按实际底图状态同步成都市边界注记
  // 出参: 无
  if (!_map) return
  setBasemap('img')
}

// 天地图底图类型映射：base=底图层, anno=注记层（天地图每种底图均由「底图+注记」两层叠加）
const TDT_BASEMAP_TYPES = {
  none: null,                         // 不显示底图，仅保留业务图层
  img: { base: 'img', anno: 'cia' },  // 卫星影像 + 影像注记
  vec: { base: 'vec', anno: 'cva' },  // 矢量电子地图 + 矢量注记
  ter: { base: 'ter', anno: 'cta' },  // 地形晕渲 + 地形注记
}

// 当前底图类型（响应式，供 MapPanel.vue 渲染激活态按钮）
const _basemapType = ref('img')

export function getBasemapType() {
  return _basemapType
}

// 入参: type 底图类型 ('none'|'img'|'vec'|'ter')
// 方法: 移除所有旧底图层（basemark 标记）→ 按类型注入新的底图层+注记层（zIndex 0/1）
//   底图与业务图层用 'basemap' 标记隔离，互不影响；无 key 时告警不抛错
// 出参: 无
export function setBasemap(type) {
  if (!_map) return
  if (!(type in TDT_BASEMAP_TYPES)) return
  const conf = TDT_BASEMAP_TYPES[type]
  // 移除旧底图层（保留业务图层与边界）
  const toRemove = []
  _map.getLayers().forEach((layer) => {
    if (layer.get('basemap')) toRemove.push(layer)
  })
  toRemove.forEach((layer) => _map.removeLayer(layer))
  if (!conf) {
    _basemapType.value = type
    refreshDefaultBoundary(true)
    return
  }
  if (!TDT_KEY) {
    console.warn('[useMap] 未配置 VITE_TDT_KEY，地图将无天地图底图。请在 frontend-vue/.env 中设置并重启 dev server')
    _basemapType.value = 'none'
    refreshDefaultBoundary(true)
    return
  }
  const baseLayer = createTdtLayer(conf.base, 'w', 1)
  const annoLayer = createTdtLayer(conf.anno, 'w', 1)
  baseLayer.set('basemap', true)
  annoLayer.set('basemap', true)
  baseLayer.setZIndex(0)
  annoLayer.setZIndex(1)
  _map.addLayer(baseLayer)
  _map.addLayer(annoLayer)
  _basemapType.value = type
  refreshDefaultBoundary(false)
}

// ★ GetFeatureInfo：点击地图查要素属性
// 入参: popupEl 弹窗 DOM（OL Overlay 锚点）
// 方法: singleclick → 先命中客户端 WFS VectorLayer → 未命中再遍历 WMS 图层 getFeatureInfoUrl(JSON)
// 出参: 无（结果写入 popupEl + 定位 Overlay）
export function setupFeatureInfo(popupEl) {
  if (!_map || !popupEl) return
  const overlay = new Overlay({ element: popupEl, autoPan: true, autoPanAnimation: { duration: 250 } })
  _map.addOverlay(overlay)
  _map.on('singleclick', (evt) => {
    const vectorHit = findVectorFeatureAtPixel(evt.pixel)
    if (vectorHit) {
      renderFeaturePopup(popupEl, [featureToPopupData(vectorHit.feature)], vectorHit.layer.get('layerName'))
      overlay.setPosition(evt.coordinate)
      return
    }
    const viewResolution = _map.getView().getResolution()
    const projection = _map.getView().getProjection()
    const layers = []
    _map.getLayers().forEach((l) => {
      if (l.get('business') && l.getVisible() && l.get('layerName') !== 'chengduqu') layers.push(l)
    })
    if (!layers.length) return
    // 逐图层尝试（命中即停）
    layers.reduce((chain, layer) => chain.then((done) => {
      if (done) return true
      const source = layer.getSource()
      if (typeof source.getFeatureInfoUrl !== 'function') return false
      const url = source.getFeatureInfoUrl(evt.coordinate, viewResolution, projection, {
        INFO_FORMAT: 'application/json', FEATURE_COUNT: 5,
      })
      if (!url) return false
      return fetch(url).then((r) => r.json()).then((data) => {
        if (!(data.features || []).length) return false
        renderFeaturePopup(popupEl, data.features, layer.get('layerName'))
        overlay.setPosition(evt.coordinate)
        return true
      }).catch(() => false)
    }), Promise.resolve(false))
  })
}

// 入参: pixel 地图点击像素坐标
// 方法: 通过 OpenLayers 客户端命中检测查找 WFS VectorLayer 要素，命中容差覆盖建筑边线点击误差
// 出参: { feature, layer } 或 null
function findVectorFeatureAtPixel(pixel) {
  if (!_map) return null
  const hit = _map.forEachFeatureAtPixel(
    pixel,
    (feature, layer) => ({ feature, layer }),
    {
      hitTolerance: 6,
      layerFilter: (layer) => layer instanceof VectorLayer
        && layer.get('business')
        && layer.getVisible()
        && layer.get('layerName') !== 'chengduqu',
    },
  )
  return hit || null
}

// 入参: feature OpenLayers 要素
// 方法: 提取属性并移除 geometry，转换为属性弹窗使用的 GeoJSON-like 数据结构
// 出参: { properties: Record<string, unknown> }
function featureToPopupData(feature) {
  const properties = { ...(feature?.getProperties?.() || {}) }
  delete properties.geometry
  return { properties }
}

// 入参: el, features GeoJSON 数组, layerName
// 出参: 无 —— 构造属性表 HTML（标题+键值表+关闭）写入 el
function renderFeaturePopup(el, features, layerName) {
  const rows = Object.entries(features[0].properties || {})
    .map(([k, v]) => `<tr><td>${k}</td><td>${v ?? '—'}</td></tr>`).join('')
  el.innerHTML = `<div class="popup-header"><span class="popup-title">${layerName}</span>` +
    `<button class="popup-close" type="button">×</button></div><table class="popup-table"><tbody>${rows}</tbody></table>`
  el.querySelector('.popup-close').onclick = () => {
    el.innerHTML = ''
    _map?.getOverlays().forEach((o) => o.setPosition(undefined))
  }
}

export function useMap() {
  return {
    setMap,
    getMap,
    clearMap,
    hasLayer,
    addWmsLayer,
    addWfsLayer,
    addStaticImageLayer,
    removeLayer,
    clearBusinessLayers,
    loadDefaultLayers,
    setBasemap,
    getBasemapType,
    setupFeatureInfo,
    getLegendState,
    setLegend,
    clearLegend,
  }
}
