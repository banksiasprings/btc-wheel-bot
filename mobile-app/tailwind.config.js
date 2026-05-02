// Tailwind colour tokens read from /theme.json — the same file the
// dashboard's Streamlit CSS reads. CONSISTENCY.md Pass B.1: one source of
// truth for the cross-surface palette. Edit theme.json at the repo root,
// then `npm run build` here + reload the dashboard.

import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const themePath = resolve(__dirname, '..', 'theme.json')
const theme = JSON.parse(readFileSync(themePath, 'utf8'))

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Legacy aliases kept so existing components (bg-navy, bg-card,
        // border-border) don't have to change. Values come from theme.json.
        navy:    theme.dark.bg,
        card:    theme.dark.card,
        border:  theme.dark.border,
        // Explicit theme-prefixed aliases for cross-surface consistency.
        // Use these in new components (bg-theme-card, text-theme-muted, etc.)
        // so a single hex change in theme.json updates both surfaces.
        'theme-text':    theme.dark.text,
        'theme-muted':   theme.dark.muted,
        'theme-blue':    theme.dark.blue,
        'theme-green':   theme.dark.green,
        'theme-red':     theme.dark.red,
        'theme-amber':   theme.dark.amber,
        'theme-emerald': theme.dark.emerald,
      },
    },
  },
  plugins: [],
}
