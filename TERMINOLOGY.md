# Cross-surface Terminology

Single source of truth for the words used across the dashboard, mobile
PWA, README, OPERATIONS, CLOUD_MIGRATION, NIGHT_LOG, schedule prompts,
and Telegram notifier copy. Pick one term per concept, use it
everywhere. CONSISTENCY.md Pass B.3.

## Status states

| Concept | Canonical term | Emoji | Hex |
|---|---|---|---|
| Bot is running and able to enter trades | **active** | 🟢 | `theme-green` |
| Bot is alive but trades are blocked (KILL_SWITCH or PAUSED) | **paused** | 🟡 | `theme-amber` |
| Bot process not running | **stopped** | 🔴 | `theme-red` |
| Heartbeat older than 6 h, bot may be hung | **stale** | 🟡 | `theme-amber` |
| Forecast snapshot horizon hasn't elapsed | **pending** | 🕐 | `theme-muted` |
| Forecast horizon elapsed, not yet validated | **due** | 🟡 | `theme-amber` |
| Validation: actual within forecast envelope | **pass** | 🟢 | `theme-green` |
| Validation: actual partially diverges | **warning** | 🟡 | `theme-amber` |
| Validation: actual materially worse than forecast | **fail** | 🔴 | `theme-red` |

Always use the emoji + the canonical term together (`🟢 active`,
`🟡 paused`). Never invent a synonym.

## Position-related vocabulary

| Concept | Canonical term | Avoid |
|---|---|---|
| The currently-open option position | **open position** | ~~"active position"~~, ~~"current trade"~~, ~~"live leg"~~ |
| Cash-secured collateral on Deribit, in USD | **collateral** | ~~"margin"~~ in user-facing copy (use "margin" only for `margin_used` API fields and the `Margin` column in the Fleet leaderboard) |
| Premium received in USD per BTC of underlying | **premium per BTC** | ~~"premium per contract"~~ (Deribit "contract" is ambiguous) |
| Total premium across the position in USD | **premium collected** | ~~"total premium"~~, ~~"prem received"~~ |
| Strike price minus premium per BTC | **breakeven** | ~~"BE"~~ as a label (use "BE" only as a tight space-saving abbreviation when the term has been spelled out nearby) |
| Days remaining to option expiry | **DTE** | ~~"days to expiry"~~ in chart labels (full term elsewhere) |
| Magnitude of option delta | **|Δ|** or "delta" | ~~"d"~~, ~~"deltaval"~~ |

## Trading-action vocabulary

| Concept | Canonical term | Avoid |
|---|---|---|
| Sell-to-open a short option | **open** | ~~"sell"~~ as a verb (Deribit jargon collides with the perp-hedge "sell"), ~~"enter"~~ |
| Buy-to-close a short option | **close** | ~~"unwind"~~, ~~"exit"~~ |
| Stop allowing new entries | **pause trading** | ~~"kill switch"~~ in user-facing copy (file is still `KILL_SWITCH` for ops continuity) |
| Resume new entries | **resume trading** | ~~"unpause"~~, ~~"clear kill switch"~~ |
| Settle an option at expiry | **settle** | ~~"expire"~~ as a verb |
| Close all of a bot's positions immediately | **emergency close** | ~~"force close"~~, ~~"liquidate"~~ |

## Capital / equity vocabulary

| Concept | Canonical term |
|---|---|
| Total account value (cash + position MTM) | **equity** |
| Equity not currently locked as collateral | **free equity** |
| Total return as % of starting equity | **ROI** (in tables) / **total return** (in prose) |
| Annualised total return % | **annualised return** |
| Annualised return per dollar of margin deployed | **margin ROI** |
| Premium collected ÷ margin deployed (over a window) | **premium / margin** or **premium-on-margin yield** |
| Smallest equity at which any trade fired in a backtest | **min viable capital** |
| Mean fraction of equity locked as collateral | **margin utilisation** |

## Scope / process vocabulary

| Concept | Canonical term |
|---|---|
| The Streamlit-served control panel | **dashboard** (lowercase in prose, **Dashboard** in headers) |
| The PWA / React app | **mobile app** or **the PWA** |
| The supervisor that runs many paper bots | **bot farm** or **the farm** |
| One trading subprocess in the farm | **bot** (never "bot instance") |
| The whole repo / system | **bot system** or **the project** (avoid "BTC Wheel Bot" except in titles) |
| Cron-fired remote agent task | **scheduled routine** |
| Weekly forecast freeze | **snapshot** (not "forecast", not "prediction") |
| Comparing snapshot to actual trades | **validation** (not "verification", not "audit") |

## Where this applies

When you write user-facing copy, follow this dictionary in:

1. Streamlit dashboard buttons / headers / banners (`dashboard_ui.py`)
2. Mobile component labels / status badges (`mobile-app/src/components/`)
3. Telegram notifier strings (`notifier.py`)
4. Scheduled-routine prompts (the markdown blocks in `mcp__RemoteTrigger`
   create calls)
5. README, OPERATIONS, CLOUD_MIGRATION, NIGHT_LOG, CONSISTENCY docs

Do NOT change:
- Field names in `data/trades.csv` (existing schema, would break the
  optimizer / forecast validator)
- API JSON keys (existing endpoints; renaming would break the mobile app)
- File names (`KILL_SWITCH`, `PAUSED`, `bot_heartbeat.json`, etc.)
- Python class / function names that are already in use

## Migration

A few existing labels still need to be brought into line:

- [ ] `dashboard_ui.py` Fleet tab leaderboard column "Open Pos" →
      "Open Position" (currently truncated to "Open Pos" for table width;
      keep the abbreviation if column space remains tight).
- [ ] `notifier.py:notify_position_risk` uses "PUT/CALL" — match the
      mobile's "short put" / "short call" wording for consistency.
- [ ] `OPERATIONS.md` has a `Pause Trading` heading but body text still
      mentions "kill switch" — sweep the prose.
- [ ] `mobile-app/src/components/Farm.tsx` button text (already aligned —
      double-check after this commit).

These migrations land incrementally. Don't do mass renames in one go;
do them as files are edited for other reasons.
