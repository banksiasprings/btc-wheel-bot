"""
grid_backtest.py — spot grid-trading backtest on hourly BTC. No leverage.

A grid places buy orders on a price ladder; each filled buy at rung i is sold
when price rises to rung i+1, banking the spread. Profit scales with how much
price oscillates. Sustained trends are the failure mode: a crash leaves unsold
lots ("bags") marked down; a melt-up sells everything and sits in cash.

This finds the CEILING: the grid range is fitted to each period with hindsight
(legitimate for "what's the best this could have done"), sweeping levels/band.
Fills are optimistic (low then high within each bar) — a best case, by design.

Usage:
    python3.11 grid_backtest.py
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

HOURLY = Path(__file__).resolve().parent.parent / "data" / "raw" / "spot" / "btc_1h.csv"
CAPITAL = 100_000.0
FEE = 0.0006          # 0.06% per fill (spot taker)
HOURS_PER_YEAR = 24 * 365


def load_hourly(start=None, end=None) -> pd.DataFrame:
    df = pd.read_csv(HOURLY)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df[["ts", "high", "low", "close"]].astype(
        {"high": float, "low": float, "close": float})
    if start is not None:
        df = df[df["ts"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["ts"] < pd.Timestamp(end)]
    return df.reset_index(drop=True)


def run_grid(lows, highs, closes, lines, capital=CAPITAL, fee=FEE):
    """Inventory-stack grid. Returns (equity_curve, n_trades)."""
    n_levels = len(lines) - 1
    buy_usd = capital / n_levels
    cash = capital
    held: dict[int, float] = {}     # rung index -> qty held
    trades = 0
    eq = np.empty(len(closes))
    prev = closes[0]                # a buy only fills when price falls THROUGH a line from above
    for k in range(len(closes)):
        lo, hi, cl = lows[k], highs[k], closes[k]
        # buys: rungs that sat below the prior price and were reached by this bar's low
        for i in range(n_levels):
            if i not in held and lines[i] <= prev and lo <= lines[i]:
                cost = buy_usd * (1 + fee)
                if cash >= cost:
                    cash -= cost
                    held[i] = buy_usd / lines[i]
                    trades += 1
        # sells: each held lot exits one rung up
        for i in list(held.keys()):
            if hi >= lines[i + 1]:
                qty = held.pop(i)
                cash += qty * lines[i + 1] * (1 - fee)
                trades += 1
        inv = sum(q * cl for q in held.values())
        eq[k] = cash + inv
        prev = cl
    return eq, trades


def run_ladder(lows, highs, closes, spacing=0.02, max_lots=50, capital=CAPITAL, fee=FEE,
               ma_hours=0):
    """Realistic deployable grid: a global geometric ladder at fixed % spacing.

    No hindsight — the same config runs on every regime. Buy one lot each time
    price steps down a ladder line; sell it one line up (~spacing %). Cash-capped
    so it never uses leverage: in a deep downtrend it laddered down until cash runs
    out, then rides the inventory ('bags') down — that loss shows in the curve.

    ma_hours>0 adds a trend-stop: when price is below its moving average (confirmed
    downtrend) the grid liquidates to cash and stops buying, capping bag drawdown.
    """
    g = math.log1p(spacing)
    rung = lambda p: int(math.floor(math.log(p) / g))
    price_at = lambda r: math.exp(r * g)
    buy_usd = capital / max_lots
    cash = capital
    held: dict[int, float] = {}     # rung -> qty; at most one lot per rung
    trades = 0
    eq = np.empty(len(closes))
    ma = (pd.Series(closes).rolling(ma_hours, min_periods=1).mean().values
          if ma_hours else None)
    r_prev = rung(closes[0])
    # Close-to-close fills only: no same-bar buy+sell (avoids fantasy intrabar round-trips).
    # Conservative — a real resting-limit grid captures somewhat more intrabar oscillation.
    for k in range(len(closes)):
        cl = closes[k]
        if ma is not None and cl < ma[k]:           # trend-stop: downtrend → flat, no bags
            for r in list(held.keys()):
                cash += held.pop(r) * cl * (1 - fee)
                trades += 1
            r_prev = rung(cl)
            eq[k] = cash
            continue
        r_now = rung(cl)
        if r_now < r_prev:                          # price stepped down → buy crossed rungs
            for r in range(r_prev - 1, r_now - 1, -1):
                if r not in held and len(held) < max_lots and cash >= buy_usd * (1 + fee):
                    cash -= buy_usd * (1 + fee)
                    held[r] = buy_usd / price_at(r)
                    trades += 1
        elif r_now > r_prev:                        # price stepped up → take profit one rung up
            for r in list(held.keys()):
                if r + 1 <= r_now:
                    cash += held.pop(r) * price_at(r + 1) * (1 - fee)
                    trades += 1
        r_prev = r_now
        eq[k] = cash + sum(q * cl for q in held.values())
    return eq, trades


def max_drawdown(eq) -> float:
    peak = np.maximum.accumulate(eq)
    return float(np.max((peak - eq) / peak))


def apr(eq, n_bars) -> float:
    years = n_bars / HOURS_PER_YEAR
    if years <= 0:
        return 0.0
    return (eq[-1] / CAPITAL) ** (1.0 / years) - 1.0


def best_grid_for(df, label):
    lows, highs, closes = df["low"].values, df["high"].values, df["close"].values
    pmin, pmax = lows.min(), highs.max()
    p5, p95 = np.percentile(closes, 5), np.percentile(closes, 95)

    bands = {"full[min,max]": (pmin, pmax), "p5-p95": (p5, p95)}
    best = None
    for band_name, (lo, hi) in bands.items():
        if hi <= lo:
            continue
        for n_levels in (20, 50, 100, 200):
            lines = np.geomspace(lo, hi, n_levels + 1)
            eq, trades = run_grid(lows, highs, closes, lines)
            curve = np.concatenate(([CAPITAL], eq))
            ret = curve[-1] / CAPITAL - 1.0
            cand = {
                "band": band_name, "levels": n_levels, "ret": ret,
                "apr": apr(curve, len(df)), "mdd": max_drawdown(curve), "trades": trades,
            }
            if best is None or cand["apr"] > best["apr"]:
                best = cand

    bh = closes[-1] / closes[0] - 1.0
    print(f"{label:<22} {best['ret']*100:>9.1f} {best['apr']*100:>9.1f} "
          f"{best['mdd']*100:>8.1f} {best['trades']:>8} {bh*100:>10.1f} "
          f"  {best['band']},{best['levels']}L")
    return best


PERIODS = [
    ("FULL 2019-2026", "2019-01-01", "2026-06-01"),
    ("2019 (chop)", "2019-01-01", "2020-01-01"),
    ("2021 (volatile)", "2021-01-01", "2022-01-01"),
    ("2022 (bear)", "2022-01-01", "2023-01-01"),
    ("2023 (recovery)", "2023-01-01", "2024-01-01"),
    ("2024 (bull)", "2024-01-01", "2025-01-01"),
    ("2025H2-26 (our bear)", "2025-06-01", "2026-06-01"),
]


def main():
    print(f"\n=== GRID CEILING (hindsight range, fee={FEE*100:.2f}%/fill, no leverage) ===")
    print(f"{'Period':<22} {'Return%':>9} {'APR%':>9} {'MaxDD%':>8} {'Trades':>8} "
          f"{'BuyHold%':>10}   best-config")
    print("-" * 92)
    for label, s, e in PERIODS:
        df = load_hourly(s, e)
        if len(df) < 50:
            print(f"{label:<22}  (no data)")
            continue
        best_grid_for(df, label)
    print("\nNote: hindsight-fitted range + optimistic intrabar fills = upper bound, not a forecast.")


def realistic_main(spacing=0.02, max_lots=50):
    print(f"\n=== GRID REALISTIC (fixed config: spacing={spacing*100:.1f}%, "
          f"max_lots={max_lots}, fee={FEE*100:.2f}%/fill, no leverage, no hindsight) ===")
    print(f"{'Period':<22} {'Return%':>9} {'APR%':>9} {'MaxDD%':>8} {'Trades':>8} {'BuyHold%':>10}")
    print("-" * 74)
    for label, s, e in PERIODS:
        df = load_hourly(s, e)
        if len(df) < 50:
            print(f"{label:<22}  (no data)")
            continue
        lows, highs, closes = df["low"].values, df["high"].values, df["close"].values
        eq, trades = run_ladder(lows, highs, closes, spacing=spacing, max_lots=max_lots)
        curve = np.concatenate(([CAPITAL], eq))
        ret = curve[-1] / CAPITAL - 1.0
        bh = closes[-1] / closes[0] - 1.0
        print(f"{label:<22} {ret*100:>9.1f} {apr(curve, len(df))*100:>9.1f} "
              f"{max_drawdown(curve)*100:>8.1f} {trades:>8} {bh*100:>10.1f}")

    print(f"\n--- sensitivity on FULL period ---")
    df = load_hourly("2019-01-01", "2026-06-01")
    lows, highs, closes = df["low"].values, df["high"].values, df["close"].values
    print(f"{'spacing':>8} {'lots':>6} {'Return%':>9} {'APR%':>9} {'MaxDD%':>8} {'Trades':>8}")
    for sp in (0.01, 0.02, 0.03, 0.05):
        for ml in (20, 50, 100):
            eq, trades = run_ladder(lows, highs, closes, spacing=sp, max_lots=ml)
            curve = np.concatenate(([CAPITAL], eq))
            print(f"{sp*100:>7.0f}% {ml:>6} {(curve[-1]/CAPITAL-1)*100:>9.1f} "
                  f"{apr(curve, len(df))*100:>9.1f} {max_drawdown(curve)*100:>8.1f} {trades:>8}")

    print(f"\n--- trend-stop: plain grid vs grid + 30d MA filter (config 2%/50) ---")
    print(f"{'Period':<22} {'plainAPR%':>10} {'plainDD%':>9} {'stopAPR%':>9} "
          f"{'stopDD%':>8} {'plainMAR':>9} {'stopMAR':>8}")
    for label, s, e in PERIODS:
        df = load_hourly(s, e)
        if len(df) < 50:
            continue
        lows, highs, closes = df["low"].values, df["high"].values, df["close"].values
        e0, _ = run_ladder(lows, highs, closes, ma_hours=0)
        e1, _ = run_ladder(lows, highs, closes, ma_hours=720)
        c0, c1 = np.concatenate(([CAPITAL], e0)), np.concatenate(([CAPITAL], e1))
        a0, d0 = apr(c0, len(df)), max_drawdown(c0)
        a1, d1 = apr(c1, len(df)), max_drawdown(c1)
        mar0 = a0 / d0 if d0 > 0 else 0.0
        mar1 = a1 / d1 if d1 > 0 else 0.0
        print(f"{label:<22} {a0*100:>10.1f} {d0*100:>9.1f} {a1*100:>9.1f} "
              f"{d1*100:>8.1f} {mar0:>9.2f} {mar1:>8.2f}")

    print("\nNote: one fixed config across all regimes = an honest forward expectation, not a fit.")
    print("MAR = APR/MaxDD (higher = better risk-adjusted). Trend-stop trades return for safety.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grid backtest")
    parser.add_argument("--mode", choices=["ceiling", "realistic"], default="realistic")
    args = parser.parse_args()
    if args.mode == "ceiling":
        main()
    else:
        realistic_main()
