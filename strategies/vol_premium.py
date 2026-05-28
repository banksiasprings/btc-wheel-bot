"""
vol_premium.py — is a LONG-volatility bot (long-gamma / straddle scalp) worth it?

A long-gamma bot profits when realised volatility comes in ABOVE the implied vol
it paid. A short-vol bot (grid/wheel) profits the opposite way. So the question is
the sign of the Variance Risk Premium: implied vol minus the realised vol that
actually followed. Positive VRP = sellers win, buyers (long-gamma) lose.

Uses real Deribit IV (rl_agent/data/btc_daily.csv: date, close, iv) vs forward
realised vol from the close series.
"""

from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "rl_agent" / "data" / "btc_daily.csv"


def main():
    df = pd.read_csv(DATA)
    close = df["close"].values.astype(float)
    iv = df["iv"].values.astype(float) / 100.0          # annualised fraction
    logret = np.diff(np.log(close))

    print("\n=== Variance Risk Premium (real Deribit IV vs forward realised vol) ===")
    print(f"{'Horizon':>8} {'mean IV%':>9} {'mean RV%':>9} {'VRP pts':>9} {'IV>RV':>7} {'verdict':>22}")
    print("-" * 70)
    for n in (7, 14, 30):
        vrps, ivs, rvs = [], [], []
        for i in range(len(logret) - n):
            seg = logret[i:i + n]
            rv = seg.std() * np.sqrt(365)
            if rv > 0:
                vrps.append(iv[i] - rv)
                ivs.append(iv[i])
                rvs.append(rv)
        vrps = np.array(vrps)
        pct_iv_high = 100 * np.mean(vrps > 0)
        verdict = "sellers win (long-vol loses)" if vrps.mean() > 0 else "buyers win (long-vol wins)"
        print(f"{n}d{'':>5} {np.mean(ivs)*100:>8.1f} {np.mean(rvs)*100:>8.1f} "
              f"{vrps.mean()*100:>+8.1f} {pct_iv_high:>6.0f}% {verdict:>22}")

    print("\nLong-gamma (buying vol) only wins if realised > implied. A positive VRP means")
    print("you'd pay more for vol than it delivered, on average — i.e. long-gamma bleeds")
    print("in calm and only pays off in sharp moves/crashes (it's 'crash insurance').")


if __name__ == "__main__":
    main()
