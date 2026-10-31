import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/theme.css'
import './styles/content.css'
import './styles/overlays.css'
import '@/shared/styles/shell.css'
import App from './App.vue'
import router from './router'

// 入参: 无
// 方法: 装配 Pinia + ElementPlus + Router 后挂载根组件
// 出参: 无（副作用：挂载 #app；首次导航由 router 接管）
const app = createApp(App)
app.use(createPinia())
app.use(ElementPlus)
app.use(router)
app.mount('#app')

// 入参: 无
// 方法: 清理旧版「底图瓦片缓存 Service Worker」：注销并删除 rsiis-tiles-* 缓存。
//   ★ 该 SW 会接管 Esri 瓦片请求（cache-first），代理抖动时会抛
//     "FetchEvent ... promise was rejected / Failed to fetch"，导致整片底图空白。
//     现在底图与天地图一样由浏览器直接请求（走浏览器 HTTP 缓存），不再统一拦截。
//   已注册过 SW 的浏览器需要刷新一次页面完成交接。
// 出参: 无
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations()
    .then((regs) => Promise.all(
      regs
        .filter((reg) => (reg.active?.scriptURL || reg.installing?.scriptURL || reg.waiting?.scriptURL || '')
          .includes('basemap-cache-sw'))
        .map((reg) => reg.unregister()),
    ))
    .catch(() => {})
  if (typeof caches !== 'undefined') {
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith('rsiis-tiles-')).map((key) => caches.delete(key)),
      ))
      .catch(() => {})
  }
  // 旧版 SW 仍控制当前页面时，注销不会立刻放开已打开的页面；强制刷新一次完成交接，
  // 避免它继续用缓存里的坏瓦片把 Esri 底图挡成空白（每个标签页只做一次）。
  if (navigator.serviceWorker.controller) {
    const KEY = 'rsiis:sw-handoff:v1'
    try {
      if (!sessionStorage.getItem(KEY)) {
        sessionStorage.setItem(KEY, '1')
        navigator.serviceWorker.getRegistrations()
          .then((regs) => Promise.all(regs.map((reg) => reg.unregister())))
          .catch(() => {})
          .finally(() => window.location.reload())
      }
    } catch {
      /* 隐私模式禁用 sessionStorage 时忽略 */
    }
  }
}

// 管理端会话彻底过期（refresh 也失败）→ 回管理端登录页
window.addEventListener('rsiis:auth-expired', () => {
  const current = router.currentRoute.value
  if (current.path.startsWith('/admin') && current.name !== 'admin-login') {
    router.replace({ path: '/admin/login', query: { redirect: current.fullPath } })
  }
})
