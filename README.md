# BTC Wheel Bot

A modular Python bot for collecting premium via a Bitcoin options wheel strategy on Deribit. Sells OTM puts and calls in a repeating cycle to harvest theta/vega without a directional BTC bet.

> **Phase 1 complete:** Backtester is live. Paper/live modules are scaffolded.  
> **Phase 2:** Paper trading on Deribit testnet (4+ weeks required).  
> **Phase 3:** Live trading with small size after Phase 2 validation.

---

## Quick Start

### 1. Install dependencies

```bash
cd btc-wheel-bot
pip install -r requirements.txt
```

### 2. Run the backtest (Phase 1)

No credentials needed — uses Deribit public data only.

```bash
python main.py --mode backtest
```

This will:
- Download 12 months of BTC price + IV data from Deribit
- Simulate weekly wheel cycles
- Print results table to console
- Save `backtest_results.png` (equity curve)
- Save `data/backtest_trades.csv`

### 3. Run unit tests

```bash
pytest tests/ -v
```

---

## Configuration

All parameters live in `config.yaml`. No magic numbers in Python files.

Key settings:

| Setting | Default | Description |
|---|---|---|
| `strategy.iv_rank_threshold` | 0.50 | Only sell when IV rank > this |
| `strategy.target_delta_min/max` | 0.15–0.30 | Delta range for strike selection |
| `strategy.max_dte` | 35 | Maximum days to expiry |
| `sizing.max_equity_per_leg` | 0.05 | Max 5% of account per trade |
| `risk.max_daily_drawdown` | 0.10 | Pause if down 10% from peak |
| `backtest.lookback_months` | 12 | How far back to simulate |

---

## Paper Trading (Phase 2)

1. Get testnet API credentials from https://www.deribit.com/account/testnet/api
2. Copy `.env.example` → `.env` and fill in your keys
3. Ensure `DERIBIT_TESTNET=true` in `.env`
4. Run for minimum 4 weeks:

```bash
python main.py --mode paper
```

**Phase 2 pass criteria:**
- Win rate > 60%
- Max drawdown < 10%
- Sharpe ratio > 0.8 annualised

---

## Live Trading (Phase 3)

Only proceed after Phase 2 passes. Start with minimum size (0.01–0.05 BTC collateral).

```bash
# In .env:
DERIBIT_TESTNET=false
DERIBIT_API_KEY=your_mainnet_key
DERIBIT_API_SECRET=your_mainnet_secret
```

```bash
python main.py --mode live
```

You will be prompted to type `YES I UNDERSTAND` before the bot connects to mainnet.

---

## Emergency Kill Switch

Create a file named `KILL_SWITCH` in the bot's working directory:

```bash
touch KILL_SWITCH
```

The bot checks for this file every loop and halts all trading immediately if found. Delete the file to resume.

---

## Docker

### Run backtest in Docker

```bash
docker-compose --profile backtest up backtest
```

### Run paper/live in Docker

```bash
cp .env.example .env
# Fill in your API keys in .env
docker-compose up wheel-bot
```

---

## Adding ML Later

Strike selection in `strategy.py` includes `# ML_HOOK` stubs where a scikit-learn model can be plugged in:

```python
# In strategy.py → select_strike()
# ML_HOOK: Replace score calculation with ML model output:
from ml_model import StrikeSelector
selector = StrikeSelector.load("models/strike_selector.pkl")
candidates = selector.rank(candidates, market_features)
```

Suggested approach:
1. Export `data/backtest_trades.csv` as training data
2. Features: IV rank, DTE, delta, skew, term structure, day-of-week
3. Target: premium yield / win rate per cycle
4. Train with `sklearn.ensemble.GradientBoostingClassifier` or XGBoost
5. Load model in `strategy.py` behind the `# ML_HOOK` stub

---

## Project Structure

```
btc-wheel-bot/
├── main.py              # Entry point (--mode backtest|paper|live)
├── config.py            # Typed config loader
├── config.yaml          # All strategy parameters
├── deribit_client.py    # REST + WebSocket client
├── strategy.py          # Wheel logic, IV rank, strike selection
├── risk_manager.py      # Sizing, collateral, drawdown checks
├── backtester.py        # Phase 1: historical simulation
├── bot.py               # Phase 2/3: async trading loop
├── dashboard.py         # Console position display
├── tests/               # pytest unit tests
├── data/                # Trade CSVs (gitignored)
├── logs/                # Log files (gitignored)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Monitoring

```bash
# Tail live log
tail -f logs/bot.log

# Show dashboard
python main.py --mode dashboard

# Check kill switch status
ls -la KILL_SWITCH 2>/dev/null && echo "KILL SWITCH ACTIVE" || echo "Bot running"
```

---

## Disclaimer

This bot is for educational and research purposes. Options trading involves substantial risk of loss. Never trade with money you cannot afford to lose. Always validate thoroughly on testnet before using real funds.
