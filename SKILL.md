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
│   └── tick_log.csv     (appended every tick)
└── logs/
    └── *.log            (loguru rotating logs)
```
