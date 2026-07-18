import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// ★ 开发期：/api 同时转发 HTTP 与 WebSocket（ws:true 覆盖 /api/v1/agent/ws/chat）
//   生产期：后端 StaticFiles 挂载 dist/，前后端同源，无需 proxy
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
      '/api': {
        target: 'http://127.0.0.1:8020',
        changeOrigin: true,
        ws: true,
      },
      '/geoserver': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
})
