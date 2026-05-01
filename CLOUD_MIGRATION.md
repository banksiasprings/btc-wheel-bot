# Cloud Migration Plan

When you move from "iMac at home with caffeinate" to "always-on cloud
infrastructure," several things have to change at the same time. This doc
captures the order, the minimum bar to clear before flipping each switch,
and the gotchas the audit surfaced.

## Today's setup

- **Bot host:** the iMac (`/Users/openclaw/Documents/btc-wheel-bot`),
  paper mode, started by `nohup python3.11 main.py --mode=paper`.
- **Validation agents:** three Anthropic-cloud routines that connect back
  to the iMac via the `bridge` environment
  (`env_01QrSZ2ZWgHH95juNmk3BE94`, "Opens-iMac:mcnichol-invoices:3da3").
  - One-off May 31 validator: [trig_0166anmoxiuAPgxVoHBcTtCs](https://claude.ai/code/routines/trig_0166anmoxiuAPgxVoHBcTtCs)
  - Weekly Sunday creator: [trig_01D91zMqVVxvatAoCaXgDy6p](https://claude.ai/code/routines/trig_01D91zMqVVxvatAoCaXgDy6p)
  - Weekly Sunday validator: [trig_0196eX5aRfCXUTRXfFBTiz4D](https://claude.ai/code/routines/trig_0196eX5aRfCXUTRXfFBTiz4D)
- **Why bridge for now:** snapshot files (`data/forecasts/*.json`) and
  trade history (`data/trades.csv`) live on local disk. `data/` is
  gitignored — the cloud env can't see them.

## Pre-migration gates

Don't migrate until ALL of these are true. Each one is a distinct failure
mode the audit caught.

1. **The bot has been continuously running for at least 30 days.** Heartbeat
   < 5 min stale at all times. The first weekly forecast (Sunday May 3) and
   its validation (Sunday May 31) have both completed and produced a
   meaningful comparison.
2. **At least one validated snapshot has `overall_status: pass`** with
   ≥ 5 trades in the window. Zero-trade dormancy doesn't tell you the
   strategy works — it tells you IV rank stayed below threshold. You need
   real fills.
3. **Pre-flight against testnet passes** with `Read + Trade access
   confirmed` (the new check requires `trade:read_write` in the OAuth
   scope). The audit fix in `preflight.py` will block live launch otherwise.
4. **A full open → close cycle has fired on testnet without any
   "Order placement failed" entries** in `logs/bot.log`. Search:
   `grep -c "Order placement failed" logs/bot.log` should be zero across
   the most recent run window.
5. **The `data/experience.jsonl` file has at least 10 entries** so the
   adaptive calibration can blend real outcomes with backtest fitness.
   Below 5 trades the optimizer is purely backtest-driven.

If you can't tick every box, the move to live is premature.

## Migration step-by-step

### A. Move the bot host to a cloud VPS

The bot needs an always-on Linux host with:
- Python 3.11+, the deps in `requirements.txt`
- Outbound HTTPS to `*.deribit.com`
- Persistent disk (snapshots, trades.csv, experience.jsonl)
- ~512 MB RAM, ~1 GB disk is plenty

A small DigitalOcean / Hetzner / Fly.io machine works. **Don't** put the
bot in a serverless function — it needs to maintain a long-lived
WebSocket to Deribit.

Set up:
```bash
git clone https://github.com/banksiasprings/btc-wheel-bot.git /opt/wheel-bot
cd /opt/wheel-bot
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Copy .env from your local machine — never commit it
scp ~/Documents/btc-wheel-bot/.env user@vps:/opt/wheel-bot/.env

# systemd unit so the bot restarts on crash + reboot
sudo cp scripts/wheel-bot.service /etc/systemd/system/
sudo systemctl enable --now wheel-bot
```

(The systemd unit doesn't exist yet — write one before migration. It runs
`python3.11 main.py --mode=testnet` first, then `--mode=live` after the
testnet soak.)

### B. Make the data files cloud-reachable

The validation agents need to read `data/forecasts/*.json` and
`data/trades.csv`. Three options, in increasing complexity:

**Option 1 — Commit data to a private repo (simplest).**
Remove `data/` from `.gitignore` for the forecasts and trades subset, OR
create a separate private repo (`btc-wheel-bot-data`) that the bot pushes
to on each tick. The validation agents check out that repo instead of
the local path.

Pros: trivial. Cons: every tick is a git commit, push history bloats fast.

**Option 2 — Run a tiny read-only HTTP service on the bot host.**
Expose `data/forecasts/` and `data/trades.csv` over HTTPS with basic auth.
The validation agent fetches via `curl`. Same agent prompts, just swap
`cd ~/Documents/btc-wheel-bot` for `curl https://wheel-bot.yourdomain/data/...`.

Pros: minimal infrastructure, decoupled from git. Cons: write your own
auth, manage TLS.

**Option 3 — Sync to S3/R2/GCS on every snapshot/trade.**
Bot writes locally AND to a cloud bucket. Validation agent reads from the
bucket.

Pros: scales, audit trail. Cons: most setup.

**Recommended: Option 2.** Add a `serve_data.py` script (FastAPI or
similar) that serves `data/forecasts/` and `data/trades.csv` read-only on
a path like `https://your-vps/data/forecasts/forecast_<id>.json`. The
validation agent's prompt becomes:
```
1. curl -fsS https://your-vps/data/forecasts/list | jq ...
2. python3 forecast_validator.py validate (running on the same VPS via SSH)
```

Or simpler: run the validation agents ON the same VPS (no need for
remote agents at all). A daily cron + a shell script writing to a Slack
webhook is sufficient. The Anthropic-cloud agents are only useful while
the bot is on a machine you can't easily SSH into (i.e., this iMac).

### C. Switch each routine's environment_id

The three scheduled routines currently use the bridge env. Once the bot
is on a VPS, you have two paths:

**Path 1 — Decommission the cloud routines, run cron on the VPS.**
Add to the VPS:
```cron
# Sunday 06:00 UTC: capture weekly snapshot
0 6 * * 0  cd /opt/wheel-bot && /opt/wheel-bot/.venv/bin/python forecast_validator.py create --horizon-days 30 --starting-equity $(jq .equity_usd bot_heartbeat.json) --note "weekly auto-snapshot" 2>&1 | logger -t wheelbot

# Sunday 06:30 UTC: validate due
30 6 * * 0  cd /opt/wheel-bot && /opt/wheel-bot/.venv/bin/python forecast_validator.py validate 2>&1 | logger -t wheelbot
```
Pros: simpler, no cloud round trips. Cons: no LLM-generated diagnosis.

**Path 2 — Keep the cloud routines, swap env to `anthropic_cloud`.**
Update each routine via `RemoteTrigger` action `update`:
```json
{
  "trigger_id": "trig_...",
  "body": {
    "job_config": {
      "ccr": {
        "environment_id": "env_01Ua4eCH5DUmKsm8LGYuaFzk"
      }
    }
  }
}
```
Then update the prompts to fetch data from the VPS (curl + auth) instead
of `cd ~/Documents/btc-wheel-bot`.

Pros: keep the LLM diagnosis. Cons: more moving parts (auth, TLS, DNS).

**Recommended: Path 1.** The bot host is small enough that running cron
+ a Slack webhook handler is less code than maintaining the bridge.
Promote the cloud LLM diagnosis only if a routine fails — wrap the cron
job to call a one-off Anthropic-cloud agent only on FAIL exit codes.

### D. Flip live trading

Edit `config.yaml`:
```yaml
deribit:
  testnet: false        # ← was true
sizing:
  starting_equity:      # whatever your real Deribit equity is
```

Verify the API key on `https://www.deribit.com/account/BTC/api` has
`trade:read_write` (mainnet, not testnet — these are separate keys).

Run:
```bash
python main.py --preflight     # must show "Read + Trade access confirmed"
python main.py --mode=live     # prompts "YES I UNDERSTAND" — type it
```

Watch the first 24 hours. Expected:
- Heartbeat updates every 60s
- `trades.csv` accumulates real fills
- `experience.jsonl` populates so the optimizer's calibration kicks in
- Telegram alerts fire on opens/closes/risk transitions
- No `Order placement failed` entries

If anything diverges from the validated paper-mode behaviour, pull the
kill switch (`echo STOP > KILL_SWITCH`) and investigate before letting
the next leg fire.

## Post-migration validation

The same forecast-vs-actual loop continues to be your truth signal:

- The Sunday create/validate routines keep producing rolling 30-day
  snapshots.
- A FAIL on a live snapshot means the backtest is mispricing real
  fills. Likely culprits in this order:
  1. Black-Scholes vs real Deribit chain divergence (yield gap > 30%)
  2. Slippage on entry/close worse than zero (cite `slippage_btc` in
     trades.csv)
  3. Real fills happening at different times than the daily-bar
     backtest assumes (intraday IV moves)
  4. The wheel cycle alternation working differently than simulated
     (real settlement events vs `_check_expired_positions`)

Each of these has a fix path. None of them is "trust the backtest more"
— they're all "make the backtest more honest."

## Open work before going live

Tracked here so it doesn't get lost:

- [ ] Write `scripts/wheel-bot.service` systemd unit
- [ ] Write `scripts/serve_data.py` (FastAPI) for Option 2 data access
- [ ] Decide between Path 1 (VPS cron) and Path 2 (cloud LLM)
- [ ] Move `.env` secrets to the VPS (never commit)
- [ ] Verify mainnet API key has `trade:read_write` scope
- [ ] Set up a Slack/Telegram webhook for cron job output
- [ ] Take a fresh forecast snapshot on the VPS before switching to live
      so post-cutover validation has a clean baseline
