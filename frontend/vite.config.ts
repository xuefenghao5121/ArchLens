import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev 时把 /api 代理到 wf.py 的 viz server（platform/viz/server.py，端口 8899）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8899',
        changeOrigin: true,
      },
    },
  },
})
