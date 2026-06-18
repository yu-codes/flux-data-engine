import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const backendProxy = {
  '/api': { target: 'http://backend:38000', changeOrigin: true },
  '/health': { target: 'http://backend:38000', changeOrigin: true },
  '/static': { target: 'http://backend:38000', changeOrigin: true },
}

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // 使用 127.0.0.1（而非 localhost）避免 Windows 上 localhost 解析為 IPv6 的歧義
      '/api': { target: 'http://127.0.0.1:38000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:38000', changeOrigin: true },
      '/static': { target: 'http://127.0.0.1:38000', changeOrigin: true },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 3001,
    proxy: backendProxy,
  },
})
