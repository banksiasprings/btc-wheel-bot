#!/usr/bin/env python3
"""
download_spot.py — BTC/USD historical OHLCV downloader from Binance public API
Downloads:
  - Daily candles from 2017-01-01 to today  → data/raw/spot/btc_daily.csv
  - 1-hour candles from 2019-01-01 to today → data/raw/spot/btc_1h.csv

No API key required. Uses Binance klines endpoint.
Usage: python3 data_pipeline/download_spot.py
"""

import csv
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SPOT_DIR = ROOT / "data" / "raw" / "spot"
LOG_DIR = ROOT / "data" / "logs"
SPOT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "download.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("download_spot")

BINANCE_URL = "https://api.binance.com/api/v3/klines"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-rl-v2-data-pipeline/1.0"})

CSV_HEADERS = ["timestamp", "open", "high", "low", "close", "volume",
               "close_time", "quote_volume", "num_trades",
               "taker_buy_base", "taker_buy_quote", "ignore"]


def ts_ms(dt: datetime) -> int:
    """Convert datetime to milliseconds timestamp."""
    return int(dt.timestamp() * 1000)


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Fetch one page of klines (up to 1000 candles)."""
    retries = 0
    max_retries = 8

    while retries < max_retries:
        try:
            resp = SESSION.get(
                BINANCE_URL,
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = min(60, 2 ** retries)
                log.warning(f"Rate limited (429). Sleeping {wait}s …")
                time.sleep(wait)
                retries += 1
                continue
            if resp.status_code == 418:  # IP banned
                log.error("IP banned by Binance (418). Sleeping 60s …")
                time.sleep(60)
                retries += 1
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            wait = min(60, 2 ** retries)
            log.warning(f"Request error: {e}. Retrying in {wait}s …")
            time.sleep(wait)
            retries += 1

    raise RuntimeError(f"Failed to fetch klines after {max_retries} retries")


def interval_ms(interval: str) -> int:
    """Return interval duration in milliseconds."""
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    n = int(interval[:-1])
    unit = interval[-1]
    return n * units[unit]


def download_ohlcv(
    symbol: str,
    interval: str,
    start_dt: datetime,
    end_dt: datetime,
    out_path: Path,
) -> int:
    """Download full OHLCV history and write to CSV. Returns number of candles."""
    log.info(f"Downloading {symbol} {interval} from {start_dt.date()} to {end_dt.date()} → {out_path.name}")

    start_ms = ts_ms(start_dt)
    end_ms = ts_ms(end_dt)
    step = interval_ms(interval) * 1000  # 1000 candles per page

    total_candles = 0
    page_num = 0
    current_start = start_ms

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)

        while current_start < end_ms:
            page_end = min(current_start + step, end_ms)
            candles = fetch_klines(symbol, interval, current_start, page_end)

            if not candles:
                break

            writer.writerows(candles)
            total_candles += len(candles)
            page_num += 1

            last_ts = candles[-1][0]
            current_start = last_ts + interval_ms(interval)

            if page_num % 20 == 0:
                dt_str = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                log.info(f"  Page {page_num}: {total_candles:,} candles so far (up to {dt_str})")

            time.sleep(0.05)  # polite rate limiting

    log.info(f"  ✓ {total_candles:,} {interval} candles saved → {out_path}")
    return total_candles


def main():
    now = datetime.now(tz=timezone.utc)

    # ── Daily candles from 2017-01-01 ──────────────────────────────────────
    daily_path = SPOT_DIR / "btc_daily.csv"
    if daily_path.exists() and daily_path.stat().st_size > 100_000:
        log.info(f"Daily file already exists ({daily_path.stat().st_size:,} bytes). Skipping.")
    else:
        n = download_ohlcv(
            symbol="BTCUSDT",
            interval="1d",
            start_dt=datetime(2017, 1, 1, tzinfo=timezone.utc),
            end_dt=now,
            out_path=daily_path,
        )
        log.info(f"Daily download complete: {n:,} candles")

    # ── 1-hour candles from 2019-01-01 ────────────────────────────────────
    hourly_path = SPOT_DIR / "btc_1h.csv"
    if hourly_path.exists() and hourly_path.stat().st_size > 5_000_000:
        log.info(f"Hourly file already exists ({hourly_path.stat().st_size:,} bytes). Skipping.")
    else:
        n = download_ohlcv(
            symbol="BTCUSDT",
            interval="1h",
            start_dt=datetime(2019, 1, 1, tzinfo=timezone.utc),
            end_dt=now,
            out_path=hourly_path,
        )
        log.info(f"Hourly download complete: {n:,} candles")

    # ── Print summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SPOT DOWNLOAD COMPLETE")
    for path in [daily_path, hourly_path]:
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  {path.name}: {size_mb:.2f} MB")
    print("=" * 60)


if __name__ == "__main__":
    main()
