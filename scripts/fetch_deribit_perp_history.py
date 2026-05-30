#!/usr/bin/env python3
"""
fetch_deribit_perp_history.py — Deribit BTC-PERPETUAL hourly history downloader.

Pulls two endpoints and joins them on the hourly grid:
  • get_tradingview_chart_data → OHLCV (close = perp mark close)
  • get_funding_rate_history   → hourly index_price snapshots

Writes:  data/raw/deribit/btc_perp_1h.csv

Columns:
    timestamp_utc, timestamp_ms, open, high, low, close, volume, volume_usd,
    mark_price, index_price, index_source, basis_abs, basis_pct

  - mark_price   = close                              (perp mark, USD)
  - index_price  = Deribit BTC composite index, USD   (joined from funding rates)
  - index_source = "funding_snapshot" if the funding endpoint emitted a sample
                   inside this hour, else "forward_fill_<N>h" where N is the
                   age in hours of the most recent snapshot (≤ 6h tolerance).
                   Blank if no snapshot within tolerance.
  - basis_abs    = mark_price - index_price           (USD)
  - basis_pct    = basis_abs / index_price            (unitless fraction)
    multiply by 10_000 for bps.

Why forward-fill: Deribit's `get_funding_rate_history` endpoint emits one
snapshot per funding-rate change (typical cadence ~1–3 h, denser in vol
spikes). To produce a row per perp bar we forward-fill the most recent
snapshot up to a 6-hour age. Rows that would be filled from older data
get blank index/basis fields. Use `index_source == "funding_snapshot"`
to filter to strict-only samples downstream.

Idempotent: if the CSV exists, the last timestamp_ms is read and fetching
resumes from there. Safe to re-run as a cron.

Spec: bsf-research-briefs/specs/03-basis-arb-spec.md §8 (Gate 3 data plan).
Public endpoints only — no API keys needed.

Usage:
    python3 scripts/fetch_deribit_perp_history.py
    python3 scripts/fetch_deribit_perp_history.py --start 2019-04-01
    python3 scripts/fetch_deribit_perp_history.py --end 2024-12-31
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
OUT_DIR = ROOT / "data" / "raw" / "deribit"
LOG_DIR = ROOT / "data" / "logs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "btc_perp_1h.csv"

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "fetch_deribit_perp.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("fetch_deribit_perp")

BASE_URL = "https://www.deribit.com/api/v2/public"
INSTRUMENT = "BTC-PERPETUAL"
HOUR_MS = 3_600_000

# chart_data hard-caps at ~5000 ticks per call → 180 days * 24h = 4320 < 5000.
CHART_CHUNK_DAYS = 180
# funding_rate_history silently caps at ~744 records per call (Deribit's hidden
# limit) and returns the *most recent* slice when over. 20 days × 24 = 480
# records leaves headroom for ≥hourly cadence without hitting the cap.
FUNDING_CHUNK_DAYS = 20

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-wheel-bot/basis-arb-fetch/1.0"})

CSV_HEADERS = [
    "timestamp_utc", "timestamp_ms",
    "open", "high", "low", "close",
    "volume", "volume_usd",
    "mark_price", "index_price", "index_source", "basis_abs", "basis_pct",
]

# Maximum age of a funding snapshot we are willing to forward-fill.
INDEX_FILL_TOLERANCE_HOURS = 6


def ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def iso_utc(ts_ms: int) -> str:
    """RFC 3339 UTC stamp for the start of the hour."""
    return datetime.fromtimestamp(
        ts_ms / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def api_get(method: str, params: dict, max_retries: int = 8) -> Any:
    """GET a public Deribit method with exponential backoff."""
    url = f"{BASE_URL}/{method}"
    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = min(60, 2 ** attempt)
                log.warning(f"429 rate-limit; sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("result")
        except requests.exceptions.RequestException as exc:
            wait = min(30, 2 ** attempt)
            log.warning(f"{method} error: {exc}; retry {attempt+1}/{max_retries} in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"{method} failed after {max_retries} retries")


def fetch_chart_chunk(start_ms: int, end_ms: int) -> list[dict[str, float]]:
    """One page of hourly OHLCV from Deribit's tradingview endpoint."""
    result = api_get("get_tradingview_chart_data", {
        "instrument_name": INSTRUMENT,
        "start_timestamp": start_ms,
        "end_timestamp":   end_ms,
        "resolution":      "60",
    })
    if not result or result.get("status") == "no_data":
        return []
    ticks   = result.get("ticks",   [])
    opens   = result.get("open",    [])
    highs   = result.get("high",    [])
    lows    = result.get("low",     [])
    closes  = result.get("close",   [])
    volumes = result.get("volume",  [])
    costs   = result.get("cost",    [])
    rows: list[dict[str, float]] = []
    for i, ts in enumerate(ticks):
        rows.append({
            "timestamp_ms": int(ts),
            "open":   float(opens[i]),
            "high":   float(highs[i]),
            "low":    float(lows[i]),
            "close":  float(closes[i]),
            "volume": float(volumes[i]) if i < len(volumes) else 0.0,
            "cost":   float(costs[i])   if i < len(costs)   else 0.0,
        })
    return rows


def fetch_funding_chunk(start_ms: int, end_ms: int) -> list[dict[str, float]]:
    """One page of funding-rate snapshots; we keep only timestamp + index_price."""
    raw = api_get("get_funding_rate_history", {
        "instrument_name": INSTRUMENT,
        "start_timestamp": start_ms,
        "end_timestamp":   end_ms,
    })
    if not raw:
        return []
    return [
        {"timestamp_ms": int(r["timestamp"]),
         "index_price":  float(r["index_price"])}
        for r in raw
        if r.get("index_price") is not None
    ]


def fetch_perp_ohlcv(start_ms: int, end_ms: int) -> list[dict[str, float]]:
    """Page through chart_data; merge chunks and sort/dedupe by timestamp."""
    rows: list[dict[str, float]] = []
    cursor = start_ms
    chunk_ms = CHART_CHUNK_DAYS * 86_400_000
    pages = 0
    while cursor < end_ms:
        page_end = min(cursor + chunk_ms, end_ms)
        chunk = fetch_chart_chunk(cursor, page_end)
        if chunk:
            rows.extend(chunk)
        pages += 1
        if pages % 5 == 0:
            log.info(
                f"  OHLCV page {pages}: {len(rows):,} bars so far "
                f"(through {iso_utc(page_end)})"
            )
        cursor = page_end + 1
        time.sleep(0.1)
    # Dedupe (chunks may overlap by one tick at boundaries) and sort.
    seen: dict[int, dict[str, float]] = {}
    for r in rows:
        seen[r["timestamp_ms"]] = r
    return sorted(seen.values(), key=lambda r: r["timestamp_ms"])


def fetch_index_samples(start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """
    Fetch funding-rate snapshots across the range and return a chronologically
    sorted list of (timestamp_ms, index_price) tuples. The endpoint typically
    emits ~1 sample per 1–3 hours (denser in vol spikes, sparser in calm).

    The caller is responsible for any per-hour join / forward-fill.
    """
    samples: list[tuple[int, float]] = []
    cursor = start_ms
    chunk_ms = FUNDING_CHUNK_DAYS * 86_400_000
    pages = 0
    while cursor < end_ms:
        page_end = min(cursor + chunk_ms, end_ms)
        chunk = fetch_funding_chunk(cursor, page_end)
        samples.extend((r["timestamp_ms"], r["index_price"]) for r in chunk)
        pages += 1
        if pages % 5 == 0:
            log.info(
                f"  index page {pages}: {len(samples):,} samples "
                f"(through {iso_utc(page_end)})"
            )
        cursor = page_end + 1
        time.sleep(0.1)
    # Sort + dedupe by exact timestamp.
    seen: dict[int, float] = {}
    for ts, idx in samples:
        seen[ts] = idx
    return sorted(seen.items())


def read_resume_cursor() -> int | None:
    """Return last timestamp_ms in the CSV, or None if file is missing/empty."""
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
        # Skip the bar we already have; start at the next hourly slot.
        fetch_start_ms = resume_ts + HOUR_MS
        log.info(
            f"Resuming from {iso_utc(fetch_start_ms)} "
            f"(last cached bar: {iso_utc(resume_ts)})"
        )
    else:
        fetch_start_ms = ts_ms(start_dt)
        log.info(f"Fresh fetch starting at {iso_utc(fetch_start_ms)}")

    fetch_end_ms = ts_ms(end_dt)
    if fetch_start_ms >= fetch_end_ms:
        log.info("Already up-to-date; nothing to fetch.")
        return 0

    t0 = time.time()

    log.info(f"Fetching BTC-PERPETUAL OHLCV "
             f"{iso_utc(fetch_start_ms)} → {iso_utc(fetch_end_ms)} …")
    ohlcv = fetch_perp_ohlcv(fetch_start_ms, fetch_end_ms)
    log.info(f"  → {len(ohlcv):,} hourly bars")

    log.info(f"Fetching Deribit BTC index price (funding-rate endpoint) …")
    index_samples = fetch_index_samples(fetch_start_ms, fetch_end_ms)
    log.info(f"  → {len(index_samples):,} raw index samples "
             f"(median spacing ≈ "
             f"{((fetch_end_ms - fetch_start_ms) / max(1, len(index_samples))) / HOUR_MS:.1f}h)")

    # Forward-fill the index onto the perp's hourly grid, capped at
    # INDEX_FILL_TOLERANCE_HOURS. Walk both sorted lists in one pass.
    #
    # Timestamp alignment: a perp bar with tick=T covers [T, T+1h) and its
    # `close` is the mark at T+1h. A funding-rate sample with timestamp=T'
    # is the index at T'. So the index that pairs with a bar's close is
    # the funding sample at T' = T + 1h. We anchor the join on bar_close.
    tolerance_ms = INDEX_FILL_TOLERANCE_HOURS * HOUR_MS
    rows: list[dict[str, Any]] = []
    snapshot_count = 0
    fill_count = 0
    gap_count = 0
    si = 0
    last_idx_ts: int | None = None
    last_idx_val: float | None = None
    for bar in ohlcv:
        ts = bar["timestamp_ms"]
        bar_close_ts = ts + HOUR_MS

        # Advance the index cursor to the latest sample with funding_ts <= bar_close_ts.
        while si < len(index_samples) and index_samples[si][0] <= bar_close_ts:
            last_idx_ts, last_idx_val = index_samples[si]
            si += 1

        if last_idx_val is None or last_idx_ts is None:
            idx: float | None = None
            source = ""
        else:
            age_ms = bar_close_ts - last_idx_ts
            if age_ms == 0:
                idx = last_idx_val
                source = "funding_snapshot"
                snapshot_count += 1
            elif 0 < age_ms <= tolerance_ms:
                idx = last_idx_val
                hours_old = max(1, int(age_ms // HOUR_MS) + (1 if age_ms % HOUR_MS else 0))
                source = f"forward_fill_{hours_old}h"
                fill_count += 1
            else:
                idx = None
                source = ""

        if idx is None:
            gap_count += 1
            basis_abs = None
            basis_pct = None
        else:
            basis_abs = bar["close"] - idx
            basis_pct = basis_abs / idx if idx else None

        rows.append({
            "timestamp_utc": iso_utc(ts),
            "timestamp_ms":  ts,
            "open":          bar["open"],
            "high":          bar["high"],
            "low":           bar["low"],
            "close":         bar["close"],
            "volume":        bar["volume"],
            "volume_usd":    bar["cost"],
            "mark_price":    bar["close"],
            "index_price":   idx if idx is not None else "",
            "index_source":  source,
            "basis_abs":     basis_abs if basis_abs is not None else "",
            "basis_pct":     basis_pct if basis_pct is not None else "",
        })

    if not rows:
        log.info("No new bars to append.")
        return 0

    write_rows(rows, append=append)
    elapsed = time.time() - t0

    first_ts = rows[0]["timestamp_ms"]
    last_ts  = rows[-1]["timestamp_ms"]
    expected = (last_ts - first_ts) // HOUR_MS + 1
    missing_bars = max(0, expected - len(rows))

    print("\n" + "=" * 72)
    print("DERIBIT BTC-PERPETUAL FETCH COMPLETE")
    print(f"  Output      : {OUT_PATH.relative_to(ROOT)}")
    print(f"  Mode        : {'append' if append else 'fresh write'}")
    print(f"  New rows    : {len(rows):,}")
    print(f"  Range (new) : {iso_utc(first_ts)} → {iso_utc(last_ts)}")
    print(f"  Index source: {snapshot_count:,} funding_snapshot  "
          f"{fill_count:,} forward_fill (≤{INDEX_FILL_TOLERANCE_HOURS}h)  "
          f"{gap_count:,} blank")
    print(f"  Bar gaps    : {missing_bars:,} missing hourly bars in the new range")
    print(f"  Elapsed     : {elapsed:.1f}s")
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
