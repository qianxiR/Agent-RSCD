// ==================== 统一路由（工作台 + 管理端）====================
// 一个应用、一套路由：
//   /            工作台（现有三栏：会话 + 地图 + Copilot），默认入口
//   /admin/**    管理控制台（独立布局与守卫，强制 MFA）
// 管理端页面按需异步加载，避免工作台首屏为治理页面付出体积。
import { createRouter, createWebHashHistory } from 'vue-router'
import { useAdminAuthStore } from '@/shared/auth/authStore'

import WorkbenchView from '@/views/WorkbenchView.vue'

// 管理端布局与登录页按需加载：工作台首屏不背治理代码与 Element Plus 之外的管理样式
const AdminShell = () => import('@/admin/layouts/AdminShell.vue')
const AdminLoginPage = () => import('@/admin/pages/LoginPage.vue')

const adminRoutes = [
  { path: '', redirect: '/admin/overview' },
  {
    path: 'overview',
    name: 'admin-overview',
    component: () => import('@/admin/pages/OverviewPage.vue'),
    meta: { title: '运营总览', permission: 'user:manage' },
  },
  {
    path: 'users',
    name: 'admin-users',
    component: () => import('@/admin/pages/UsersPage.vue'),
    meta: { title: '用户与组织', permission: 'user:manage' },
  },
  {
    path: 'roles',
    name: 'admin-roles',
    component: () => import('@/admin/pages/RolesPage.vue'),
    meta: { title: '角色与权限', permission: 'role:manage' },
  },
  {
    path: 'projects',
    name: 'admin-projects',
    component: () => import('@/admin/pages/ProjectsPage.vue'),
    meta: { title: '项目管理', permission: 'project:read' },
  },
  {
    path: 'datasets',
    name: 'admin-datasets',
    component: () => import('@/admin/pages/DatasetsPage.vue'),
    meta: { title: '数据治理', permission: 'dataset:read' },
  },
  {
    path: 'layers',
    name: 'admin-layers',
    component: () => import('@/admin/pages/LayersPage.vue'),
    meta: { title: '图层服务', permission: 'layer:read' },
  },
  {
    path: 'jobs',
    name: 'admin-jobs',
    component: () => import('@/admin/pages/JobsPage.vue'),
    meta: { title: '任务与模型', permission: 'job:read' },
  },
  {
    path: 'storage',
    name: 'admin-storage',
    component: () => import('@/admin/pages/StoragePage.vue'),
    meta: { title: '存储与配额', permission: 'storage:manage' },
  },
  {
    path: 'audit',
    name: 'admin-audit',
    component: () => import('@/admin/pages/AuditPage.vue'),
    meta: { title: '审计日志', permission: 'audit:read' },
  },
  {
    path: 'settings',
    name: 'admin-settings',
    component: () => import('@/admin/pages/SettingsPage.vue'),
    meta: { title: '系统设置', permission: 'system:config' },
  },
  {
    path: '403',
    name: 'admin-forbidden',
    component: () => import('@/admin/pages/ForbiddenPage.vue'),
    meta: { title: '无权限', skipPermission: true },
  },
]

const routes = [
  { path: '/', name: 'workbench', component: WorkbenchView },
  {
    path: '/admin/login',
    name: 'admin-login',
    component: AdminLoginPage,
    meta: { public: true, title: '管理员登录' },
  },
  { path: '/admin', component: AdminShell, children: adminRoutes },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

// 管理员判定：system_admin，或持有任一管理类权限
function isAdminLike(auth) {
  return (
    auth.isAdmin ||
    ['user:manage', 'role:manage', 'audit:read', 'system:config'].some((p) => auth.hasPermission(p))
  )
}

router.beforeEach(async (to) => {
  // 工作台不做登录拦截（现有行为：security.mode=off 下未登录也可用）
  if (!to.path.startsWith('/admin')) return true

  const auth = useAdminAuthStore()

  if (to.meta.public) {
    if (auth.isAuthenticated) {
      const ok = await auth.ensureSession()
      if (ok) {
        // 已登录但不是管理员：直接去 403，避免先跳 overview 再被拦一次
        if (!isAdminLike(auth)) return { name: 'admin-forbidden' }
        const redirect =
          typeof to.query.redirect === 'string' && to.query.redirect.startsWith('/admin')
            ? to.query.redirect
            : '/admin/overview'
        return redirect
      }
    }
    return true
  }

  const ok = await auth.ensureSession()
  if (!ok) {
    return { path: '/admin/login', query: { redirect: to.fullPath } }
  }

  // 403 页本身不再做角色/权限判定，否则会自我重定向（Infinite redirect）
  if (to.meta.skipPermission) return true

  if (!isAdminLike(auth)) return { name: 'admin-forbidden' }
  if (to.meta.permission && !auth.hasPermission(to.meta.permission)) {
    return { name: 'admin-forbidden' }
  }
  return true
})

router.afterEach((to) => {
  if (typeof document === 'undefined') return
  if (to.path.startsWith('/admin')) {
    document.title = to.meta.title ? `${to.meta.title} · 管理控制台` : '国土智察 · 管理控制台'
  } else {
    document.title = '国土智察 · 智能解译工作台'
  }
})

export default router
