#!/usr/bin/env python3
"""
download_deribit_trades.py — Deribit per-instrument historical trade downloader
Downloads all historical trades for a single instrument with full pagination.

Usage:
  python3 data_pipeline/download_deribit_trades.py BTC-31MAR23-30000-P
  python3 data_pipeline/download_deribit_trades.py --instrument BTC-31MAR23-30000-P
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
TRADES_DIR = ROOT / "data" / "raw" / "deribit" / "trades"
LOG_DIR = ROOT / "data" / "logs"
TRADES_DIR.mkdir(parents=True, exist_ok=True)
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
log = logging.getLogger("download_trades")

BASE_URL = "https://www.deribit.com/api/v2/public"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-rl-v2-data-pipeline/1.0"})

COUNT_PER_PAGE = 1000  # max allowed by Deribit


def fetch_page(instrument_name: str, start_seq: int | None = None, end_seq: int | None = None) -> dict:
    """Fetch one page of trades. Uses get_last_trades_by_instrument_and_time."""
    url = f"{BASE_URL}/get_last_trades_by_instrument_and_time"
    params = {
        "instrument_name": instrument_name,
        "count": COUNT_PER_PAGE,
        "sorting": "asc",
        # Fetch from beginning of time to now
        "start_timestamp": 1483228800000,  # 2017-01-01 00:00 UTC in ms
        "end_timestamp": int(time.time() * 1000),
    }
    if start_seq is not None:
        params["start_seq"] = start_seq

    retries = 0
    max_retries = 8

    while retries < max_retries:
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = min(60, 2 ** retries)
                log.warning(f"Rate limited (429). Sleeping {wait}s …")
                time.sleep(wait)
                retries += 1
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError as e:
            wait = min(60, 2 ** retries)
            log.warning(f"Connection error: {e}. Retrying in {wait}s …")
            time.sleep(wait)
            retries += 1
        except requests.exceptions.RequestException as e:
            wait = min(60, 2 ** retries)
            log.warning(f"Request error: {e}. Retrying in {wait}s …")
            time.sleep(wait)
            retries += 1

    raise RuntimeError(f"Failed after {max_retries} retries for {instrument_name}")


def download_instrument_trades(instrument_name: str) -> dict:
    """Download all trades for an instrument. Returns metadata dict."""
    out_path = TRADES_DIR / f"{instrument_name}.json"

    # Resume if partial file exists with metadata
    if out_path.exists():
        log.info(f"Output file already exists: {out_path} — skipping (delete to re-download)")
        with open(out_path) as f:
            existing = json.load(f)
        return existing.get("metadata", {})

    log.info(f"Downloading trades for {instrument_name} …")

    all_trades = []
    page_num = 0
    has_more = True
    last_seq = None

    while has_more:
        result = fetch_page(instrument_name, start_seq=last_seq)

        if "result" not in result:
            log.error(f"Unexpected response: {result}")
            break

        trades = result["result"].get("trades", [])
        has_more = result["result"].get("has_more", False)

        if trades:
            all_trades.extend(trades)
            last_seq = trades[-1].get("trade_seq")
            if last_seq:
                last_seq += 1  # start next page after last seen

        page_num += 1
        log.info(
            f"  Page {page_num}: {len(trades)} trades fetched "
            f"(total so far: {len(all_trades):,}, has_more={has_more})"
        )

        if not trades:
            break

        # Be polite to the API
        time.sleep(0.05)

    # Build output
    metadata = {
        "instrument_name": instrument_name,
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_trades": len(all_trades),
        "pages_fetched": page_num,
    }
    if all_trades:
        metadata["first_trade_timestamp"] = all_trades[0].get("timestamp")
        metadata["last_trade_timestamp"] = all_trades[-1].get("timestamp")

    output = {"metadata": metadata, "trades": all_trades}
    with open(out_path, "w") as f:
        json.dump(output, f)

    log.info(f"  ✓ Saved {len(all_trades):,} trades → {out_path}")
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Download Deribit trades for one instrument")
    parser.add_argument(
        "instrument",
        nargs="?",
        help="Instrument name, e.g. BTC-31MAR23-30000-P",
    )
    parser.add_argument("--instrument", dest="instrument_flag", help="Instrument name (alternative flag form)")
    args = parser.parse_args()

    instrument_name = args.instrument or args.instrument_flag
    if not instrument_name:
        parser.error("Must provide an instrument name, e.g. BTC-31MAR23-30000-P")

    meta = download_instrument_trades(instrument_name)

    print(f"\nDone. {meta.get('total_trades', 0):,} trades downloaded.")


if __name__ == "__main__":
    main()
