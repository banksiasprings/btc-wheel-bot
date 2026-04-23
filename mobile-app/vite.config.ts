import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  base: '/btc-wheel-bot/',
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['*.svg'],
      manifest: {
        name: 'Wheel Bot',
        short_name: 'Wheel Bot',
        description: 'BTC Wheel Strategy Bot Monitor',
        theme_color: '#0f172a',
        background_color: '#0f172a',
        display: 'standalone',
        start_url: '/btc-wheel-bot/',
        scope: '/btc-wheel-bot/',
        icons: [
          {
            src: 'icon-192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: 'icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        skipWaiting: true,
        clientsClaim: true,
      },
    }),
  ],
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
