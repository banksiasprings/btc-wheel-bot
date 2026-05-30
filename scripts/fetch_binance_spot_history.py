#!/usr/bin/env python3
"""
fetch_binance_spot_history.py — Binance BTCUSDT hourly OHLCV downloader.

Writes:  data/raw/binance/btc_spot_1h.csv

This is the **cross-reference spot price** for the Basis Arb spec's
open question #2 — Gate 3 compares Deribit's own composite index against
Binance spot to quantify cross-reference noise. The answer determines
whether the bot can go live single-venue (Deribit-only) or whether it
needs cross-venue spot to be honest.

Columns:
    timestamp_utc, timestamp_ms, open, high, low, close, volume, volume_usd,
    num_trades

  - volume     : base BTC volume traded that hour
  - volume_usd : quote USDT notional (Binance "quote_volume")

Idempotent: if the CSV exists, the last timestamp_ms is read and fetching
resumes from there. Safe to re-run as a cron.

Public endpoint only — no API keys needed.

Note: a separate Binance spot file already exists at
`data/raw/spot/btc_1h.csv` (used by other backtests). It uses a different
schema (raw Binance kline tuples). We write a fresh, semantically-tagged
file here so the basis-arb dataset has predictable column names and is
not coupled to whatever the other consumers expect.

Usage:
    python3 scripts/fetch_binance_spot_history.py
    python3 scripts/fetch_binance_spot_history.py --start 2019-04-01
    python3 scripts/fetch_binance_spot_history.py --end 2024-12-31
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "raw" / "binance"
LOG_DIR = ROOT / "data" / "logs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "btc_spot_1h.csv"

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "fetch_binance_spot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("fetch_binance_spot")

BINANCE_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "1h"
HOUR_MS = 3_600_000
PAGE_LIMIT = 1000  # Binance kline max per call

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-wheel-bot/basis-arb-fetch/1.0"})

CSV_HEADERS = [
    "timestamp_utc", "timestamp_ms",
    "open", "high", "low", "close",
    "volume", "volume_usd", "num_trades",
]


def ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def iso_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(
        ts_ms / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_klines(start_ms: int, end_ms: int) -> list[list[Any]]:
    """One page of Binance klines, with exponential backoff on 429/418."""
    for attempt in range(8):
        try:
            resp = SESSION.get(
                BINANCE_URL,
                params={
                    "symbol":    SYMBOL,
                    "interval":  INTERVAL,
                    "startTime": start_ms,
                    "endTime":   end_ms,
                    "limit":     PAGE_LIMIT,
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = min(60, 2 ** attempt)
                log.warning(f"429 rate-limit; sleeping {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code == 418:
                log.error("418 IP-banned; sleeping 60s")
                time.sleep(60)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            wait = min(30, 2 ** attempt)
            log.warning(f"Request error: {exc}; retry {attempt+1}/8 in {wait}s")
            time.sleep(wait)
    raise RuntimeError("Binance klines fetch failed after 8 retries")


def read_resume_cursor() -> int | None:
    if not OUT_PATH.exists() or OUT_PATH.stat().st_size == 0:
        return None
    last_ts: int | None = None
    with open(OUT_PATH, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                last_ts = int(row["timestamp_ms"])
            except (KeyError, ValueError):
                continue
    return last_ts


def write_rows(rows: list[dict[str, Any]], append: bool) -> None:
    mode = "a" if append else "w"
    with open(OUT_PATH, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not append:
            writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--start", default="2019-04-01",
                    help="UTC start date YYYY-MM-DD (default 2019-04-01).")
    ap.add_argument("--end", default=None,
                    help="UTC end date YYYY-MM-DD (default: yesterday).")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(tz=timezone.utc)
    default_end = (now - timedelta(days=1)).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = (datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    ) if args.end else default_end)

    resume_ts = read_resume_cursor()
    append = resume_ts is not None
    if append:
        cursor = resume_ts + HOUR_MS
        log.info(
            f"Resuming from {iso_utc(cursor)} "
            f"(last cached bar: {iso_utc(resume_ts)})"
        )
    else:
        cursor = ts_ms(start_dt)
        log.info(f"Fresh fetch starting at {iso_utc(cursor)}")

    end_ms = ts_ms(end_dt)
    if cursor >= end_ms:
        log.info("Already up-to-date; nothing to fetch.")
        return 0

    t0 = time.time()
    rows: list[dict[str, Any]] = []
    pages = 0
    page_span_ms = PAGE_LIMIT * HOUR_MS

    while cursor < end_ms:
        page_end = min(cursor + page_span_ms, end_ms)
        klines = fetch_klines(cursor, page_end)
        if not klines:
            break
        for k in klines:
            # k = [open_time, open, high, low, close, volume, close_time,
            #      quote_volume, num_trades, taker_buy_base, taker_buy_quote, ignore]
            ts = int(k[0])
            rows.append({
                "timestamp_utc": iso_utc(ts),
                "timestamp_ms":  ts,
                "open":          float(k[1]),
                "high":          float(k[2]),
                "low":           float(k[3]),
                "close":         float(k[4]),
                "volume":        float(k[5]),
                "volume_usd":    float(k[7]),
                "num_trades":    int(k[8]),
            })
        last_ts = int(klines[-1][0])
        cursor = last_ts + HOUR_MS
        pages += 1
        if pages % 10 == 0:
            log.info(f"  page {pages}: {len(rows):,} bars (through {iso_utc(last_ts)})")
        time.sleep(0.05)

    if not rows:
        log.info("No new bars returned.")
        return 0

    write_rows(rows, append=append)
    elapsed = time.time() - t0

    first_ts = rows[0]["timestamp_ms"]
    last_ts  = rows[-1]["timestamp_ms"]
    expected = (last_ts - first_ts) // HOUR_MS + 1
    missing  = max(0, expected - len(rows))

    print("\n" + "=" * 72)
    print("BINANCE BTCUSDT SPOT FETCH COMPLETE")
    print(f"  Output      : {OUT_PATH.relative_to(ROOT)}")
    print(f"  Mode        : {'append' if append else 'fresh write'}")
    print(f"  New rows    : {len(rows):,}")
    print(f"  Range (new) : {iso_utc(first_ts)} → {iso_utc(last_ts)}")
    print(f"  Bar gaps    : {missing:,} missing hourly bars in the new range")
    print(f"  Elapsed     : {elapsed:.1f}s")
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
