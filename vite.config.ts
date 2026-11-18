import { fileURLToPath, URL } from 'node:url'
import { createLogger, defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on :8000 (`make dev`). Tests point the proxy elsewhere with ENSEMBLE_API_URL.
const API = process.env.ENSEMBLE_API_URL ?? 'http://127.0.0.1:8000'

// When the API is down the UI runs in mock mode and polls /api/health; say so once instead of a stack trace per poll.
const logger = createLogger()
let warnedDown = false
const error = logger.error
logger.error = (msg, opts) => {
  if (msg.includes('http proxy error')) {
    if (!warnedDown) logger.warn(`[ensemble] API not reachable at ${API}; the UI runs in mock mode until it is.`)
    warnedDown = true
    return
  }
  error(msg, opts)
}

export default defineConfig({
  plugins: [react()],
  customLogger: logger,
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5190,
    strictPort: true,
    proxy: {
      '/api': {
        target: API,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', () => { warnedDown = false })
          // Answer 503 with a JSON body (instead of Vite's bare 500) so the client can tell "API down" from "API error".
          proxy.on('error', (_err, _req, res) => {
            if (res && 'writeHead' in res && !res.headersSent) {
              res.writeHead(503, { 'Content-Type': 'application/json', 'X-Ensemble-Proxy': 'api-unreachable' })
              res.end(JSON.stringify({ detail: 'API unreachable' }))
            }
          })
        },
      },
    },
  },
})
