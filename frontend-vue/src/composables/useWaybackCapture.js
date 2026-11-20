// ==================== Esri Wayback 高分辨率截图（浏览器拼接）====================
// 背景: Wayback 官方主域在部分网络会被 DNS 污染/重置，改用官方备用子域 wayback-a/b；
//   瓦片模板直接复用版本清单返回的 release.tileUrl（default028mm 矩阵集）。
// 方法: 按 AOI 计算 WebMercator 瓦片范围 → 浏览器并发取瓦片 → canvas 拼图/裁剪 →
//       返回 PNG Blob 与精确 bbox，交给 /api/v1/imagery/georeference 写 GeoTIFF。
const DEFAULT_TILE_BASE = 'https://wayback-a.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile'
const FALLBACK_TILE_BASE = 'https://wayback-b.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile'
const TILE_SIZE = 256
// 上限决定实际取到的层级：Wayback 该矩阵集最高 z19（z20 返回 404），
// 4096px / 1200 瓦片可在 2 km 范围仍取到 z17（约 1 m/px）。
const MAX_SIZE = 4096
const MAX_TILES = 1200
// 已加载瓦片缓存：同一 release 重复抓图（同区域再抓、切回原视图）直接复用
const TILE_CACHE_LIMIT = 1600
const _tileCache = new Map()

// 入参: release { tileUrl, m }
// 方法: 统一占位符（官方 {level}/{row}/{col} → {z}/{y}/{x}），缺失时用默认 host 拼
// 出参: 模板字符串
function tileTemplate(release) {
  let template = String(release?.tileUrl || '')
  if (!template && release?.m) template = `${DEFAULT_TILE_BASE}/${release.m}/{z}/{y}/{x}`
  return template
    .replace('{level}', '{z}')
    .replace('{row}', '{y}')
    .replace('{col}', '{x}')
}

function lonToTileX(lon, zoom) {
  return ((lon + 180) / 360) * 2 ** zoom
}

function latToTileY(lat, zoom) {
  const safe = Math.max(-85.05112878, Math.min(85.05112878, lat))
  const rad = (safe * Math.PI) / 180
  return ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * 2 ** zoom
}

function tileXToLon(x, zoom) {
  return (x / 2 ** zoom) * 360 - 180
}

function tileYToLat(y, zoom) {
  const n = Math.PI - (2 * Math.PI * y) / 2 ** zoom
  return (180 / Math.PI) * Math.atan(Math.sinh(n))
}

// 入参: bbox 4326, 输出上限像素, forcedZoom 指定缩放 | 方法: 从 z19 向下找能放进 MAX_SIZE 的瓦片层级 | 出参: plan
function planTiles(bbox, maxSize = MAX_SIZE, forcedZoom = null) {
  const [minx, miny, maxx, maxy] = bbox
  const zoomList = forcedZoom ? [Number(forcedZoom)] : Array.from({ length: 13 }, (_, i) => 19 - i)
  for (const zoom of zoomList) {
    const x0f = lonToTileX(minx, zoom)
    const x1f = lonToTileX(maxx, zoom)
    const y0f = latToTileY(maxy, zoom)
    const y1f = latToTileY(miny, zoom)
    const x0 = Math.floor(x0f)
    const x1 = Math.floor(x1f)
    const y0 = Math.floor(y0f)
    const y1 = Math.floor(y1f)
    const cols = x1 - x0 + 1
    const rows = y1 - y0 + 1
    if (cols * TILE_SIZE > maxSize || rows * TILE_SIZE > maxSize || cols * rows > MAX_TILES) continue
    const left = Math.round(x0f * TILE_SIZE) - x0 * TILE_SIZE
    const top = Math.round(y0f * TILE_SIZE) - y0 * TILE_SIZE
    const right = Math.round(x1f * TILE_SIZE) - x0 * TILE_SIZE
    const bottom = Math.round(y1f * TILE_SIZE) - y0 * TILE_SIZE
    const width = right - left
    const height = bottom - top
    if (width < 1 || height < 1) continue
    const gx0 = x0 * TILE_SIZE + left
    const gx1 = x0 * TILE_SIZE + right
    const gy0 = y0 * TILE_SIZE + top
    const gy1 = y0 * TILE_SIZE + bottom
    return {
      zoom,
      x0,
      y0,
      x1,
      y1,
      cols,
      rows,
      left,
      top,
      width,
      height,
      bbox: [
        tileXToLon(gx0 / TILE_SIZE, zoom),
        tileYToLat(gy1 / TILE_SIZE, zoom),
        tileXToLon(gx1 / TILE_SIZE, zoom),
        tileYToLat(gy0 / TILE_SIZE, zoom),
      ],
    }
  }
  throw new Error('区域过大，无法在限制内拼接 Esri 影像，请缩小范围')
}

// 入参: url, 重试次数 | 方法: 先查内存缓存，未命中再加载瓦片 | 出参: Image
async function loadTileImage(url, retries = 3) {
  const hit = _tileCache.get(url)
  if (hit) {
    // LRU：命中后移到队尾，淘汰时从队首出
    _tileCache.delete(url)
    _tileCache.set(url, hit)
    return hit
  }
  const image = await loadTileImageRemote(url, retries)
  _tileCache.set(url, image)
  if (_tileCache.size > TILE_CACHE_LIMIT) {
    const oldest = _tileCache.keys().next().value
    if (oldest) _tileCache.delete(oldest)
  }
  return image
}

// 入参: url, 重试次数 | 方法: 用 Image 加载瓦片（避免 fetch 被 Service Worker/连接中断拖垮） | 出参: Image
async function loadTileImageRemote(url, retries = 3) {
  let lastError = null
  for (let attempt = 0; attempt < retries; attempt += 1) {
    try {
      return await new Promise((resolve, reject) => {
        const img = new Image()
        img.crossOrigin = 'anonymous'
        img.decoding = 'async'
        img.onload = () => resolve(img)
        img.onerror = () => reject(new Error('瓦片加载失败'))
        img.src = url
      })
    } catch (e) {
      lastError = e
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)))
    }
  }
  throw lastError || new Error('瓦片加载失败')
}

// 入参: release {m,date,tileUrl}, plan, 输出画布, 裁剪偏移, 进度回调
// 方法: 逐块取瓦片并直接画进输出画布，每批回调一次进度
//   单块瓦片两次重试仍失败时跳过，不中断整幅抓图；缺口在最终成败判定里统计
// 出参: 失败瓦片数
async function drawTilesInto(ctx, release, plan, cropLeft, cropTop, onProgress) {
  const template = tileTemplate(release)
  // 主域被污染时 a/b 可能只有一个可用：取失败自动换另一个备用域
  const fallbackTemplate = template.includes('wayback-a')
    ? template.replace('wayback-a', 'wayback-b')
    : (template.includes('wayback-b') ? template.replace('wayback-b', 'wayback-a') : '')
  const jobs = []
  for (let row = 0; row < plan.rows; row += 1) {
    for (let col = 0; col < plan.cols; col += 1) {
      jobs.push({
        x: plan.x0 + col,
        y: plan.y0 + row,
        px: col * TILE_SIZE - cropLeft,
        py: row * TILE_SIZE - cropTop,
      })
    }
  }
  const fill = (tpl, job) => tpl
    .replace('{z}', String(plan.zoom))
    .replace('{y}', String(job.y))
    .replace('{x}', String(job.x))

  const batchSize = 8
  let done = 0
  let failed = 0
  for (let start = 0; start < jobs.length; start += batchSize) {
    const batch = jobs.slice(start, start + batchSize)
    await Promise.all(batch.map(async (job) => {
      let image = null
      try {
        image = await loadTileImage(fill(template, job))
      } catch (e) {
        if (!fallbackTemplate) {
          failed += 1
          return
        }
        try {
          image = await loadTileImage(fill(fallbackTemplate, job))
        } catch (e2) {
          failed += 1
          return
        }
      }
      ctx.drawImage(image, job.px, job.py)
    }))
    done += batch.length
    onProgress?.({ done, total: jobs.length, failed })
  }
  return failed
}

// 入参: 无 | 方法: 按当前上限预测给定范围会取到的层级与地面分辨率 | 出参: { zoom, gsd }
export function planWaybackResolution(bbox) {
  const plan = planTiles(bbox)
  const lat = (bbox[1] + bbox[3]) / 2
  const gsd = (156543.03392 / 2 ** plan.zoom) * Math.cos((lat * Math.PI) / 180)
  return { zoom: plan.zoom, gsd }
}

// 入参: { release, bbox, maxSize?, zoom?, exactBbox?, canvas?, onProgress? }
// 方法: 计算瓦片计划 → 逐块取瓦片并直接画进输出画布 → PNG
//   exactBbox=true 时输出严格裁到请求 bbox（双期共用同一像素帧）
//   zoom 指定时复用另一期的缩放，保证两张图宽高一致
//   canvas 传入时复用调用方画布；onProgress 每批瓦片回调一次
// 出参: { blob, bbox, width, height, zoom, release }
export async function captureWaybackImage({
  release,
  bbox,
  maxSize = MAX_SIZE,
  zoom = null,
  exactBbox = false,
  canvas = null,
  onProgress = null,
}) {
  if (!release?.m) throw new Error('缺少 Esri Wayback 版本')
  if (!Array.isArray(bbox) || bbox.length !== 4) throw new Error('缺少有效范围')
  const plan = planTiles(bbox, maxSize, zoom)

  let cropLeft = plan.left
  let cropTop = plan.top
  let cropWidth = plan.width
  let cropHeight = plan.height
  let outBbox = plan.bbox

  if (exactBbox) {
    const [minx, miny, maxx, maxy] = bbox
    const level = plan.zoom
    cropLeft = lonToTileX(minx, level) * TILE_SIZE - plan.x0 * TILE_SIZE
    cropTop = latToTileY(maxy, level) * TILE_SIZE - plan.y0 * TILE_SIZE
    const cropRight = lonToTileX(maxx, level) * TILE_SIZE - plan.x0 * TILE_SIZE
    const cropBottom = latToTileY(miny, level) * TILE_SIZE - plan.y0 * TILE_SIZE
    cropWidth = Math.round(cropRight - cropLeft)
    cropHeight = Math.round(cropBottom - cropTop)
    if (cropWidth < 1 || cropHeight < 1) throw new Error('范围过小，无法生成影像')
    outBbox = [minx, miny, maxx, maxy]
  }

  const output = canvas || document.createElement('canvas')
  output.width = cropWidth
  output.height = cropHeight
  const ctx = output.getContext('2d')
  if (!ctx) throw new Error('浏览器不支持 canvas')
  // 直接裁剪绘制，省掉整幅 mosaic 的中间画布
  const failed = await drawTilesInto(ctx, release, plan, cropLeft, cropTop, onProgress)
  // 全部瓦片都取不到才算失败；零星缺口保留，避免一个坏瓦片丢掉整幅影像
  if (failed >= plan.cols * plan.rows) {
    throw new Error('影像瓦片全部加载失败，请检查网络后重试')
  }

  const blob = await new Promise((resolve, reject) => {
    try {
      output.toBlob((value) => (value ? resolve(value) : reject(new Error('PNG 编码失败'))), 'image/png')
    } catch (e) {
      reject(new Error(`影像编码失败（跨域限制）: ${e.message}`))
    }
  })
  return {
    blob,
    bbox: outBbox,
    width: cropWidth,
    height: cropHeight,
    zoom: plan.zoom,
    release,
  }
}
