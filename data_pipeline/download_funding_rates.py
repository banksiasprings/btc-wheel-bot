#!/usr/bin/env python3
"""
download_funding_rates.py — Deribit BTC perpetual funding rate history
Downloads 8-hour funding rates going back as far as the API allows (~2019).

Saves to: data/raw/deribit/funding_rates.json

Usage: python3 data_pipeline/download_funding_rates.py
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DERIBIT_DIR = ROOT / "data" / "raw" / "deribit"
LOG_DIR = ROOT / "data" / "logs"
DERIBIT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "download.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("download_funding_rates")

BASE_URL = "https://www.deribit.com/api/v2/public"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-rl-v2-data-pipeline/1.0"})


def api_get(endpoint: str, params: dict, max_retries: int = 8):
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = min(60, 2 ** attempt)
                log.warning(f"Rate limited. Sleeping {wait}s …")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("result")
        except requests.exceptions.RequestException as e:
            wait = min(30, 2 ** attempt)
            log.warning(f"Request error: {e}. Retry {attempt+1}/{max_retries} in {wait}s …")
            time.sleep(wait)
    return None


def main():
    out_path = DERIBIT_DIR / "funding_rates.json"

    if out_path.exists():
        log.info(f"funding_rates.json already exists. Skipping.")
        with open(out_path) as f:
            data = json.load(f)
        print(f"Existing: {data.get('count', 0):,} funding rate records")
        return

    log.info("Downloading BTC perpetual funding rate history …")

    all_records = []
    # BTC perpetual instrument
    instrument_name = "BTC-PERPETUAL"
    # Start from 2019-01-01
    start_ms = 1546300800000
    end_ms = int(time.time() * 1000)
    count_per_page = 720  # 8h intervals, 720 = 240 days per page

    current_end = end_ms
    page = 0

    while current_end > start_ms:
        result = api_get("get_funding_rate_history", {
            "instrument_name": instrument_name,
            "start_timestamp": start_ms,
            "end_timestamp": current_end,
            "count": count_per_page,  # not a standard param, but try
        })
        time.sleep(0.15)

        if result is None:
            log.error("Failed to fetch funding rate page, stopping")
            break

        records = result if isinstance(result, list) else []

        if not records:
            break

        all_records.extend(records)
        page += 1

        # Records are newest-first, so walk backward
        if isinstance(records[0], dict):
            oldest_ts = min(r.get("timestamp", current_end) for r in records)
        else:
            oldest_ts = records[-1][0] if records else current_end

        if oldest_ts >= current_end:
            break  # no progress

        current_end = oldest_ts - 1

        if page % 5 == 0:
            dt = datetime.fromtimestamp(oldest_ts / 1000, tz=timezone.utc)
            log.info(f"  Page {page}: {len(all_records):,} records, back to {dt.date()}")

        if oldest_ts <= start_ms:
            break

    # Sort by timestamp ascending
    if all_records and isinstance(all_records[0], dict):
        all_records.sort(key=lambda x: x.get("timestamp", 0))

    data = {
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "instrument_name": instrument_name,
        "description": "8-hour funding rates for BTC-PERPETUAL",
        "count": len(all_records),
        "data": all_records,
    }

    if all_records:
        if isinstance(all_records[0], dict):
            ts_first = all_records[0].get("timestamp", 0)
            ts_last = all_records[-1].get("timestamp", 0)
        else:
            ts_first = all_records[0][0]
            ts_last = all_records[-1][0]
        data["first_timestamp"] = datetime.fromtimestamp(ts_first / 1000, tz=timezone.utc).isoformat()
        data["last_timestamp"] = datetime.fromtimestamp(ts_last / 1000, tz=timezone.utc).isoformat()

    with open(out_path, "w") as f:
        json.dump(data, f)

    log.info(f"  ✓ {len(all_records):,} funding rate records saved → {out_path}")
    print(f"\nFunding rates: {len(all_records):,} records")
    if all_records:
        print(f"  Range: {data.get('first_timestamp')} → {data.get('last_timestamp')}")


if __name__ == "__main__":
    main()
