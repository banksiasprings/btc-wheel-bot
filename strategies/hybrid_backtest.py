"""
hybrid_backtest.py — "core BTC holding + income grid bot" blend.

Steven believes BTC rises long-term (asset inflation), so a purely neutral grid
leaves the uptrend on the table. This splits capital: a fraction held as plain
BTC (rides the trend) and the rest run by the grid+trend-stop income bot.

Set-and-forget split (no rebalancing): blend the two equity curves. Shows the
trade-off — more BTC core = more upside, but also more downside in a crash.
"""

from __future__ import annotations

import numpy as np

from grid_backtest import CAPITAL, apr, load_hourly, max_drawdown, run_ladder

GRID = dict(spacing=0.05, max_lots=20, ma_hours=360)   # "Balanced" preset


def curves(df):
    lo, hi, cl = df["low"].values, df["high"].values, df["close"].values
    eq, _ = run_ladder(lo, hi, cl, **GRID)
    grid = np.concatenate(([CAPITAL], eq))                 # grid bot on full capital
    bh = CAPITAL * np.concatenate(([cl[0]], cl)) / cl[0]   # buy & hold BTC
    return bh, grid


def main():
    periods = [
        ("FULL 2019-2026", "2019-01-01", "2026-06-01"),
        ("2022 (−64% crash)", "2022-01-01", "2023-01-01"),
        ("2024 (big bull)", "2024-01-01", "2025-01-01"),
        ("2025H2-26 (bear)", "2025-06-01", "2026-06-01"),
    ]
    print("\n=== CORE BTC + INCOME GRID blend (Balanced grid: 5% / 20 lots / 15d stop) ===")
    print("'% in BTC core' = share held as plain Bitcoin; rest run by the income bot.\n")
    for label, s, e in periods:
        df = load_hourly(s, e)
        if len(df) < 50:
            continue
        bh, grid = curves(df)
        n = min(len(bh), len(grid))
        bh, grid = bh[:n], grid[:n]
        print(f"{label}")
        print(f"{'% in BTC core':>14} {'APR%':>8} {'MaxDD%':>8}")
        for f in (0.0, 0.25, 0.5, 0.75, 1.0):
            blend = f * bh + (1 - f) * grid       # both start at CAPITAL → blend starts at CAPITAL
            print(f"{int(f*100):>13}% {apr(blend, n)*100:>8.1f} {max_drawdown(blend)*100:>8.1f}")
        print()
    print("0% core = pure income bot; 100% = just holding Bitcoin. The blend lets you")
    print("dial how much you bet on the long-term rise vs. play it safe. (Drawdown here is")
    print("idealized; real crashes gap through the bot's stop, so expect somewhat more.)")


if __name__ == "__main__":
    main()
