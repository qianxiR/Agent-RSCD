// ==================== 框选解译 UI 冒烟 ====================
// 用法: node scripts/capture-check.mjs  （需 Python:8020 / Vite:5173）
// 产物: .tmp/capture-01-esri.png ~ capture-04-result.png
import { chromium } from 'playwright'

const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173'
const log = (...args) => console.log('[capture-check]', ...args)
const errors = []

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.on('pageerror', (e) => errors.push(`pageerror: ${e}`))
page.on('console', (msg) => {
  if (msg.type() === 'error') errors.push(`console: ${msg.text()}`)
  if (msg.text().startsWith('[capture]')) log('BROWSER', msg.text())
})
page.on('response', (res) => {
  if (res.status() >= 400) errors.push(`HTTP ${res.status()} ${res.url()}`)
  if (res.url().includes('/api/v1/imagery/capture')) log('capture HTTP', res.status())
})

await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
await page.waitForSelector('.layout')
await page.waitForTimeout(1200)

// 1. 切 Esri 高分底图并放大到街区级
await page.locator('.basemap-btn').filter({ hasText: 'Esri' }).click()
await page.waitForTimeout(4000)
await page.screenshot({ path: '.tmp/capture-01-esri.png' })
for (let i = 0; i < 8; i += 1) {
  await page.locator('.ol-zoom-in').click()
  await page.waitForTimeout(350)
}
await page.waitForTimeout(3000)

// 2. 框选解译
await page.locator('.capture-entry').click()
await page.waitForSelector('.draw-hint', { timeout: 5000 })
const box = await page.locator('.ol-map-host').boundingBox()
const x1 = box.x + box.width * 0.38
const y1 = box.y + box.height * 0.38
const x2 = box.x + box.width * 0.55
const y2 = box.y + box.height * 0.55
await page.mouse.move(x1, y1)
await page.mouse.down()
await page.waitForTimeout(150)
for (let i = 1; i <= 10; i += 1) {
  await page.mouse.move(x1 + ((x2 - x1) * i) / 10, y1 + ((y2 - y1) * i) / 10)
  await page.waitForTimeout(30)
}
await page.mouse.up()
await page.waitForSelector('.capture-dialog', { timeout: 10000 })
log('dialog opened')
await page.waitForTimeout(500)
// 选 1m 分辨率，把单次推理控制在 1 个滑窗内
await page.locator('.capture-dialog .el-select').click()
await page.waitForTimeout(300)
await page.locator('.el-select-dropdown__item').filter({ hasText: '1m' }).first().click()
await page.waitForTimeout(300)
await page.screenshot({ path: '.tmp/capture-02-dialog.png' })

// 3. 开始解译（默认 Esri 0.5m + 建筑/水体/植被）
await page.locator('.cap-primary').click()
try {
  await page.waitForSelector('.capture-dialog', { state: 'hidden', timeout: 20000 })
} catch {
  log('dialog still visible:', (await page.locator('.capture-dialog').innerText()).replace(/\s+/g, ' ').slice(0, 240))
  await page.screenshot({ path: '.tmp/capture-error.png' })
  throw new Error('capture dialog did not close')
}
await page.waitForTimeout(4000)
await page.screenshot({ path: '.tmp/capture-03-sent.png' })

// 4. 等待 Agent 回复/解译结果（模型首次加载 + 推理可能 1–3 分钟）
let text = ''
for (let i = 0; i < 100; i += 1) {
  await page.waitForTimeout(3000)
  text = await page.locator('.ai-chat').innerText().catch(() => '')
  const running = text.includes('执行中')
  const failed = /(失败|无法|错误|不可用)/.test(text)
  const finished = /(分割完成|解译完成|提取完成|掩膜|图斑|面积|已完成|结果如下)/.test(text)
  if (i % 10 === 0) log('wait', i * 3, 's running=', running, 'len=', text.length)
  if (failed) break
  if (!running && finished && text.length > 300) break
}
log('chat text length=', text.length)
log('chat excerpt=', text.replace(/\s+/g, ' ').slice(-360))
await page.screenshot({ path: '.tmp/capture-04-result.png' })

const uniqueErrors = [...new Set(errors)]
log('errors=', uniqueErrors.length, uniqueErrors.slice(0, 10))
await browser.close()
