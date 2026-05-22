"""
fetch_deribit_data.py — Pull real BTC price + IV history from Deribit public API.

No auth needed. Uses public endpoints only.

Outputs:
    rl_agent/data/btc_daily.csv   — columns: date, close, iv_rank
                                    (iv_rank computed from historical vol series)

Usage:
    python fetch_deribit_data.py
    python fetch_deribit_data.py --days 1095   # 3 years
"""

import argparse
import math
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pandas as pd
import numpy as np

BASE_URL = "https://www.deribit.com/api/v2/public"

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_CSV = DATA_DIR / "btc_daily.csv"


def deribit_get(method: str, params: dict) -> dict:
    """Simple Deribit public REST call with retry."""
    url = f"{BASE_URL}/{method}"
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            if "result" not in data:
                raise ValueError(f"No result field: {data}")
            return data["result"]
        except Exception as exc:
            wait = 2 ** attempt
            print(f"  [{method}] attempt {attempt+1} failed: {exc} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {method} after 5 attempts")


def fetch_btc_prices(days: int = 1095) -> pd.DataFrame:
    """
    Fetch BTC-PERPETUAL daily OHLCV from Deribit.
    Returns DataFrame with columns: date (datetime), close (float).
    """
    end_ts = int(datetime.now(tz=timezone.utc).timestamp())
    start_ts = end_ts - days * 86400

    print(f"Fetching BTC-PERPETUAL daily candles ({days} days) ...")
    result = deribit_get("get_tradingview_chart_data", {
        "instrument_name": "BTC-PERPETUAL",
        "start_timestamp": start_ts * 1000,    # Deribit uses ms
        "end_timestamp": end_ts * 1000,
        "resolution": "1D",
    })

    if result.get("status") == "no_data" or not result.get("ticks"):
        raise RuntimeError("No price data returned from Deribit")

    ticks  = result["ticks"]
    closes = result["close"]

    rows = []
    for ts_ms, close in zip(ticks, closes):
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
        rows.append({"date": dt, "close": float(close)})

    df = pd.DataFrame(rows)
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    print(f"  Got {len(df)} daily price bars "
          f"({df['date'].iloc[0]} → {df['date'].iloc[-1]})")
    return df


def fetch_btc_iv(currency: str = "BTC") -> pd.DataFrame:
    """
    Fetch Deribit historical (implied) volatility for BTC.
    Returns DataFrame with columns: date (datetime.date), iv (float, annualised %).

    Deribit returns a list of [timestamp_ms, iv_value] pairs.
    """
    print(f"Fetching BTC historical IV ...")
    result = deribit_get("get_historical_volatility", {"currency": currency})
    # result is a list of [ts_ms, iv] pairs
    rows = []
    for entry in result:
        ts_ms = int(entry[0])
        iv_val = float(entry[1])
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
        rows.append({"date": dt, "deribit_iv": iv_val})

    df = pd.DataFrame(rows)
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    print(f"  Got {len(df)} daily IV values "
          f"({df['date'].iloc[0]} → {df['date'].iloc[-1]})")
    return df


def compute_iv_rank(iv_series: np.ndarray, window: int = 252) -> np.ndarray:
    """
    IV rank = percentile rank of today's IV within the trailing window.
    Values in [0, 1]; 1 = highest IV in the window.
    """
    n = len(iv_series)
    iv_rank = np.full(n, 0.5)
    for i in range(window, n):
        w = iv_series[i - window : i]
        mn, mx = w.min(), w.max()
        if mx > mn:
            iv_rank[i] = (iv_series[i] - mn) / (mx - mn)
        else:
            iv_rank[i] = 0.5
    # Partial window fill: use available data
    for i in range(1, min(window, n)):
        w = iv_series[0:i]
        mn, mx = w.min(), w.max()
        if mx > mn:
            iv_rank[i] = (iv_series[i] - mn) / (mx - mn)
        else:
            iv_rank[i] = 0.5
    return np.clip(iv_rank, 0.0, 1.0)


def build_dataset(days: int = 1095) -> pd.DataFrame:
    price_df = fetch_btc_prices(days=days)
    iv_df    = fetch_btc_iv()

    # Merge on date
    df = pd.merge(price_df, iv_df, on="date", how="left")

    # For days without Deribit IV (early dates or gaps), estimate from realised vol
    log_rets = np.zeros(len(df))
    log_rets[1:] = np.log(df["close"].values[1:] / df["close"].values[:-1])

    rv10 = np.zeros(len(df))
    for i in range(10, len(df)):
        rv10[i] = np.std(log_rets[i-10:i]) * math.sqrt(252) * 100  # as %
    rv10[:10] = rv10[10] if len(df) > 10 else 80.0

    # Use Deribit IV where available, else realised vol as proxy
    iv_arr = df["deribit_iv"].values.copy()
    for i, v in enumerate(iv_arr):
        if pd.isna(v) or v <= 0:
            iv_arr[i] = rv10[i]

    df["iv"] = iv_arr
    df["iv_rank"] = compute_iv_rank(iv_arr)

    df = df[["date", "close", "iv", "iv_rank"]].copy()
    df.columns = ["date", "close", "iv", "iv_rank"]
    return df


def main():
    parser = argparse.ArgumentParser(description="Fetch real Deribit BTC data for RL training")
    parser.add_argument("--days", type=int, default=1095, help="Days of history to fetch (default 1095 = 3 years)")
    args = parser.parse_args()

    print(f"\n=== Deribit Data Fetcher ===")
    print(f"Target: {args.days} days of BTC daily price + IV")

    df = build_dataset(days=args.days)

    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved {len(df)} rows → {OUTPUT_CSV}")
    print(f"Date range: {df['date'].iloc[0]} → {df['date'].iloc[-1]}")
    print(f"Price range: ${df['close'].min():,.0f} – ${df['close'].max():,.0f}")
    print(f"IV range: {df['iv'].min():.1f}% – {df['iv'].max():.1f}%")
    print(f"IV rank range: {df['iv_rank'].min():.3f} – {df['iv_rank'].max():.3f}")
    print("\nFirst 5 rows:")
    print(df.head().to_string(index=False))
    print("\nLast 5 rows:")
    print(df.tail().to_string(index=False))
    print("\nDone. Run: python train.py --data rl_agent/data/btc_daily.csv")


if __name__ == "__main__":
    main()
