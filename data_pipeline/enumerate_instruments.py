#!/usr/bin/env python3
"""
enumerate_instruments.py — Deribit BTC options instrument enumerator
Fetches all expired and live BTC option instruments from Deribit public API.
Saves to data/raw/deribit/instruments_YYYYMMDD.json

Usage: python3 data_pipeline/enumerate_instruments.py
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
DATA_DIR = ROOT / "data" / "raw" / "deribit"
LOG_DIR = ROOT / "data" / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
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
log = logging.getLogger("enumerate_instruments")

BASE_URL = "https://www.deribit.com/api/v2/public"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-rl-v2-data-pipeline/1.0"})


def fetch_instruments(currency: str = "BTC", kind: str = "option", expired: bool = False) -> list:
    """Fetch all instruments, handling pagination and rate limits."""
    url = f"{BASE_URL}/get_instruments"
    params = {
        "currency": currency,
        "kind": kind,
        "expired": str(expired).lower(),
    }
    retries = 0
    max_retries = 8

    while retries < max_retries:
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** retries
                log.warning(f"Rate limited (429). Sleeping {wait}s …")
                time.sleep(wait)
                retries += 1
                continue
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                return data["result"]
            log.error(f"Unexpected response structure: {list(data.keys())}")
            return []
        except requests.exceptions.RequestException as e:
            wait = 2 ** retries
            log.warning(f"Request error: {e}. Retrying in {wait}s …")
            time.sleep(wait)
            retries += 1

    raise RuntimeError(f"Failed to fetch instruments after {max_retries} retries")


def parse_expiry_date(instrument: dict) -> datetime | None:
    """Parse expiration timestamp (ms) to datetime."""
    ts_ms = instrument.get("expiration_timestamp")
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def main():
    today_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    out_path = DATA_DIR / f"instruments_{today_str}.json"

    log.info("Fetching EXPIRED BTC options instruments …")
    expired = fetch_instruments(expired=True)
    log.info(f"  → {len(expired):,} expired instruments")
    time.sleep(0.5)

    log.info("Fetching LIVE BTC options instruments …")
    live = fetch_instruments(expired=False)
    log.info(f"  → {len(live):,} live instruments")

    all_instruments = expired + live

    # ── analysis ──────────────────────────────────────────────────────────────
    expiry_dates = []
    puts = calls = 0
    for inst in all_instruments:
        dt = parse_expiry_date(inst)
        if dt:
            expiry_dates.append(dt)
        kind = inst.get("option_type", "")
        if kind == "put":
            puts += 1
        elif kind == "call":
            calls += 1

    if expiry_dates:
        earliest = min(expiry_dates)
        latest = max(expiry_dates)
    else:
        earliest = latest = None

    summary = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_instruments": len(all_instruments),
        "expired_count": len(expired),
        "live_count": len(live),
        "puts": puts,
        "calls": calls,
        "earliest_expiry": earliest.isoformat() if earliest else None,
        "latest_expiry": latest.isoformat() if latest else None,
    }

    output = {"summary": summary, "instruments": all_instruments}

    with open(out_path, "w") as f:
        json.dump(output, f)

    log.info("=" * 60)
    log.info(f"SUMMARY")
    log.info(f"  Total instruments : {len(all_instruments):,}")
    log.info(f"  Expired           : {len(expired):,}")
    log.info(f"  Live              : {len(live):,}")
    log.info(f"  Puts / Calls      : {puts:,} / {calls:,}")
    log.info(f"  Earliest expiry   : {earliest}")
    log.info(f"  Latest expiry     : {latest}")
    log.info(f"  Saved to          : {out_path}")
    log.info("=" * 60)

    # Also write a simple CSV instrument list for easy inspection
    csv_path = DATA_DIR / f"instrument_names_{today_str}.txt"
    with open(csv_path, "w") as f:
        for inst in sorted(all_instruments, key=lambda x: x.get("instrument_name", "")):
            f.write(inst.get("instrument_name", "") + "\n")
    log.info(f"Instrument name list: {csv_path}")

    return summary


if __name__ == "__main__":
    main()
