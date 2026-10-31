// ==================== 统一 REST 客户端（用户端 / 管理端共享）====================
// 职责: JSON 请求、Bearer 注入、401 静默刷新后重试一次、统一错误对象。
// 边界: 不认识业务语义；token 由 authStore 通过 setAuthBridge 注入，避免循环依赖。
//   刷新失败时只回调 onAuthFailure（由路由守卫跳登录），不在这里操作路由。

let bridge = {
  getAccessToken: () => '',
  refresh: async () => false,
  onAuthFailure: () => {},
}

let refreshing = null

export function setAuthBridge(next) {
  bridge = { ...bridge, ...next }
}

export class ApiError extends Error {
  constructor(message, { code = '', status = 0, data = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.data = data
  }
}

async function parseBody(res) {
  const text = await res.text().catch(() => '')
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

// 401 并发去重：多个请求同时过期时只发一次 refresh
async function refreshOnce() {
  if (refreshing) return refreshing
  const run = Promise.resolve()
    .then(() => bridge.refresh())
    .catch(() => false)
  refreshing = run
  try {
    return await run
  } finally {
    if (refreshing === run) refreshing = null
  }
}

// 入参: path, { method, body, auth, retry, headers, signal, raw }
// 方法: 自动 JSON 序列化；auth=true 时附带 Bearer；401 先刷新再重试一次
// 出参: 解析后的 JSON（raw=true 时返回 Response，用于下载/流）
export async function request(path, options = {}) {
  const {
    method = 'GET',
    body,
    auth = true,
    retry = true,
    headers = {},
    signal = undefined,
    raw = false,
  } = options

  const isForm = typeof FormData !== 'undefined' && body instanceof FormData
  const finalHeaders = { Accept: 'application/json', ...headers }
  if (body !== undefined && !isForm) finalHeaders['Content-Type'] = 'application/json'
  if (auth && bridge.getAccessToken()) {
    finalHeaders.Authorization = `Bearer ${bridge.getAccessToken()}`
  }

  const res = await fetch(path, {
    method,
    headers: finalHeaders,
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    signal,
  })

  if (res.status === 401 && auth && retry) {
    const ok = await refreshOnce()
    if (ok) return request(path, { ...options, retry: false })
    bridge.onAuthFailure()
  }

  if (raw) {
    if (!res.ok) {
      const data = await parseBody(res)
      throw new ApiError((data && data.msg) || `HTTP ${res.status}`, {
        code: data && data.code,
        status: res.status,
        data,
      })
    }
    return res
  }

  const data = await parseBody(res)
  if (!res.ok) {
    const message = (data && (data.msg || data.message)) || `HTTP ${res.status}`
    throw new ApiError(message, {
      code: (data && data.code) || '',
      status: res.status,
      data,
    })
  }
  return data
}

export const api = {
  get: (path, options) => request(path, { ...options, method: 'GET' }),
  post: (path, body, options) => request(path, { ...options, method: 'POST', body }),
  put: (path, body, options) => request(path, { ...options, method: 'PUT', body }),
  patch: (path, body, options) => request(path, { ...options, method: 'PATCH', body }),
  del: (path, options) => request(path, { ...options, method: 'DELETE' }),
}

export default api
