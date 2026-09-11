import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from "path"

import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const station = loadEnv(mode, process.cwd(), '').VITE_MEETING_STATION === 'true'
  return {
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'meeting-station-document',
      transformIndexHtml(html) {
        if (!station) return html
        return html
          .replace('<title>Scriberr - Audio Transcription</title>', '<title>Meeting Station · Your private meeting workspace</title>')
          .replace('href="/favicon.svg"', 'href="/station-mark.svg"')
          .replace('<link rel="apple-touch-icon" href="/icon512_rounded.png" />', '<meta name="theme-color" content="#14776b" /><meta name="application-name" content="Meeting Station" /><meta name="description" content="Your private workspace for meeting transcripts, decisions, action items and reports." />')
      },
    },
    VitePWA({
      registerType: 'autoUpdate',
      workbox: { navigateFallbackDenylist: [/^\/api\//] },
      includeAssets: station ? ['station-mark.svg'] : ['favicon.ico', 'apple-touch-icon.png', 'mask-icon.svg'],
      manifest: {
        name: station ? 'Meeting Station' : 'Scriberr',
        short_name: station ? 'Meeting Station' : 'Scriberr',
        description: station ? 'Private meeting transcripts, decisions and next steps' : 'Offline Audio Transcription',
        theme_color: station ? '#ef6c22' : '#8936FF',
        background_color: station ? '#f7f8f4' : '#2EC6FE',
        display: 'standalone',
        orientation: 'any',
        start_url: station ? '/meeting-intelligence' : '/',
        id: station ? 'meeting-station' : 'scriberr-transcription',
        icons: station ? [{ src: 'station-mark.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }] : [
          {
            src: 'icon512_maskable.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable'
          },
          {
            src: 'icon512_rounded.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any'
          }
        ]
      }
    })
  ],
  clearScreen: false, // Disable clear screen to preserve logs
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "dist",
    assetsDir: "assets",
    rollupOptions: {
      output: {
        manualChunks: {
          // Separate vendor chunks for better caching
          'react-vendor': ['react', 'react-dom'],
          'ui-vendor': ['@radix-ui/react-dialog', '@radix-ui/react-popover', '@radix-ui/react-tooltip'],
          'markdown-vendor': ['react-markdown', 'remark-math', 'rehype-katex', 'rehype-raw', 'rehype-highlight'],
          'table-vendor': ['@tanstack/react-table'],
          'lucide-vendor': ['lucide-react'],
        },
      },
    },
    // Improve performance by optimizing chunk sizes
    chunkSizeWarningLimit: 1000,
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/swagger': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/install.sh': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/install-cli.sh': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      }
    }
  },
  base: "/",
  }
})
