#!/usr/bin/env python3
"""
generate_instrument_names.py — Generate all historical Deribit BTC option instrument names

Strategy:
  1. Download all historical delivery prices from get_delivery_prices (goes back to 2016)
  2. For each expiry date + settlement price, generate all plausible strike/type combos
  3. Output a text file of candidate instrument names for the bulk downloader

Deribit naming convention: BTC-DDMMMYY-STRIKE-C/P
  Example: BTC-31MAR23-28000-C

Strike intervals (approximate, vary with BTC price):
  BTC < $500      : 25, 50
  BTC $500-2k     : 100, 250
  BTC $2k-10k     : 250, 500, 1000
  BTC $10k-30k    : 500, 1000, 2500
  BTC $30k-80k    : 1000, 2000, 5000
  BTC > $80k      : 2000, 5000, 10000

Usage: python3 data_pipeline/generate_instrument_names.py
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
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
log = logging.getLogger("generate_instrument_names")

BASE_URL = "https://www.deribit.com/api/v2/public"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-rl-v2-data-pipeline/1.0"})

# Month abbreviations as used by Deribit
MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
    5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
    9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


def date_to_deribit(dt: datetime) -> str:
    """Convert a date to Deribit's format: DDMMMYY (e.g. 31MAR23)."""
    return f"{dt.day:02d}{MONTH_ABBR[dt.month]}{str(dt.year)[-2:]}"


def strike_intervals(price: float) -> list[int]:
    """Return the standard strike intervals for a given BTC price."""
    if price < 500:
        return [25, 50]
    elif price < 2000:
        return [100, 250, 500]
    elif price < 5000:
        return [250, 500, 1000]
    elif price < 10000:
        return [500, 1000, 2500]
    elif price < 20000:
        return [500, 1000, 2500, 5000]
    elif price < 50000:
        return [1000, 2000, 5000]
    elif price < 100000:
        return [1000, 2000, 5000]
    else:
        return [2000, 5000, 10000]


def round_to_interval(price: float, interval: int) -> int:
    """Round price to nearest multiple of interval."""
    return round(price / interval) * interval


def generate_strikes_for_price(price: float) -> list[int]:
    """Generate all reasonable strikes around a given BTC price."""
    strikes = set()
    intervals = strike_intervals(price)

    # Range: 30% to 250% of current price (covers most tradeable options)
    for interval in intervals:
        low = max(interval, int(price * 0.30))
        high = int(price * 2.50)
        strike = round_to_interval(low, interval)
        while strike <= high:
            if strike > 0:
                strikes.add(strike)
            strike += interval

    return sorted(strikes)


def download_all_delivery_prices() -> list[dict]:
    """Download all delivery prices with pagination. Returns list of {date, delivery_price}."""
    all_records = []
    offset = 0
    count = 100
    total = None

    while True:
        retries = 0
        while retries < 5:
            try:
                r = SESSION.get(
                    f"{BASE_URL}/get_delivery_prices",
                    params={"index_name": "btc_usd", "offset": offset, "count": count},
                    timeout=30,
                )
                if r.status_code == 429:
                    time.sleep(2 ** retries)
                    retries += 1
                    continue
                r.raise_for_status()
                result = r.json().get("result", {})
                records = result.get("data", [])
                total = result.get("records_total", total)
                break
            except Exception as e:
                time.sleep(2 ** retries)
                retries += 1
                records = []

        if not records:
            break

        all_records.extend(records)
        offset += len(records)

        if offset % 500 == 0:
            log.info(f"  Delivery prices: {offset}/{total or '?'}")

        if total and offset >= total:
            break

        time.sleep(0.1)

    log.info(f"Downloaded {len(all_records)} delivery price records")
    return all_records


def generate_all_instrument_names(delivery_prices: list[dict]) -> list[str]:
    """Generate all candidate instrument names from delivery prices."""
    all_names = set()

    for record in delivery_prices:
        try:
            date_str = record["date"]  # e.g. "2023-03-31"
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            price = float(record["delivery_price"])
        except (KeyError, ValueError) as e:
            continue

        if price <= 0:
            continue

        # Only process from 2019-03-01 onward (Deribit options launch)
        if dt < datetime(2019, 3, 1, tzinfo=timezone.utc):
            continue

        expiry_str = date_to_deribit(dt)
        strikes = generate_strikes_for_price(price)

        for strike in strikes:
            for option_type in ["C", "P"]:
                name = f"BTC-{expiry_str}-{strike}-{option_type}"
                all_names.add(name)

    return sorted(all_names)


def main():
    out_names_path = DERIBIT_DIR / "historical_instrument_names.txt"
    out_prices_path = DERIBIT_DIR / "delivery_prices.json"

    # Download delivery prices
    log.info("Downloading all delivery prices from Deribit …")
    delivery_prices = download_all_delivery_prices()

    if not delivery_prices:
        log.error("No delivery prices retrieved. Exiting.")
        sys.exit(1)

    # Save raw delivery prices
    with open(out_prices_path, "w") as f:
        json.dump({
            "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
            "count": len(delivery_prices),
            "data": delivery_prices,
        }, f)
    log.info(f"Saved {len(delivery_prices)} delivery prices → {out_prices_path}")

    # Generate all instrument names
    log.info("Generating historical instrument names …")
    all_names = generate_all_instrument_names(delivery_prices)

    # Also add the known live instruments
    live_file = sorted(DERIBIT_DIR.glob("instruments_*.json"), reverse=True)
    if live_file:
        with open(live_file[0]) as f:
            live_data = json.load(f)
        live_names = [i["instrument_name"] for i in live_data.get("instruments", [])]
        all_names_set = set(all_names) | set(live_names)
        all_names = sorted(all_names_set)
        log.info(f"Added {len(live_names)} live instrument names")

    # Save to file
    with open(out_names_path, "w") as f:
        for name in all_names:
            f.write(name + "\n")

    # Summary
    log.info("=" * 60)
    log.info(f"INSTRUMENT NAME GENERATION COMPLETE")
    log.info(f"  Delivery price records : {len(delivery_prices)}")
    log.info(f"  Total instrument names : {len(all_names):,}")
    log.info(f"  Saved to               : {out_names_path}")

    # Year breakdown
    by_year = {}
    for name in all_names:
        parts = name.split("-")
        if len(parts) == 4:
            exp = parts[1]
            # Last 2 chars of expiry = year (e.g. "23" → 2023)
            yr = "20" + exp[-2:]
            by_year[yr] = by_year.get(yr, 0) + 1
    log.info("  By year:")
    for yr in sorted(by_year):
        log.info(f"    {yr}: {by_year[yr]:,} instruments")
    log.info("=" * 60)

    return len(all_names)


if __name__ == "__main__":
    main()
