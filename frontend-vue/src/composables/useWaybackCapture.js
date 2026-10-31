// ==================== Esri Wayback 高分辨率截图（浏览器拼接）====================
// 背景: Wayback 官方主域在部分网络会被 DNS 污染/重置，改用官方备用子域 wayback-a/b；
//   瓦片模板直接复用版本清单返回的 release.tileUrl（default028mm 矩阵集）。
// 方法: 按 AOI 计算 WebMercator 瓦片范围 → 浏览器并发取瓦片 → canvas 拼图/裁剪 →
//       返回 PNG Blob 与精确 bbox，交给 /api/v1/imagery/georeference 写 GeoTIFF。
const DEFAULT_TILE_BASE = 'https://wayback-a.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile'
const FALLBACK_TILE_BASE = 'https://wayback-b.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile'
const TILE_SIZE = 256
const MAX_SIZE = 1200
const MAX_TILES = 100

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

// 入参: bbox 4326, 输出上限像素 | 方法: 从 z19 向下找能放进 MAX_SIZE 的瓦片层级 | 出参: plan
function planTiles(bbox, maxSize = MAX_SIZE) {
  const [minx, miny, maxx, maxy] = bbox
  for (let zoom = 19; zoom >= 7; zoom -= 1) {
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

// 入参: url, 重试次数 | 方法: 用 Image 加载瓦片（避免 fetch 被 Service Worker/连接中断拖垮） | 出参: Image
async function loadTileImage(url, retries = 2) {
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

// 入参: release {m,date,tileUrl}, plan | 方法: 并发取瓦片并绘制到完整 mosaic | 出参: canvas
async function buildMosaic(release, plan) {
  const canvas = document.createElement('canvas')
  canvas.width = plan.cols * TILE_SIZE
  canvas.height = plan.rows * TILE_SIZE
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('浏览器不支持 canvas')
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
        px: col * TILE_SIZE,
        py: row * TILE_SIZE,
      })
    }
  }
  const fill = (tpl, job) => tpl
    .replace('{z}', String(plan.zoom))
    .replace('{y}', String(job.y))
    .replace('{x}', String(job.x))
  const batchSize = 8
  for (let start = 0; start < jobs.length; start += batchSize) {
    const batch = jobs.slice(start, start + batchSize)
    await Promise.all(batch.map(async (job) => {
      let image = null
      try {
        image = await loadTileImage(fill(template, job))
      } catch (e) {
        if (!fallbackTemplate) throw e
        image = await loadTileImage(fill(fallbackTemplate, job))
      }
      ctx.drawImage(image, job.px, job.py)
    }))
  }
  return canvas
}

// 入参: { release, bbox, maxSize? }
// 方法: 计算瓦片计划 → 拼接 → 裁到 bbox → PNG | 出参: { blob, bbox, width, height, zoom }
export async function captureWaybackImage({ release, bbox, maxSize = MAX_SIZE }) {
  if (!release?.m) throw new Error('缺少 Esri Wayback 版本')
  if (!Array.isArray(bbox) || bbox.length !== 4) throw new Error('缺少有效范围')
  const plan = planTiles(bbox, maxSize)
  const mosaic = await buildMosaic(release, plan)
  const output = document.createElement('canvas')
  output.width = plan.width
  output.height = plan.height
  const ctx = output.getContext('2d')
  if (!ctx) throw new Error('浏览器不支持 canvas')
  ctx.drawImage(
    mosaic,
    plan.left, plan.top, plan.width, plan.height,
    0, 0, plan.width, plan.height,
  )
  const blob = await new Promise((resolve, reject) => {
    try {
      output.toBlob((value) => (value ? resolve(value) : reject(new Error('PNG 编码失败'))), 'image/png')
    } catch (e) {
      reject(new Error(`影像编码失败（跨域限制）: ${e.message}`))
    }
  })
  return {
    blob,
    bbox: plan.bbox,
    width: plan.width,
    height: plan.height,
    zoom: plan.zoom,
    release,
  }
}
