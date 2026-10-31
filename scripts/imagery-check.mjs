// ==================== 影像检索 UI 冒烟 ====================
// 用法: node scripts/imagery-check.mjs  （需 Python:8020 / Vite:5173）
// 产物: .tmp/imagery-01.png ~ imagery-04.png
import { chromium } from 'playwright'
import path from 'node:path'

const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173'
const log = (...args) => console.log('[imagery-check]', ...args)
const errors = []

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.on('pageerror', (e) => errors.push(`pageerror: ${e}`))
page.on('console', (msg) => {
  if (msg.type() === 'error') errors.push(`console: ${msg.text()}`)
  if (msg.text().startsWith('[draw]')) log('BROWSER', msg.text())
})
page.on('response', (res) => {
  if (res.status() >= 400) errors.push(`HTTP ${res.status()} ${res.url()}`)
})

await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
await page.waitForSelector('.layout')
await page.waitForTimeout(1200)

// 1. 左栏「影像」入口
await page.click('.tool-btn[title="检索开源遥感影像"]')
await page.waitForSelector('.imagery-dialog', { timeout: 15000 })
await page.screenshot({ path: '.tmp/imagery-01-open.png' })

// 2. 等待自动检索
await page.waitForTimeout(15000)
const cards = await page.locator('.im-card:not(.im-skeleton)').count()
const skel = await page.locator('.im-card.im-skeleton').count()
const alert = await page.locator('.im-alert').innerText().catch(() => '')
log('cards=', cards, 'skeleton=', skel, 'alert=', alert.replace(/\s+/g, ' '))
await page.screenshot({ path: '.tmp/imagery-02-results.png' })

// 3. 第一景预览（叠加到地图）
if (cards > 0) {
  await page.locator('.im-card .im-link').filter({ hasText: '预览' }).first().click()
  await page.waitForTimeout(3500)
  await page.screenshot({ path: '.tmp/imagery-03-preview.png' })
}

// 4. 再打开 + 框选小范围
await page.click('.tool-btn[title="检索开源遥感影像"]')
await page.waitForSelector('.imagery-dialog')
await page.locator('.im-side .el-select').first().click()
await page.waitForTimeout(400)
await page.locator('.el-select-dropdown__item').filter({ hasText: '框选范围' }).click()
await page.waitForTimeout(800)
const dialogVisible = await page.locator('.imagery-dialog').isVisible()
const hint = await page.locator('.draw-hint').count()
const topEl = await page.evaluate(() => {
  const el = document.elementFromPoint(window.innerWidth * 0.5, window.innerHeight * 0.5)
  return el ? `${el.tagName}.${el.className}` : 'none'
})
log('dialog after draw request visible=', dialogVisible, 'draw-hint=', hint, 'elementFromPoint=', topEl)
const box = await page.locator('.ol-map-host').boundingBox()
if (box) {
  const x1 = box.x + box.width * 0.32
  const y1 = box.y + box.height * 0.32
  const x2 = box.x + box.width * 0.55
  const y2 = box.y + box.height * 0.55
  await page.mouse.move(x1, y1)
  await page.mouse.down()
  await page.waitForTimeout(200)
  const steps = 12
  for (let i = 1; i <= steps; i += 1) {
    const x = x1 + ((x2 - x1) * i) / steps
    const y = y1 + ((y2 - y1) * i) / steps
    await page.mouse.move(x, y)
    await page.waitForTimeout(30)
  }
  await page.waitForTimeout(200)
  await page.mouse.up()
  log('dragged', Math.round(x1), Math.round(y1), '->', Math.round(x2), Math.round(y2))
}
await page.waitForTimeout(1200)
const reopened = await page.locator('.imagery-dialog').isVisible()
log('dialog reopened=', reopened)
await page.waitForTimeout(12000)
await page.screenshot({ path: '.tmp/imagery-04-drawn.png' })

// 5. 下载 AOI 裁剪影像
const downloadBtn = page.locator('.im-card .im-link').filter({ hasText: '下载' }).first()
if (await downloadBtn.count()) {
  await downloadBtn.click()
  const result = page.locator('.im-download-result')
  await result.waitFor({ timeout: 120000 }).catch(() => {})
  const hasResult = await result.count()
  log('download result=', hasResult)
  if (hasResult) {
    log('download file=', (await result.locator('.im-dr-name').innerText()).trim())
    await page.screenshot({ path: '.tmp/imagery-05-download.png' })
    await result.locator('.im-link').filter({ hasText: '加载到地图' }).click()
    await page.waitForTimeout(3500)
    await page.screenshot({ path: '.tmp/imagery-06-loaded.png' })
  }
}

const uniqueErrors = [...new Set(errors)]
log('errors=', uniqueErrors.length, uniqueErrors.slice(0, 12))
await browser.close()
