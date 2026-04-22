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

## Improvement #4 — Strike Laddering (2026-04-23)

**Goal:** Split a single large put into multiple smaller puts at different strikes.

**Changes:**
- `config.py`: Added `ladder_enabled: bool = False` and `ladder_legs: int = 2` to `SizingConfig`; wired in `load_config()`
- `config.yaml`: New `sizing.ladder_enabled: false`, `sizing.ladder_legs: 2` settings (opt-in)
- `strategy.py`: Added `delta_target_override` parameter to `select_strike()` (explicit delta target, bypasses IV-rank interpolation). Added `select_ladder_strikes()` method that returns N candidates at evenly-spaced delta positions across [target_delta_min, target_delta_max]. Duplicate strikes are excluded so each leg is at a distinct price level.
- `bot.py`: "Open new leg" section is now ladder-aware. When `ladder_enabled=True`, the bot tracks open put count and opens up to `ladder_legs` positions simultaneously. Each leg receives `max_equity_per_leg / ladder_legs` equity allocation so total exposure equals a single standard leg.
- `risk_manager.py`: `calculate_contracts()` accepts optional `equity_fraction` override for per-leg sizing.

**How to enable:** Set `sizing.ladder_enabled: true` and `sizing.max_open_legs: 2` (or 3) in config.yaml. For 2 ladder legs, the two puts will target delta ≈ 1/3 and 2/3 of the configured range (conservative + aggressive).

**Why it helps:** A single large put at one strike creates binary exposure — BTC either stays above it (full win) or crosses it (full loss). Two smaller puts at different strikes smooth this out: if BTC drops to the lower (aggressive) leg's strike but not the conservative leg's strike, you only take partial assignment. Each leg also hits a different gamma/theta profile which reduces mark-to-market volatility.

**Backtester note:** The backtester remains single-leg (it's a simulation of representative trades). The laddering benefit (smoother P&L curve, reduced concentration risk) shows up in live/paper operation.

**Files changed:** `config.py`, `config.yaml`, `strategy.py`, `bot.py`, `risk_manager.py`

---

## Improvement #5 — Roll Losing Positions Before Expiry (2026-04-23)

**Goal:** Buy back a breached put before expiry and re-sell at a better strike / further out in time.

**Changes:**
- `config.py`: Added `roll_enabled: bool = False` and `roll_min_dte: int = 3` to `RiskConfig`; wired in `load_config()`
- `config.yaml`: New `risk.roll_enabled: false`, `risk.roll_min_dte: 3` settings (opt-in)
- `bot.py`: Added roll check loop in `_tick()` after mark-price updates. When `roll_enabled=True`, each open position is inspected via `risk_manager.should_roll()`. If a breach is detected AND `dte_remaining >= roll_min_dte`, the position is closed (bought back) with reason `roll_<reason>`. The existing "open new leg" logic then immediately opens a replacement put. The wheel guard (`_put_cycle_complete=False`) ensures the replacement is another put, not a call.

**How rolling finds the replacement:** After the breached put is closed, the bot falls through to the normal signal generation on the same tick. `generate_signal()` computes IV rank, applies dynamic delta, and selects the best available put — which will typically be at a lower strike (since BTC moved down, the same delta target maps to a lower strike). This naturally achieves "rolling down and out" without hardcoded offset logic.

**How to enable:** Set `risk.roll_enabled: true` in config.yaml. Tune `risk.roll_min_dte` (default 3) — positions within 3 days of expiry are left to settle naturally since rolling costs more in slippage than it saves.

**Triggers:** Inherits from `risk_manager.should_roll()`:
- `delta_breach`: `|delta| > max_adverse_delta` (0.40 by default)
- `loss_breach`: unrealised loss > `max_loss_per_leg` (2% of equity)

**Backtester note:** Rolling is not simulated in the backtester — it only applies in live/paper operation.

**Files changed:** `config.py`, `config.yaml`, `bot.py`

---

## Improvement #6 — Full Wheel Cycle: Covered Calls After Assignment (pending)

**Goal:** Sell covered calls after put assignment to earn additional premium while holding BTC.
