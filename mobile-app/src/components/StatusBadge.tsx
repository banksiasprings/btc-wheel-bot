import { theme, severityTone, Severity } from '../lib/theme'

// Canonical status badge — one component, one set of colours, one set of
// emoji. Use this anywhere the user needs to see a state at a glance.
//
// Variants:
//   pill  — small rounded chip ("🟢 active"), good for inline labels
//   block — left-border block, good for stacking on top of cards
//
// All hex / sizing values come from theme.json so the dashboard's
// equivalent helper produces visually identical chrome.

type Variant = 'pill' | 'block'

interface Props {
  severity: Severity
  /** Override the auto-derived label (default: capitalised severity word) */
  label?:    string
  /** Optional secondary line shown below the label in block variant */
  subtitle?: string
  variant?:  Variant
  /** Hide the emoji — useful when an outer icon already conveys the state */
  noEmoji?:  boolean
  className?: string
}

export default function StatusBadge({
  severity, label, subtitle, variant = 'pill', noEmoji = false, className = '',
}: Props) {
  const tone     = severityTone(severity, 'dark')
  const text     = label ?? severity[0].toUpperCase() + severity.slice(1)
  const sz       = theme.sizing
  const emoji    = noEmoji ? null : tone.emoji

  if (variant === 'block') {
    return (
      <div
        className={`flex items-start gap-2 ${className}`}
        style={{
          background:    theme.dark.card,
          borderLeft:    `${sz.border_width_px * 4}px solid ${tone.hex}`,
          borderRadius:  `${sz.card_radius_px}px`,
          padding:       `${sz.card_padding_px}px ${sz.card_padding_px + 4}px`,
        }}
      >
        {emoji ? <span style={{ fontSize: 16, lineHeight: 1.2 }}>{emoji}</span> : null}
        <div className="flex-1 min-w-0">
          <p
            style={{
              color: tone.hex, fontWeight: 600, fontSize: 11,
              letterSpacing: 0.5, textTransform: 'uppercase',
            }}
          >{text}</p>
          {subtitle ? (
            <p style={{ color: theme.dark.text, fontSize: 13, marginTop: 2 }}>
              {subtitle}
            </p>
          ) : null}
        </div>
      </div>
    )
  }

  // pill variant
  return (
    <span
      className={`inline-flex items-center gap-1 ${className}`}
      style={{
        background:    `${tone.hex}26`, // 15% alpha tint
        color:         tone.hex,
        borderRadius:  `${sz.pill_radius_px}px`,
        padding:       `${sz.pill_padding_y_px}px ${sz.pill_padding_x_px}px`,
        fontSize:      11,
        fontWeight:    600,
        letterSpacing: 0.3,
        textTransform: 'uppercase',
        border:        `${sz.border_width_px}px solid ${tone.hex}66`, // 40% alpha
      }}
    >
      {emoji}<span>{text}</span>
    </span>
  )
}
