"""
funding_backtest.py — delta-neutral funding harvest (cash-and-carry).

Hold $N of spot BTC and short $N of perp. Spot/perp price moves cancel
(direction-neutral), and you collect the perpetual funding each period:
positive funding → shorts receive (you earn), negative funding → you pay.

This is the survivable income FLOOR: no bag-holding, tiny drawdowns, but it
needs ~2x capital (both legs) and funding goes negative in deep bears.

Data: data/raw/deribit/funding_rates.json (hourly interest_1h, signed).

Usage:
    python3.11 funding_backtest.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FUNDING = Path(__file__).resolve().parent.parent / "data" / "raw" / "deribit" / "funding_rates.json"
MS_PER_YEAR = 365.0 * 24 * 3600 * 1000


def load():
    d = json.load(open(FUNDING))
    rows = sorted(d["data"], key=lambda r: r["timestamp"])
    ts = np.array([r["timestamp"] for r in rows], dtype=np.float64)
    rate1h = np.array([r["interest_1h"] for r in rows], dtype=np.float64)
    return ts, rate1h


def summarise(ts, rate1h, label):
    # Records are sampled (~8/day), so annualize by the AVERAGE hourly rate
    # (gap-robust) rather than summing sampled records over wall-clock time.
    # Delta-neutral: price PnL cancels; you earn the funding rate continuously.
    apr = float(np.mean(rate1h)) * 24 * 365
    curve = 1.0 + np.cumsum(rate1h)            # shape only (gap-affected magnitude)
    peak = np.maximum.accumulate(curve)
    mdd = float(np.max((peak - curve) / np.maximum(peak, 1e-9)))
    pct_pos = float(np.mean(rate1h > 0)) * 100
    print(f"{label:<22} {apr*100:>9.1f} {mdd*100:>8.2f} {pct_pos:>9.1f} {len(rate1h):>8}")
    return apr


def main():
    ts, rate1h = load()
    print("\n=== FUNDING HARVEST (delta-neutral long-spot/short-perp, gross of fees) ===")
    print(f"{'Period':<22} {'APR%':>9} {'MaxDD%':>8} {'%Funding+':>9} {'Recs':>8}")
    print("-" * 60)
    summarise(ts, rate1h, "FULL 2019-2026")

    import datetime as dt
    def yr_mask(y0, y1):
        a = dt.datetime(y0, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000
        b = dt.datetime(y1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000
        return (ts >= a) & (ts < b)

    for y in (2019, 2020, 2021, 2022, 2023, 2024, 2025):
        m = yr_mask(y, y + 1)
        if m.sum() > 100:
            summarise(ts[m], rate1h[m], f"{y}")
    print("\nNote: gross of trading/borrow fees and the ~2x capital both legs tie up. "
          "Direction-neutral with near-zero drawdown — a floor, not a moonshot.")


if __name__ == "__main__":
    main()
