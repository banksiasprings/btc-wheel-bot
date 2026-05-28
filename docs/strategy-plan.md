# BTC Bot — Strategy Decision & Forward Plan
**Last updated:** 2026-05-28

## Context — how we got here

The project spent ~180 CPU-hours training an RL agent to trade an options put-wheel. A real-data eval harness then showed the painful truth: the wheel returns ~1-5% on a bear slice (~11%/yr over a full cycle), and **no RL model beat a 5-line baseline.** Root cause: the synthetic training data had no real edge in it (IV hardcoded to 1.2×RV), and a put-wheel is a *short-volatility* strategy — it loves calm and is hurt by big moves, the **opposite** of what Steven wants.

Steven's actual goal: **maximum ROI, direction-agnostic (no market prediction), no leverage, survives crashes.** His brother reportedly runs a "simple rules" BTC bot at >100%/yr whose risk signature ("stagnant market is the danger, volatility makes money") points to a **grid bot**, not an options wheel. Brother is off-limits for details (Steven will reverse-engineer, and would only compare notes once he has his own working bot to offer in exchange).

So we built and measured the candidate strategies on real BTC data. This doc records what we found and what to build.

## The evidence (all unleveraged, real data, costs on, bugs found & fixed)

| Strategy | APR (typical) | Max Drawdown | Notes |
|---|---|---|---|
| Buy & hold | regime-dependent | **30-64%** | +1948% over 7yr but −64% in 2022; pure direction bet |
| Funding harvest (delta-neutral) | ~6% | ~0% | safe floor, boring, needs 2× capital, slightly −ve in bears |
| Wheel — IV-gated (realistic) | ~11% | ~3% | low-risk, capped upside, secretly long |
| Wheel — perfect-foresight oracle | 27% | 0% | the wheel's *hard ceiling*, impossible live |
| Grid — aggressive (no stop) | **~16-30%** | **25-33%** | high return, but holds "bags" through crashes |
| **Grid + 15d trend-stop (5% spacing)** | **~10-19% all-weather** | **~1% idealized / low-single-digit real** | positive in *every* regime incl. the −64% crash; best risk-adjusted |

Key facts established:
- **No unleveraged strategy reaches >100%.** Even a perfect-foresight wheel caps at 27%. The brother's >100% almost certainly used leverage and/or a single exceptional ranging period. >100% and "no-leverage + survival" are mutually exclusive on BTC.
- **The grid is the best fit for the goal** — it's paid by volatility, makes no directional call, needs no leverage, and beat buy-and-hold by 35-50 points in *both* bear markets.
- **A trend-stop fixes the grid's only real flaw.** Going to cash when price < 30-day MA cuts max drawdown from ~33% to ~3.5% and turns the 2022 crash positive, at the cost of bull-market return. The same fixed config gives ~8-12% APR across 7 different years → robust, not overfit.
- **A fully delta-neutral grid does NOT work** — hedging inventory with a short perp loses exactly the 2% the grid gains (the grid's profit *is* a mean-reversion directional bet). Partial risk control via the trend-stop is the right lever, not a hedge.

## Recommended strategy

**A spot grid bot with a 15-day-MA trend-stop**, deployed unleveraged. A frontier sweep (`strategies/grid_frontier.py`) found **5% spacing with a 15-day MA stop** dominates — wider spacing + a faster stop beat the original 2%/30d badly. Three presets:

- **Steady (start live here):** 5% spacing, 50 lots, 15-day MA stop → ~10% APR, ~0.6% *idealized* DD. Positive in every regime.
- **Balanced (the standout):** 5% spacing, 20 lots, 15-day MA stop → **~18-19% APR, ~1.4% idealized DD** (MAR ~14). Best return-for-risk found.
- **Aggressive (no stop):** 5% spacing, 20 lots → ~29% APR but 18-33% DD. Only after the stopped version is proven live.

> **Idealized vs real drawdown:** the backtest exits exactly at the close when price crosses below the MA. A real crash *gaps through* the stop, so live drawdown will be higher than the <1% shown — expect low-to-mid single digits. Slippage and downtime add more. The trend-stop's drawdown control is strong but not the sub-1% fantasy; paper-trading will reveal the true figure.

Rationale: it's the only candidate that clears Steven's whole bar (direction-agnostic, no leverage, survivable, clearly beats a term deposit). ~18% all-weather with low-single-digit drawdown is a genuine, deployable "bot of my own that makes money" — and the entry ticket to comparing notes with his brother. Run **funding harvest on idle cash** as a small optional overlay (~+5% on uninvested capital).

## Forward plan (staged, vertical slices)

**Decisions locked (2026-05-28):** pure income bot (0% held BTC) to start; Balanced preset (5%/20/15d-MA); own money only (no leverage); $5-10k once proven; exchange TBD (lean spot venue, not Deribit). Steven is not finance-literate — explain in plain English + dollar terms.

**Phase A — Deployable grid engine (paper). ✅ ENGINE DONE.**
1. ✅ `strategies/grid_bot.py` — live-capable `GridBot` class (event-driven `on_close(price)`, trend-stop built in). **Self-check: reproduces the backtest exactly** ($356,131, 2444 trades). Paper-test on last 12 months of real prices: $10k → $12.7k (+27%) while BTC fell 20%.
2. ✅ `strategies/paper_live.py` — forward paper-test on LIVE Deribit prices, pretend money, no API keys. Reuses `DeribitPublicREST.get_ticker("BTC-PERPETUAL")` (uses `mark_price`; `underlying_price` is absent on perps). Warms up the 15-day MA from real history, persists state to `paper_state.json` (resume-safe), logs to `paper_live.log`. **Verified end-to-end live** (warmed up 361h, fetched live $73,158, save/resume exact). Run: `cd strategies && caffeinate -s python3.11 paper_live.py`.
3. TODO (real-money step): exchange decision = **Deribit** (reuses existing `deribit_client.py`). Settle the perp-vs-spot wrinkle (perp = margin product, run strictly 1x). Add a live execution adapter using `DeribitPrivateREST` + keys, daily scoring via `metrics.py`, and reuse the heartbeat/KILL_SWITCH patterns.

**Phase B — Paper-trade for 4+ weeks.** Run the steady preset on live data, no capital. Gate to go live: realized behavior matches backtest (drawdown < 5%, positive or flat, fill assumptions hold).

**Phase C — Go live, minimum capital.** Start with a small fixed stake on a real exchange (spot, no leverage). Compare live vs paper vs backtest weekly. Keep the kill-switch pattern from the existing bot.

**Phase D — Tune & scale.** Once 1-2 months of live data confirm the edge: tune spacing/lots/MA against live fills, consider the aggressive preset for a sleeve, add the funding overlay. Scale capital only after sustained positive live returns.

**Where RL fits (later, optional):** the wheel oracle showed ~16 points of headroom between the realistic IV-gate (11%) and perfect selectivity (27%). If we ever revisit ML, its job is *selectivity/timing* (when to run the grid, when to widen spacing) — not replacing the rules. Not now.

## Risks & assumptions (be honest)
- **Backtest fills are conservative (close-to-close).** A live limit-order grid may do somewhat better — but live also has slippage, downtime, and exchange risk that backtests ignore. Paper-trading is non-negotiable before capital.
- **The grid assumes BTC keeps mean-reverting and eventually recovers.** Historically true; not guaranteed. The trend-stop is the insurance against a regime where it isn't.
- **Returns are real but modest (~10%, not >100%).** Manage expectations: this is a steady, crash-resistant compounder, not a moonshot. Leverage could multiply it — and the wipeout risk — but that's a separate, deliberate decision.
- **Fixed lot size** in the backtest understates compounding; a live bot should scale lots with equity (raises both return and drawdown).

## Open decisions for Steven
1. **Risk appetite:** I lean toward the Balanced preset (5%/20/15d-MA → ~18% APR, low-single-digit DD) as the live starting point, with Steady (~10%) as the cautious option. Which?
2. **Leverage:** stays off (per your constraint)? Confirm — it's the only path to >100% but it breaks survival.
3. **Exchange:** which venue for live (Binance spot, Deribit, other)? Affects fees and the data/execution wiring.
4. **Capital:** what's the initial live stake once paper-trading passes?
