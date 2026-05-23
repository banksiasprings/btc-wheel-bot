"""
Phase 2 data loader: assemble all raw historical data into a single feature matrix
indexed on a common 1h UTC timestamp, ready to feed the gym environment.

Sources expected under data/raw/:
  spot/btc_1h.csv                          Binance-style hourly BTC OHLCV
  deribit/dvol_history.json                Deribit DVOL (implied vol index), hourly
  deribit/funding_rates.json               BTC perpetual funding (hourly samples)
  deribit/delivery_prices.json             Option settlement prices, daily
  onchain/coin_metrics_daily.csv           MVRV, exchange flows, etc., daily
  onchain/fear_greed.json                  Fear & Greed index, daily
  onchain/rv_from_spot.csv                 Realised vol 7d/30d/90d, hourly

Output (load_feature_matrix):
  pandas.DataFrame indexed by hourly UTC timestamps with columns:
    spot OHLCV: open, high, low, close, volume
    returns:    ret_1h, ret_4h, ret_24h, ret_7d
    rv:         rv_7d, rv_30d, rv_90d
    iv:         dvol, dvol_z, iv_ratio_short_long (dvol vs rv_30d)
    funding:    funding_1h, funding_z (7d rolling z-score of funding_1h)
    fear/greed: fear_greed (0..100), fear_greed_norm (0..1)
    on-chain:   mvrv, mvrv_z, exchange_netflow_usd, exchange_netflow_z,
                hash_rate_z, active_addr_z
    price ctx:  log_price, price_52w_pct, drawdown_from_ath_pct,
                recent_drawdown_30d
    temporal:   hour_sin, hour_cos, dow_sin, dow_cos
    misc:       valid (bool — True for rows safe to use as episode starts)

Anything missing in source data is forward-filled where appropriate; rows with
critical data still missing after the alignment are flagged `valid=False`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"


def _to_utc(ts_ms_or_s: pd.Series) -> pd.DatetimeIndex:
    """Convert a numeric series to a tz-aware UTC DatetimeIndex.

    Auto-detects ms vs s by magnitude (post-2001 in ms is > 1e12).
    """
    arr = ts_ms_or_s.to_numpy()
    unit = "ms" if np.nanmax(arr) > 1e12 else "s"
    return pd.to_datetime(arr, unit=unit, utc=True)


def _load_spot_1h() -> pd.DataFrame:
    df = pd.read_csv(DATA_RAW / "spot" / "btc_1h.csv")
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    df["ts"] = _to_utc(df["timestamp"])
    df = df.drop(columns=["timestamp"]).set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def _load_rv() -> pd.DataFrame:
    df = pd.read_csv(DATA_RAW / "onchain" / "rv_from_spot.csv")
    df["ts"] = _to_utc(df["timestamp"])
    df = df[["ts", "rv_7d", "rv_30d", "rv_90d"]].set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def _load_dvol() -> pd.DataFrame:
    raw = json.load(open(DATA_RAW / "deribit" / "dvol_history.json"))
    rows = raw["data"]  # [ts_ms, open, high, low, close]
    arr = np.asarray(rows, dtype=float)
    ts = pd.to_datetime(arr[:, 0].astype(np.int64), unit="ms", utc=True)
    df = pd.DataFrame({"dvol": arr[:, 4]}, index=ts)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def _load_funding() -> pd.DataFrame:
    raw = json.load(open(DATA_RAW / "deribit" / "funding_rates.json"))
    rows = raw["data"]
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
    df = df[["ts", "interest_1h", "interest_8h", "index_price"]].set_index("ts").sort_index()
    df = df.rename(columns={"interest_1h": "funding_1h", "interest_8h": "funding_8h"})
    df = df[~df.index.duplicated(keep="last")]
    return df


def _load_fear_greed() -> pd.DataFrame:
    raw = json.load(open(DATA_RAW / "onchain" / "fear_greed.json"))
    rows = raw["data"]
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="s", utc=True)
    df["fear_greed"] = df["value"].astype(float)
    df = df[["ts", "fear_greed"]].set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def _load_coin_metrics() -> pd.DataFrame:
    df = pd.read_csv(DATA_RAW / "onchain" / "coin_metrics_daily.csv")
    df["ts"] = pd.to_datetime(df["date"], utc=True)
    keep = {
        "mvrv": "mvrv",
        "FlowInExUSD": "exchange_inflow_usd",
        "FlowOutExUSD": "exchange_outflow_usd",
        "HashRate": "hash_rate",
        "AdrActCnt": "active_addr",
        "CapMrktCurUSD": "market_cap_usd",
    }
    out = df[["ts", *keep.keys()]].rename(columns=keep)
    out = out.set_index("ts").sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out["exchange_netflow_usd"] = out["exchange_inflow_usd"] - out["exchange_outflow_usd"]
    return out


def _rolling_z(s: pd.Series, window: int, min_periods: Optional[int] = None) -> pd.Series:
    if min_periods is None:
        min_periods = max(8, window // 4)
    mean = s.rolling(window=window, min_periods=min_periods).mean()
    std = s.rolling(window=window, min_periods=min_periods).std()
    return (s - mean) / std.replace(0.0, np.nan)


def load_feature_matrix(start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    """Build the aligned, feature-rich hourly DataFrame for the env.

    Args:
        start, end: optional ISO date strings to slice the result.

    Returns:
        DataFrame with a tz-aware DatetimeIndex (hourly, UTC) and ~30 columns.
        A boolean column `valid` marks rows safe to use as RL episode starts
        (all critical features present, far enough in to have rolling stats).
    """
    spot = _load_spot_1h()
    rv = _load_rv()
    dvol = _load_dvol()
    funding = _load_funding()
    fg = _load_fear_greed()
    cm = _load_coin_metrics()

    # Choose master index = full hourly range covering all sources.
    t_start = min(spot.index.min(), dvol.index.min(), funding.index.min())
    t_end = max(spot.index.max(), dvol.index.max(), funding.index.max())
    idx = pd.date_range(t_start.floor("h"), t_end.ceil("h"), freq="1h", tz="UTC")

    # Reindex spot (no fill — leave nan if missing; OHLCV gaps are real).
    spot = spot.reindex(idx).ffill(limit=4)

    # rv is already hourly — reindex+ffill briefly.
    rv = rv.reindex(idx).ffill(limit=4)

    # dvol hourly — reindex+ffill briefly.
    dvol = dvol.reindex(idx).ffill(limit=4)

    # funding ~hourly with gaps; ffill up to 8h.
    funding = funding.reindex(idx).ffill(limit=8)

    # daily series — ffill to hourly.
    fg = fg.reindex(idx, method="ffill")
    cm = cm.reindex(idx, method="ffill")

    df = pd.concat([spot, rv, dvol, funding[["funding_1h"]], fg, cm], axis=1)

    # ---- derived: returns ----
    log_close = np.log(df["close"].replace(0.0, np.nan))
    df["log_price"] = log_close
    df["ret_1h"] = log_close.diff(1)
    df["ret_4h"] = log_close.diff(4)
    df["ret_24h"] = log_close.diff(24)
    df["ret_7d"] = log_close.diff(24 * 7)

    # ---- derived: 52w & drawdown ----
    win_52w = 24 * 365
    rolling_max = df["close"].rolling(win_52w, min_periods=24 * 30).max()
    rolling_min = df["close"].rolling(win_52w, min_periods=24 * 30).min()
    rng = (rolling_max - rolling_min).replace(0.0, np.nan)
    df["price_52w_pct"] = ((df["close"] - rolling_min) / rng).clip(0.0, 1.0)
    df["drawdown_from_ath_pct"] = (df["close"] / rolling_max - 1.0).clip(-1.0, 0.0)

    win_30d = 24 * 30
    recent_max = df["close"].rolling(win_30d, min_periods=24 * 3).max()
    df["recent_drawdown_30d"] = (df["close"] / recent_max - 1.0).clip(-1.0, 0.0)

    # ---- derived: vol surface proxies ----
    # DVOL is already an annualised IV percent (Deribit publishes it that way).
    df["dvol"] = df["dvol"].astype(float)
    df["dvol_z"] = _rolling_z(df["dvol"], window=24 * 30)  # 30d rolling z-score

    # Term-structure-ish proxy: short-term IV (DVOL, ~30d) vs realised vol_30d.
    rv30_annual_pct = df["rv_30d"] * 100.0
    df["iv_ratio_short_long"] = df["dvol"] / rv30_annual_pct.replace(0.0, np.nan)

    # ---- derived: funding ----
    df["funding_z"] = _rolling_z(df["funding_1h"], window=24 * 7)

    # ---- derived: on-chain ----
    df["mvrv_z"] = _rolling_z(df["mvrv"], window=24 * 90)
    df["exchange_netflow_z"] = _rolling_z(df["exchange_netflow_usd"], window=24 * 30)
    df["hash_rate_z"] = _rolling_z(df["hash_rate"], window=24 * 90)
    df["active_addr_z"] = _rolling_z(df["active_addr"], window=24 * 30)
    df["fear_greed_norm"] = (df["fear_greed"] / 100.0).clip(0.0, 1.0)

    # ---- temporal ----
    hours = df.index.hour.values
    dows = df.index.dayofweek.values
    df["hour_sin"] = np.sin(2 * math.pi * hours / 24.0)
    df["hour_cos"] = np.cos(2 * math.pi * hours / 24.0)
    df["dow_sin"] = np.sin(2 * math.pi * dows / 7.0)
    df["dow_cos"] = np.cos(2 * math.pi * dows / 7.0)

    # ---- validity mask ----
    must_have = ["close", "dvol", "rv_30d", "log_price"]
    df["valid"] = df[must_have].notna().all(axis=1)

    if start is not None:
        df = df.loc[pd.Timestamp(start, tz="UTC"):]
    if end is not None:
        df = df.loc[:pd.Timestamp(end, tz="UTC")]

    # Final tidy: fill anything still nan in non-essential cols with 0 so the
    # env never sees nan in its obs vector. Essentials stay nan and `valid` is
    # False there.
    fill_zero_cols = [
        "ret_1h", "ret_4h", "ret_24h", "ret_7d",
        "rv_7d", "rv_30d", "rv_90d",
        "dvol_z", "iv_ratio_short_long",
        "funding_1h", "funding_z",
        "fear_greed", "fear_greed_norm",
        "mvrv", "mvrv_z",
        "exchange_netflow_usd", "exchange_netflow_z",
        "hash_rate", "hash_rate_z",
        "active_addr", "active_addr_z",
        "price_52w_pct", "drawdown_from_ath_pct", "recent_drawdown_30d",
    ]
    for col in fill_zero_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    return df


if __name__ == "__main__":
    df = load_feature_matrix()
    print(f"rows: {len(df):,}, cols: {df.shape[1]}")
    print(f"range: {df.index.min()} → {df.index.max()}")
    print(f"valid rows: {df['valid'].sum():,} ({df['valid'].mean():.1%})")
    print()
    print(df.tail(3))
