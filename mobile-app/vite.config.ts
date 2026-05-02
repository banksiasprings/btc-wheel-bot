import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import pkg from './package.json'

export default defineConfig({
  base: '/btc-wheel-bot/',
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['*.svg', '*.png', '*.ico', 'icons/*.png'],
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
            src: '/btc-wheel-bot/icons/icon-192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: '/btc-wheel-bot/icons/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable',
          },
          {
            src: '/btc-wheel-bot/icons/apple-touch-icon.png',
            sizes: '180x180',
            type: 'image/png',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        skipWaiting: true,
        clientsClaim: true,
        cleanupOutdatedCaches: true,
      },
    }),
  ],
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
