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
      '/api': 'http://localhost:9198',
      '/health': 'http://localhost:9198',
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
      },
    },
  },
})
