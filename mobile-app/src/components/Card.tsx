import { ReactNode, CSSProperties } from 'react'
import { theme } from '../lib/theme'

// Canonical card chrome — produces identical geometry to the dashboard's
// card_div() helper (same border-radius, padding, border-width — all from
// theme.json sizing block).
//
// Use anywhere the dashboard's tab_paper / tab_fleet / etc. would render an
// inline `<div style="background:C_CARD; border:1px solid C_GRID; ...">`.

interface Props {
  children:   ReactNode
  /** Border colour override for severity (defaults to theme.dark.border) */
  borderHex?: string
  /** Optional Tailwind classes for layout */
  className?: string
  /** Inline style override */
  style?:     CSSProperties
}

export default function Card({
  children, borderHex, className = '', style = {},
}: Props) {
  const sz = theme.sizing
  return (
    <div
      className={className}
      style={{
        background:    theme.dark.card,
        border:        `${sz.border_width_px}px solid ${borderHex ?? theme.dark.border}`,
        borderRadius:  `${sz.card_radius_px}px`,
        padding:       `${sz.card_padding_px}px ${sz.card_padding_px + 4}px`,
        ...style,
      }}
    >
      {children}
    </div>
  )
}
