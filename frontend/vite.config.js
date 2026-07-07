import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Flask backend (main-DVR.py) serves the REST + WebSocket API on :8000.
// In dev, Vite runs on :5173 and proxies the API calls through so the frontend
// can keep using same-origin paths (location.host) exactly like the old
// single-file page did.
const BACKEND = process.env.BACKEND_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Plain WebSocket used by the agent stream (ws.onmessage / wsSend)
      '/chat': { target: BACKEND, ws: true, changeOrigin: true },
      // Fleet data loader: GET /<fleet_id>/load-data  (regex key -> RegExp)
      '^/[^/]+/load-data': { target: BACKEND, changeOrigin: true },
      '/health': { target: BACKEND, changeOrigin: true },
      // Static assets referenced by the original markup (logo, title bar)
      '/images': { target: BACKEND, changeOrigin: true },
      '/static': { target: BACKEND, changeOrigin: true },
    },
  },
})
