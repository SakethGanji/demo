import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies the API so the UI is same-origin (/api/... -> :8001).
// ANALYTICS_API overrides the backend origin if it runs elsewhere.
const API = process.env.ANALYTICS_API || 'http://localhost:8001'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: API, changeOrigin: true },
    },
  },
})
