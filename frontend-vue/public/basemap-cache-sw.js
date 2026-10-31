/* ==================== 底图瓦片持久缓存 Service Worker ====================
 * 目标: Esri / Esri Wayback / 天地图 瓦片一次加载后写入 Cache Storage，
 *   平移、回看、刷新页面直接命中本地缓存，不再走公网；这是「缓存到系统」的浏览器侧实现。
 * 策略: 只拦截瓦片 GET 请求（不碰 /api、页面资源和 HMR），cache-first；
 *   30 天过期、超过 MAX_TILES 张按插入顺序淘汰；瓦片 URL 自带版本/日期，天然 immutable。
 * 说明: 页面侧通过 src/utils/tileCache.js 读取/清理同一个 Cache。
 */
const CACHE_NAME = 'rsiis-tiles-v2'
const CACHE_PREFIX = 'rsiis-tiles-'
const MAX_TILES = 2500
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000
const CACHED_AT = 'x-rsiis-cached-at'

// 只缓存 Esri 影像域（底图/历史时相/卷帘都走这里）。
// 天地图按 Referer 校验浏览器 Key，Service Worker 回源可能改变 Referer，先不接管，避免 403。
const TILE_HOSTS = [
  'server.arcgisonline.com',
  'wayback.maptiles.arcgis.com',
  'wayback-a.maptiles.arcgis.com',
  'wayback-b.maptiles.arcgis.com',
]

// 入参: url 请求地址 | 方法: 判断是否为底图瓦片请求（ArcGIS /tile/z/y/x、天地图 /wmts） | 出参: boolean
function isTileRequest(url) {
  if (!TILE_HOSTS.includes(url.hostname)) return false
  return url.pathname.includes('/tile/') || url.pathname.includes('/wmts')
}

// 入参: 无 | 方法: 激活后立刻接管页面，避免首次加载不受控 | 出参: 无
self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // 清掉旧版本缓存（例如曾把代理返回的 HTML 错误页当成瓦片存下来）
    const names = await caches.keys()
    await Promise.all(
      names.filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
        .map((name) => caches.delete(name)),
    )
    await self.clients.claim()
  })())
})

// 入参: 请求 | 方法: 缓存命中且未过期直接返回；未命中/过期回源并写入缓存；回源失败退回旧瓦片 | 出参: Response
async function cacheFirst(request) {
  let cache = null
  try {
    cache = await caches.open(CACHE_NAME)
  } catch {
    // Cache API 不可用（隐私模式/配额异常）→ 直接回源，不能把底图搞挂
    return fetch(request)
  }
  const hit = await cache.match(request, { ignoreVary: true })
  if (hit) {
    const age = Date.now() - Number(hit.headers.get(CACHED_AT) || 0)
    if (!Number.isFinite(age) || age < MAX_AGE_MS) return hit
  }
  try {
    const response = await fetch(request)
    // 只缓存真正的图片响应：代理/网关故障时可能返回 200 的 HTML 错误页，
    // 一旦写进缓存会在有效期内一直坏图。
    const contentType = (response.headers.get('content-type') || '').toLowerCase()
    if (response && response.ok && contentType.startsWith('image/')) {
      try {
        // 复制一份带时间戳的响应再入库，原响应直接返回给页面
        const headers = new Headers(response.headers)
        headers.set(CACHED_AT, String(Date.now()))
        const body = await response.clone().blob()
        if (body.size > 0) {
          await cache.put(request, new Response(body, {
            status: response.status,
            statusText: response.statusText,
            headers,
          }))
          await trimCache(cache)
        }
      } catch {
        /* 写缓存失败不影响这一块瓦片显示 */
      }
    }
    return response
  } catch (e) {
    // 离线/超时：过期瓦片也比空白好
    if (hit) return hit
    throw e
  }
}

// 入参: cache | 方法: 超出上限时按插入顺序删最旧的 | 出参: Promise<void>
async function trimCache(cache) {
  const keys = await cache.keys()
  const overflow = keys.length - MAX_TILES
  if (overflow <= 0) return
  await Promise.all(keys.slice(0, overflow).map((key) => cache.delete(key)))
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  let url
  try {
    url = new URL(request.url)
  } catch {
    return
  }
  if (!isTileRequest(url)) return
  event.respondWith(cacheFirst(request))
})
