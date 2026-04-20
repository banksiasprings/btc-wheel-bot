# btc-wheel-bot

## Mobile App

Monitor and control the bot from your phone via a PWA that talks to the local FastAPI server through a Cloudflare Tunnel.

### Architecture

```
Phone → Cloudflare Tunnel → FastAPI (api.py :8765) → bot state files / config.yaml
```

### Setup (one-time)

**1. Start the API server** (on your Mac):
```bash
/usr/local/bin/python3.11 -m uvicorn api:app --host 0.0.0.0 --port 8765
```
On first run it auto-generates `WHEEL_API_KEY` in `.env` — note the value.

Or start both bot + API together:
```bash
./scripts/start_bot_with_api.sh --mode paper
```

**2. Start the Cloudflare Tunnel** (in a separate terminal):
```bash
./scripts/start_tunnel.sh
```
Copy the `https://xxx.trycloudflare.com` URL it prints.

**3. Open the PWA** on your phone:
```
https://banksiasprings.github.io/btc-wheel-bot
```
Enter the tunnel URL and API key on the setup screen. Tap "Add to Home Screen" to install as a PWA.

### API key

The key lives in `.env` as `WHEEL_API_KEY`. To show it:
```bash
grep WHEEL_API_KEY .env
```

---


A modular Python bot for a Bitcoin options **wheel-strategy** (premium-collection) on Deribit.

Sells ~0.20–0.30 delta OTM puts (or calls) to harvest theta/vega decay.  
No directional BTC bias.  Alternates put/call each cycle to stay roughly delta-neutral.

---

## Quick start

### 1. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the backtest

Downloads 12 months of real BTC price + IV data from Deribit (no API key needed):

```bash
python main.py --mode=backtest
```

Outputs:
- Summary table to stdout
- `backtest_results.png` — equity curve + drawdown chart
- `data/backtest_trades.csv` — per-trade log

### 3. Paper-trade (live data, no real orders)

```bash
cp .env.example .env      # no credentials needed for paper mode
python main.py --mode=paper
```

### 4. Live trading

```bash
# 1. Fill in .env with real Deribit API key/secret
# 2. Set deribit.testnet: false in config.yaml
python main.py --mode=live
```

---

## Configuration

All strategy parameters live in `config.yaml`.  
Secrets (API key/secret) come from `.env` or environment variables — never in YAML.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `strategy.iv_rank_threshold` | 0.50 | Only sell when IV rank > 50% |
| `strategy.target_delta_min` | 0.15 | Minimum delta for strike selection |
| `strategy.target_delta_max` | 0.30 | Maximum delta for strike selection |
| `strategy.expiry_preference` | weekly | Weekly or monthly expirations |
| `sizing.max_equity_per_leg` | 0.05 | Max 5% of equity per leg |
| `risk.max_adverse_delta` | 0.40 | Roll trigger: delta breach |
| `risk.max_loss_per_leg` | 0.02 | Roll trigger: 2% unrealised loss |
| `risk.max_daily_drawdown` | 0.10 | Pause trading at 10% drawdown |
| `backtest.starting_equity` | 10000 | Starting capital (USD) |
| `backtest.lookback_months` | 12 | Simulation horizon |

---

## Module overview

| Module | Role |
|--------|------|
| `config.py` | YAML + env loader, typed dataclasses |
| `deribit_client.py` | Public REST (backtest) + WebSocket scaffold (live) |
| `strategy.py` | IV rank, cycle decision, strike selection, `# ML_HOOK` stubs |
| `risk_manager.py` | Sizing, collateral check, roll triggers, drawdown guard |
| `backtester.py` | Historical simulation with Black-Scholes pricing |
| `bot.py` | Async 60s poll loop (paper/live) |
| `dashboard.py` | Rich/plain console status panel |
| `main.py` | CLI entry: `--mode=backtest|paper|live` |

---

## Emergency kill switch

Create a file named `KILL_SWITCH` in the project root to immediately halt all trading:

```bash
touch KILL_SWITCH   # halt
rm KILL_SWITCH      # resume
```

---

## Docker

```bash
# Build
docker compose build

# Backtest (one-shot)
docker compose --profile backtest up backtest

# Paper trading (continuous)
docker compose up -d btc-wheel-bot
docker compose logs -f btc-wheel-bot
```

---

## Running tests

```bash
pytest tests/ -v
```

---

## Adding ML models (future Phase 2)

The code contains `# ML_HOOK` comments at the three primary decision points:

1. **`strategy.py` → `calculate_iv_rank()`** — replace with a trained IV-regime classifier
2. **`strategy.py` → `decide_cycle()`** — replace with skew/trend predictor for put/call choice
3. **`strategy.py` → `select_strike()`** — replace with ML-ranked candidate scoring

Example hook replacement:

```python
# ML_HOOK: uncomment and train the model
# from ml_model import IVRankPredictor
# predictor = IVRankPredictor.load("models/iv_rank_model.pkl")
# return predictor.predict(iv_history, spot_price, term_structure)
```

---

## Monitoring

```bash
# Tail live log
tail -f logs/btc-wheel-bot.log

# Check current position
grep "OPEN\|EXPIRY\|ROLL" logs/btc-wheel-bot.log | tail -20

# Drawdown alert
grep "PAUSE\|drawdown" logs/btc-wheel-bot.log
```
