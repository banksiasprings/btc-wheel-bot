# Cross-surface Consistency Plan

The BTC Wheel Bot has two user-facing surfaces:
1. **Dashboard** — Streamlit (`dashboard_ui.py`), runs at `localhost:8501`,
   keyboard-and-mouse, dense data tables and analytical depth.
2. **Mobile PWA** — React + Vite (`mobile-app/`), served at the
   user's domain, touch-first, glanceable cards and visual hierarchy.

Goal: pull the best from each so a user feels they're using **one
product** across both, not two products that happened to ship together.

## Tab inventory (today, 2026-05-02)

| Mobile (5 tabs) | Dashboard (8 tabs) | Status |
|---|---|---|
| 🏭 Farm | 🛰 Fleet | ✅ Both have multi-bot view; metrics align after `why_not_trading` integration |
| 💹 Trading | 📈 Paper Trading | ⚠️ Dashboard's Paper Trading is functional but visually plainer than Mobile TradingView (P&L zones, buffer pill, IV gauge, paired markers) |
| 📊 Performance | (split: Fleet + Recommendations) | ⚠️ Mobile has dedicated Performance ranking; dashboard shows it across two tabs |
| 🧬 Pipeline | 🧬 Optimizer + 📋 Recommendations + 📊 Forecasts | ⚠️ Mobile has unified workflow (Evolve → Validate → Sweep → AI Review → Black Swan → Promote); dashboard has it split |
| ⚙️ Settings | ⚙️ Config + 🔧 Settings | ✅ Both surfaces; mobile combines them |
| (none) | 📊 Backtest | ❌ Dashboard-only — interactive parameter sliders. Mobile has none. |
| (none) | 📊 Forecasts | ❌ Dashboard-only — forecast validation snapshots. Mobile has none. |

## Visual/UX strengths to keep

**Dashboard wins these:**
- Interactive Backtest with real-time slider feedback
- Optimizer evolution + sweep visualisation (Plotly charts)
- Capital efficiency scatter + cross-bot equity curves on Fleet tab
- Forecast validation surface (snapshot creation + comparison reports)
- Recommendations tab — historical baseline + parameter group analysis

**Mobile wins these:**
- TradingView's rich visual position card (P&L zones, buffer pill,
  IV gauge, paired-trade markers, projection toggle)
- Pipeline tab as a guided workflow (steps with status icons,
  connector arrows, save-as-config flow)
- Why-not-trading diagnostic prominently surfaced
- Pause toggle with global + per-bot scope
- Check for Update self-service flow
- Touch-friendly cards, no horizontal scroll, glanceable hierarchy

## Concrete consistency gaps

In rough priority order:

### 🔴 1. Mobile lacks forecast validation
The dashboard's Forecasts tab is the user's **truth signal** for
backtest-vs-reality drift. Mobile should at least be able to LIST
snapshots and show their pass/warning/fail status.
**Smallest fix:** add `/forecasts/snapshots` REST endpoints + a small
list panel inside mobile Performance.tsx (or a new Forecasts tab).

### 🔴 2. Dashboard Paper Trading visual is plain vs Mobile TradingView
Mobile shows a single position with P&L zones, buffer pill, paired
markers; dashboard shows the same data as plain metric cards.
**Smallest fix:** import the position-card visual into the dashboard's
Paper Trading tab (HTML via st.markdown — no new dependency).

### 🟡 3. Mobile lacks Backtest tab
The interactive parameter slider experience is a dashboard exclusive.
For a touch-only user, this means they can never explore "what if I
change IV threshold to 0.45?" without picking up a laptop.
**Smallest fix:** mobile Backtest as a simplified slider sheet that
hits an existing `/optimizer/sweep` style endpoint. ~200 lines.

### 🟡 4. Dashboard Optimizer is split across 3 tabs
Mobile's Pipeline is one continuous workflow; dashboard has Optimizer
+ Recommendations + Forecasts as separate tabs. Same content, more
clicks. **Fix:** unified "Pipeline" tab on dashboard that mirrors
mobile's step-by-step flow. Could shadow the existing tabs.

### 🟢 5. Settings split inconsistently
Mobile has one Settings tab; dashboard has Config (edit YAML) and
Settings (kill switch / logs / trades CSV). **Fix:** consolidate into
one dashboard Settings tab with sub-sections (or sub-tabs).

### 🟢 6. Bot run controls
Dashboard's Paper Trading has Start/Stop buttons; mobile's Settings
has pause/resume. Different verbiage for similar concepts.
**Fix:** standardise on "Pause / Resume" on both, with the kill switch
as the explicit hard-stop.

## Roadmap

Suggesting two passes:

**Pass A — feature parity** (each surface gets every essential capability):
- A.1 — `/forecasts/snapshots` endpoints + mobile Forecasts panel
- A.2 — Mobile Backtest tab (simplified)
- A.3 — Dashboard Paper Trading borrowing Mobile TradingView visuals
- A.4 — Standardise Pause/Resume verbiage + KILL_SWITCH semantics

**Pass B — design system** (both surfaces look like one product):
- B.1 — Shared colour tokens (currently duplicated in `dashboard_ui.py`
  CSS block and `mobile-app/tailwind.config.js`)
- B.2 — Card/section header styles synced
- B.3 — Status-badge palette synced (🟢 ready / 🟡 caution / 🔴 fail)
- B.4 — Cross-surface terminology dictionary (e.g. "Open Pos" vs
  "Active Position", "Margin" vs "Collateral")

## Progress log

### 2026-05-02

- **A.1 Forecasts on mobile** — `/forecasts/snapshots` endpoint + mobile
  `Forecasts.tsx` (commit `c5deb42`).
- **A.4 Pause/Resume verbiage** — dashboard adopts mobile language
  (commit `4b959d8`).
- **A.3 Rich position card on dashboard** — buffer pill + IV gauge
  pulled from mobile TradingView into dashboard Paper Trading tab
  (commit `d93f54a`).

### 2026-05-03

- **A.2 Mobile Backtest tab** — `POST /backtest/run` endpoint + mobile
  `Backtest.tsx` with sliders + presets. **Pass A complete (4/4)**.
- **B.1 Shared theme tokens** — `theme.json` at the repo root is the
  canonical palette. `dashboard_ui.py` reads it for `C_BG/C_CARD/...`;
  `mobile-app/tailwind.config.js` reads it via ES-module `readFileSync`
  for `bg-navy`, `bg-card`, `border-border`, plus new `theme-*`
  semantic aliases.
- **B.3 Terminology dictionary** — `TERMINOLOGY.md` codifies the
  canonical word for every status state, position concept, capital
  metric, and process term. Future copy follows this dictionary; old
  drift gets cleaned up incrementally.

## Pass B remaining

- **B.2** — Card / section header style sync (currently each surface
  has its own padding / border-radius conventions; aim is one `Card`
  primitive on each surface that produces visually identical chrome).
- **B.4** — Status-badge palette reconciliation. The four severity
  tiers (active / paused / warning / fail) are documented in
  TERMINOLOGY.md with their hex values; remaining work is a sweep
  through both surfaces to use those exact hexes via `theme-*`
  Tailwind classes / `C_*` Python constants instead of inline values.
- Old-label cleanup pass per TERMINOLOGY.md "Migration" section —
  done incrementally as files are touched for other reasons.
