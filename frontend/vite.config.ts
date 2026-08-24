import { fileURLToPath, URL } from 'node:url'

import { quasar, transformAssetUrls } from '@quasar/vite-plugin'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const backend = process.env.VITE_BACKEND_PROXY ?? 'http://127.0.0.1:38000'

export default defineConfig({
  plugins: [
    vue({ template: { transformAssetUrls } }),
    quasar({ sassVariables: fileURLToPath(new URL('./src/css/quasar.variables.scss', import.meta.url)) }),
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 3001,
    host: true,
    //  The dev container bind-mounts this directory from the host. Filesystem
    //  events do not cross that boundary on Windows or macOS, so without
    //  polling the server keeps serving the version it read at start-up — an
    //  edit appears to do nothing, which is worse than an outright failure.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/health': { target: backend, changeOrigin: true },
    },
  },
})
