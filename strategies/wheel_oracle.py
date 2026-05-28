"""
wheel_oracle.py — the theoretical ceiling of the put-wheel on real data.

Sells a cash-secured 20-delta weekly put. Three policies:
  - oracle:   perfect selectivity — only sell on weeks the put expires OTM (never
              assigned). This is the absolute MAX a put-seller could capture.
  - iv_gated: realistic — sell only when iv_rank > 0.5, take assignment losses.
  - always:   naive — sell every week, take assignment losses.

Cash-secured (no leverage): collateral = strike notional, sized to equity.
Uses real Deribit IV for pricing (rl_agent/data/btc_daily.csv).

Usage:
    python3.11 wheel_oracle.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rl_agent"))
from env import bs_put_price, find_put_strike_for_delta   # noqa: E402

DATA = ROOT / "rl_agent" / "data" / "btc_daily.csv"
R = 0.04
T = 7.0 / 365.0
SIZE = 0.1                 # BTC per contract
TAKER = 0.0003
SPREAD = 0.02
START_EQ = 100_000.0


def trade_cost(S, premium, contracts):
    return S * SIZE * contracts * TAKER + abs(premium) * SPREAD


def run(df, policy):
    close = df["close"].values
    iv = np.clip(df["iv"].values / 100.0, 0.10, 3.0)
    ivr = df["iv_rank"].values
    n = len(df)
    eq = START_EQ
    curve = [eq]
    wins = trades = assigned = 0
    t = 0
    while t + 7 < n:
        S, sigma = close[t], iv[t]
        K = find_put_strike_for_delta(S, 0.20, T, R, sigma)
        unit = bs_put_price(S, K, T, R, sigma)
        contracts = int(eq / (K * SIZE)) if K * SIZE > 0 else 0
        if contracts < 1:
            t += 1
            continue
        premium = unit * contracts * SIZE
        cost = trade_cost(S, premium, contracts)
        net = premium - cost
        S_exp = close[t + 7]
        loss = max(K - S_exp, 0.0) * contracts * SIZE

        sell = (
            (policy == "oracle" and S_exp >= K) or
            (policy == "iv_gated" and ivr[t] > 0.5) or
            (policy == "always")
        )
        if sell:
            eq += net - loss
            trades += 1
            if loss > 0:
                assigned += 1
            if net - loss > 0:
                wins += 1
            t += 7              # hold to expiry
        else:
            t += 1
        curve.append(eq)

    curve = np.asarray(curve)
    years = n / 365.0
    apr = (eq / START_EQ) ** (1.0 / years) - 1.0 if eq > 0 else -1.0
    peak = np.maximum.accumulate(curve)
    mdd = float(np.max((peak - curve) / peak))
    return {
        "ret": eq / START_EQ - 1.0, "apr": apr, "mdd": mdd,
        "trades": trades, "win": wins / trades if trades else 0.0,
        "assigned": assigned,
    }


def main():
    df = pd.read_csv(DATA)
    years = len(df) / 365.0
    print(f"\n=== WHEEL ORACLE (20Δ cash-secured weekly puts, real IV, {years:.1f}yr) ===")
    print(f"{'Policy':<12} {'Return%':>9} {'APR%':>9} {'MaxDD%':>8} {'Win%':>7} "
          f"{'Trades':>7} {'Assigned':>9}")
    print("-" * 70)
    for pol in ("oracle", "iv_gated", "always"):
        s = run(df, pol)
        print(f"{pol:<12} {s['ret']*100:>9.1f} {s['apr']*100:>9.1f} {s['mdd']*100:>8.1f} "
              f"{s['win']*100:>7.1f} {s['trades']:>7} {s['assigned']:>9}")
    bh = df['close'].values[-1] / df['close'].values[0] - 1.0
    print(f"\nBuy & hold over same window: {bh*100:+.1f}%")
    print("oracle = perfect-foresight selectivity (impossible live) = the wheel's hard ceiling.")


if __name__ == "__main__":
    main()
