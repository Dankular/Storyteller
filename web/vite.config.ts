import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Proxies /api and /ws to the FastAPI backend during `npm run dev` (novel_harness.webapi.app's
// own CORS also allows this dev-server origin directly, but the proxy means the client code just
// uses relative paths everywhere -- the same paths work unchanged once app.py serves this app's
// built `dist/` from the same origin in production, no env-var-switched base URL needed.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
