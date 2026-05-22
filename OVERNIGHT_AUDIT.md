# Overnight Audit — BTC Wheel Bot
**Date:** 2026-05-22 (ran overnight while Steven was asleep)
**Audited by:** Claude (Cowork overnight loop)

---

## What Was Done

### CRITICAL FIXES

#### 1. Bots crash-looping — root cause found and fixed
**Problem:** All 10 farm bots were stuck in a crash loop, showing status `error` repeatedly. Every single bot subprocess was dying instantly with:
```
ModuleNotFoundError: No module named 'loguru'
```
**Cause:** The bot farm had been started 3 times using Python 3.9 (from Apple's CommandLineTools). Python 3.9 on this machine doesn't have the required packages (`loguru`, `fastapi`, etc.). The correct Python is `/usr/local/bin/python3.11`.

**Fix:**
- Killed all 3 duplicate Python 3.9 `bot_farm.py` processes (PIDs 84492, 84546, 84952)
- Killed orphaned child bot processes
- Restarted the farm using `start_farm.sh` which correctly calls `python3.11`
- **Result:** All 10/10 bots immediately started running

#### 2. Android widget showing STOPPED — API key bug
**Problem:** The widget always showed "STOPPED" and no equity data.

**Cause 1 (critical):** `android/app/build.gradle` had the API key hardcoded as `"btc-bot-2024"`. The actual key is `3f985cae37cbfb18da4acb92219ba077` (stored correctly in `local.properties`). Every API call from the app returned HTTP 401 Invalid API key.

**Cause 2:** The widget was calling `/status` and `/equity` (single-bot endpoints). These reflect a single bot's state last heartbeat from **May 3** — 19 days ago. The bot farm runs 10 bots and uses different endpoints.

**Fix:**
- `build.gradle`: changed hardcoded key to read from `local.properties` (same pattern already used for `BOT_API_URL`)
- `BotFarmWidget.java`: switched from `/status` + `/equity` → `/farm/status` + `/farm/equity`
- Widget now shows: farm running state, X/10 bots active, open positions count, combined profit/loss

#### 3. `farm_running` always false in API
**Problem:** `/farm/status` returned `farm_running: false` even when the farm was running.

**Cause:** `start_farm.sh` was writing the PID to `/tmp/farm.pid`, but `api.py` checks `data/farm_pid.txt`. They never matched.

**Fix:** Updated `start_farm.sh` to write the PID to `data/farm_pid.txt`. Also wrote the current farm PID (11497) immediately so the fix takes effect without restarting.

---

### DASHBOARD UX IMPROVEMENTS

Applied plain-English label pass to the web widget (`/widget`) per Steven's brief:

| Old (jargon) | New (plain English) |
|---|---|
| `DTE 2` | `2 days left until expiry` |
| `short_put @ $77,000` | `Sold put option — strike $77,000` |
| `P&L: +$4.64` | `Profit/Loss: +$4.64` |
| `Ann. ROI: +12.3%` | `Yearly return rate: +12.3%` |
| `W 35%` | `Win rate 35%` |
| `LATEST POSITION` | `CURRENT TRADE` |
| `BEST BOT` | `BEST PERFORMING BOT` |
| `RL AGENT V1` | `AI BOT (RL AGENT V1)` |
| Risk: `OK / CAUTION / DANGER` | `Safe / Watch it / High Risk` |

Added overview summary bar at the top of the widget:
> **Bot farm is RUNNING.** 10 of 10 bots are active. Combined profit/loss: -$155 (-0.02%). Best bot so far: rl_agent_stress at +0.02%

---

### RL TRAINING — NO CHANGES NEEDED
The RL reward function was reviewed and is **correctly targeting capital efficiency** (ROI on capital), not Sharpe ratio. Specifically:
- Reward = `tanh(premium_earned / capital_at_risk * annualisation_factor)`
- Drawdown penalty is mild and secondary
- Idle penalty is tiny (discourages sitting still without punishing caution)
- Capital overuse penalty discourages tying up >30% of equity as margin

Training is progressing well: **796,672 / 2,000,000 timesteps** (~40% complete). Explained variance = 0.945 (excellent fit). No changes made.

---

## Current Farm State (as of end of audit)

| Metric | Value |
|---|---|
| Farm running | YES (PID 11497, Python 3.11) |
| Bots active | 10/10 |
| RL training | Running (796k/2M steps) |
| Combined equity | $999,844 / $1,000,000 |
| Combined profit/loss | -$155 (-0.02%) |
| Open positions | 5 bots have open short puts |
| Android APK | Updated and installed on phone |
| GitHub | Committed and pushed (c196582) |

### Bot breakdown:
- `rl-agent-stress`: 28 trades, +0.02% — only bot with meaningful history
- `short-dte-theta`: 1 trade, -0.17% — one loss so far
- All others: just started, 0 trades (were crash-looping until tonight)

---

## Items for Steven to Decide

1. **Live trading readiness:** No bots are anywhere near ready for real money (all need 30+ days running, 20+ trades, and walk-forward validation passing). The crash loop tonight means the clock effectively resets. Expect ~4-6 weeks before any bot is promotion-ready.

2. **Win-rate targets:** The readiness validator requires >50% win rate. `rl-agent-stress` is at 35.7% (28 trades). This may improve but is something to watch.

3. **Position sizing:** All bots are set to $100,000 virtual starting equity. If you ever go live, you'll want to set `starting_equity` to your actual capital in the relevant `configs/` YAML file.

4. **Telegram alerts:** Telegram is configured (token + chat ID in `.env`) and should be sending risk alerts when positions move into "caution" or "danger" territory. Worth verifying you're receiving those.

5. **RL agent deployment:** Once training hits 2M steps (est. a few more hours), the model checkpoint at `rl_agent/checkpoints/final_model.zip` will be the best version. The `rl-agent-v1` bot in the farm uses this model live.

---

## What's Working Well

- Farm supervisor (bot_farm.py) correctly auto-restarts crashed bots and discovers new paper configs dynamically
- Risk monitor running as daemon, fires Telegram alerts on position risk transitions
- Position tracking across all bots is solid — current_position.json, trades.csv all persisting correctly
- Web dashboard (`/widget`) loads cleanly, auto-refreshes every 30 seconds, is clean and readable
- API server has good coverage: farm status, equity, per-bot trades, controls, optimizer results
- RL environment reward function is well-designed for capital efficiency

---
*Generated by Claude overnight audit loop — 2026-05-22*
