// ==================== 开源遥感影像检索 API ====================
// 走 Python 服务 /api/v1/imagery/*（Vite proxy 已把 /api 转发到 8020）。

async function request(url, options = {}) {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.msg || data.detail || `请求失败 (${response.status})`)
  }
  return data
}

// 入参: 无
// 出参: { status, providers:[{key,label,configured,supports,collections,...}], max_area_deg2 }
export function getImageryProviders() {
  return request('/api/v1/imagery/providers')
}

// 入参: { providers, collections, bbox, start, end, cloud_max, max_gsd, limit, sort }
// 出参: { status, items, count, total, errors }
export function searchImagery(body) {
  return request('/api/v1/imagery/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// 入参: { provider, collection, item, bbox, name? }
// 出参: { status, file_name, file_path, download_url, bbox, width, height, ... }
export function downloadImagery(body) {
  return request('/api/v1/imagery/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// 入参: { source: 'esri'|'tianditu', bbox, target_gsd }
// 出参: { status, file_path, preview_url, bbox, crs, width, height, gsd, ... }
export function captureImagery(body) {
  return request('/api/v1/imagery/capture', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// 入参: { bbox, t1_date, t2_date, window_days?, cloud_max?, collection? }
// 出参: { status, source, t1:{file_path,preview_url,acquired_at,...}, t2:{...} }
// 说明: legacy Sentinel-2 路径，当前变化检测 UI 已改用 Esri Wayback + georeference。
export function captureChangeImagery(body) {
  return request('/api/v1/imagery/change-captures', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// 入参: { blob 浏览器拼接的 Esri 截图, bbox [minx,miny,maxx,maxy], name, date }
// 出参: { status, file_path, preview_url, bbox, crs, width, height, gsd, ... }
export async function georeferenceImagery({ blob, bbox, name = '', date = '' }) {
  const form = new FormData()
  form.append('file', blob, `${name || 'wayback'}.png`)
  form.append('bbox', JSON.stringify(bbox))
  form.append('name', name)
  form.append('date', date)
  const response = await fetch('/api/v1/imagery/georeference', { method: 'POST', body: form })
  const data = await response.json().catch(() => ({}))
  if (!response.ok || data.status === 'error') {
    throw new Error(data.msg || data.detail || `截图配准失败 (${response.status})`)
  }
  return data
}

// ==================== Esri Wayback 历史版本 ====================
// 背景: 官方主域 wayback.maptiles.arcgis.com 在部分网络会被 DNS 污染/重置连接，
//   但官方备用子域 wayback-a / wayback-b 可直连且带 CORS。这里按顺序探测，
//   首个能返回版本清单的域名作为本轮瓦片域名，避免用户选完日期后整片空白。
// 说明: 瓦片路径使用官方 WMTS default028mm 矩阵集（与 3857 视图一致，无需重投影）。
const WAYBACK_HOSTS = [
  'https://wayback-a.maptiles.arcgis.com',
  'https://wayback-b.maptiles.arcgis.com',
  'https://wayback.maptiles.arcgis.com',
]
const WAYBACK_SERVICE_PATH = '/arcgis/rest/services/World_Imagery'
const WAYBACK_TILE_PATH = `${WAYBACK_SERVICE_PATH}/WMTS/1.0.0/default028mm/MapServer/tile`
// 缓存键升级到 v2：旧缓存里的 tileUrl 可能指向不可达的主域，必须丢弃
const WAYBACK_CACHE_KEY = 'rsiis:wayback:releases:v2'
const WAYBACK_CACHE_TTL = 6 * 3600 * 1000
const WAYBACK_FETCH_TIMEOUT = 8000
let waybackMemoryCache = null

// 内置年度快照：清单接口全部不可达时兜底（每年取最新发布版），保证双时相入口不空
// 说明: m 为 Esri release 号（与官方 Selection 的 M 字段一致），改动前请先用瓦片探活
const WAYBACK_FALLBACK = [
  { date: '2026-10-29', id: 'WB_2026_R10', m: '43291' },
  { date: '2025-12-18', id: 'WB_2025_R12', m: '13192' },
  { date: '2024-12-12', id: 'WB_2024_R13', m: '16453' },
  { date: '2023-12-07', id: 'WB_2023_R11', m: '56102' },
  { date: '2022-12-14', id: 'WB_2022_R15', m: '45134' },
  { date: '2021-12-21', id: 'WB_2021_R17', m: '26120' },
  { date: '2020-12-16', id: 'WB_2020_R16', m: '29260' },
  { date: '2019-12-12', id: 'WB_2019_R16', m: '4756' },
  { date: '2018-12-14', id: 'WB_2018_R17', m: '23448' },
  { date: '2017-11-16', id: 'WB_2017_R19', m: '25521' },
  { date: '2016-12-20', id: 'WB_2016_R22', m: '18966' },
  { date: '2015-12-16', id: 'WB_2015_R23', m: '28163' },
  { date: '2014-12-30', id: 'WB_2014_R21', m: '5844' },
]

// 入参: item 官方 Selection 项, host 可用域名
// 方法: 解析发布日期与 release 号，生成可直接给 OpenLayers XYZ 的瓦片模板
// 出参: release 对象或 null
function toWaybackRelease(item, host) {
  const date = /(\d{4}-\d{2}-\d{2})/.exec(item.Name || item.date || '')?.[1] || ''
  const rawId = item.M || item.m
  if (!date || !rawId) return null
  const m = String(rawId)
  return {
    id: item.ID || item.id || `WB_${date}_${m}`,
    m,
    date,
    label: date,
    tileUrl: `${host}${WAYBACK_TILE_PATH}/${m}/{z}/{y}/{x}`,
    maxZoom: 19,
  }
}

// 入参: 无 | 方法: 用首选域名生成内置年度快照 | 出参: release 数组（日期倒序）
function fallbackWaybackReleases() {
  return WAYBACK_FALLBACK.map((item) => toWaybackRelease(item, WAYBACK_HOSTS[0]))
    .filter(Boolean)
    .sort((a, b) => b.date.localeCompare(a.date))
}

// 入参: 无 | 方法: 读取内存/本地缓存（6 小时内有效） | 出参: release 数组或 null
function readWaybackCache() {
  if (waybackMemoryCache && Date.now() - waybackMemoryCache.at < WAYBACK_CACHE_TTL) {
    return waybackMemoryCache.data
  }
  try {
    const raw = localStorage.getItem(WAYBACK_CACHE_KEY)
    if (!raw) return null
    const cached = JSON.parse(raw)
    if (!cached?.at || Date.now() - cached.at > WAYBACK_CACHE_TTL) return null
    if (!Array.isArray(cached.data) || !cached.data.length) return null
    waybackMemoryCache = cached
    return cached.data
  } catch {
    return null
  }
}

// 入参: releases | 方法: 写内存 + localStorage（隐私模式写入失败忽略） | 出参: 无
function writeWaybackCache(releases) {
  waybackMemoryCache = { at: Date.now(), data: releases }
  try {
    localStorage.setItem(WAYBACK_CACHE_KEY, JSON.stringify(waybackMemoryCache))
  } catch {
    /* 隐私模式/配额不足时忽略 */
  }
}

// 入参: host 候选域名
// 方法: 拉取 World_Imagery MapServer 元数据 → 解析 Selection → 按日期倒序
// 出参: Promise<release[]>；失败抛错（由上层切下一个域名）
async function fetchWaybackReleases(host) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), WAYBACK_FETCH_TIMEOUT)
  try {
    const response = await fetch(`${host}${WAYBACK_SERVICE_PATH}/MapServer?f=json`, {
      signal: controller.signal,
      cache: 'no-store',
    })
    if (!response.ok) throw new Error(`Wayback 版本清单请求失败 (${response.status})`)
    const payload = await response.json()
    const releases = (payload.Selection || [])
      .map((item) => toWaybackRelease(item, host))
      .filter(Boolean)
      .sort((a, b) => (a.date === b.date
        ? Number(b.m) - Number(a.m)
        : b.date.localeCompare(a.date)))
    if (!releases.length) throw new Error('Wayback 未返回可用版本')
    return releases
  } finally {
    clearTimeout(timer)
  }
}

// 入参: { force? } 是否跳过缓存
// 方法: 依次探测 wayback-a → wayback-b → 主域；首个可用域名写入每个 release.tileUrl；
//   全部不可达时返回内置年度快照（source=fallback），不阻塞入口
// 出参: { releases:[{id,m,date,label,tileUrl,maxZoom}], source, host, error? }
export async function getEsriWaybackReleases({ force = false } = {}) {
  if (!force) {
    const cached = readWaybackCache()
    if (cached) return { releases: cached, source: 'cache' }
  }
  let lastError = null
  for (const host of WAYBACK_HOSTS) {
    try {
      const releases = await fetchWaybackReleases(host)
      writeWaybackCache(releases)
      return { releases, source: 'network', host }
    } catch (e) {
      lastError = e
    }
  }
  const releases = fallbackWaybackReleases()
  writeWaybackCache(releases)
  return {
    releases,
    source: 'fallback',
    host: WAYBACK_HOSTS[0],
    error: lastError?.name === 'AbortError' ? 'Wayback 版本清单请求超时' : (lastError?.message || ''),
  }
}
