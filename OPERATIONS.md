# Operations Runbook

How to run, monitor, and recover this bot. Written 2026-05-01 after the
audit + the autonomous overnight session that hardened the capital-efficiency
plumbing.

## TL;DR mental model

Three independent processes, file-based IPC:

```
main.py / bot.py          ──► bot_heartbeat.json   ──► dashboard_ui.py
(the trading loop)        ──► data/trades.csv      ──► api.py (for mobile app)
                          ──► data/experience.jsonl
                          ──► data/forecasts/      ──► forecast_validator.py
                                                       (validates monthly)

api.py                    ──► data/bot_commands.json ──► bot.py reads + acts
(FastAPI on :8765)        ──► data/bot_state.json (UI status)

bot_farm.py               ──► farm/<bot>/ subprocesses
(parallel paper bots)
```

Kill switch is a file: `KILL_SWITCH` at the bot root. Its presence halts trading
within ≤ 60 s. Delete it to resume.

---

## Daily checks (≤ 60 s)

```bash
cd ~/Documents/btc-wheel-bot

# 1. Bot alive?
ps -p $(cat data/bot_pid.txt) -o pid,etime,comm

# 2. Heartbeat fresh? (should be < 2 min old)
python3.11 -c "
import json, time
hb = json.load(open('bot_heartbeat.json'))
age = time.time() - hb['timestamp']
print(f'mode={hb[\"mode\"]} equity=\${hb[\"equity_usd\"]:,.0f} btc=\${hb[\"btc_price\"]:,.0f} iv={hb[\"iv_rank\"]*100:.1f}% age={age:.0f}s')
"

# 3. Any order failures?
grep -c "Order placement failed" logs/bot.log

# 4. Tests still green?
/usr/local/bin/python3.11 -m pytest tests/ -q | tail -1
```

If heartbeat age > 5 min, the bot has stalled — check `logs/btc-wheel-bot.log`.

---

## Starting the bot (paper mode, on the iMac)

```bash
cd ~/Documents/btc-wheel-bot
rm -f bot_heartbeat.json data/bot_pid.txt
nohup /usr/local/bin/python3.11 main.py --mode=paper > logs/paper-mode.log 2>&1 &
echo $! > data/bot_pid.txt
disown
```

`disown` is critical — without it the process dies when the terminal closes.

`caffeinate -d` should be running in another terminal so the iMac doesn't
sleep. `caffeinate -d -i &` runs it in the background.

To start under launchd (survives reboot), add a `~/Library/LaunchAgents/com.user.wheelbot.plist`. Not currently set up; see `CLOUD_MIGRATION.md` for the
move to systemd on a VPS.

---

## Stopping the bot

**Graceful:**
```bash
echo "manual stop $(date -u +%FT%TZ)" > KILL_SWITCH
# Wait for the bot to write `running: false` to data/bot_state.json (≤ 90s)
# Then optionally kill the process:
kill $(cat data/bot_pid.txt)
```

**Hard (only if KILL_SWITCH is ignored):**
```bash
kill -9 $(cat data/bot_pid.txt)
```

**Resume:**
```bash
rm KILL_SWITCH
# If the bot process was killed, restart per "Starting the bot" above.
```

---

## Bot farm (running 15+ paper bots in parallel)

The farm runs every config in `configs/` whose `_meta.status == "paper"`
as its own subprocess, with isolated state under `farm/<slug>/`. As of
2026-05-02 the fleet is 15 bots — the main config + 14 thesis variants.

### Start the farm

```bash
cd ~/Documents/btc-wheel-bot
nohup /usr/local/bin/python3.11 bot_farm.py > logs/farm.log 2>&1 &
disown
echo $! > /tmp/farm.pid
```

The supervisor discovers paper configs every 60 s and spawns or stops
subprocesses as configs are added/removed/changed.

### Check farm health

```bash
# Supervisor + every bot subprocess
ps -ef | grep -E "bot_farm|main\.py --mode=paper" | grep -v grep

# Per-bot status (updated every 60 s by the supervisor)
cat farm/status.json | jq '.bots[] | "\(.id): trades=\(.metrics.num_trades) equity=$\(.metrics.current_equity)"'

# Per-bot heartbeat (each bot writes its own)
ls -la farm/*/bot_heartbeat.json | head -20
```

### Stop the farm

```bash
# Graceful — supervisor catches SIGTERM and stops each bot
kill $(cat /tmp/farm.pid)

# Per-bot kill switch (stop just one bot)
echo "stop $(date)" > farm/<slug>/KILL_SWITCH
```

### Adding a new test bot

```bash
# Edit / add a config in configs/<name>.yaml with _meta.status: paper
# The farm picks it up within 60 s automatically.
```

### Removing a test bot

Set `_meta.status` to anything other than "paper" (e.g. `archived`,
`draft`). The supervisor stops the subprocess on the next discovery tick.

### Forecast validation across the fleet

```bash
# Create a 30-day snapshot for every paper bot
python3.11 forecast_validator.py create --all-paper-bots \
    --horizon-days 30 --starting-equity 100000

# Validate any due snapshots across all paper bots
python3.11 forecast_validator.py validate --all-paper-bots

# List snapshots from every paper bot
python3.11 forecast_validator.py list --all-paper-bots
```

Each bot's snapshots live in `farm/<slug>/data/forecasts/`. The Sunday
cloud routine ([trig_0153UrVWvYz2yv58yjEQhndk](https://claude.ai/code/routines/trig_0153UrVWvYz2yv58yjEQhndk))
runs both create + validate weekly across the fleet.

---

## Forecast validation loop

The forecast-validator captures the backtest's predictions at a fixed time and
compares them to actual outcomes after the horizon elapses. This is the truth
signal — it surfaces gaps between simulated and real performance that no
single backtest can detect.

### Manual

```bash
# Capture a 30-day forecast based on current config + paper equity
python3.11 forecast_validator.py create \
  --horizon-days 30 \
  --starting-equity 100000 \
  --note "manual snapshot — context here"

# List all snapshots and their state
python3.11 forecast_validator.py list

# Validate any snapshot whose horizon has elapsed (idempotent)
python3.11 forecast_validator.py validate

# Show a specific snapshot in full
python3.11 forecast_validator.py show 20260501_113757
```

### Automated (already running)

Three Anthropic-cloud routines fire on schedule via the iMac bridge:

| Routine ID | Trigger | What it does |
|---|---|---|
| `trig_0166anmoxiuAPgxVoHBcTtCs` | One-off 2026-05-31 12:00 UTC | Validates the 2026-05-01 snapshot |
| `trig_01D91zMqVVxvatAoCaXgDy6p` | Cron `0 6 * * 0` (Sundays) | Creates a fresh 30-day snapshot |
| `trig_0196eX5aRfCXUTRXfFBTiz4D` | Cron `30 6 * * 0` (Sundays) | Validates any due snapshots and reports |
| `trig_01GyDwFJ7jGQViA9RuEvigv9` | One-off 2026-06-05 12:00 UTC | Month-1 review — READY/NOT-READY verdict |

Manage them at https://claude.ai/code/routines

The bridge environment requires the iMac to be on at the scheduled time. If
the iMac is asleep or offline, the routine retries within a small window then
fails. Check the routine page for results.

---

## Dashboard (Streamlit)

```bash
cd ~/Documents/btc-wheel-bot
/usr/local/bin/streamlit run dashboard_ui.py --server.port 8501 --server.headless true
```

Open `http://localhost:8501`. Tabs:

- **📊 Backtest** — run a single backtest with current config
- **📈 Paper Trading** — live status of the running bot
- **🧬 Optimizer** — parameter sweep + genetic evolve
- **📋 Recommendations** — applies optimizer winners to config
- **📊 Forecasts** — snapshot creation, validation, forecast-vs-actual
- **⚙️ Config** — edit config.yaml from the UI
- **🔧 Settings** — kill switch, log viewer, trades CSV management

The Forecasts tab is the dashboard side of `forecast_validator.py`. Snapshots
in `data/forecasts/` show up with badges (pending / due / pass / warning / fail)
and a forecast vs actual table once validated.

---

## Mobile app + REST API

```bash
cd ~/Documents/btc-wheel-bot
/usr/local/bin/python3.11 -m uvicorn api:app --host 0.0.0.0 --port 8765
```

The mobile PWA in `mobile-app/` connects to this API. Endpoints documented
inline in `api.py`. The Pipeline tab inside the PWA drives the optimizer
genetic evolution and validation steps.

API auth via `WHEEL_BOT_API_KEY` env var (auto-generated to
`~/Documents/btc-wheel-bot/.api_key` if unset).

---

## Pre-launch (testnet → live) gates

Five gates from `CLOUD_MIGRATION.md`. Don't go live until every box is ticked:

- [ ] **G1**: bot has run continuously for ≥ 30 days, heartbeat never > 6 h stale
- [ ] **G2**: ≥ 1 validated forecast snapshot with `overall_status: pass` AND ≥ 5 trades in the window
- [ ] **G3**: full open → close cycle on testnet, zero `Order placement failed` entries in the run window
- [ ] **G4**: `data/experience.jsonl` has ≥ 10 entries
- [ ] **G5**: across all validated snapshots, no single metric is FAIL in > 50%

The June 5 routine (`trig_01GyDwFJ7jGQViA9RuEvigv9`) checks all five and
gives a READY / NOT-READY verdict.

---

## Common failures

### "Order placement failed: Invalid params" looping in bot.log

The audit case. Two likely causes:

1. **API key has Read scope only.** Re-check the Deribit account at
   https://test.deribit.com/account/BTC/api (or `/account/BTC/api` on
   mainnet). Edit the key to grant `trade:read_write`. Re-run preflight:
   `python3.11 main.py --preflight --testnet` — must show
   `Read + Trade access confirmed` (post-audit fix).

2. **Stranded ITM expired position.** The bot now self-heals: a rejected
   close order whose instrument expired > 30 min ago is settled locally
   with the actual Deribit settlement price, the trade is recorded, and
   the position is removed from `_positions`. Look for
   `Stranded expired position … settled locally` in the log.

If the alert fires after 5 consecutive failures (`notify_order_failures`),
investigate before letting it spin further.

### Heartbeat goes stale

The bot is dead, hung, or the iMac is asleep.

1. Check `ps -p $(cat data/bot_pid.txt)`.
2. If alive but hung, check the most recent stack trace via `py-spy dump --pid $PID`.
3. If dead, restart per "Starting the bot" above.
4. If iMac asleep, restart `caffeinate -d`.

### Drawdown limit halts trading

The bot writes `KILL_SWITCH` and stops opening new legs but lets open
positions settle naturally. Review the equity curve, decide whether the
config is wrong or the market is wrong, then `rm KILL_SWITCH` to resume.

The Telegram alert (`notify_drawdown_warning`) fires when drawdown crosses
50% of the limit — early warning before the hard stop.

### Hedge state goes stale on restart

Symptom: the log shows `Stale hedge position detected on startup` and the
hedge state is reset.

This is intentional — if the bot was killed mid-session with an open hedge,
the next startup wouldn't know what option position the hedge corresponds
to. The reset clears the orphaned state. The downside is that the realised
P&L on that perp position is lost from the bot's accounting (it's still
correct on Deribit). For paper mode this is informational only. For live
mode you'd want to manually reconcile via the Deribit UI.

### Optimizer has no data

If `data/optimizer/` is empty (e.g. after Round 3 archived all pre-fix
artifacts, or after a fresh clone), the Pipeline UI's leaderboards will
show empty states. Run a sweep + evolve:

```bash
python3.11 optimizer.py sweep
python3.11 optimizer.py evolve --goal capital_roi --seed-from-sweep
```

The first sweep takes ~10 minutes; evolve another 20–60 minutes depending
on `--population` and `--generations`.

---

## Source-of-truth files

Things that should NEVER be edited by hand while the bot runs:

- `data/trades.csv` — bot writes one row per closed trade; manual edits
  break the experience-jsonl alignment that drives the optimizer
  calibration.
- `data/experience.jsonl` — same, append-only.
- `bot_heartbeat.json`, `data/bot_state.json`, `data/current_position.json`,
  `data/hedge_state.json` — bot writes every tick; manual edits will be
  overwritten silently.
- `data/forecasts/forecast_*.json` — the forecast_validator writes the
  `validation` block. Don't edit unless you're recovering from a buggy
  validate run.

Things that are safe to edit:

- `config.yaml` — takes effect on next bot restart. `iv_rank_threshold`,
  `target_delta_min/max`, `min_dte`/`max_dte`, `max_equity_per_leg` are
  the four levers that change strategy behaviour. The audit fixed the
  collateral math so changing these no longer triggers the 10× sizing
  bug.
- `KILL_SWITCH` — create to halt, delete to resume.
- Anything under `data/optimizer/_archive_*/` — archived for reference only.

---

## Lessons logged from the audit (2026-05-01)

These hit hardest because they were silent:

1. **Backtester sized 10× larger than live.** Every saved evolution genome
   pre-2026-05-01 is overfitted to that. The collateral fix in
   `backtester._size`, `risk_manager.check_collateral`,
   `risk_manager.check_free_margin`, and `ai_overseer.build_brief` corrected
   it; pinned by `tests/test_collateral_consistency.py`.

2. **Preflight passed read-only API keys.** Trade scope is now required;
   pinned by `tests/test_preflight_scope.py`.

3. **Stranded expired option looped forever.** Self-healing recovery now
   in `bot._close_position`; pinned by `tests/test_expired_position.py`.

4. **No periodic exchange reconciliation in live mode.** Now reconciles
   every ~hour; idempotent — safe to re-call.

5. **Hedge funding understated 3×.** Calibrated to `HEDGE_FUNDING_DAILY =
   0.0003` in `backtester.py`; pinned by
   `tests/test_hedge_cost_calibration.py`.

6. **Capital-efficiency metrics computed but hidden from UI.** Now surfaced
   in the Pipeline winner card and Forecasts tab. The `capital_roi` fitness
   was rewritten to actually reward low capital + low margin util; pinned
   by `tests/test_capital_roi_fitness.py`.

If anything in this list silently regresses, those tests will fail. Trust
the test suite.

---

## Where to find things

- Architecture overview: see the skill `.claude/skills/btc-wheel-bot/SKILL.md`
- Migration to live cloud: `CLOUD_MIGRATION.md`
- Audit findings + the night log: `NIGHT_LOG.md`
- Per-test rationale: every test file has a top docstring explaining what
  bug it pins

---

## Idle-bot diagnosis (2026-05-02)

Audit of the running farm: of the ~21 paper bots, only `chaos-hedged` and
`chaos-tester` had executed any trades. Everything else was sitting idle
with `num_trades = 0`. Root causes, in order of how many bots were affected:

1. **Low IV rank** — by far the dominant cause.  Current IV rank from the
   Deribit historical-volatility feed reads as **~0.0011** (≈ 0%). Almost
   every bot's `iv_rank_threshold` is 0.20 or higher, so the entry gate at
   `bot.py:773` and `strategy.py:360` rejects every signal.  Only the two
   `chaos-*` bots, which set `iv_rank_threshold: 0.0`, ever fire.

2. **Insufficient equity to size the minimum 0.1 BTC contract.**  Five bots
   were configured with `starting_equity: 1_000` USD:
   `safest-v1`, `capital-roi-v1`, `max-yield-v1`, `sharpe-v1`,
   `daily-trader-v1`.  At BTC ≈ $78 k and `max_equity_per_leg ≈ 0.08`, the
   max notional is only ~$80 — `risk_manager.check_position_size()`
   correctly refuses to open. **Fixed in this commit: equity raised to
   $100,000 in both `farm/<bot>/config.yaml` and `configs/<name>.yaml`.**
   (`roi-rest-1` and `bot_3` are also under-funded but were not in the
   user-named list; flag for follow-up.)

3. **DTE range** — every bot has a sane min/max DTE so this was never the
   blocker; documented for completeness.

4. **No bot was kill-switched or in an error state** during the audit.

**Surfaced in the mobile app:** every idle bot card now shows an amber
`ⓘ Why not trading?` chip, backed by `GET /farm/bot/{bot_id}/why_not_trading`,
which returns `{ ready, reason, checks }` with per-gate booleans (kill
switch, heartbeat, position-open, sizing, IV rank, DTE range).

**Note on equity changes:** running bots cache their config at startup, so
updating `starting_equity` only takes effect after a farm restart. Use
`POST /farm/stop` then `POST /farm/start` to reload.

---

## Go-Live Checklist

Do not deploy a bot to live with real capital until **every box** is
checked. Each item is a hard gate, not a suggestion.

- [ ] At least one bot has completed **10+ full trade cycles** (entry →
      expiry / assignment → re-entry) end-to-end in paper or testnet.
- [ ] Bot has survived at least one **±15 % BTC price move** without
      unexpected behaviour (no orphan positions, no incorrect P&L, no
      stuck cycle state).
- [ ] Per-bot readiness diagnostic shows green for the target bot
      (`GET /farm/bot/{bot_id}/why_not_trading` returns `ready: true` or
      `reason` is `"Already holding a position"` / `"Eligible — waiting…"`).
- [ ] **Every line of the P&L is understood** — no unexplained numbers in
      `data/trades.csv`. If you can't reconstruct a row by hand from
      entry/exit/contracts, do not go live.
- [ ] **Kill switch manually tested** — `touch KILL_SWITCH` confirmed to
      stop the bot within 60 seconds; bot resumed cleanly after the file
      was removed.
- [ ] **Assignment path tested** — confirmed bot behaviour when a put
      goes deep ITM through expiry: BTC delivered, equity moves to BTC,
      wheel transitions to call-mode, covered call opens on next cycle.
- [ ] **Starting live capital: $500 max on first deployment**, one bot
      only (`safest-v1`).
- [ ] **Scale up only after 10+ live trade cycles** behave as expected.
      If anything surprises you in the first 10 cycles, halt and
      diagnose before adding capital.
