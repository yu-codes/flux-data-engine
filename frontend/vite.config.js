import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 後端 proxy 目標：容器內由 VITE_BACKEND_PROXY 指向 backend:38000；
// 主機開發預設 127.0.0.1（避免 Windows 上 localhost 解析為 IPv6 的歧義）
const devTarget = process.env.VITE_BACKEND_PROXY || 'http://127.0.0.1:38000'
const mkProxy = (target) => ({
  '/api': { target, changeOrigin: true },
  '/health': { target, changeOrigin: true },
  '/static': { target, changeOrigin: true },
})

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: mkProxy(devTarget),
    // Docker（尤其 Windows）bind mount 不會傳遞 inotify 事件，
    // 改用輪詢確保 HMR 能偵測到主機端的檔案變更。
    watch: { usePolling: true, interval: 300 },
  },
  preview: {
    host: '0.0.0.0',
    port: 3001,
    proxy: mkProxy(devTarget),
  },
})
