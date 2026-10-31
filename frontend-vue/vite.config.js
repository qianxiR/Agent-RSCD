import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// ★ 双服务分流 (2026-10): 更具体的路径放前面。
//   Java 业务服务 (8081): auth / me / admin / projects / workspace / conversations / tasks
//   Python AI 服务 (8020): 其余 /api (含推理 WS) + files/geoserver 管理端点
//   规则与生产 nginx (deploy/nginx.conf) 保持一致。
const JAVA_TARGET = process.env.JAVA_BACKEND_URL || 'http://127.0.0.1:8081'
const PYTHON_TARGET = process.env.PYTHON_BACKEND_URL || 'http://127.0.0.1:8020'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api/v1/auth': { target: JAVA_TARGET, changeOrigin: true },
      '/api/v1/me': { target: JAVA_TARGET, changeOrigin: true },
      '/api/v1/admin': { target: JAVA_TARGET, changeOrigin: true },
      '/api/v1/projects': { target: JAVA_TARGET, changeOrigin: true },
      '/api/v1/workspace': { target: JAVA_TARGET, changeOrigin: true },
      '/api/v1/conversations': { target: JAVA_TARGET, changeOrigin: true },
      '/api/v1/tasks': { target: JAVA_TARGET, changeOrigin: true },
      '/api': { target: PYTHON_TARGET, changeOrigin: true, ws: true },
      '/geoserver': { target: 'http://127.0.0.1:8080', changeOrigin: true },
    },
  },
  // ★ 单入口 + 路由分流: / 工作台，/admin/** 管理端（页面异步 chunk）
  //   生产只需把 dist/ 挂到同一个域名（用户端 app.*），管理端按需要再拆子域。
  build: {
    rollupOptions: {
      input: {
        app: fileURLToPath(new URL('./index.html', import.meta.url)),
      },
    },
  },
})
