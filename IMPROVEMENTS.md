# Strategy Improvements Log

## Improvement #1 — Increase Trade Frequency (2026-04-23)

**Goal:** Target Deribit weekly expiries to roughly double trade frequency.

**Changes:**
- `config.yaml`: `strategy.min_dte` 8 → 7 (live bot instrument filter now captures weekly options with 7 DTE)
- `config.yaml`: `strategy.max_dte` 21 → 14 (caps at bi-weekly; prevents bot sitting in 3-week positions when weeklies are available)

**Why:** The backtester already uses 7-DTE trades (because `expiry_preference[0] == "weekly"`), but the live bot's instrument pre-filter in `bot.py` was silently excluding options with exactly 7 DTE remaining. Lowering `min_dte` to 7 aligns live behaviour with the simulation. Capping `max_dte` at 14 prevents the bot from picking longer-dated monthlies when shorter-dated weeklies are available, keeping capital cycling faster.

**Files changed:** `config.yaml`

---

## Improvement #2 — Regime Filter (2026-04-23)

**Goal:** Skip put-selling during BTC downtrends to reduce assignment risk.

**Changes:**
- `config.py`: Added `use_regime_filter: bool = False` and `regime_ma_days: int = 50` to `SizingConfig` dataclass with proper wiring in `load_config()`
- `bot.py`: Added `_regime_daily_prices` deque (one sample per UTC calendar day), `_last_regime_sample_date` tracking, and `_is_above_regime_ma()` method. New position opening in `_tick()` is now gated on the regime check.
- `backtester.py`: Pre-computes rolling N-day SMA on the price DataFrame and skips new leg entries when spot < SMA (with warmup period pass-through for the first N rows).
- `config.yaml`: `use_regime_filter` remains `false` (opt-in) — the 12-month backtest period is mostly bearish, so enabling by default would reduce trades from 11 → 1. Enable manually when you want downtrend protection.

**How to enable:** Set `sizing.use_regime_filter: true` in config.yaml. The bot will skip opening new put legs whenever BTC spot is below its `regime_ma_days`-day simple moving average, resuming automatically once BTC recovers above it. Existing open positions are always tracked to expiry regardless.

**Why kept opt-in:** The 12-month backtested period (Apr 2025–Apr 2026) saw a significant BTC downtrend, which would have blocked 10 of 11 trades. In a bullish regime the filter has minimal impact; in a bear market it virtually halts trading — which is the intended behaviour for capital preservation, but the user should consciously decide to enable it.

**Files changed:** `config.py`, `bot.py`, `backtester.py`, `config.yaml`

---

## Improvement #3 — Dynamic Delta Based on IV Rank (2026-04-23)

**Goal:** Sell more aggressive delta (closer ATM) when IV rank is high, conservative when low.

**Changes:**
- `config.py`: Added `iv_dynamic_delta: bool = False` to `StrategyConfig`; wired in `load_config()`
- `config.yaml`: `iv_dynamic_delta: true` (enabled by default — validated improvement)
- `strategy.py`: `select_strike()` now accepts `iv_rank` parameter. When `iv_dynamic_delta=True`, the target delta midpoint shifts linearly from `target_delta_min` (IV rank = 0, conservative, far OTM) to `target_delta_max` (IV rank = 1, aggressive, closer ATM). `generate_signal()` passes `iv_rank` through.
- `backtester.py`: `_target_strike()` updated with the same linear interpolation, receiving `ivr/100.0` from the simulation loop.

**Backtest improvement (12 months):**

| Metric           | Before     | After      |
|------------------|------------|------------|
| Total return     | +67.64%    | +74.40%    |
| Sharpe ratio     | 1.16       | 1.22       |
| Max drawdown     | -20.05%    | -19.42%    |
| Avg premium yield| 1.34%/ct   | 1.57%/ct   |

**Why it works:** When IV rank is high, options are expensively priced — selling closer-to-ATM (higher delta) captures more premium per contract without meaningfully increasing risk because the elevated IV provides a larger cushion. When IV rank is low, selling far OTM (lower delta) avoids overexposure to a market that's already calm and unlikely to deliver enough premium to justify the risk of assignment.

**Files changed:** `config.py`, `config.yaml`, `strategy.py`, `backtester.py`

---

## Improvement #4 — Strike Laddering (pending)

**Goal:** Split a single large put into multiple smaller puts at different strikes.

---

## Improvement #5 — Roll Losing Positions Before Expiry (pending)

**Goal:** Buy back a breached put before expiry and re-sell further OTM / longer dated.

---

## Improvement #6 — Full Wheel Cycle: Covered Calls After Assignment (pending)

**Goal:** Sell covered calls after put assignment to earn additional premium while holding BTC.
