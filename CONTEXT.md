# CONTEXT.md

> **Maintenance contract:** Read this first in any new session — it is the canonical anchor for what this project is and how it is shaped. Update it whenever the architecture, core strategy, or operating model changes meaningfully. **This file takes priority over README.md** when they disagree; in fact most of the root docs (README/SKILL/OPERATIONS/CLOUD_MIGRATION/TERMINOLOGY/CONSISTENCY/IMPROVEMENTS) describe the **retired options system** and are superseded by this file — they carry a banner pointing here. If you make a structural change without updating CONTEXT.md, you have created doc rot.

## What this project is (current — 2026-06-11)

`btc-wheel-bot` is, today, **the phone dashboard/widget host for the sibling project Freyr** plus a residual BTC paper "bot farm" that feeds it survivor data. The repo serves a 6-tab mobile web app (FastAPI `api.py` on `:8765`, launchd `com.wheelbot.api`, Cloudflare tunnel → `https://bot.banksiaspringsfarm.com`) that **leads with Freyr** — it reads Freyr's paper snapshots, live Hyperliquid-testnet account, and weekly reviews **directly off disk** from `/Users/openclaw/Documents/freyr/`. Everything is still **paper / testnet — no real money.**

Two eras have come and gone here, and the working tree still carries both:
- **Options put-wheel (retired).** The project began as a Deribit options wheel + an RL agent. A real-data eval proved the wheel returned only ~1–5%/yr and no RL model beat a 5-line baseline — and the wheel is *short-vol* (the opposite of survival-first). Pivoted **2026-05-28** to a grid/multi-strategy farm; the options system is **retired → `legacy_options/`** (kept, not deleted). Most root `.md` files still describe this era — they are superseded by this CONTEXT.
- **Grid bot farm (residual).** The 34-ish-bot paper farm (`grid_farm.py`) still runs, but is now demoted to a survivor data source under the dashboard's `/farm` route. The lead surface is Freyr.

## The framing this project runs on (do not let docs drift back to textbook trading)

Steven's systems are deliberately **non-traditional**. These principles are load-bearing — they're already encoded in `api.py` (the docs are what lagged). Don't silently re-import always-on-portfolio / drawdown-risk / textbook-specialist / pre-flight-audit orthodoxy:

1. **Dispatcher, not always-on portfolio.** Freyr's books switch **on/off by regime gate**. A backtest CAGR assumes always-on, so it is **one lens, not the canonical metric** (`api.py:2064` says this verbatim; the Board shows a real-deployment "Pace" column *and* a separate "Model CAGR" lens). Steven's own "Mine" portfolio is itself a manual dispatcher.
2. **Data layer first → correlations → targeted specialists.** Freyr's specialists (e.g. Mímir, Váli) are tagged **"data-discovered"** from a correlation sweep over a comprehensive input layer, **not** derived from literature priors.
3. **Shared equity pool, weighted allocation.** Freyr allocates from **one shared margin pool**; a book *standalone* (its own $10k full-notional track in the Books tab) is a **different test** from the same book as a weighted slice of the pool. This is surfaced deliberately, not conflated.
4. **BSF Solar Dispatch is the architectural sibling.** Freyr inherited its design from Steven's lived solar-dispatch system at Banksia Springs Farm (`~/Documents/wikis/bsf/`): both are surplus-resource dispatchers with comprehensive inputs, a rule registry, real-world iteration, and a kill-switch safety net (solar **LOCKOUT** ↔ trading **portfolio kill switch**). See Freyr's `MISSION_CONTROL.md` for the full mapping.
5. **"Don't die", not "don't draw down."** Risk is **wipe-out**, not drawdown magnitude. Deep drawdown with a working escape is acceptable. (The old options docs' `max_daily_drawdown` "pause at 10% DD" framing is part of what's retired.)
6. **Chaos book over audit.** Kill switches are verified by **firing** them — Freyr's `books/chaos.py` deliberately trips every kill path on testnet via a daily armed cron — not by reading the code. (The only "chaos" strings in *this* repo are the retired options `chaos-*` bots — unrelated.)
7. **Build/test/iterate fast under the most accurate conditions.** Real testnet > simulated stress; live observation > Monte Carlo.

## The dashboard: a 6-tab mobile app (all rendered by `api.py`)

One server-rendered document, client-side tab toggle (bottom nav). Builders in parentheses:

| Tab | Builder | What it shows |
|-----|---------|---------------|
| 🏆 **Board** (default) | `_leaderboard_tab` | EVERYTHING head-to-head in one sortable table — the 3 Freyr ensembles, every specialist, farm survivors, and Steven's portfolio. Columns: Return / **Pace** (real-deployment annualised) / Sharpe / Max DD / Switching cost, with a separate **Model CAGR** lens. `PNL_NORMALISED=1` stops mixing model-CAGR against linear pace. |
| 🔌 **Testnet** | `_testnet_tab` | The authoritative **live Hyperliquid testnet** account (real orders, fake money) — equity chart, stat grid, positions/orders/fills with **per-book attribution**. Read-only from `freyr/paper/snapshots/testnet_live.json`; this repo **never touches the venue.** |
| 🛡️ **Portfolios** | `_freyr_card` ×3 | The 3 Freyr ensembles only (v0.1.1 / v0.2 / v0.3). |
| 🤖 **Books** | `_books_tab` | The **books union** (see below), bucketed into regime "bracket" shelves. |
| 📊 **Review** | `_review_tab` | Renders Freyr's auto-generated weekly review from `freyr/reviews/YYYY-MM-DD.md`. |
| 👤 **Mine** | `_steven_panel` | **Steven's manual portfolio** — see below. |

`FREYR_VARIANTS` (`api.py:1684`) = **14**: 3 ensembles (v0.1.1/v0.2/v0.3) + 11 specialists (surtr, vidar, thor, idunn, loki, aegir, sif, skadi, hermod, mimir, vali). The old "7 family tabs via `?tab=`" view still exists, demoted to `/farm` ("BTC Farm").

### 👤 Mine — Steven's manual dispatcher (`steven_portfolio.py`)
Steven's human-vs-algo tournament book. He hand-picks bots from the whole universe (Freyr + farm + specialists + embedded books) and sets each to **ON / OFF / AUTO**: `ON` force-trades, `OFF` parks in cash, **`AUTO` defers to the bot's own regime gate**. It steps a paper NAV ($10k start) each farm tick and races the Freyr auto-portfolios. So Mine is itself a manual dispatcher over a shared roster — the dispatcher pattern, by hand. State is gitignored under `paper/`.

### 🤖 Books union (`BOOKS_FULL_UNION`)
"The full union" surfaces, as first-class standalone bots: (1) the **12 internal strategy books that compose the Freyr ensembles** (`BOOK_NAMES`) — each on its own $10k full-notional standalone track read from Freyr's `v0.1.1` snapshot — plus (2) every standalone **specialist** and **farm survivor**. The key design note in-code: a book *standalone* is a different test from the same book *inside an ensemble* (a weighted slice of one shared pool) — direct evidence of principle #3. Shelved by regime bracket (`BRACKET_ORDER`: 🔥 Crash · 🐂 Bull · 🌪 Chop · 😴 Calm · 📉 Crisis-alpha · 🏃 Cheap-exit · 🦉 Contrarian · 🌐 Cross-asset-lead · 📊 Options).

### Chaos book — lives in Freyr, surfaced here only indirectly
There is **no chaos book in this repo.** The chaos book is `freyr/books/chaos.py` — a deliberately-misbehaving book that *fires* every Freyr safeguard (F1–F6) on testnet via a **daily armed cron**, rather than auditing the code. This dashboard sees it only via the weekly Review markdown. (Principle #6.)

## Architecture (the residual farm + the Freyr-fronting dashboard)

Independent processes, **file-based IPC** (no queue — a missing file self-heals; a broken queue is an outage). The dependency runs **one way: this repo reads Freyr off disk; Freyr never reads this repo.**

```
grid_farm.py  ──hourly──► grid_farm/status.json        ◄──reads── api.py
              ──hourly──► grid_farm/<slug>/equity.csv  ◄──reads── api.py
   │  (33 bots; PUBLIC Deribit prices only, no API keys) demoted to /farm survivors
   └─ DeribitPublicREST

freyr/paper/snapshots/*.json   ──read off disk──►  api.py  (Board, Portfolios, Books)
freyr/paper/snapshots/testnet_live.json (60s poller in FREYR) ──read──► api.py (🔌 Testnet)
freyr/reviews/YYYY-MM-DD.md    ──read off disk──►  api.py  (📊 Review)

api.py (FastAPI :8765) ── serves the 6-tab app + /farm + /btc chart + widget JSON
   └─ Cloudflare tunnel ──► https://bot.banksiaspringsfarm.com
telegram_summary.py ── daily 8am digest    telegram_weekly.py ── Sun 5pm scorecard
```

- **`api.py`** holds all the framing copy and tab builders. The widget JSON contract (`/farm/status`, `/farm/equity`, needing `X-API-Key: WHEEL_API_KEY`) is **load-bearing** — the compiled Android widget reads it.
- **`grid_farm.py`** — the farm supervisor; `VARIANTS` defines the bots, `step_all()` dispatches per engine type. Adding a bot needs **both** `.api` AND `.gridfarm` restarted (the gridfarm holds `VARIANTS` in memory and only re-reads on process start).
- **launchd** services: `com.wheelbot.{api,tunnel,gridfarm,dailysummary,weeklysummary}`. Restart one with `launchctl kickstart -k gui/$(id -u)/com.wheelbot.<svc>`.
- **Python 3.11**, home dir **`/Users/openclaw`** (never `/Users/smcnichol`). Git → `github.com/banksiasprings/btc-wheel-bot`, branch `main`.

## Hard rules

- **Everything is PAPER / testnet.** No real money in this repo. (Freyr's own 21-day sprint to real capital is governed by *its* gates, not here.)
- **Never regenerate `WHEEL_API_KEY`** — the compiled Android widget APK was built with it; changing it breaks the widget with no rebuild.
- **This repo never touches the trading venue.** The 🔌 Testnet tab is a read-only view of a JSON file Freyr writes. Order flow is Freyr's job.
- **Never commit secrets** — `data/`, `.env`, `config/deribit_testnet.json` are gitignored. **Stage files by name, never `git add -A`.**
- **Don't pressure Steven's brother** for his strategy details — reverse-engineer instead (memory `feedback_brother-info-boundary`).

## Where the evidence lives

- **Ground truth for the current era:** `WIDGET_MIGRATION_2026-06-09.md` + `WIDGET_MIGRATION_2026-06-10.md` (the migration to the 6-tab Freyr-fronting app) — frozen dated records, the primary source behind this CONTEXT.
- **Freyr itself:** `/Users/openclaw/Documents/freyr/MISSION_CONTROL.md` (architecture SSOT), `DEPLOYMENT_PLAN.md` (sprint), `ESCAPE.md` ("don't die" layer), `books/chaos.py` (verification).
- **Residual farm:** `grid_farm.py`, `strategies/`, `docs/strategy-plan.md` (the frozen pivot rationale).
- **Retired options system:** `legacy_options/` (bot.py, bot_farm.py, old dashboard, configs). The root `README/SKILL/OPERATIONS/CLOUD_MIGRATION/TERMINOLOGY/CONSISTENCY/IMPROVEMENTS` docs describe *this* era and are superseded — banner points here.

## Out of scope (for now)

- Live/real-money execution in this repo (Freyr owns the path to real capital).
- Restarting the options put-wheel or the RL agent (paused — don't, without reason).
- Re-pointing the dependency (this repo reads Freyr, not the reverse).
