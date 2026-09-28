import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        configure: (proxy) => {
          // When the backend restarts mid-stream, the proxy would otherwise
          // leave the browser side of the admin SSE stream open but silent,
          // so EventSource never reconnects. Drop it too.
          proxy.on('proxyRes', (proxyRes, _req, res) => {
            proxyRes.on('close', () => {
              if (!proxyRes.complete) res.destroy()
            })
          })
        },
      },
    },
  },
})
