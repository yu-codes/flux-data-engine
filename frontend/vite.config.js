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
      '/api': { target: 'http://localhost:38000', changeOrigin: true },
      '/health': { target: 'http://localhost:38000', changeOrigin: true },
      '/static': { target: 'http://localhost:38000', changeOrigin: true },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 3001,
    proxy: backendProxy,
  },
})
