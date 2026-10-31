// ==================== 地图单例: WMS/WFS/ImageStatic 叠加 + 图例 + GetFeatureInfo ====================
// ★ 对应原生 map/wms-render.js 的 _map + showWmsInPanel/showWfsInPanel/showImageInPanel; 走后端代理非直连 GeoServer
import { ref } from 'vue'
import TileLayer from 'ol/layer/Tile'
import ImageLayer from 'ol/layer/Image'
import VectorLayer from 'ol/layer/Vector'
import WMTS from 'ol/source/WMTS'
import TileWMS from 'ol/source/TileWMS'
import ImageStatic from 'ol/source/ImageStatic'
import XYZ from 'ol/source/XYZ'
import VectorSource from 'ol/source/Vector'
import GeoJSON from 'ol/format/GeoJSON'
import Style from 'ol/style/Style'
import Stroke from 'ol/style/Stroke'
import Fill from 'ol/style/Fill'
import Overlay from 'ol/Overlay'
import { transformExtent, toLonLat, get as getProjection } from 'ol/proj'
import { getWidth, getTopLeft } from 'ol/extent'
import WMTSTileGrid from 'ol/tilegrid/WMTS'
import { makeBoundarySLD } from '@/utils/sld'
import { getImageMeta } from '@/services/api'
import { debugLog } from '@/utils/debugLog'
import { createRetryingTileLoad } from '@/utils/tileLoader'

const WMS_URL = '/api/v1/geoserver/wms' // 后端代理(同时承载 WFS)
const TDT_KEY = import.meta.env.VITE_TDT_KEY || ''
const TDT_PROJECTION = 'EPSG:3857'
// 天地图支持 t0–t7 多子域；轮询可并行拉瓦片，降低单域排队
const TDT_HOSTS = Array.from({ length: 8 }, (_, i) => `https://t${i}.tianditu.gov.cn`)
const TDT_LEVELS = 18
// 瓦片源缓存上限（张）：默认 512 偏小，平移/回看时容易反复请求同一批瓦片
const TILE_CACHE_SIZE = 1024
// 图层定位：小图斑需要到 18–20 级才能看清；之前固定 maxZoom=16 会把 100m 图斑压成几十像素。
// padding 留出边距，让图层在视口内约占 80%，既有上下文又不至于过小。
const FIT_MAX_ZOOM = 20
const FIT_PADDING = [56, 56, 56, 56]
const DEFAULT_BOUNDARY = {
  name: 'chengdushi',
  workspace: 'cd_shp',
  fullName: 'cd_shp:chengdushi',
  strokeColor: '#4d6bfe',
}

// 矢量边界线样式(fill 透明 + stroke 主题蓝, 与 makeBoundarySLD 视觉一致)
const _vectorStyle = new Style({
  fill: new Fill({ color: 'rgba(0,0,0,0)' }),
  stroke: new Stroke({ color: '#4d6bfe', width: 2 }),
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
    // 视图是 3857：瓦片按 3857 向 GeoServer 请求，避免客户端再做一次逐瓦片重投影
    SRS: 'EPSG:3857',
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
  // ★ 已加载时只定位，不重复加层：图层管理里反复点名称不应叠出多个同名 WFS
  if (hasLayer(name, workspace)) {
    if (normalizedBbox && forceFit) fitExtent(normalizedBbox)
    return Promise.resolve({ loaded: true })
  }
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
  layer.set('workspace', 'imagery')
  layer.set('business', true)
  layer.set('imagery', true)
  layer.set('label', path.split(/[\\/]/).pop())
  _map.addLayer(layer)
  bumpImageryVersion()
  _map.getView().fit(extent, { padding: FIT_PADDING, maxZoom: FIT_MAX_ZOOM }) // 先 fit 视图触发懒加载

  return new Promise((resolve) => {
    let settled = false
    const cleanup = () => {
      if (_map) _map.removeLayer(layer)
      bumpImageryVersion()
    }
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true
        cleanup()
        resolve({ loaded: false, error: '影像加载超时 (15s)' })
      }
    }, 15000)
    source.on('imageloadend', () => { if (!settled) { settled = true; clearTimeout(timer); resolve({ loaded: true }) } })
    source.on('imageloaderror', () => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        cleanup()
        resolve({ loaded: false, error: '影像加载失败' })
      }
    })
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
  if (toRemove.some((layer) => layer.get('imagery') || layer.get('workspace') === 'imagery-outline')) {
    bumpImageryVersion()
  }
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
// 方法: 转换投影 → 按图层外接矩形自适应缩放；小图斑允许到 20 级，
//   避免 100m 级图斑被 maxZoom=16 压成几十像素；零面积范围回退到中心点。
// 出参: 无
function fitExtent(bbox) {
  if (!_map || !bbox) return
  const normalizedBbox = normalizeBbox(bbox)
  if (!normalizedBbox) return
  const view = _map.getView()
  const extent = transformExtent(normalizedBbox, 'EPSG:4326', view.getProjection())
  const [minx, miny, maxx, maxy] = extent
  if (![minx, miny, maxx, maxy].every(Number.isFinite)) return
  // 点/零面积图层：fit 无法计算分辨率，直接居中并给一个可读层级
  if (Math.abs(maxx - minx) < 1e-9 && Math.abs(maxy - miny) < 1e-9) {
    view.animate({ center: [(minx + maxx) / 2, (miny + maxy) / 2], zoom: 18, duration: 240 })
    return
  }
  view.fit(extent, {
    padding: FIT_PADDING,
    maxZoom: FIT_MAX_ZOOM,
    duration: 240,
  })
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
    urls: TDT_HOSTS.map(
      (host) => `${host}/${layerName}_${tileMatrixSet}/wmts?tk=${encodeURIComponent(TDT_KEY)}`,
    ),
    layer: layerName,
    matrixSet: tileMatrixSet,
    format: 'tiles',
    style: 'default',
    wrapX: true,
    crossOrigin: 'anonymous',
    projection: TDT_PROJECTION,
    tileGrid,
    cacheSize: TILE_CACHE_SIZE,
    transition: 0,
  })
  return new TileLayer({
    source,
    opacity,
    useInterimTilesOnError: true,
  })
}

// 入参: showLabel 是否显示 name 注记
// 方法: 移除旧成都市边界 WMS，再按底图状态重新注入边界 SLD；Esri/无底图时显示注记，天地图有自带注记时隐藏
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

// ★ 默认加载图层：Esri 影像底图 + 成都市边界（cd_shp:chengdushi，默认显示 name 注记）
export function loadDefaultLayers() {
  // 入参: 无（依赖模块级 _map）
  // 方法: setBasemap('esri') 注入 Esri 高分影像底图，并由 setBasemap 按实际底图状态同步成都市边界注记
  // 出参: 无
  if (!_map) return
  setBasemap('esri')
}

// 天地图底图类型映射：base=底图层, anno=注记层（天地图每种底图均由「底图+注记」两层叠加）
const TDT_BASEMAP_TYPES = {
  none: null,                         // 不显示底图，仅保留业务图层
  img: { base: 'img', anno: 'cia' },  // 卫星影像 + 影像注记
  vec: { base: 'vec', anno: 'cva' },  // 矢量电子地图 + 矢量注记
  ter: { base: 'ter', anno: 'cta' },  // 地形晕渲 + 地形注记
  esri: { esri: true },               // Esri World Imagery（高分底图，供框选解译取图）
}

const ESRI_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
// Wayback 官方主域在部分网络会被 DNS 污染；备用子域 wayback-a/b 可直连。
// 瓦片路径与 release.tileUrl 保持一致，默认用官方 default028mm（GoogleMapsCompatible 亦兼容）。
const WAYBACK_DEFAULT_HOST = 'https://wayback-a.maptiles.arcgis.com'
const WAYBACK_TILE_PATH = '/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile'

// 入参: message 错误信息 | 方法: 向页面冒泡一次底图错误（MapPanel 统一提示） | 出参: 无
function reportBasemapError(message) {
  window.dispatchEvent(new CustomEvent('rsiis:basemap-error', { detail: { message } }))
}

// 入参: template 瓦片模板, 坐标 | 方法: 用当前视图中心算一个低层级探活瓦片 | 出参: url
function buildProbeTileUrl(template) {
  if (!_map) return ''
  const center = _map.getView().getCenter()
  if (!center) return ''
  const [lon, lat] = toLonLat(center)
  const z = 5
  const n = 2 ** z
  const rad = (Math.max(-85, Math.min(85, lat)) * Math.PI) / 180
  const x = Math.max(0, Math.min(n - 1, Math.floor(((lon + 180) / 360) * n)))
  const y = Math.max(0, Math.min(n - 1, Math.floor(
    ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n,
  )))
  return template
    .replace('{z}', String(z))
    .replace('{y}', String(y))
    .replace('{x}', String(x))
}

// 入参: url 探活瓦片 | 方法: 用 Image 加载一次（带超时），判断该 host/release 是否可用 | 出参: Promise<boolean>
function probeTile(url, timeout = 7000) {
  return new Promise((resolve) => {
    if (!url) {
      resolve(false)
      return
    }
    const img = new Image()
    let settled = false
    const finish = (ok) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      img.onload = null
      img.onerror = null
      resolve(ok)
    }
    const timer = setTimeout(() => finish(false), timeout)
    img.crossOrigin = 'anonymous'
    img.onload = () => finish(img.naturalWidth > 0)
    img.onerror = () => finish(false)
    img.src = url
  })
}

// 入参: release { m, tileUrl } | 方法: 兼容官方 {level}/{row}/{col} 与本项目 {z}/{y}/{x} 模板 | 出参: 模板
function normalizeWaybackTemplate(release) {
  let template = String(release?.tileUrl || '')
  if (!template && release?.m) template = `${WAYBACK_DEFAULT_HOST}${WAYBACK_TILE_PATH}/${release.m}/{z}/{y}/{x}`
  template = template
    .replace('{level}', '{z}')
    .replace('{row}', '{y}')
    .replace('{col}', '{x}')
  return template.includes('{z}') && template.includes('{y}') && template.includes('{x}')
    ? template
    : ''
}

// 入参: layer, { onFail } | 方法: 统计首批瓦片；在「一块都没成功」时执行一次失败回调 | 出参: 无
function attachTileHealth(layer, { threshold = 6, onFail } = {}) {
  const source = layer?.getSource?.()
  if (!source) return
  let loaded = false
  let errors = 0
  let reported = false
  source.on('tileloadend', () => {
    loaded = true
  })
  source.on('tileloaderror', () => {
    errors += 1
    if (loaded || errors < threshold || reported) return
    reported = true
    onFail?.()
  })
}

// 当前底图类型（响应式，供 MapPanel.vue 渲染激活态按钮）
const _basemapType = ref('esri')

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
  if (conf.esri) {
    // Esri World Imagery: 高分底图，直接使用官方 XYZ 瓦片（CORS *）
    // ★ 视图同为 3857，瓦片无需重投影；cacheSize 加大让回看/小幅平移复用瓦片
    const esriLayer = new TileLayer({
      source: new XYZ({
        url: ESRI_TILE_URL,
        projection: 'EPSG:3857',
        crossOrigin: 'anonymous',
        maxZoom: 19,
        cacheSize: TILE_CACHE_SIZE,
        transition: 0,
        attributions: ['Esri, Maxar, Earthstar Geographics'],
      }),
      opacity: 1,
      // 缩放/平移时先用上一级瓦片顶上，避免出现空白底图
      useInterimTilesOnError: true,
    })
    esriLayer.set('basemap', true)
    esriLayer.setZIndex(0)
    _map.addLayer(esriLayer)
    // 底图一块都没加载成功时给出可读提示（仍保留图层，网络恢复后自动补瓦片）
    attachTileHealth(esriLayer, {
      threshold: 8,
      onFail: () => reportBasemapError('Esri 影像底图加载失败，请检查网络或代理设置'),
    })
    _basemapType.value = 'esri'
    // 成都市边界 + name 注记要显示在 Esri 底图上，否则默认 Esri 时看不到市界标注
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

// 入参: template 瓦片模板 | 方法: 在主域/备用子域之间换一个 host | 出参: 新模板或 ''
function altWaybackTemplate(template) {
  if (template.includes('wayback-a.')) return template.replace('wayback-a.', 'wayback-b.')
  if (template.includes('wayback-b.')) return template.replace('wayback-b.', 'wayback-a.')
  if (template.includes('//wayback.maptiles')) return template.replace('//wayback.maptiles', '//wayback-a.maptiles')
  return ''
}

// 入参: release { id, m, date, tileUrl } Esri Wayback 发布版本
// 方法: 先用一张低层级瓦片探活（避免不可达 host 把可用底图顶掉）→ 换图 → 统计首批瓦片；
//   探活失败时换备用子域再试，仍失败则保持当前底图并返回 false
// 出参: Promise<boolean> 是否切换成功
export async function setWaybackBasemap(release) {
  if (!_map || !release?.m) return false
  let template = normalizeWaybackTemplate(release)
  if (!template) return false
  const probeUrl = buildProbeTileUrl(template)
  if (!probeUrl) return false
  // 首次探活失败可能是瞬时抖动：等 700ms 再试一次
  let alive = await probeTile(probeUrl)
  if (!alive) {
    await new Promise((resolve) => setTimeout(resolve, 700))
    alive = await probeTile(probeUrl)
  }
  if (!alive) {
    // 清单域名与瓦片域名可能不同步：换 wayback-a/b 再探一次
    const alt = altWaybackTemplate(template)
    if (!alt) return false
    const altProbe = buildProbeTileUrl(alt)
    if (!altProbe || !(await probeTile(altProbe))) return false
    template = alt
  }
  // 探活成功才移除旧底图，保证切换过程中地图始终有影像
  const toRemove = []
  _map.getLayers().forEach((layer) => {
    if (layer.get('basemap')) toRemove.push(layer)
  })
  toRemove.forEach((layer) => _map.removeLayer(layer))
  const layer = new TileLayer({
    source: new XYZ({
      url: template,
      projection: 'EPSG:3857',
      crossOrigin: 'anonymous',
      maxZoom: 19,
      cacheSize: TILE_CACHE_SIZE,
      transition: 0,
      attributions: ['Esri, Maxar, Earthstar Geographics'],
    }),
    opacity: 1,
    useInterimTilesOnError: true,
  })
  layer.set('basemap', true)
  layer.set('waybackBasemap', true)
  layer.set('label', `Esri Wayback ${release.date || ''}`.trim())
  layer.setZIndex(0)
  _map.addLayer(layer)
  attachTileHealth(layer, {
    threshold: 8,
    onFail: () => {
      if (!_map || !_map.getLayers().getArray().includes(layer)) return
      setBasemap('esri')
      reportBasemapError('Esri 历史影像瓦片加载失败，已自动切回最新影像')
    },
  })
  _basemapType.value = 'esri'
  refreshDefaultBoundary(true)
  return true
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
      renderFeaturePopup(popupEl, [featureToPopupData(vectorHit.feature)], vectorHit.layer)
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
        renderFeaturePopup(popupEl, data.features, layer)
        overlay.setPosition(evt.coordinate)
        return true
      }).catch(() => false)
    }), Promise.resolve(false))
  })
}

// 入参: pixel 地图点击像素坐标
// 方法: 通过 OpenLayers 客户端命中检测查找 WFS VectorLayer 要素，命中容差覆盖建筑边线点击误差
//   ★ 影像轮廓（workspace=imagery-outline）默认不参与命中（框选范围框等无属性）；
//     只有登记了场景元信息（sceneMeta=true）的影像轮廓允许点击，用于弹出该景元信息
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
        && layer.get('layerName') !== 'chengduqu'
        && (layer.get('workspace') !== 'imagery-outline' || layer.get('sceneMeta') === true),
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

// 入参: value 任意属性值 | 方法: 转义 HTML，避免属性值破坏弹窗结构 | 出参: 安全文本
function escapeHtml(value) {
  return String(value ?? '—').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]))
}

// 入参: el 弹窗容器, features GeoJSON 数组, layer 命中图层
// 出参: 无 —— 构造属性表 HTML（标题+键值表+关闭）写入 el；
//   影像场景用 layer.popupTitle（影像元信息）作为标题，普通业务图层仍用图层名
function renderFeaturePopup(el, features, layer) {
  const title = layer?.get?.('popupTitle') || layer?.get?.('layerName') || '属性'
  const rows = Object.entries(features[0].properties || {})
    .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(v)}</td></tr>`).join('')
  el.innerHTML = `<div class="popup-header"><span class="popup-title">${escapeHtml(title)}</span>` +
    '<button class="popup-close" type="button">×</button></div>' +
    `<table class="popup-table"><tbody>${rows}</tbody></table>`
  el.querySelector('.popup-close').onclick = () => {
    el.innerHTML = ''
    _map?.getOverlays().forEach((o) => o.setPosition(undefined))
  }
}

// ==================== 影像检索: XYZ 预览 / 范围轮廓 / 视野 bbox ====================

// 影像预览图层版本号：地图按钮据此显示“清除预览”
const _imageryVersion = ref(0)
function bumpImageryVersion() {
  _imageryVersion.value += 1
}

export function getImageryVersion() {
  return _imageryVersion
}

// 检索结果的景轮廓样式（虚线蓝，和业务矢量图层区分）
const _footprintStyle = new Style({
  stroke: new Stroke({ color: '#4d6bfe', width: 1.5, lineDash: [7, 5] }),
  fill: new Fill({ color: 'rgba(77, 107, 254, 0.05)' }),
})

// 入参: { url 含 {z}/{x}/{y} 的瓦片模板, name 唯一图层名, label 展示名, bbox?, attribution? }
// 方法: 叠加外部 XYZ 瓦片（EPSG:3857，与当前 3857 视图同投影）→ 同名去重 → fit 范围
// 出参: { loaded, error? }
export function addXyzLayer({ url, name, bbox = null, attribution = '', label = '' }) {
  if (!_map) return { loaded: false, error: '地图未初始化' }
  if (!url) return { loaded: false, error: '缺少瓦片地址' }
  if (hasLayer(name, 'imagery')) {
    if (bbox) fitExtent(bbox)
    return { loaded: true }
  }
  const source = new XYZ({
    url,
    projection: 'EPSG:3857',
    crossOrigin: 'anonymous',
    cacheSize: TILE_CACHE_SIZE,
    transition: 0,
    // 影像预览瓦片（PC/ES）偶发失败，重试 1 次
    tileLoadFunction: createRetryingTileLoad({ retries: 1 }),
    attributions: attribution ? [attribution] : [],
  })
  const layer = new TileLayer({ source, zIndex: 12 })
  layer.set('layerName', name)
  layer.set('workspace', 'imagery')
  layer.set('business', true)
  layer.set('imagery', true)
  layer.set('label', label || name)
  if (bbox) {
    // ★ 限制图层范围: 景边界外的瓦片 PC 会返回 404, 设 extent 后 OL 不再请求
    //   bbox 是 4326，layer extent 必须用视图投影（3857）
    layer.setExtent(transformExtent(bbox, 'EPSG:4326', _map.getView().getProjection()))
    fitExtent(bbox)
  }
  _map.addLayer(layer)
  bumpImageryVersion()
  return { loaded: true }
}

// 入参: { url 图片地址, name 唯一图层名, bbox 4326, label? }
// 方法: 以 ImageStatic 叠加一张带范围图片（用于瓦片不可用时的快视图兜底）
// 出参: { loaded, error? }
export function addImageUrlLayer({ url, name, bbox, label = '' }) {
  if (!_map) return { loaded: false, error: '地图未初始化' }
  if (!url || !bbox) return { loaded: false, error: '缺少图片地址或范围' }
  if (hasLayer(name, 'imagery')) {
    fitExtent(bbox)
    return { loaded: true }
  }
  const source = new ImageStatic({
    url,
    imageExtent: bbox,
    projection: 'EPSG:4326',
    crossOrigin: 'anonymous',
  })
  const layer = new ImageLayer({ source, zIndex: 11 })
  layer.set('layerName', name)
  layer.set('workspace', 'imagery')
  layer.set('business', true)
  layer.set('imagery', true)
  layer.set('label', label || name)
  fitExtent(bbox)
  _map.addLayer(layer)
  bumpImageryVersion()
  return { loaded: true }
}

// 入参: { name, geometry GeoJSON geometry, bbox?, properties? 场景元信息, title? 弹窗标题 }
// 方法: 用虚线绘制单景 footprint，便于识别影像覆盖范围；
//   properties 非空时把该轮廓登记为「可点击场景」，点击影像弹出景号/日期/云量/数据源
// 出参: { loaded, error? }
export function addFootprintLayer({ name, geometry, bbox = null, properties = {}, title = '' }) {
  if (!_map) return { loaded: false, error: '地图未初始化' }
  if (!geometry) return { loaded: false, error: '缺少范围几何' }
  const layerName = `outline:${name}`
  if (hasLayer(layerName, 'imagery-outline')) {
    if (bbox) fitExtent(bbox)
    return { loaded: true }
  }
  const hasMeta = Object.keys(properties || {}).length > 0
  const feature = new GeoJSON().readFeature(
    { type: 'Feature', geometry, properties: hasMeta ? { ...properties } : {} },
    { featureProjection: _map.getView().getProjection() },
  )
  const source = new VectorSource({ features: [feature] })
  // zIndex 低于业务矢量（15），避免场景大面盖住解译图斑导致点不到建筑；
  // 又高于影像预览层（11/12），保证轮廓可见。
  const layer = new VectorLayer({ source, style: _footprintStyle, zIndex: 13 })
  layer.set('layerName', layerName)
  layer.set('workspace', 'imagery-outline')
  layer.set('business', true)
  layer.set('sceneMeta', hasMeta)
  layer.set('popupTitle', title || layerName)
  _map.addLayer(layer)
  bumpImageryVersion()
  return { loaded: true }
}

// 入参: 无
// 方法: 取当前视图范围并转 EPSG:4326（供「当前地图范围」检索）
// 出参: [minx, miny, maxx, maxy] 或 null
export function getViewBbox() {
  if (!_map) return null
  const size = _map.getSize()
  if (!size) return null
  const extent = _map.getView().calculateExtent(size)
  const [minx, miny, maxx, maxy] = transformExtent(
    extent, _map.getView().getProjection(), 'EPSG:4326',
  )
  return [minx, miny, maxx, maxy].map((v) => Number(v.toFixed(6)))
}

// 入参: 无
// 方法: 列出当前叠加的检索影像图层（不含轮廓）
// 出参: [{ name, label }]
export function listImageryLayers() {
  if (!_map) return []
  const layers = []
  _map.getLayers().forEach((layer) => {
    if (layer.get('imagery')) {
      layers.push({ name: layer.get('layerName'), label: layer.get('label') || layer.get('layerName') })
    }
  })
  return layers
}

// 入参: 无
// 方法: 移除全部检索影像预览图层与范围轮廓
// 出参: 移除数量
export function clearImageryLayers() {
  if (!_map) return 0
  const removable = []
  _map.getLayers().forEach((layer) => {
    if (layer.get('imagery') || layer.get('workspace') === 'imagery-outline') removable.push(layer)
  })
  removable.forEach((layer) => _map.removeLayer(layer))
  bumpImageryVersion()
  return removable.length
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
    addXyzLayer,
    addImageUrlLayer,
    addFootprintLayer,
    removeLayer,
    clearBusinessLayers,
    clearImageryLayers,
    listImageryLayers,
    getImageryVersion,
    getViewBbox,
    loadDefaultLayers,
    setBasemap,
    setWaybackBasemap,
    getBasemapType,
    setupFeatureInfo,
    getLegendState,
    setLegend,
    clearLegend,
  }
}
