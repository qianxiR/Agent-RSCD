// ==================== 双入口 UI 冒烟 + 截图脚本 ====================
// 入口: 单入口路由 —— / 工作台（现有界面），/admin/** 管理端；工作台右上角“管理控制台”进入
// 运行: node scripts/ui-shell-check.mjs  （需 Java:8081 / Python:8020 / Vite:5173 已启动）
// 边界: 只读操作 + 管理端首次 MFA 绑定；会创建一次演示分析员账号（若不存在）。
import { chromium } from 'playwright'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173'
const SHOTS = path.resolve('docs/platform/screens')
// 首次绑定后把 TOTP 密钥本地留存，便于重复跑脚本（仅开发环境，勿提交）
const SECRET_FILE = path.resolve('.tmp/admin-totp-secret.txt')
const ADMIN = { username: 'admin', password: 'admin123' }
const ANALYST = { username: 'analyst1', password: 'analyst1-pass-2026' }

fs.mkdirSync(SHOTS, { recursive: true })

// ---------- TOTP（RFC 6238, SHA1/6位/30秒） ----------
function base32Decode(input) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  const clean = input.replace(/=+$/, '').replace(/\s/g, '').toUpperCase()
  let bits = 0
  let value = 0
  const out = []
  for (const ch of clean) {
    const idx = alphabet.indexOf(ch)
    if (idx < 0) continue
    value = (value << 5) | idx
    bits += 5
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff)
      bits -= 8
    }
  }
  return Buffer.from(out)
}

function totp(secret, digits = 6, period = 30) {
  const key = base32Decode(secret)
  const counter = Math.floor(Date.now() / 1000 / period)
  const buf = Buffer.alloc(8)
  buf.writeUInt32BE(Math.floor(counter / 2 ** 32), 0)
  buf.writeUInt32BE(counter >>> 0, 4)
  const hmac = crypto.createHmac('sha1', key).update(buf).digest()
  const offset = hmac[hmac.length - 1] & 0x0f
  const code = (hmac.readUInt32BE(offset) & 0x7fffffff) % 10 ** digits
  return String(code).padStart(digits, '0')
}

const log = (...args) => console.log('[ui-check]', ...args)

async function shot(page, name) {
  await page.waitForTimeout(320)
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false })
  log('screenshot', name)
}

function watch(page, label, errors) {
  page.on('pageerror', (e) => errors.push(`${label} pageerror: ${e}`))
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(`${label} console: ${msg.text()}`)
  })
  page.on('response', (res) => {
    if (res.status() >= 400) errors.push(`${label} HTTP ${res.status()} ${res.url()}`)
  })
}

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const errors = []

  // ---------- 1. 用户端 = 现有工作台 ----------
  const workbench = await context.newPage()
  watch(workbench, '[user]', errors)
  await workbench.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await workbench.waitForSelector('.layout')
  await workbench.waitForTimeout(1200)
  await shot(workbench, '01-user-workbench')

  // ---------- 2. 工作台 → 管理端（路由 /admin） ----------
  const admin = await context.newPage()
  watch(admin, '[admin]', errors)
  await admin.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await admin.waitForSelector('.layout')
  await admin.click('.admin-entry')
  await admin.waitForURL(/#\/admin\/login/, { timeout: 15000 })
  await admin.waitForSelector('text=管理控制台')
  await shot(admin, '02-admin-login')

  await admin.fill('#auth-username', ADMIN.username)
  await admin.fill('#auth-password', ADMIN.password)
  await admin.click('button[type=submit]')
  await admin.waitForSelector('.auth-secret-value, .auth-account', { timeout: 15000 })

  if (await admin.locator('.auth-secret-value').count()) {
    const secret = (await admin.locator('.auth-secret-value').innerText()).trim()
    log('需要绑定 MFA，已获取密钥')
    fs.mkdirSync(path.dirname(SECRET_FILE), { recursive: true })
    fs.writeFileSync(SECRET_FILE, secret, 'utf8')
    await shot(admin, '03-admin-mfa-setup')
    await admin.check('.auth-saved input[type=checkbox]')
    await admin.fill('#auth-setup-code', totp(secret))
    await admin.click('button:has-text("完成绑定并登录")')
  } else {
    log('MFA 已绑定，走动态码校验')
    await shot(admin, '03-admin-mfa-verify')
    const saved = fs.existsSync(SECRET_FILE) ? fs.readFileSync(SECRET_FILE, 'utf8').trim() : ''
    const secret = process.env.ADMIN_TOTP_SECRET || saved
    if (!secret) throw new Error('需要 ADMIN_TOTP_SECRET 环境变量或 .tmp/admin-totp-secret.txt')
    await admin.fill('#auth-verify-code', totp(secret))
    // 6 位数字会自动提交；只有未自动提交时才点按钮，避免重复请求
    const arrived = await admin
      .waitForSelector('text=运营总览', { timeout: 8000 })
      .then(() => true)
      .catch(() => false)
    if (!arrived) await admin.click('button:has-text("验证并进入")')
  }

  await admin.waitForSelector('text=运营总览', { timeout: 15000 })
  await shot(admin, '04-admin-overview')

  // ---------- 3. 管理端核心页 ----------
  await admin.goto(`${BASE}/#/admin/users`, { waitUntil: 'networkidle' })
  await admin.waitForSelector('table')
  await shot(admin, '05-admin-users')

  await admin.goto(`${BASE}/#/admin/roles`, { waitUntil: 'networkidle' })
  await admin.waitForSelector('.role-list')
  await shot(admin, '06-admin-roles')

  await admin.goto(`${BASE}/#/admin/audit`, { waitUntil: 'networkidle' })
  await admin.waitForSelector('table')
  await shot(admin, '07-admin-audit')

  // ---------- 4. 演示账号（工作台登录用） ----------
  const usersResp = await admin
    .evaluate(async () => {
      const token = localStorage.getItem('rsiis_admin_access_token')
      const res = await fetch('/api/v1/admin/users?limit=200', {
        headers: { Authorization: `Bearer ${token}` },
      })
      return res.json()
    })
    .catch(() => null)

  if (!(usersResp?.users || []).some((u) => u.username === ANALYST.username)) {
    const created = await admin.evaluate(async (analyst) => {
      const token = localStorage.getItem('rsiis_admin_access_token')
      const res = await fetch('/api/v1/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          username: analyst.username,
          email: `${analyst.username}@rsiis.local`,
          password: analyst.password,
          roleCodes: ['analyst'],
        }),
      })
      return { status: res.status, body: await res.json() }
    }, ANALYST)
    log('创建演示账号', JSON.stringify(created))
  } else {
    log('演示账号已存在')
  }

  // ---------- 5. 工作台登录（新 token 契约） ----------
  await workbench.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await workbench.waitForSelector('.layout')
  await workbench.getByRole('button', { name: '登录', exact: true }).click()
  await workbench.waitForSelector('.el-dialog')
  await workbench.fill('input[placeholder="请输入用户名"]', ANALYST.username)
  await workbench.fill('input[autocomplete="current-password"]', ANALYST.password)
  await workbench.click('.el-dialog__footer .el-button--primary')
  await workbench.waitForSelector('.auth-user', { timeout: 15000 })
  const loggedIn = await workbench.locator('.auth-user').innerText()
  log('工作台登录成功：', loggedIn.trim())
  // 等弹窗淡出（Element Plus 关闭动画）再截图
  await workbench
    .waitForFunction(
      () => {
        const ov = document.querySelector('.el-overlay')
        return !ov || getComputedStyle(ov).display === 'none'
      },
      null,
      { timeout: 5000 },
    )
    .catch(() => {})
  await shot(workbench, '08-workbench-login')

  // ---------- 6. 回归：非管理员登录管理端 → 403（不能 Infinite redirect） ----------
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const nonAdmin = await ctx2.newPage()
  watch(nonAdmin, '[nonadmin]', errors)
  await nonAdmin.goto(`${BASE}/#/admin/login`, { waitUntil: 'networkidle' })
  await nonAdmin.fill('#auth-username', ANALYST.username)
  await nonAdmin.fill('#auth-password', ANALYST.password)
  await nonAdmin.click('button[type=submit]')
  await nonAdmin.waitForSelector('text=当前账号没有管理控制台权限', { timeout: 15000 })
  if (!nonAdmin.url().includes('/admin/403')) {
    errors.push(`[nonadmin] 非管理员未落到 403：${nonAdmin.url()}`)
  } else {
    log('非管理员进管理端 → /admin/403（无无限重定向）')
  }
  await shot(nonAdmin, '09-admin-forbidden')
  await ctx2.close()

  await browser.close()

  if (errors.length) {
    log('页面错误：')
    for (const e of errors.slice(0, 20)) console.log('  -', e)
    process.exitCode = 1
  } else {
    log('全部页面无 console/page/4xx 错误')
  }
  log('截图目录', SHOTS)
}

main().catch((e) => {
  console.error('[ui-check] FAILED', e)
  process.exit(1)
})
