#!/usr/bin/env python3
"""
download_iv_history.py — Deribit BTC IV and DVOL historical data downloader
Downloads:
  - Historical 30-day realized volatility: GET /public/get_historical_volatility
  - DVOL index (Deribit Volatility Index): GET /public/get_index_price_names + get_index_price
  - Volatility index history via get_volatility_index_data (DVOL)

Saves to:
  data/raw/deribit/iv_history.json
  data/raw/deribit/dvol_history.json

Usage: python3 data_pipeline/download_iv_history.py
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DERIBIT_DIR = ROOT / "data" / "raw" / "deribit"
LOG_DIR = ROOT / "data" / "logs"
DERIBIT_DIR.mkdir(parents=True, exist_ok=True)
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
log = logging.getLogger("download_iv_history")

BASE_URL = "https://www.deribit.com/api/v2/public"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-rl-v2-data-pipeline/1.0"})


def api_get(endpoint: str, params: dict = None, max_retries: int = 8):
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params or {}, timeout=30)
            if resp.status_code == 429:
                wait = min(60, 2 ** attempt)
                log.warning(f"Rate limited. Sleeping {wait}s …")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data.get("result")
        except requests.exceptions.RequestException as e:
            wait = min(30, 2 ** attempt)
            log.warning(f"Request error: {e}. Retry {attempt+1}/{max_retries} in {wait}s …")
            time.sleep(wait)
    log.error(f"Failed to fetch {endpoint} after {max_retries} retries")
    return None


def download_historical_volatility():
    """Download hourly 30-day realized volatility history."""
    out_path = DERIBIT_DIR / "iv_history.json"

    if out_path.exists():
        log.info(f"iv_history.json already exists. Skipping.")
        with open(out_path) as f:
            return json.load(f)

    log.info("Downloading BTC historical volatility (30d realized) …")
    result = api_get("get_historical_volatility", {"currency": "BTC"})

    if result is None:
        log.error("Failed to fetch historical volatility data")
        return None

    # Result is a list of [timestamp_ms, volatility_pct] pairs
    data = {
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "currency": "BTC",
        "description": "30-day realized volatility, hourly, annualized percentage",
        "count": len(result) if result else 0,
        "data": result,
    }

    if result:
        ts_first = datetime.fromtimestamp(result[0][0] / 1000, tz=timezone.utc)
        ts_last = datetime.fromtimestamp(result[-1][0] / 1000, tz=timezone.utc)
        data["first_timestamp"] = ts_first.isoformat()
        data["last_timestamp"] = ts_last.isoformat()
        log.info(f"  Historical vol: {len(result):,} hourly points from {ts_first.date()} to {ts_last.date()}")

    with open(out_path, "w") as f:
        json.dump(data, f)
    log.info(f"  ✓ Saved → {out_path}")
    return data


def download_dvol_history():
    """Download DVOL (Deribit Volatility Index) history via get_volatility_index_data."""
    out_path = DERIBIT_DIR / "dvol_history.json"

    if out_path.exists():
        log.info(f"dvol_history.json already exists. Skipping.")
        with open(out_path) as f:
            return json.load(f)

    log.info("Downloading DVOL (Deribit Volatility Index) history …")

    # get_volatility_index_data returns OHLCV for the vol index at various resolutions
    # resolution=3600 = 1h candles
    all_data = []
    start_ms = 1550000000000  # ~2019-02-12 (DVOL launch was ~late 2019 but try from earlier)
    end_ms = int(time.time() * 1000)
    resolution = 3600  # 1-hour

    page_count = 0
    current_start = start_ms

    while current_start < end_ms:
        result = api_get("get_volatility_index_data", {
            "currency": "BTC",
            "start_timestamp": current_start,
            "end_timestamp": min(current_start + resolution * 1000 * 1000, end_ms),  # ~1000 candles
            "resolution": resolution,
        })
        time.sleep(0.15)

        if result is None:
            break

        candles = result.get("data", [])
        if not candles:
            break

        all_data.extend(candles)
        page_count += 1

        # Each candle: [timestamp_ms, open, high, low, close]
        last_ts = candles[-1][0]
        current_start = last_ts + resolution * 1000

        if page_count % 10 == 0:
            dt = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc)
            log.info(f"  DVOL page {page_count}: {len(all_data):,} candles (up to {dt.date()})")

        if len(candles) < 100:
            break  # Near the end

    data = {
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "currency": "BTC",
        "description": "DVOL (Deribit Volatility Index) 1h candles [timestamp, open, high, low, close]",
        "resolution": "1h",
        "count": len(all_data),
        "data": all_data,
    }

    if all_data:
        ts_first = datetime.fromtimestamp(all_data[0][0] / 1000, tz=timezone.utc)
        ts_last = datetime.fromtimestamp(all_data[-1][0] / 1000, tz=timezone.utc)
        data["first_timestamp"] = ts_first.isoformat()
        data["last_timestamp"] = ts_last.isoformat()
        log.info(f"  DVOL: {len(all_data):,} 1h candles from {ts_first.date()} to {ts_last.date()}")

    with open(out_path, "w") as f:
        json.dump(data, f)
    log.info(f"  ✓ Saved → {out_path}")
    return data


def get_index_price_names():
    """List available index names (includes DVOL indices)."""
    log.info("Fetching index price names …")
    result = api_get("get_index_price_names")
    if result:
        log.info(f"  Available indices: {result}")
    return result


def main():
    log.info("=" * 60)
    log.info("IV History Downloader")
    log.info("=" * 60)

    # List available indices for reference
    indices = get_index_price_names()
    time.sleep(0.3)

    # Download 30-day realized volatility history
    iv_data = download_historical_volatility()
    time.sleep(0.3)

    # Download DVOL history
    dvol_data = download_dvol_history()

    log.info("=" * 60)
    log.info("IV DOWNLOAD COMPLETE")
    if iv_data:
        log.info(f"  Realized vol : {iv_data.get('count', 0):,} hourly points")
    if dvol_data:
        log.info(f"  DVOL         : {dvol_data.get('count', 0):,} hourly candles")
    log.info("=" * 60)

    print("\nSummary:")
    if iv_data:
        print(f"  Historical vol: {iv_data.get('count', 0):,} pts ({iv_data.get('first_timestamp','?')} → {iv_data.get('last_timestamp','?')})")
    if dvol_data:
        print(f"  DVOL:           {dvol_data.get('count', 0):,} 1h candles ({dvol_data.get('first_timestamp','?')} → {dvol_data.get('last_timestamp','?')})")


if __name__ == "__main__":
    main()
