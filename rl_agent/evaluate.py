"""
evaluate.py — Evaluate a trained PPO model on the holdout (last 30%) data.

Usage:
    python evaluate.py --model checkpoints/final_model.zip
    python evaluate.py --model checkpoints/final_model.zip --render

Exit codes:
    0  — sharpe > 0.3 AND max_drawdown < 0.20 (PASS)
    1  — criteria not met (FAIL)
"""

import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env import BTCOptionsEnv, load_or_generate_data

DEFAULT_MODEL = str(Path(__file__).resolve().parent / "checkpoints" / "final_model.zip")

# Pass criteria
SHARPE_THRESHOLD = 0.3
MAX_DD_THRESHOLD = 0.20  # 20%


def evaluate(model_path: str, render: bool = False, data_path: str = None) -> dict:
    from stable_baselines3 import PPO

    print(f"[evaluate] Loading model: {model_path}")
    model = PPO.load(model_path)

    print("[evaluate] Loading test data (last 30% of history) ...")
    prices, iv_rank = load_or_generate_data(data_path=data_path)
    source = data_path if data_path else "synthetic GBM"
    print(f"[evaluate] Data source: {source} ({len(prices)} total days, using last 30%)")
    env = BTCOptionsEnv(prices=prices, iv_rank=iv_rank, split="test", seed=0)

    # Run a full deterministic episode (no random start in test mode)
    # Override: use the full test split from day 20 (skip warmup)
    env._day = 20
    env._equity = env.starting_equity
    env._peak_equity = env.starting_equity
    env._position = None
    env._days_since_trade = 0
    env._realised_pnl_total = 0.0

    obs = env._obs()
    daily_equity = [env._equity]
    trade_count = 0
    trade_pnls = []
    terminated = False
    truncated = False
    prev_equity = env._equity

    while not (terminated or truncated):
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(int(action))
        daily_equity.append(info["equity"])

        # Count trades
        if "trade" in info:
            trade_count += 1

        if render:
            env.render()

    # --- Compute metrics ---
    eq = np.array(daily_equity, dtype=np.float64)
    n = len(eq)

    total_return = (eq[-1] / eq[0]) - 1.0
    n_years = n / 365.0
    annualised_return = (1.0 + total_return) ** (1.0 / max(n_years, 1e-6)) - 1.0

    # Daily returns
    daily_rets = np.diff(eq) / eq[:-1]
    mean_ret = daily_rets.mean()
    std_ret = daily_rets.std()
    sharpe = (mean_ret * math.sqrt(252)) / (std_ret * math.sqrt(252) + 1e-9) if std_ret > 1e-10 else 0.0

    # Max drawdown
    peak = np.maximum.accumulate(eq)
    drawdowns = (peak - eq) / np.maximum(peak, 1.0)
    max_dd = drawdowns.max()

    # Win rate (approximation via positive daily returns when in position)
    positive_days = (daily_rets > 0).sum()
    win_rate = positive_days / max(len(daily_rets), 1)

    results = {
        "total_return": total_return,
        "annualised_return": annualised_return,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "n_days": n,
        "final_equity": eq[-1],
        "starting_equity": eq[0],
    }
    return results


def print_report(results: dict):
    print()
    print("=" * 55)
    print("  BTCOptionsEnv — Evaluation Report")
    print("=" * 55)
    print(f"  Days evaluated:      {results['n_days']}")
    print(f"  Starting equity:     ${results['starting_equity']:>12,.2f}")
    print(f"  Final equity:        ${results['final_equity']:>12,.2f}")
    print(f"  Total return:        {results['total_return']:>+.2%}")
    print(f"  Annualised return:   {results['annualised_return']:>+.2%}")
    print(f"  Max drawdown:        {results['max_drawdown']:.2%}")
    print(f"  Sharpe ratio:        {results['sharpe_ratio']:.3f}")
    print(f"  Trade count:         {results['trade_count']}")
    print(f"  Win rate (daily):    {results['win_rate']:.2%}")
    print("=" * 55)

    passed = results["sharpe_ratio"] > SHARPE_THRESHOLD and results["max_drawdown"] < MAX_DD_THRESHOLD
    if passed:
        print(f"  VERDICT: PASS  (sharpe > {SHARPE_THRESHOLD} AND max_dd < {MAX_DD_THRESHOLD:.0%})")
    else:
        reasons = []
        if results["sharpe_ratio"] <= SHARPE_THRESHOLD:
            reasons.append(f"sharpe {results['sharpe_ratio']:.3f} <= {SHARPE_THRESHOLD}")
        if results["max_drawdown"] >= MAX_DD_THRESHOLD:
            reasons.append(f"max_dd {results['max_drawdown']:.2%} >= {MAX_DD_THRESHOLD:.0%}")
        print(f"  VERDICT: FAIL  ({'; '.join(reasons)})")
    print("=" * 55)
    print()
    return passed


def main():
    parser = argparse.ArgumentParser(description="Evaluate trained model on holdout data")
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help="Path to saved model .zip (default: checkpoints/final_model.zip)",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Print environment state each step",
    )
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="Path to real data CSV (same as used for training). If omitted uses synthetic data.",
    )
    args = parser.parse_args()

    if not Path(args.model).exists():
        print(f"[evaluate] ERROR: model not found at {args.model}")
        sys.exit(1)

    results = evaluate(model_path=args.model, render=args.render, data_path=args.data)
    passed = print_report(results)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
