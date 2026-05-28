# CONTEXT.md

> **Maintenance contract:** Read this first in any new session — it is the canonical anchor for what this project is and how it is shaped. Update it whenever the architecture, core strategy, or operating model changes meaningfully. **This file takes priority over README.md** when they disagree; the README is user-facing setup, this is the design rationale. If you make a structural change without updating CONTEXT.md, you have created doc rot.

## What this project is (current — 2026-05-29)

A **Bitcoin paper-trading "bot farm"** for Steven (QLD farmer, not finance-literate, lives on his phone). It runs **25 strategy bots side-by-side on live Deribit prices with pretend money** ($10k each), so we can watch them compete and pick real winners with evidence. **Nothing is live with real money yet** — the whole farm is paper.

Core philosophy: **direction-agnostic, unleveraged, survival-first.** We are not predicting whether Bitcoin goes up or down; we harvest its volatility and we prize *surviving every crash* over flashy returns. Target for real money eventually: a survivable ~15–25%/yr, unleveraged, own money only.

### History (why it looks the way it does)
The project began as an **options put-wheel** + an RL agent that traded it. A real-data eval harness proved the wheel returned only ~1–5%/yr and **no RL model beat a 5-line baseline** — and the wheel is a *short-vol* strategy (loves calm, hurt by big moves), the opposite of what we want. So on **2026-05-28 it pivoted** to a spot **grid-bot farm** and then a multi-strategy farm. The old options system is **retired → `legacy_options/`** (kept, not deleted). The RL effort is paused. Don't restart the put-wheel without a reason.

## The farm: 25 bots across 7 "tabs" (families)

Each bot is a paper account stepped hourly on the live BTC price. Engines live in `strategies/`:

| Tab | Engine file / class | What it does (plain) |
|-----|--------------------|----------------------|
| **Grid** (7) | `grid_bot.py:GridBot` | Buy-low/sell-high ladder on BTC's wiggles. **The lead candidate.** Variants: Vault/Steady/Balanced/Brisk/Aggressive/Wild/Degen(3× leverage, paper-only "for kicks"). Balanced (5% spacing / 20 lots / 15-day trend-stop) is Steven's pick. |
| **Funding** (4) | `income_bots.py:FundingBot` | Market-neutral carry — collects perp funding, ~6%/yr, near-zero risk. The safe floor. |
| **Long-Vol** (4) | `income_bots.py:LongVolBot` | Long volatility — profits from big moves/crashes, bleeds in calm. The grid's crash hedge. |
| **Premium** (2) | `more_bots.py:ShortVolBot` | Sells volatility (the wheel's spirit) — earns in calm, loses in big moves. |
| **Trend** (2) | `more_bots.py:TrendBot` | Directional MA-follow — the "does predicting direction beat neutral?" contrast. |
| **Stack** (3) | `more_bots.py:RebalanceBot/DCABot/BuyHoldBot` | Accumulation + the buy&hold benchmark everything must beat. |
| **Convex** (3) | `convex_bots.py:TailHedgeBot/GammaScalpBot/BackspreadBot` | Options "big-payoff" structures: crash insurance (bleeds ~1%/yr, +~70% in a flash crash), gamma scalp (long-vol that *actually trades*), backspread (cheap big-move bet). Simplified per-step models. |

The vol bots (Long-Vol/Premium/Convex) are **simplified per-step models**, NOT full options sims — labelled as such in the UI. They were sanity-tested on `rl_agent/data/btc_daily.csv`. **Be skeptical of any backtest showing 0% drawdown or >40%/yr unleveraged** — two fill-logic bugs already produced fantasy numbers.

## Architecture (live system, all at repo root)

Three independent processes, **file-based IPC** (no queue — simplicity is the feature; a missing file self-heals, a broken queue is an outage):

```
grid_farm.py  ──hourly──► grid_farm/status.json        ◄──reads── api.py
              ──hourly──► grid_farm/<slug>/equity.csv  ◄──reads── api.py
              ──hourly──► grid_farm/<slug>/state.json  (resume-safe per-bot state)
   │
   └─ uses deribit_client.DeribitPublicREST (PUBLIC prices only — no API keys)

api.py (FastAPI :8765) ── serves ──► dashboard + /btc chart + JSON for the phone widget
   └─ Cloudflare tunnel ──► https://bot.banksiaspringsfarm.com

dashboard_ui.py  ── alternate LOCAL Streamlit view (streamlit run dashboard_ui.py)
telegram_summary.py  ── daily 8am digest      telegram_weekly.py ── Sun 5pm scorecard
```

- **`grid_farm.py`** — the supervisor. `VARIANTS` list defines all 25 bots; `make_bot()` builds by `type`; `step_all()` dispatches: funding→`step(funding_1h)`, longvol/shortvol/tailhedge/gammascalp/backspread→`step(price, dvol)`, grid→`on_close(price, low)`, others→`step(price)`. Run: `caffeinate -s python3.11 grid_farm.py` (or `--once` for cron).
- **`api.py`** — endpoints: `/health`; `/farm/status` + `/farm/equity` (need `X-API-Key: WHEEL_API_KEY` — **the Android widget reads these, contract is load-bearing**); `/` + `/widget` (HTML dashboard, 7 tabs via `?tab=`); `/bot/{slug}` (per-bot detail + equity graph); `/btc` (live BTC price chart, `?range=1D|1W|1M|1Y|5Y|Max`, Deribit candles back to 2019, in-process cached so the dashboard never blocks on Deribit).
- **Bot interface contract** (so the farm can drive any engine): every bot has `step(...)`, `to_dict()/load_dict()`; price-holding bots also have `equity(price)`, `btc_held()`, `.cash`; grid has `on_close(price, low=)` + `equity(price)`. `tab` is decoupled from engine `type` in `VARIANTS`.

## Operating model (deployment)

- **launchd auto-start** (macOS login items, installed by `scripts/install_launchd.sh`): `com.wheelbot.api`, `.tunnel`, `.gridfarm`, `.dailysummary` (8am), `.weeklysummary` (Sun 5pm). Restart one with `launchctl kickstart -k gui/$(id -u)/com.wheelbot.<svc>`.
- **Python 3.11**, home dir **`/Users/openclaw`** (never `/Users/smcnichol`). Git → `github.com/banksiasprings/btc-wheel-bot`, branch `main`.
- **Telegram** creds in `data/notifier_config.json` (gitignored).

## Hard rules

- **Everything is PAPER.** No real money until Steven explicitly decides, after the bake-off.
- **Real money would be UNLEVERAGED, own money, start tiny then scale.** The leveraged bots (Degen, Funding 3×, etc.) are **paper-only "for kicks"** — they exist to show the wipeout risk, never to fund.
- **Never regenerate `WHEEL_API_KEY`** — the already-compiled Android widget APK was built with it; changing it breaks the widget with no rebuild.
- **Never commit secrets** — `data/`, `.env`, `config/deribit_testnet.json` are gitignored. Stage files by name, never `git add -A`.
- **Don't pressure Steven's brother** for his strategy details — reverse-engineer instead (see memory `feedback_brother-info-boundary`).

## Testing plan (Steven's decisions, 2026-05-29)

1. **Bake the paper farm ~8–12 weeks** before the first cull (he chose the most patient option — wait for a real market move to test the crash-hedges). First proper review ≈ late July–August 2026.
2. **Judge on a survival-first scorecard**, not raw profit: profit, *worst dip*, smoothness (return per dip), did-it-survive (no liquidation), and does-live-match-backtest (divergence = red flag/overfit). `telegram_weekly.py` sends this every Sun 5pm: SAFEST = unleveraged ranked by *return minus worst-dip*, plus biggest gainers + a 💀/big-dip watch list.
3. **Cull** losers/scary-dippers/liquidated/backtest-divergent → shortlist 1–3 unleveraged real-money candidates.
4. **Go-live gates** (all required before a real $): survived the bake with acceptable dips; live ≈ backtest; the Deribit **perp-vs-spot** execution wrinkle solved; an alert/kill-switch wired.
5. **First real stake size = decided later** (don't assume).
6. **RL only later** — as a *tuning layer* on a proven-live bot, kept only if it beats the simple rule head-to-head. Not the engine, not now.

Open build idea (not done): a one-click "paper-vs-backtest consistency" check + a weekly "market-mood" note.

## Where the evidence lives

- `docs/strategy-plan.md` — the forward strategy doc + rationale.
- `strategies/grid_backtest.py`, `grid_frontier.py`, `funding_backtest.py`, `vol_premium.py` — real-data backtests (the yardstick).
- `rl_agent/` — paused RL effort + `eval_harness.py`, `baselines.py`, `data/btc_daily.csv` (used to sanity-test vol bots).
- `legacy_options/` — the retired options system (bot.py, bot_farm.py, old dashboard, configs/, farm/). Its developer guide is in git history; only relevant if working in that folder.

## Out of scope (for now)

- Live/real-money execution (until the bake-off picks winners + the perp-vs-spot wrinkle is solved).
- Multi-asset (BTC only). Restarting the options put-wheel or the RL agent (paused — don't, without reason).
- A held-BTC "core" hybrid (Steven's possible later call — he started pure-income; govt money-printing thesis might bring it back).
