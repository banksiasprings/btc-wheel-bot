#!/usr/bin/env python3
"""
download_options_bulk.py — Bulk Deribit BTC options historical data downloader
Reads the instrument list from enumerate_instruments.py output and downloads:
  - Settlement prices via get_delivery_prices
  - Trade history via get_last_trades_by_instrument (OHLCV fallback)
  - OHLCV candles via get_tradingview_chart_data

Designed to be resumable: skips instruments already downloaded.
Run in background: nohup python3 data_pipeline/download_options_bulk.py &

Progress is logged to data/logs/download.log and data/logs/bulk_progress.json
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DERIBIT_DIR = ROOT / "data" / "raw" / "deribit"
TRADES_DIR = DERIBIT_DIR / "trades"
OHLCV_DIR = DERIBIT_DIR / "ohlcv"
LOG_DIR = ROOT / "data" / "logs"
PROGRESS_FILE = LOG_DIR / "bulk_progress.json"
for d in [DERIBIT_DIR, TRADES_DIR, OHLCV_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "download.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("download_options_bulk")

BASE_URL = "https://www.deribit.com/api/v2/public"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-rl-v2-data-pipeline/1.0"})

SLEEP_BETWEEN_CALLS = 0.12  # ~8 req/s, well under 20 req/s limit


# ── helpers ───────────────────────────────────────────────────────────────────

def api_get(endpoint: str, params: dict, max_retries: int = 5) -> dict | None:
    """GET with exponential backoff on 429/5xx. Returns None (no retry) on 400/404."""
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params, timeout=20)
            if resp.status_code == 429:
                wait = min(30, 2 ** attempt)
                time.sleep(wait)
                continue
            if resp.status_code in (400, 404):
                # Instrument not found — not retryable
                return None
            if resp.status_code >= 500:
                wait = min(20, 2 ** attempt)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("result")
        except requests.exceptions.RequestException as e:
            wait = min(15, 2 ** attempt)
            time.sleep(wait)
    return None


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed": [], "failed": [], "total_trades": 0, "start_time": datetime.now(tz=timezone.utc).isoformat()}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def find_instrument_file() -> Path | None:
    """Find the most recent instruments JSON file."""
    files = sorted(DERIBIT_DIR.glob("instruments_*.json"), reverse=True)
    return files[0] if files else None


def load_instruments_from_names_file() -> list[dict]:
    """Load instrument names from the generated historical names text file."""
    names_file = DERIBIT_DIR / "historical_instrument_names.txt"
    if not names_file.exists():
        return []
    with open(names_file) as f:
        names = [line.strip() for line in f if line.strip()]
    return [{"instrument_name": n} for n in names]


def load_instruments(path: Path) -> list[dict]:
    """Load instruments from JSON file, returning all option instruments."""
    with open(path) as f:
        data = json.load(f)
    instruments = data.get("instruments", [])
    # Include all — both expired and live (we'll skip live ones that already have data)
    return instruments


def download_ohlcv(instrument_name: str, out_path: Path) -> int:
    """Download daily OHLCV candles via get_tradingview_chart_data."""
    # Resolution 1D = 1440 minutes
    result = api_get("get_tradingview_chart_data", {
        "instrument_name": instrument_name,
        "start_timestamp": 1483228800000,  # 2017-01-01
        "end_timestamp": int(time.time() * 1000),
        "resolution": "1D",
    })
    time.sleep(SLEEP_BETWEEN_CALLS)

    if result is None or result.get("status") == "no_data":
        return 0

    data = {
        "instrument_name": instrument_name,
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "ohlcv": result,
    }
    with open(out_path, "w") as f:
        json.dump(data, f)

    ticks = len(result.get("ticks", []))
    return ticks


def download_trades(instrument_name: str, out_path: Path) -> int:
    """Download recent trades via get_last_trades_by_instrument."""
    result = api_get("get_last_trades_by_instrument", {
        "instrument_name": instrument_name,
        "count": 1000,
        "sorting": "desc",
    })
    time.sleep(SLEEP_BETWEEN_CALLS)

    if result is None:
        return 0

    trades = result.get("trades", [])
    data = {
        "instrument_name": instrument_name,
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "trade_count": len(trades),
        "trades": trades,
    }
    with open(out_path, "w") as f:
        json.dump(data, f)
    return len(trades)


def process_instrument(instrument: dict, progress: dict) -> tuple[int, int]:
    """Process one instrument. Returns (ohlcv_ticks, trade_count)."""
    name = instrument["instrument_name"]
    ohlcv_path = OHLCV_DIR / f"{name}.json"
    trades_path = TRADES_DIR / f"{name}.json"

    ohlcv_ticks = 0
    trade_count = 0

    if not ohlcv_path.exists():
        ohlcv_ticks = download_ohlcv(name, ohlcv_path)

    if not trades_path.exists():
        trade_count = download_trades(name, trades_path)

    return ohlcv_ticks, trade_count


def main():
    log.info("=" * 60)
    log.info("BTC Options Bulk Downloader — Phase 1 Data Pipeline")
    log.info(f"PID: {os.getpid()}")
    log.info("=" * 60)

    # Prefer the comprehensive generated names file; fall back to API JSON
    names_file = DERIBIT_DIR / "historical_instrument_names.txt"
    if names_file.exists():
        log.info(f"Loading from generated historical instrument names …")
        instruments = load_instruments_from_names_file()
    else:
        inst_file = find_instrument_file()
        if inst_file is None:
            log.error("No instruments file found. Run generate_instrument_names.py first.")
            sys.exit(1)
        log.info(f"Loading instruments from {inst_file.name} …")
        instruments = load_instruments(inst_file)

    log.info(f"Loaded {len(instruments):,} instruments to process")

    if not instruments:
        log.error("No expired instruments found in file.")
        sys.exit(1)

    # Load or init progress
    progress = load_progress()
    completed_set = set(progress.get("completed", []))
    failed_set = set(progress.get("failed", []))
    total_trades = progress.get("total_trades", 0)

    # Filter to instruments not yet completed
    remaining = [i for i in instruments if i["instrument_name"] not in completed_set]
    log.info(f"Already completed: {len(completed_set):,} | Remaining: {len(remaining):,} | Failed: {len(failed_set)}")

    n_total = len(instruments)
    n_done = len(completed_set)
    start_time = time.time()
    last_save = start_time

    for idx, instrument in enumerate(remaining):
        name = instrument["instrument_name"]
        try:
            ohlcv_ticks, trade_count = process_instrument(instrument, progress)
            completed_set.add(name)
            n_done += 1
            total_trades += trade_count

            # Log every 50 instruments
            if (idx + 1) % 50 == 0 or idx == 0:
                elapsed = time.time() - start_time
                rate = (idx + 1) / elapsed if elapsed > 0 else 0
                remaining_n = len(remaining) - (idx + 1)
                eta_sec = remaining_n / rate if rate > 0 else 0
                eta_h = eta_sec / 3600

                log.info(
                    f"Progress: {n_done}/{n_total} ({100*n_done/n_total:.1f}%) | "
                    f"Trades: {total_trades:,} | "
                    f"Rate: {rate:.1f}/s | "
                    f"ETA: {eta_h:.1f}h"
                )

            # Save progress every 25 instruments (count-based) or 60 seconds
            if (idx + 1) % 25 == 0 or time.time() - last_save > 30:
                progress["completed"] = list(completed_set)
                progress["failed"] = list(failed_set)
                progress["total_trades"] = total_trades
                progress["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
                save_progress(progress)
                last_save = time.time()

        except KeyboardInterrupt:
            log.info("Interrupted. Saving progress …")
            break
        except Exception as e:
            log.error(f"Error processing {name}: {e}")
            failed_set.add(name)
            time.sleep(1)

    # Final save
    progress["completed"] = list(completed_set)
    progress["failed"] = list(failed_set)
    progress["total_trades"] = total_trades
    progress["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    progress["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    save_progress(progress)

    log.info("=" * 60)
    log.info(f"BULK DOWNLOAD COMPLETE (or interrupted)")
    log.info(f"  Completed: {len(completed_set):,}/{n_total:,} instruments")
    log.info(f"  Failed   : {len(failed_set):,}")
    log.info(f"  Trades   : {total_trades:,}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
