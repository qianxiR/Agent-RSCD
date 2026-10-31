// ==================== 共享认证 Store 工厂（用户端 / 管理端）====================
// 职责: 登录、MFA 绑定/校验、refresh、me、logout；token 持久化。
// 边界: 仅存 access/refresh/user；MFA setup_token / mfa_token 只放内存（不落盘）。
//   两个门户共用本工厂，但 localStorage key 按 namespace 隔离：
//   开发期同源（5173）也不会互相顶掉会话；生产期分属两个子域，天然隔离。
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { request, setAuthBridge } from '@/shared/api/client'

// 入参: namespace ('user' | 'admin')
// 出参: Pinia store 定义（useXxxAuthStore）
export function createAuthStore(namespace) {
  const ACCESS_KEY = `rsiis_${namespace}_access_token`
  const REFRESH_KEY = `rsiis_${namespace}_refresh_token`
  const USER_KEY = `rsiis_${namespace}_auth_user`

  function readUser() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY)) || null
    } catch {
      return null
    }
  }

  return defineStore(`auth_${namespace}`, () => {
    const accessToken = ref(localStorage.getItem(ACCESS_KEY) || '')
    const refreshToken = ref(localStorage.getItem(REFRESH_KEY) || '')
    const user = ref(readUser())
    const setupToken = ref('')
    const mfaToken = ref('')
    // 本次页面加载是否已向 /me 校过一次（内存标记，不持久化）
    const hydrated = ref(false)

    const isAuthenticated = computed(() => !!accessToken.value)
    const roles = computed(() => user.value?.roles || [])
    const permissions = computed(() => user.value?.permissions || [])
    const isAdmin = computed(() => roles.value.includes('system_admin'))
    const displayName = computed(() => user.value?.username || '未登录')
    const displayInitial = computed(() => (displayName.value || '?').slice(0, 1).toUpperCase())

    function persistSession(data) {
      if (data.access_token) {
        accessToken.value = data.access_token
        localStorage.setItem(ACCESS_KEY, data.access_token)
      }
      if (data.refresh_token) {
        refreshToken.value = data.refresh_token
        localStorage.setItem(REFRESH_KEY, data.refresh_token)
      }
      if (data.user) {
        user.value = data.user
        localStorage.setItem(USER_KEY, JSON.stringify(data.user))
      }
      setupToken.value = ''
      mfaToken.value = ''
    }

    function persistTokens(data) {
      if (data.access_token) {
        accessToken.value = data.access_token
        localStorage.setItem(ACCESS_KEY, data.access_token)
      }
      if (data.refresh_token) {
        refreshToken.value = data.refresh_token
        localStorage.setItem(REFRESH_KEY, data.refresh_token)
      }
    }

    function clear() {
      accessToken.value = ''
      refreshToken.value = ''
      user.value = null
      setupToken.value = ''
      mfaToken.value = ''
      localStorage.removeItem(ACCESS_KEY)
      localStorage.removeItem(REFRESH_KEY)
      localStorage.removeItem(USER_KEY)
    }

    // 登录: 返回 'success' | 'mfa_required' | 'mfa_setup_required'
    async function login(username, password) {
      const data = await request('/api/v1/auth/login', {
        method: 'POST',
        body: { username, password },
        auth: false,
        retry: false,
      })
      if (data.status === 'mfa_required') {
        mfaToken.value = data.mfa_token || ''
        return data.status
      }
      if (data.status === 'mfa_setup_required') {
        setupToken.value = data.setup_token || ''
        return data.status
      }
      persistSession(data)
      hydrated.value = true
      return data.status
    }

    async function startMfaSetup() {
      if (!setupToken.value) throw new Error('MFA 绑定会话已过期，请重新登录')
      return request('/api/v1/auth/mfa/setup', {
        method: 'POST',
        body: { setup_token: setupToken.value },
        auth: false,
        retry: false,
      })
    }

    async function confirmMfaSetup(code) {
      const data = await request('/api/v1/auth/mfa/setup/confirm', {
        method: 'POST',
        body: { setup_token: setupToken.value, code },
        auth: false,
        retry: false,
      })
      persistSession(data)
      hydrated.value = true
      return data
    }

    async function verifyMfa(code) {
      const data = await request('/api/v1/auth/mfa/verify', {
        method: 'POST',
        body: { mfa_token: mfaToken.value, code },
        auth: false,
        retry: false,
      })
      persistSession(data)
      hydrated.value = true
      return data
    }

    async function doRefresh() {
      if (!refreshToken.value) throw new Error('会话已过期')
      const data = await request('/api/v1/auth/refresh', {
        method: 'POST',
        body: { refresh_token: refreshToken.value },
        auth: false,
        retry: false,
      })
      persistTokens(data)
      return data
    }

    // 公开 refresh: 失败会清空本地会话后抛错
    async function refresh() {
      try {
        return await doRefresh()
      } catch (e) {
        clear()
        throw e
      }
    }

    async function fetchMe() {
      const data = await request('/api/v1/me')
      user.value = data.user
      localStorage.setItem(USER_KEY, JSON.stringify(data.user))
      return data.user
    }

    // 页面加载后首次导航调用: 有 token 就向 /me 校准一次身份与权限
    async function ensureSession() {
      if (hydrated.value) return isAuthenticated.value
      hydrated.value = true
      if (!accessToken.value) {
        clear()
        return false
      }
      try {
        await fetchMe()
        return true
      } catch {
        clear()
        return false
      }
    }

    async function logout() {
      const token = refreshToken.value
      try {
        if (token) {
          await request('/api/v1/auth/logout', {
            method: 'POST',
            body: { refresh_token: token },
            auth: false,
            retry: false,
          })
        }
      } catch {
        // 本地清理优先，不阻塞退出
      } finally {
        clear()
      }
    }

    function hasPermission(code) {
      return permissions.value.includes(code)
    }

    // 客户端只做体验层判断；后端方法级权限仍是最终权威
    function hasAnyPermission(codes) {
      if (!codes || !codes.length) return true
      return codes.some((c) => permissions.value.includes(c))
    }

    // 把 token 读写与刷新能力交给共享 client（保持单向依赖：store → client）
    setAuthBridge({
      getAccessToken: () => accessToken.value,
      refresh: async () => {
        try {
          await doRefresh()
          return true
        } catch {
          clear()
          return false
        }
      },
      onAuthFailure: () => {
        clear()
        // 通知入口层跳登录（store 不直接依赖 router，避免循环引用）
        if (typeof window !== 'undefined') window.dispatchEvent(new Event('rsiis:auth-expired'))
      },
    })

    return {
      accessToken,
      refreshToken,
      user,
      setupToken,
      mfaToken,
      isAuthenticated,
      roles,
      permissions,
      isAdmin,
      displayName,
      displayInitial,
      login,
      startMfaSetup,
      confirmMfaSetup,
      verifyMfa,
      refresh,
      fetchMe,
      ensureSession,
      logout,
      clear,
      hasPermission,
      hasAnyPermission,
    }
  })
}

export const useUserAuthStore = createAuthStore('user')
export const useAdminAuthStore = createAuthStore('admin')
