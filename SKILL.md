---
name: btc-wheel-bot
description: >
  Developer guide for the BTC Wheel Bot project at ~/Documents/btc-wheel-bot.
  USE THIS SKILL whenever working on any file in that project — bot.py, dashboard_ui.py,
  risk_manager.py, config.py, config.yaml, backtester.py, or any related file.
  It captures the architecture, all cross-file data contracts, known failure patterns
  from production incidents, and a mandatory pre-commit checklist.
  This skill exists because changes to one file silently break others —
  the checklist prevents regression bugs that have already cost real debugging time.
---

# BTC Wheel Bot — Developer Guide

## Project location
```
~/Documents/btc-wheel-bot/
```

## Architecture overview

The system has three independent processes that share state via files:

```
bot.py  ─── writes ──► bot_heartbeat.json   ◄─── reads ─── dashboard_ui.py
        ─── writes ──► data/trades.csv       ◄─── reads ─── dashboard_ui.py
        ─── writes ──► data/tick_log.csv     ◄─── reads ─── dashboard_ui.py (future)
        ─── reads  ──► config.yaml
        ─── reads  ──► KILL_SWITCH (file presence = halt)

risk_manager.py  ← imported by bot.py (RiskManager, Position)
config.py        ← imported by bot.py, risk_manager.py, backtester.py (singleton cfg)
backtester.py    ← imported by dashboard_ui.py (Backtester class)
optimizer.py     ← spawned by dashboard_ui.py as subprocess
main.py          ← entry point, starts bot.py loop
preflight.py     ← imported by dashboard_ui.py (connection checks)
```

**Deribit connection:** WebSocket via `deribit_client.py`. Testnet vs live controlled by `config.yaml → deribit.testnet`.

**Home directory is `/Users/openclaw`** — never assume `/Users/smcnichol` or any other path.

---

## Critical: Cross-file data contracts

These are the places where a change in one file MUST be mirrored in another.
Missing this is the #1 source of silent bugs in this codebase.

### 1. Position dataclass (risk_manager.py)
Any field added to `Position` must be considered in:
- `bot.py → _open_position()` — must populate the new field
- `bot.py → _print_status()` — may need to include it in heartbeat or tick_log
- `bot.py → _close_position()` — may need to write it to trades.csv
- `dashboard_ui.py` — may need to read/display it from heartbeat

### 2. Heartbeat JSON schema (bot.py → _print_status)
The heartbeat written at every tick has this exact schema:
```json
{
  "pid": int,
  "timestamp": float,           // Unix epoch
  "mode": str,                  // "paper" | "testnet" | "live"
  "equity_usd": float,
  "btc_price": float,
  "iv_rank": float,             // 0.0–1.0
  "wheel": str,                 // "→put-mode" | "→call-mode"
  "position": {                 // null when flat
    "name": str,                // instrument name e.g. "BTC-24APR26-72000-P"
    "option_type": str,         // "put" | "call"
    "strike": float,
    "delta": float,
    "dte": int,
    "dte_at_entry": int,        // 0 for positions reconciled on startup
    "entry_price": float,       // BTC
    "current_price": float,     // BTC
    "contracts": float,
    "unrealized_pnl_usd": float
  }
}
```
**If you add a key to the heartbeat, update the dashboard reader in `tab_paper()` immediately.** The dashboard silently ignores unknown keys — missing keys cause KeyError crashes.

### 3. trades.csv schema (bot.py → _close_position)
Exact 19 fields in order:
```
timestamp, instrument, option_type, strike, entry_price, exit_price, contracts,
pnl_btc, pnl_usd, equity_before, equity_after, btc_price,
iv_rank_at_entry, dte_at_entry, dte_at_close, slippage_btc, fill_time_sec, reason, mode
```
**If you add/rename a field, update ALL of these simultaneously:**
- `fieldnames` list in `_close_position()` (bot.py ~line 649)
- `show` column list in `tab_paper()` (dashboard_ui.py, Recent Trades section)
- `read_trades()` function if it filters columns

### 4. tick_log.csv schema (bot.py → _print_status)
Exact 8 fields:
```
timestamp, btc_price, equity_usd, position_name, delta, dte, iv_rank, unrealized_pnl_usd
```

### 5. config.yaml sections
All 8 sections with their keys. KeyErrors cascade silently across multiple files:
```yaml
deribit:    testnet, client_id, client_secret, ws_url, testnet_ws_url
strategy:   iv_rank_threshold, target_delta_min, target_delta_max, min_dte, max_dte,
            approx_otm_offset, min_premium_fraction, iv_rank_window_days
sizing:     max_equity_per_leg, collateral_buffer, contract_size_btc,
            min_free_equity_fraction, max_open_legs
risk:       max_adverse_delta, max_loss_per_leg, max_daily_drawdown, kill_switch_file
execution:  order_timeout_seconds, poll_interval_seconds, max_retries
backtest:   starting_equity, lookback_months, approx_otm_offset, premium_fraction_of_spot
overseer:   enabled, check_interval_minutes, model
logging:    level, rotation, retention
```

### 6. Dashboard tab layout (dashboard_ui.py → main())
**This is fragile — the tab labels and function calls must be in matching order.**
```python
tab1 → "📊 Backtest"        → tab_backtest()
tab2 → "📈 Paper Trading"   → tab_paper()
tab3 → "🧬 Optimizer"       → tab_optimizer()
tab4 → "📋 Recommendations" → tab_recommendations()
tab5 → "⚙️ Config"          → tab_config()
tab6 → "🔧 Settings"        → tab_settings()
```
**This has been swapped before (tab4/tab5 incident). Always verify after touching main().**

---

## Known failure patterns (lessons from production)

### Phantom trade bug
**What happened:** `_close_position()` wrote to trades.csv before confirming the order filled, creating phantom records on failed orders.
**Fix in place:** `_close_position()` returns `bool`. CSV write only happens after confirmed fill OR in paper mode. Failed live orders return `False` without touching CSV.
**Rule:** Never write trade records before the close is confirmed. Always check the return value:
```python
closed = await self._close_position(pos, reason, price)
if closed:
    self._positions.remove(pos)
# DO NOT remove position or write anything if closed is False
```

### osascript multi-command failures
**What happened:** `do shell script "cmd1; cmd2"` silently fails or errors in osascript.
**Fix:** Run one command per osascript call. Never chain with semicolons inside `do shell script`.

### Streamlit CSS vs BaseWeb specificity
**What happened:** `!important` on `.stTabs [data-baseweb="tab-list"]` couldn't override BaseWeb's inline `overflow: scroll hidden`. Spent 4 iterations on tab clipping.
**Fix:** Use a JavaScript MutationObserver injected via `st.markdown()` to force styles that BaseWeb owns. CSS `!important` alone is not sufficient for BaseWeb-controlled properties.

### Chrome coordinate scaling
**What happened:** `getBoundingClientRect()` returns CSS pixels; screenshot coordinates are at device pixel ratio scale (~0.808×). Direct use of JS rect coordinates for clicks fails.
**Fix:** Use Chrome MCP tools or JavaScript tab `.click()` for React/Streamlit navigation instead of pixel coordinates.

### Reconciled position metadata gap
**What happened:** Positions that already existed when the bot restarted (reconciled from Deribit API) have `dte_at_entry = 0` and `iv_rank_at_entry = 0.0` because those values weren't stored — only freshly opened positions populate them.
**Fix in place:** Ann. Return shows "N/A" when `dte_at_entry == 0`. This is correct — don't try to calculate return on positions you don't have entry metadata for.

### Collateral calculation inconsistency (fixed)
**What happened:** `check_position_size()` was using `strike × contract_size_btc` for collateral per contract, but `calculate_contracts()` used `strike` alone. These two sizing functions disagreed.
**Fix in place:** Both functions now use `strike × contracts` (no contract_size_btc multiplier). `contract_size_btc=0.1` is the minimum lot size, not a collateral multiplier. Committed in bbc4df6.
**Rule:** Whenever touching sizing logic, audit both functions together and verify they use identical collateral formulas.

### Streamlit widget key stability — silent button/slider failures
**What happened:** The Optimizer "Start" button silently did nothing after the user changed the parameter selectbox. The Settings log slider reset to 50 every time the log file changed. Both were caused by missing `key=` parameters on Streamlit widgets.
**Root cause:** Without explicit `key=`, Streamlit auto-generates widget IDs from label + render order. Changing any upstream widget (e.g. a selectbox) shifts render order, so the button gets a new fingerprint — the click registers against a ghost widget that no longer exists.
**Fix:** Every interactive widget that lives alongside other changing widgets MUST have an explicit stable `key=`. This includes buttons, selectboxes, sliders, radios, text inputs. Commit c7c65ea.
**Rule:** When adding any new Streamlit widget to dashboard_ui.py, always give it a unique descriptive `key=` string. Never leave `key` unset in a multi-widget section.
```python
# ✅ correct
param = st.selectbox("Parameter", options, key="optimizer_param")
st.button("Start Optimizer", key="optimizer_start_btn")

# ❌ wrong — silent failures when surrounding widgets change
param = st.selectbox("Parameter", options)
st.button("Start Optimizer")
```

### Order object property name mismatch — fill_time_sec always 0.0
**What happened:** `fill_time_sec` recorded 0.0 in every trades.csv row. The code called `getattr(rec, "elapsed_sec", 0.0)` but the actual property on the order record object is `elapsed_seconds`.
**Fix in place:** Now uses `rec.filled_at - rec.created_at` for exact fill duration. Committed in bbc4df6.
**Rule:** When reading properties off Deribit order record objects, verify the exact attribute name in `deribit_client.py` before using it. Silent `getattr` defaults are dangerous — they mask missing data as zeros.

### Optimizer pipeline disconnect — sweep results not feeding into evolve
**What happened:** Sweep ran and charted but results were never consumed by Evolve mode. Evolve always started from random genomes. Recommendations tab had hardcoded static data from a one-off manual run. Also, the dashboard was passing `--population`/`--generations`/`--elite`/`--mutation` subprocess flags but optimizer.py only accepted `--pop`/`--gen` and ignored `--elite`/`--mutation` entirely.
**Fix in place:** (1) `optimizer.py` now accepts `--seed-from-sweep` flag and `seed_from_sweep` parameter in `run_evolution()` — reads `sweep_results.json`, builds a seed `ParamSet` from best-per-param values, uses mutated copies as 30% of generation 0. (2) `optimizer.py` argparse renamed to `--population`/`--generations`, added `--elite`/`--mutation` wired through to `run_evolution()`. (3) Dashboard adds "🌱 Seed initial population from sweep results" checkbox (disabled until sweep_results.json exists). (4) `_render_optimizer_results()` sweep mode now shows a best-value-per-parameter table below the chart. (5) `tab_recommendations()` reads `best_genome.yaml` dynamically and shows "🏆 Optimizer Best Genome" section with metrics and one-click Apply button at the top; hardcoded data relabelled "📊 Baseline Analysis (Static)". Committed in [current commit].
**Rule:** Whenever adding a new optimizer CLI flag, update both `optimizer.py` argparse AND the subprocess `cmd` list in `tab_optimizer()` simultaneously.

### Deribit historical IV returns intra-day data — optimizer produces 0 trades
**What happened:** `get_historical_volatility` returns ~384 hourly records but only covering ~17 unique calendar days. `run_with_data()` checked `len(iv_history) >= 60` on the raw list (passes at 384), but after `drop_duplicates("date")` only 17 daily rows remained. The rolling IV-rank window requires `min_periods=30`, so ALL rows became NaN and were dropped — empty dataset, 0 trades, 0 fitness for every backtest. The optimizer sweep showed flat fitness=0 for all parameter values.
**Fix in place:** In `run_with_data()`, after deduplicating to daily, check `len(_daily) >= 60` before accepting Deribit IV; otherwise fall back to `_synthesise_iv()` (Garman-Klass realised vol × 1.25). `_build_dataset()` already had this check correctly. Committed 6dade83.
**Rule:** Never trust `len(iv_history)` on raw Deribit historical vol data — always check the daily deduplicated count. If the Deribit endpoint only covers < 60 daily rows, synthesise IV from price data instead.

### Correct Python for running optimizer scripts
**What happened:** `do shell script "python3 optimizer.py ..."` in osascript fails silently or doesn't have numpy/pandas installed (macOS ships Python 3 without pip packages). Streamlit runs on Homebrew Python 3.11.
**Fix:** Use `/usr/local/bin/python3.11` — this is the Python that has all project dependencies installed. Never use `/usr/local/bin/python3` (symlink may not exist) or `/usr/bin/python3` (system Python, no packages).
```applescript
do shell script "cd ~/Documents/btc-wheel-bot && /usr/local/bin/python3.11 optimizer.py --mode sweep"
```

### Streamlit dataframe heatmap colouring in dark theme
**What happened:** `st.dataframe()` on a DataFrame with numeric columns applies an orange/pink gradient (heatmap) to cells. In the dark theme this makes tables almost unreadable — the genome parameter table and leaderboard looked like solid orange blocks with no visible text.
**Fix:** Convert numeric columns to formatted strings before passing to `st.dataframe()`. This prevents Streamlit's auto-gradient and keeps the dark theme table readable:
```python
best_df = pd.DataFrame(
    [(k, str(round(v, 6)) if isinstance(v, float) else str(v))
     for k, v in best.items()],
    columns=["Parameter", "Optimal Value"]
)
```
For leaderboard-style tables with many columns, format each metric column explicitly and use `str` types.
**Rule:** Any `st.dataframe()` call showing numeric results in dark-theme context — convert to formatted strings first. The heatmap is Streamlit's default for float columns and has no "disable" flag.

### Duplicate keyword argument in Plotly `go.Scatter()`
**What happened:** `SyntaxError: keyword argument repeated: hovertemplate` crashed the dashboard. Occurred when the Plotly trace builder had two `hovertemplate=` assignments — one from an earlier draft (with a Python list comprehension inside the string) and a corrected one with `%{customdata[0]}` references.
**Rule:** After any refactor of a `go.Scatter()` or `go.Bar()` trace, grep for duplicate keyword arguments before committing. Python raises `SyntaxError` at import time, which kills the entire Streamlit app.

### Dashboard restart path issues
**When restarting the dashboard via osascript:**
```applescript
tell application "Terminal"
    do script "cd ~/Documents/btc-wheel-bot && streamlit run dashboard_ui.py --server.port 8501 --server.headless true"
end tell
```
Do NOT use `/Users/smcnichol` — the actual home is `/usr/local/bin/streamlit` and home dir is `/Users/openclaw`. Use `~/` in shell commands.

---

## Pre-commit checklist

Before committing ANY change to this repo, verify:

**If you changed bot.py:**
- [ ] Did you change the heartbeat schema? → Update the dashboard reader in `tab_paper()`
- [ ] Did you change trades.csv fields? → Update `fieldnames`, `show` list in dashboard, `read_trades()`
- [ ] Did you change `_close_position()`? → Verify it still returns `bool` and CSV is only written on confirmed close
- [ ] Did you add Position fields? → Check `_open_position()` populates them; check `_print_status()` includes them if needed
- [ ] Does every call to `_close_position()` check the return value before removing the position?

**If you changed risk_manager.py:**
- [ ] Did you add/remove Position fields? → Cascade check (see cross-file deps above)

**If you changed dashboard_ui.py:**
- [ ] Are the tab labels and function calls in matching order in `main()`? (tab4=Recommendations, tab5=Config)
- [ ] If you touched the Recent Trades section, does the `show` column list match the actual CSV fields?
- [ ] Did any metric card calculation use a heartbeat key that might be missing for flat (no position) state?
- [ ] Did you add any new widgets (button, selectbox, slider, radio, text_input)? → Give each a unique `key=` string. Missing keys cause silent failures when surrounding widgets change.

**If you changed config.yaml:**
- [ ] Did you add a new key? → Check `cfg.section.key` references don't crash with AttributeError on old configs

**Always:**
- [ ] Commit and push (never leave work uncommitted)
- [ ] Reload the dashboard after code changes (Streamlit hot-reloads the Python but the browser needs a manual refresh or page reload)
- [ ] Test in testnet before considering any live deployment

---

## Paper vs Testnet vs Live modes

```python
# bot.py
self._paper = (mode == "paper")          # No real API calls, simulated fills
cfg.deribit.testnet = True               # Real API calls to testnet exchange
cfg.deribit.testnet = False              # REAL MONEY — live Deribit
```

The dashboard shows the mode from the heartbeat. "testnet mode" in the dashboard = real API calls to testnet, NOT paper.

---

## Key file locations (runtime)

```
~/Documents/btc-wheel-bot/
├── bot.py               Main trading loop
├── dashboard_ui.py      Streamlit dashboard
├── risk_manager.py      Position dataclass + RiskManager
├── config.py            Config singleton (cfg)
├── config.yaml          Live config values
├── main.py              Entry point
├── backtester.py        Historical simulation
├── optimizer.py         Parameter sweep / genetic optimizer
├── preflight.py         Connection health checks
├── KILL_SWITCH          (create this file to halt bot immediately)
├── bot_heartbeat.json   (written every tick by bot.py)
├── data/
│   ├── trades.csv       (appended on every closed trade)
│   ├── tick_log.csv     (appended every tick)
│   └── experience.jsonl (appended on every closed trade for adaptive learning)
└── logs/
    └── *.log            (loguru rotating logs)
```

---

## Adaptive Learning Architecture

The system accumulates real trading experience and feeds it back into the optimizer over time.

### Data flow
```
bot.py → _close_position() → data/experience.jsonl (one JSON line per trade)
                                    ↓
optimizer.py → load_experience_calibration() → blends with backtest fitness
                                    ↓
dashboard_ui.py → tab_recommendations() → shows backtest vs reality comparison
```

### experience.jsonl schema
Each line is a JSON object:
- `timestamp`: Unix epoch when trade closed
- `mode`: "paper" | "testnet" | "live"
- `params`: full ParamSet values active when trade was opened
- `conditions_at_open`: iv_rank, btc_price, option_type, strike, dte_at_entry
- `outcome`: pnl_usd, pnl_pct, hold_days, reason, win (bool)

### Calibration blending
At < 5 trades: no calibration (pure backtest)
At 5-9 trades: 80% backtest / 20% experience
At 10-19 trades: 60% / 40%
At 20-29 trades: 50/50
At 30+ trades: 30% backtest / 70% experience (experience dominates)

### Cross-file dependency
- `bot.py` writes experience.jsonl — wrapped in try/except, NEVER interrupts trade close
- `optimizer.py` reads it via `load_experience_calibration()` in `_run_parallel()`
- `dashboard_ui.py` reads it directly in `tab_recommendations()` and imports `summarise_experience` from optimizer.py
- Use `--no-experience` flag on optimizer CLI to bypass calibration for pure backtest mode

---

## Optimizer Modes (optimizer.py)

Five CLI modes:

```
python optimizer.py --mode sweep                         # sensitivity sweep (all params)
python optimizer.py --mode sweep --param iv_rank_threshold
python optimizer.py --mode evolve --population 20 --generations 8
python optimizer.py --mode walk_forward                  # requires best_genome.yaml
python optimizer.py --mode monte_carlo --simulations 200
python optimizer.py --mode reconcile                     # requires paper_trades.json
```

### sweep / evolve
Sweep varies one parameter at a time; evolve runs a genetic algorithm over all parameters.
Output: `data/optimizer/sweep_results.json`, `data/optimizer/best_genome.yaml`.

### walk_forward
Splits the full available history 75% / 25%. Runs `best_genome.yaml` on both halves and
also runs a default baseline. Computes a **robustness score** = out-of-sample fitness /
in-sample fitness. ≥ 0.70 = robust, 0.40–0.70 = marginal, < 0.40 = likely overfit.
Output: `data/optimizer/walk_forward_results.json`.
Dashboard: shown at the bottom of the Optimizer tab as a persistent section.

### monte_carlo
Runs N simulations (default 200) each starting from a different random date in the first
50% of available history. Tests strategy robustness across regimes.
Verdict: p5 Sharpe > 0.5 = robust, 0–0.5 = marginal, < 0 = fails under stress.
Output: `data/optimizer/monte_carlo_results.json`.
Dashboard: shown at the bottom of the Optimizer tab with return + Sharpe histograms.

### reconcile
Compares backtester Black-Scholes premium predictions against actual Deribit prices from
paper trading. Detects systematic IV model bias. Requires `data/paper_trades/paper_trades.json`
with at least 3 closed trades.
Output: `data/optimizer/reconcile_results.json`.
Dashboard: shown at the bottom of the Optimizer tab as "Backtest Accuracy" section with
traffic-light banner, 4 metric cards, scatter plot (predicted vs actual P&L), and detail table.

**Accuracy thresholds:** RMSE < $50 and |bias| < $30 = good; RMSE < $150 or |bias| < $100 = moderate; else = poor.

---

## paper_trades log format (data/paper_trades/paper_trades.json)

Written by `bot.py → _close_position()` in paper mode only. JSON array, one object per trade.
Each object has:
```
entry_date:          ISO 8601 timestamp when the trade opened
expiry_date:         ISO 8601 expiry timestamp (null if expiry_ts was 0)
strike:              strike price (USD)
contracts:           number of contracts
premium_collected:   actual USD premium received at open
                     = entry_price_btc * contracts * spot_at_entry
pnl_usd:             realized P&L in USD
pnl_pct:             P&L as fraction of collateral (strike * contracts)
outcome:             "expired_worthless" | "assigned" | "closed_early"
spot_at_entry:       BTC spot price when opened
spot_at_expiry:      BTC spot price when closed (may be close price for early closes)
iv_at_entry:         Actual Deribit IV % (mark_iv from signal) at time of open
```

**Cross-file dependency:** `risk_manager.py → Position.iv_at_entry` must be populated in
`bot.py → _open_position()` via `iv_at_entry=float(getattr(signal, "mark_iv", 0.0))`.
Without this, reconcile has no IV for BS pricing and skips those trades.

---

## ParamSet fields (optimizer.py)

All 13 parameters, with their sweep ranges:

| Parameter               | Default  | Sweep range      | Notes                              |
|-------------------------|----------|------------------|------------------------------------|
| iv_rank_threshold       | 0.50     | 0.20–0.80 ×0.05  |                                    |
| target_delta_min        | 0.15     | 0.10–0.25 ×0.025 |                                    |
| target_delta_max        | 0.30     | 0.20–0.45 ×0.025 |                                    |
| approx_otm_offset       | 0.08     | 0.03–0.18 ×0.01  |                                    |
| max_dte                 | 35       | 7–45 ×7          |                                    |
| min_dte                 | 5        | 2–14 ×1          |                                    |
| max_equity_per_leg      | 0.05     | 0.02–0.12 ×0.01  |                                    |
| premium_fraction_of_spot| 0.015    | 0.008–0.030×0.002|                                    |
| iv_rank_window_days     | 365      | 90–365 ×30       |                                    |
| min_free_equity_fraction| 0.25     | 0.00–0.40 ×0.05  |                                    |
| starting_equity         | 10000    | 1000–100000×5000 |                                    |
| use_regime_filter       | 0        | 0 or 1           | 0=off, 1=skip puts below MA        |
| regime_ma_days          | 50       | 20–100 ×10       | MA window for regime filter        |

Config.yaml keys: `strategy.use_regime_filter`, `strategy.regime_ma_days`.

---

## Drawdown calculation (backtester.py — fixed 2026-04)

**Bug found and fixed**: `_simulate()` previously only updated `equity` when a position
closed (expiry or roll breach). While a short put was open, the equity_curve was flat —
unrealized MTM losses from a BTC crash were invisible. Result: max_drawdown was severely
understated (e.g. −2% when the true peak-to-trough was −20%+).

**Fix**: Track `_mtm_unreal` = unrealized P&L from the open leg each day (in the roll-check
`else` branch). Append `equity + _mtm_unreal` (mark-to-market equity) to equity_curve every
iteration. Peak/drawdown guard also uses MTM equity.

The `_metrics()` calculation was already correct (running peak-to-trough on the curve); the
bug was in the curve values fed to it.

---

## Mobile App (api.py + mobile-app/)

The bot can be remotely controlled from an iPhone via a FastAPI REST server exposed through
a Cloudflare Tunnel.

### Architecture
```
iPhone (PWA)  ←──HTTPS──►  Cloudflare Tunnel  ←──►  api.py :8765  ←──►  data/*.json
                                                                          bot.py writes
```

### Starting everything
```bash
# Terminal 1: start the bot + API together
bash scripts/start_bot_with_api.sh          # paper mode
bash scripts/start_bot_with_api.sh --live   # live mode (real money!)

# Terminal 2: expose API to the internet
bash scripts/start_tunnel.sh
# → prints:  https://XXXXXX.trycloudflare.com
```

### First-launch setup on iPhone
1. Open `https://banksiasprings.github.io/btc-wheel-bot/` in Safari
2. Safari → Share → "Add to Home Screen" to install as PWA
3. On first open, paste the tunnel URL and WHEEL_API_KEY from `.env`
4. Tap "Test & Save" — must get ✅ before the app unlocks

### API server (api.py)
- Port: 8765 (override with WHEEL_API_PORT env var)
- Auth: `X-API-Key: <WHEEL_API_KEY>` header required on every call
- Auto-generates WHEEL_API_KEY in `.env` on first run
- CORS: allow all origins (required for PWA)
- Start manually: `/usr/local/bin/python3.11 -m uvicorn api:app --host 0.0.0.0 --port 8765`

### State files written by bot.py (read by api.py)
```
data/bot_state.json         {running, mode, started_at, last_heartbeat, uptime_seconds}
data/current_position.json  {open:false} or {open:true, name, strike, delta, dte, ...}
data/equity_curve.json      [{date, equity}, ...]  — appended on each trade close
data/bot_commands.json      written by API, read+deleted by bot._process_commands() each tick
data/optimizer_pid.txt      PID of running optimizer subprocess
```

### Command flow (api.py → bot.py)
The API writes a single-record JSON to `data/bot_commands.json`. The bot reads and deletes it
each tick (~60s latency). Supported commands: `start`, `stop`, `close_position`, `set_mode`.
The file is cleared immediately after reading to prevent replay.

### PWA deployment
- GitHub Actions: `.github/workflows/deploy-mobile.yml`
- Trigger: push to main when `mobile-app/**` changes
- Deploys `mobile-app/dist` → `gh-pages` branch
- Public URL: `https://banksiasprings.github.io/btc-wheel-bot/`

### Key cross-file dependency
If you add a new API endpoint in `api.py` that reads a new state file, the bot must write
that file — add the write in `bot.py → _print_status()` or `_close_position()` wrapped in
try/except. Never let a file write interrupt the trading loop.
