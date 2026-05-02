// Cross-surface theme tokens loaded from /theme.json at build time.
// Same file the dashboard reads (dashboard_ui.py:_load_theme_tokens) so the
// hex values + sizing + severity icons all converge to one source of truth.
//
// Usage in components:
//   import { theme, severityTone } from '../lib/theme'
//   const tone = severityTone('warning')   // → { hex, emoji, token }
//   <span style={{ color: tone.hex }}>{tone.emoji} {label}</span>
//
// Hex values are inlined at build time via Vite's JSON import — no runtime
// fetch, no extra round-trip.

import themeJson from '../../../theme.json'

export type Severity =
  | 'ready' | 'ok' | 'pass' | 'active' | 'running'
  | 'caution' | 'warning' | 'due' | 'paused'
  | 'danger' | 'fail' | 'stopped'
  | 'pending'
  | 'unknown'

interface PaletteEntry {
  bg:      string
  card:    string
  border:  string
  text:    string
  muted:   string
  blue:    string
  green:   string
  red:     string
  amber:   string
  emerald: string
}

interface Sizing {
  card_radius_px:     number
  card_padding_px:    number
  pill_radius_px:     number
  pill_padding_y_px:  number
  pill_padding_x_px:  number
  border_width_px:    number
}

export const theme: {
  dark:   PaletteEntry
  light:  PaletteEntry
  sizing: Sizing
  statusEmoji:    Record<Severity, string>
  severityToken:  Record<Severity, keyof PaletteEntry>
} = {
  dark:   themeJson.dark,
  light:  themeJson.light,
  sizing: themeJson.sizing,
  statusEmoji:   themeJson._status_emoji as unknown as Record<Severity, string>,
  severityToken: themeJson._severity_token as unknown as Record<Severity, keyof PaletteEntry>,
}

/**
 * Resolve a severity word to its canonical hex + emoji.
 * Pass the desired palette ('dark' or 'light') — defaults to dark.
 */
export function severityTone(
  severity: Severity,
  palette: 'dark' | 'light' = 'dark',
): { hex: string; emoji: string; token: keyof PaletteEntry } {
  const token = theme.severityToken[severity] ?? 'muted'
  return {
    hex:   theme[palette][token],
    emoji: theme.statusEmoji[severity] ?? '⚫',
    token,
  }
}
