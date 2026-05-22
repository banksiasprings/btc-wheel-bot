# BTC RL Agent

Reinforcement learning agent for BTC options wheel strategy using PPO (Proximal Policy Optimisation).

## Files

| File | Purpose |
|------|---------|
| `env.py` | `BTCOptionsEnv` — Gymnasium-compatible environment. Simulates daily BTC options trading using Black-Scholes pricing. Generates synthetic GBM data if no CSV is provided. |
| `train.py` | PPO training script. Saves checkpoints to `checkpoints/` and TensorBoard logs to `logs/`. |
| `evaluate.py` | Evaluates a saved model on holdout data (last 30%). Prints a metrics report and returns exit code 0 (PASS) or 1 (FAIL). |
| `test_loop.sh` | End-to-end smoke test: installs deps, trains 50k steps, evaluates. |
| `requirements.txt` | Python dependencies. |

## How to run

### Quick smoke test (under 5 minutes)
```bash
cd rl_agent/
bash test_loop.sh
```

### Full training run (~2M steps, a few hours on CPU)
```bash
cd rl_agent/
python3 train.py --timesteps 2000000
```

### Evaluate a saved model
```bash
python3 evaluate.py --model checkpoints/final_model.zip
```

### TensorBoard logs
```bash
tensorboard --logdir rl_agent/logs/
```

## Environment details

**State space** (12 features, all normalised to ~[-1, 1]):
- BTC price, IV rank, current IV (realised vol proxy)
- Open position: type (none/put/call), delta, DTE, unrealised P&L
- Days since last trade
- Price momentum: 5-day and 20-day log returns
- 10-day realised volatility
- Days to next monthly expiry

**Actions** (Discrete 5):
- 0: Hold
- 1: Sell put at 0.20 delta (weekly, 7 DTE)
- 2: Sell put at 0.25 delta (weekly, 7 DTE)
- 3: Sell call at 0.20 delta (weekly, 7 DTE)
- 4: Close current position

**Reward function**:
```
reward = daily_pnl_fraction - 0.001 * max(0, drawdown - 0.05)^2 - 0.0001 * (1 if no_position else 0)
```

## Pass criteria (evaluate.py)

| Metric | Threshold |
|--------|-----------|
| Sharpe ratio | > 0.3 |
| Max drawdown | < 20% |

Note: 50k training steps (smoke test) will typically FAIL the quality thresholds — the model needs 500k+ steps to start showing meaningful performance. The test loop exits 0 regardless to confirm the pipeline runs end-to-end without errors.

## Data

If no CSV is provided, the environment generates 3 years of synthetic BTC price data using Geometric Brownian Motion (80% annualised vol, 15% drift, starting at $30k). IV rank is computed as the percentile of 10-day realised vol within a 252-day rolling window.

To use real data, pass `data_path` to `BTCOptionsEnv`:
```python
env = BTCOptionsEnv(data_path="/path/to/data.csv")
```
The CSV must have a `close` (or `price`) column.
