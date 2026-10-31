// ==================== 展示层格式化工具（双门户共享）====================
// 边界: 只做字符串/时间/数值的显示转换，不发起请求。

// 2026-10-27T10:33:00+08:00 / 2026-10-27 10:33:00.123 → '2026-10-27 10:33'
export function formatDateTime(value, fallback = '—') {
  if (!value) return fallback
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function formatDate(value, fallback = '—') {
  if (!value) return fallback
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// 相对时间: 刚刚 / 12 分钟前 / 昨天 18:20 / 3 天前 / 2026-10-01
export function fromNow(value, fallback = '—') {
  if (!value) return fallback
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  const diff = Date.now() - d.getTime()
  if (diff < 0) return formatDateTime(value)
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hours = Math.floor(min / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days === 1) return `昨天 ${formatDateTime(value).slice(11)}`
  if (days < 30) return `${days} 天前`
  return formatDate(value)
}

// 毫秒 → 01:23 / 1 小时 12 分
export function formatDuration(ms) {
  if (ms == null || Number.isNaN(Number(ms))) return '—'
  const total = Math.max(0, Math.floor(Number(ms) / 1000))
  if (total < 60) return `${total}s`
  const min = Math.floor(total / 60)
  const sec = total % 60
  if (min < 60) return `${min}:${String(sec).padStart(2, '0')}`
  const hour = Math.floor(min / 60)
  return `${hour} 小时 ${min % 60} 分`
}

export function elapsed(startedAt, finishedAt) {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  if (Number.isNaN(start) || Number.isNaN(end)) return '—'
  return formatDuration(end - start)
}

export function formatBytes(bytes, fallback = '—') {
  if (bytes == null || bytes === '') return fallback
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return fallback
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0
  let value = n
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[i]}`
}

export function percent(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, Math.round(n)))
}

// AI 任务状态 → 文案 + 徽标色
export const TASK_STATUS = {
  pending: { label: '等待中', tone: 'gray' },
  running: { label: '运行中', tone: 'blue' },
  done: { label: '已完成', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  cancelled: { label: '已取消', tone: 'amber' },
}

export function taskStatus(status) {
  return TASK_STATUS[status] || { label: status || '未知', tone: 'gray' }
}

// 用户状态
export function userStatus(status) {
  if (status === 'active') return { label: '启用', tone: 'green' }
  if (status === 'disabled') return { label: '已禁用', tone: 'red' }
  return { label: status || '未知', tone: 'gray' }
}

// 角色 code → 中文名（后端返回 code，前端展示用）
export const ROLE_LABELS = {
  system_admin: '系统管理员',
  org_admin: '组织管理员',
  project_admin: '项目管理员',
  analyst: '分析员',
  viewer: '访客',
}

export function roleLabel(code) {
  return ROLE_LABELS[code] || code
}

// 权限码前缀 → 模块中文名（角色权限矩阵用）
export const PERMISSION_MODULES = {
  project: '项目',
  dataset: '数据',
  layer: '图层',
  job: '任务',
  artifact: '产物',
  report: '报告',
  user: '用户',
  role: '角色',
  org: '组织',
  system: '系统',
  audit: '审计',
  storage: '存储',
  model: '模型',
}

// 矩阵分组展示顺序（未列出的排最后）
export const PERMISSION_ORDER = [
  '项目',
  '数据',
  '图层',
  '任务',
  '产物',
  '报告',
  '用户',
  '角色',
  '组织',
  '系统',
  '审计',
  '存储',
  '模型',
]

// 优先按 code 前缀分组（更细），前缀不认识时退回后端 module 字段
export function permissionModule(code, moduleHint) {
  const prefix = String(code || '').split(':')[0]
  if (PERMISSION_MODULES[prefix]) return PERMISSION_MODULES[prefix]
  if (moduleHint && PERMISSION_MODULES[moduleHint]) return PERMISSION_MODULES[moduleHint]
  return moduleHint || prefix || '其他'
}
