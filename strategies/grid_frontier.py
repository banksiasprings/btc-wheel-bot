"""
grid_frontier.py — find the best risk-adjusted grid+trend-stop config.

Sweeps spacing / lots / MA-period (all WITH the trend-stop on), ranks by MAR
(APR/MaxDD), then shows the top config's stability across every regime —
the real test that the choice isn't a single-period fluke.
"""

from __future__ import annotations

import numpy as np

from grid_backtest import (CAPITAL, PERIODS, apr, load_hourly, max_drawdown, run_ladder)


def score(df, spacing, lots, ma):
    lo, hi, cl = df["low"].values, df["high"].values, df["close"].values
    eq, trades = run_ladder(lo, hi, cl, spacing=spacing, max_lots=lots, ma_hours=ma)
    curve = np.concatenate(([CAPITAL], eq))
    a, d = apr(curve, len(df)), max_drawdown(curve)
    return a, d, (a / d if d > 0 else 0.0), trades


def main():
    full = load_hourly("2019-01-01", "2026-06-01")
    grid = []
    for ma in (0, 360, 720, 1440):
        for sp in (0.02, 0.03, 0.05):
            for lots in (20, 50):
                a, d, mar, tr = score(full, sp, lots, ma)
                grid.append((mar, a, d, sp, lots, ma, tr))

    print("\n=== FRONTIER on FULL 2019-2026 (ranked by MAR = APR/MaxDD) ===")
    print(f"{'spacing':>7} {'lots':>5} {'MA(h)':>6} {'APR%':>7} {'MaxDD%':>7} {'MAR':>6} {'Trades':>8}")
    for mar, a, d, sp, lots, ma, tr in sorted(grid, reverse=True)[:10]:
        print(f"{sp*100:>6.0f}% {lots:>5} {ma:>6} {a*100:>7.1f} {d*100:>7.1f} {mar:>6.2f} {tr:>8}")

    print("\n=== Highest-APR configs (any DD) ===")
    for mar, a, d, sp, lots, ma, tr in sorted(grid, key=lambda x: -x[1])[:5]:
        print(f"{sp*100:>6.0f}% {lots:>5} {ma:>6} {a*100:>7.1f} {d*100:>7.1f} {mar:>6.2f} {tr:>8}")

    # stability of the best-MAR config across every regime
    best = max(grid, key=lambda x: x[0])
    _, _, _, sp, lots, ma, _ = best
    print(f"\n=== Stability of best-MAR config ({sp*100:.0f}% / {lots} lots / MA={ma}h) per regime ===")
    print(f"{'Period':<22} {'APR%':>8} {'MaxDD%':>8} {'MAR':>6}")
    for label, s, e in PERIODS:
        df = load_hourly(s, e)
        if len(df) < 50:
            continue
        a, d, mar, _ = score(df, sp, lots, ma)
        print(f"{label:<22} {a*100:>8.1f} {d*100:>8.1f} {mar:>6.2f}")


if __name__ == "__main__":
    main()
