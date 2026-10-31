// ==================== 底图瓦片缓存（Cache Storage）====================
// 与 public/basemap-cache-sw.js 共用同一个 Cache：页面侧负责统计与清理，SW 负责拦截与写入。
// 说明: 不支持 Cache API 的环境（旧浏览器/隐私模式）返回 0，不影响地图使用。

export const TILE_CACHE_NAME = 'rsiis-tiles-v2'

// 入参: 无 | 方法: 读取已缓存的瓦片数量（失败返回 0） | 出参: Promise<number>
export async function getTileCacheCount() {
  if (typeof window === 'undefined' || !('caches' in window)) return 0
  try {
    const cache = await caches.open(TILE_CACHE_NAME)
    const keys = await cache.keys()
    return keys.length
  } catch {
    return 0
  }
}

// 入参: 无 | 方法: 清空底图瓦片缓存 | 出参: Promise<number> 清掉的张数
export async function clearTileCache() {
  if (typeof window === 'undefined' || !('caches' in window)) return 0
  try {
    const cache = await caches.open(TILE_CACHE_NAME)
    const keys = await cache.keys()
    await Promise.all(keys.map((key) => cache.delete(key)))
    return keys.length
  } catch {
    return 0
  }
}
