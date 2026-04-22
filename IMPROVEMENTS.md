# Strategy Improvements Log

## Improvement #1 — Increase Trade Frequency (2026-04-23)

**Goal:** Target Deribit weekly expiries to roughly double trade frequency.

**Changes:**
- `config.yaml`: `strategy.min_dte` 8 → 7 (live bot instrument filter now captures weekly options with 7 DTE)
- `config.yaml`: `strategy.max_dte` 21 → 14 (caps at bi-weekly; prevents bot sitting in 3-week positions when weeklies are available)

**Why:** The backtester already uses 7-DTE trades (because `expiry_preference[0] == "weekly"`), but the live bot's instrument pre-filter in `bot.py` was silently excluding options with exactly 7 DTE remaining. Lowering `min_dte` to 7 aligns live behaviour with the simulation. Capping `max_dte` at 14 prevents the bot from picking longer-dated monthlies when shorter-dated weeklies are available, keeping capital cycling faster.

**Files changed:** `config.yaml`

---

## Improvement #2 — Regime Filter (pending)

**Goal:** Skip put-selling during BTC downtrends to reduce assignment risk.

---

## Improvement #3 — Dynamic Delta Based on IV Rank (pending)

**Goal:** Sell more aggressive delta (closer ATM) when IV rank is high, conservative when low.

---

## Improvement #4 — Strike Laddering (pending)

**Goal:** Split a single large put into multiple smaller puts at different strikes.

---

## Improvement #5 — Roll Losing Positions Before Expiry (pending)

**Goal:** Buy back a breached put before expiry and re-sell further OTM / longer dated.

---

## Improvement #6 — Full Wheel Cycle: Covered Calls After Assignment (pending)

**Goal:** Sell covered calls after put assignment to earn additional premium while holding BTC.
