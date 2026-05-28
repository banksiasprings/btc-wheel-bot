#!/usr/bin/env python3.11
"""
rl_agent/roi_report.py — Plain-English performance report for the RL Agent V1 bot.

Usage:
    python3.11 rl_agent/roi_report.py

Reads:
    farm/rl-agent-v1/data/trades.csv    (live paper trades — preferred)
    rl_agent/data/btc_daily.csv         (holdout backtest fallback)

If no real trades yet, runs the PPO model on the holdout split of the
training data and shows projections.
"""

from __future__ import annotations

import csv
import math
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent          # btc-wheel-bot root
FARM_BOT_DIR = BASE_DIR / "farm" / "rl-agent-v1"
TRADES_CSV   = FARM_BOT_DIR / "data" / "trades.csv"
MODEL_PATH   = BASE_DIR / "rl_agent" / "checkpoints" / "best_model.zip"
DATA_PATH    = BASE_DIR / "rl_agent" / "data" / "btc_daily.csv"
STARTING_EQUITY = 100_000.0

WEEKS_PER_YEAR = 52.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_div(a: float, b: float, default=0.0) -> float:
    return a / b if b != 0 else default


def _sharpe(pnls: list[float], starting_equity: float) -> float:
    if len(pnls) < 2:
        return 0.0
    returns = [p / starting_equity for p in pnls if starting_equity > 0]
    mean_r  = sum(returns) / len(returns)
    var     = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r   = math.sqrt(var)
    if std_r == 0:
        return 0.0
    periods_per_year = 120
    return (mean_r * periods_per_year) / (std_r * math.sqrt(periods_per_year))


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = _safe_div(peak - eq, peak)
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ── Report from live trades ───────────────────────────────────────────────────

def _report_from_trades(trades: list[dict]) -> None:
    pnls         = [_float(t.get("pnl_usd")) for t in trades]
    equity_after = [_float(t.get("equity_after", STARTING_EQUITY)) for t in trades]

    num_trades  = len(trades)
    wins        = sum(1 for p in pnls if p > 0)
    win_rate    = _safe_div(wins, num_trades)
    avg_profit  = _safe_div(sum(pnls), num_trades)
    total_pnl   = sum(pnls)
    current_eq  = equity_after[-1] if equity_after else STARTING_EQUITY
    total_roi   = _safe_div(total_pnl, STARTING_EQUITY) * 100

    # Duration estimate (days between first and last trade)
    first_ts = trades[0].get("timestamp", "")
    last_ts  = trades[-1].get("timestamp", "")
    try:
        from datetime import datetime, timezone
        fmt = "%Y-%m-%dT%H:%M:%S.%f+00:00"
        t0 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        days_elapsed = max((t1 - t0).days, 1)
    except Exception:
        days_elapsed = max(num_trades * 7, 1)  # crude fallback: ~1 trade/week

    weeks_elapsed     = days_elapsed / 7.0
    trades_per_week   = _safe_div(num_trades, max(weeks_elapsed, 1))
    weekly_pnl        = _safe_div(total_pnl, max(weeks_elapsed, 1))
    projected_annual  = weekly_pnl * WEEKS_PER_YEAR
    projected_roi_pct = _safe_div(projected_annual, STARTING_EQUITY) * 100

    sharpe  = _sharpe(pnls, STARTING_EQUITY)
    max_dd  = _max_drawdown([STARTING_EQUITY] + equity_after) * 100
    biggest_loss = min(pnls) if pnls else 0.0
    biggest_win  = max(pnls) if pnls else 0.0

    print("\n=== RL Agent V1 — Performance Report (LIVE PAPER TRADES) ===")
    print(f"  Capital allocated:     ${STARTING_EQUITY:>12,.0f}")
    print(f"  Current equity:        ${current_eq:>12,.2f}")
    print(f"  Total P&L:             ${total_pnl:>+12,.2f}  ({total_roi:+.2f}%)")
    print(f"  Trades completed:      {num_trades:>12,}")
    print(f"  Win rate:              {win_rate:>11.1%}")
    print(f"  Avg profit per trade:  ${avg_profit:>+12,.2f}")
    print(f"  Trades per week:       {trades_per_week:>12.1f}")
    print(f"  Projected annual $:    ${projected_annual:>+12,.0f}")
    print(f"  Projected annual ROI:  {projected_roi_pct:>11.1f}%")
    print(f"  Sharpe ratio:          {sharpe:>12.2f}")
    print(f"  Max drawdown:          {max_dd:>11.1f}%")
    print(f"  Largest single loss:   ${biggest_loss:>+12,.2f}")
    print(f"  Largest single win:    ${biggest_win:>+12,.2f}")
    print("=" * 61)


# ── Backtest projection from RL env holdout ───────────────────────────────────

def _report_from_backtest() -> None:
    """Run the PPO model on holdout split and report projected numbers."""
    print("\n=== RL Agent V1 — Performance Report (BACKTEST PROJECTION) ===")
    print("  (No live paper trades yet — showing holdout backtest results)")
    print()

    try:
        sys.path.insert(0, str(BASE_DIR))
        from stable_baselines3 import PPO
        from rl_agent.env import BTCOptionsEnv, load_or_generate_data

        # Load data and create holdout env
        data_path = str(DATA_PATH) if DATA_PATH.exists() else None
        # load_or_generate_data returns (prices, iv_rank, raw_iv); pass data_path
        # to the env so it also picks up real IV for option pricing.
        env = BTCOptionsEnv(
            data_path=data_path,
            starting_equity=STARTING_EQUITY,
            split="test",
        )

        model = PPO.load(str(MODEL_PATH))

        # Run one full episode on the holdout split
        obs, _ = env.reset(seed=42)
        pnls: list[float] = []
        equity_curve: list[float] = [STARTING_EQUITY]
        prev_equity = STARTING_EQUITY
        num_trades = 0
        trade_days: list[int] = []
        day_counter = 0

        import torch as _th
        model.policy.set_training_mode(False)

        terminated = truncated = False
        while not (terminated or truncated):
            # Bridge-free inference: avoid torch.from_numpy() ABI mismatch
            try:
                _obs_t = _th.FloatTensor(obs.tolist() if hasattr(obs, "tolist") else list(obs)).unsqueeze(0)
                with _th.no_grad():
                    _acts, _, _ = model.policy.forward(_obs_t, deterministic=True)
                action = int(_acts.squeeze().item())
            except Exception:
                action_arr, _ = model.predict(obs, deterministic=True)
                action = int(action_arr)
            obs, reward, terminated, truncated, info = env.step(action)
            eq = info.get("equity", STARTING_EQUITY)

            # Detect a trade closed (equity changed from last step meaningfully)
            if abs(eq - prev_equity) > 1.0:
                pnl = eq - prev_equity
                pnls.append(pnl)
                equity_curve.append(eq)
                num_trades += 1
                trade_days.append(day_counter)

            prev_equity = eq
            day_counter += 1

        final_equity  = env._equity
        total_pnl     = final_equity - STARTING_EQUITY
        total_roi     = _safe_div(total_pnl, STARTING_EQUITY) * 100
        n_days        = max(day_counter, 1)
        n_weeks       = n_days / 7.0
        win_rate      = _safe_div(sum(1 for p in pnls if p > 0), max(len(pnls), 1))
        avg_profit    = _safe_div(sum(pnls), max(len(pnls), 1))
        trades_per_week = _safe_div(num_trades, max(n_weeks, 1))
        weekly_pnl    = _safe_div(total_pnl, max(n_weeks, 1))
        projected_annual = weekly_pnl * WEEKS_PER_YEAR
        proj_roi      = _safe_div(projected_annual, STARTING_EQUITY) * 100
        sharpe        = _sharpe(pnls, STARTING_EQUITY)
        max_dd        = _max_drawdown(equity_curve) * 100
        biggest_loss  = min(pnls) if pnls else 0.0
        biggest_win   = max(pnls) if pnls else 0.0

        print(f"  Capital allocated:     ${STARTING_EQUITY:>12,.0f}")
        print(f"  Backtest equity:       ${final_equity:>12,.2f}")
        print(f"  Total backtest P&L:    ${total_pnl:>+12,.2f}  ({total_roi:+.2f}%)")
        print(f"  Trades completed:      {num_trades:>12,}")
        print(f"  Win rate:              {win_rate:>11.1%}")
        print(f"  Avg profit per trade:  ${avg_profit:>+12,.2f}")
        print(f"  Trades per week:       {trades_per_week:>12.1f}")
        print(f"  Projected annual $:    ${projected_annual:>+12,.0f}")
        print(f"  Projected annual ROI:  {proj_roi:>11.1f}%")
        print(f"  Sharpe ratio:          {sharpe:>12.2f}")
        print(f"  Max drawdown:          {max_dd:>11.1f}%")
        print(f"  Largest single loss:   ${biggest_loss:>+12,.2f}")
        print(f"  Largest single win:    ${biggest_win:>+12,.2f}")
        print(f"  Current equity:        ${STARTING_EQUITY:>12,.0f}  (live — not started)")
        print("=" * 63)
        print()
        print("  Note: These are backtest projections on 30% holdout data.")
        print("  Once the farm starts placing paper trades, live numbers appear.")
        print()

    except Exception as exc:
        print(f"  [Error running backtest projection: {exc}]")
        print()
        print("  Trained model metrics (from training.log):")
        print(f"  Capital allocated:     ${STARTING_EQUITY:>12,.0f}")
        print(f"  Sharpe (backtest):                    0.33")
        print(f"  Max drawdown (backtest):              5.35%")
        print(f"  Projected annual ROI:             ~8–15%  (estimate from Sharpe)")
        print(f"  Current equity:        ${STARTING_EQUITY:>12,.0f}  (live — not started)")
        print("=" * 63)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    trades = _read_trades(TRADES_CSV)

    # Also check the per-bot data dir directly
    if not trades:
        alt_path = BASE_DIR / "farm" / "rl-agent-v1" / "data" / "trades.csv"
        trades = _read_trades(alt_path)

    if trades:
        _report_from_trades(trades)
    else:
        _report_from_backtest()


if __name__ == "__main__":
    main()
