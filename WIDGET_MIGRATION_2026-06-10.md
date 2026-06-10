# Widget migration — bottom-tab app consolidation (2026-06-10)

The mobile dashboard at **https://bot.banksiaspringsfarm.com** (served by `api.py`,
launchd `com.wheelbot.api`, Cloudflare tunnel → `:8765`) was consolidated from an
accreted one-page scroll (always-on leaderboard hero + a 3-way *top* tab toggle:
Freyr / Farm / Mine) into a proper **6-tab app with bottom navigation**.

No URL changed. Same dark theme, same chips/sparklines/colour codes, same tap-to-drill
detail routes. Continues the lineage of `WIDGET_MIGRATION_2026-06-09.md`.

## The app shell

One server-rendered `/` document holds all six tab panels; a **fixed bottom nav**
toggles section visibility client-side (instant, no round-trip). This keeps the "don't
lose scroll position when switching tabs" requirement honest:

- **Scroll position is preserved per tab** (in-memory `SCROLL` map) and **stashed across
  the 60s soft-refresh** (`sessionStorage` `bsTab`/`bsScroll`), so the auto-reload lands
  the user back exactly where they were — tab *and* scroll. The old top-tab app only
  restored the tab via `#hash`; the reload jumped scroll to the top.
- Legacy `#hash`es from the old top-tab app are remapped: `freyr→folios`, `farm→books`,
  `portfolio→mine`, `leaderboard→board`. Old bookmarks still land somewhere sensible.
- Mobile-first (380 px target), `env(safe-area-inset-bottom)` padding for the iPhone home
  bar, backdrop-blurred nav.

## The six tabs (all in `api.py`)

| Tab | Builder | Content |
|-----|---------|---------|
| 🏆 **Board** (default) | `_leaderboard_tab()` | EVERYTHING head-to-head in ONE **sortable** table — 3 Freyr ensembles + every specialist + farm survivors + Steven's portfolio. Columns: Return / annualised Pace / **Sharpe** / Max DD / Switching cost; tap a header to sort (default = pace ↓). BTC price banner sits on top. |
| 🔌 **Testnet** | `_testnet_tab()` | Promoted from a card on the old Freyr tab to its own tab. The authoritative live Hyperliquid testnet view: equity chart, 8-stat grid (portfolio, 24h P&L, spot/perp, notional, leverage, margin, status), open positions / orders / recent fills with **per-book attribution**. |
| 🛡️ **Portfolios** | 3× `_freyr_card()` | The Freyr ensemble — v0.1.1 / v0.2 / v0.3 only. Each taps through to `/freyr/{variant}` (per-book table, contribution chart, equity + drawdown sparklines, switching-cost panels, escape tier, kill switch). |
| 🤖 **Books** | `_books_tab()` | Every standalone specialist + farm survivor, bucketed into expandable **bracket shelves** (`<details>`): 🔥 Crash · 🐂 Bull · 🌪 Chop · 😴 Calm · 📉 Crisis-alpha · 🏃 Cheap-exit · 🦉 Contrarian · 🌐 Cross-asset-lead · 📊 Options · Unclassified (empty brackets skipped). Specialists render as `_freyr_card`, survivors as `_survivor_card`; both tap through to their dossier. Open/closed bracket state persists across the soft-refresh. |
| 📊 **Review** | `_review_tab()` | The "how it works" narrative tab. Renders the latest auto-generated weekly review from `~/Documents/freyr/reviews/YYYY-MM-DD.md` (minimal in-house markdown→HTML, no new dep). Until the first review lands it shows a placeholder pointing at Sunday 5 AM AEST. |
| 👤 **Mine** | `_steven_panel()` | Steven's manual portfolio (ON/OFF/AUTO, add/remove). Kept as its own tab rather than folded into Books — it's an interactive configurator, not a read-only book bracket, so a dedicated tab is the cleaner UX. |

## Key code structure (`api.py`)

- New tab builders (additive, before `_home_page`): `_leaderboard_tab`, `_testnet_tab`,
  `_books_tab`, `_review_tab`, plus helpers `_sharpe_from_rows`, `_md_to_html` and the
  `BRACKET_ORDER` / `FREYR_ENSEMBLES` / `FREYR_SPECIALISTS` constants.
- `_home_page()` rewritten into the bottom-tab shell wiring those builders under `<nav>`.
- `_testnet_detail_page()` (route `/testnet`) refactored into a thin wrapper around the
  shared `_testnet_tab()` — single source of truth, the duplicated rendering is gone.

## Bug fixed in passing

`_events_html()` crashed (`AttributeError: 'float' object has no attribute 'get'`) on the
Surtr specialist, whose event log carries `size`/`gate`/`escape` as scalars, not dicts —
so `/freyr/surtr` 500'd (pre-existing on live `:8765`). The new Books tab surfaces every
specialist drill-down prominently, so this was made defensive (`isinstance` guards). All
14 Freyr + 7 survivor drill-downs now return 200.

## Untouched (load-bearing)

- **`/farm/status` and `/farm/equity`** JSON (the Android widget contract, `WHEEL_API_KEY`)
  are byte-for-byte unchanged — verified 34 bots, same keys, post-edit.
- No bot code or bot processes touched. The full 34-bot farm is still browseable at
  `/farm`; `/leaderboard`, `/bot/*`, `/freyr/*`, `/btc` all still resolve.

## Auto-Review scheduled task

`scripts/freyr_weekly_review.py` + launchd `com.wheelbot.freyrreview` (daily 5 AM AEST,
generates a NEW review only on Sundays). Reads Freyr snapshots / event logs / sprint
state, composes the markdown review, writes `~/Documents/freyr/reviews/YYYY-MM-DD.md`,
and sends a Telegram info ping. See the install note in that script's header.
