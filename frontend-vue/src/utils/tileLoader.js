// ==================== 瓦片加载重试 ====================
// 背景: Esri / Wayback 走系统代理（Clash 等），偶尔出现 SSL EOF / 连接超时，
//   OL 默认一次失败就把瓦片标记为 ERROR，表现为底图/卷帘随机缺块、看起来“加载失败”。
// 方法: 自定义 OL tileLoadFunction —— 图片 onerror 后按退避重试，重试仍失败才认输；
//   重试期间 tile.load() 会重建 Image 并再次调用本函数，计数按瓦片实例累计（WeakMap）。
// 说明: 只重试网络/解码失败；Wayback 本身无覆盖的 404 也会被重试一次（代价可接受）。

// 入参: { retries 重试次数, delay 基础退避毫秒 } | 出参: (tile, src) => void
export function createRetryingTileLoad({ retries = 2, delay = 800 } = {}) {
  const attempts = new WeakMap()
  return function retryingTileLoad(tile, src) {
    const image = tile.getImage()
    const tried = attempts.get(tile) || 0
    if (tried < retries && image && typeof image.addEventListener === 'function') {
      image.addEventListener('error', () => {
        attempts.set(tile, tried + 1)
        // 退避后重新走一遍加载；tile.load() 会重置状态并调用本函数
        setTimeout(() => {
          try {
            tile.load()
          } catch {
            /* 图层/瓦片已销毁，忽略 */
          }
        }, delay * (tried + 1))
      }, { once: true })
    }
    image.src = src
  }
}
