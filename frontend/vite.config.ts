import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: true,  // Expose to network
    proxy: {
      '/api': {
        target: 'http://localhost:9195',
        // Disable buffering for SSE streams
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // Prevent buffering for SSE
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['X-Accel-Buffering'] = 'no'
            }
          })
        },
      },
      '/health': 'http://localhost:9195',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Cache busting with content hashes
    rollupOptions: {
      output: {
        // Use content hash in filenames for cache busting
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
        // Keep the framework in its own chunk (#737). Teamarr ships often and
        // its own code changes every release; React/Router/Query do not, so
        // splitting them means an upgrade re-downloads the app chunks and
        // leaves the largest single dependency in the browser cache.
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return
          // Matched on the package directory, not a bare substring, so
          // 'react-router' can't sweep in every package with 'react' in its
          // name. Rolldown wants the function form; the object map that older
          // Vite accepted is a type error here.
          const framework = [
            '/react/',
            '/react-dom/',
            '/react-router/',
            '/scheduler/',
            '/@tanstack/react-query/',
          ]
          if (framework.some((pkg) => id.includes(`node_modules${pkg}`))) {
            return 'vendor'
          }
        },
      },
    },
  },
})
