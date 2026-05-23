#!/usr/bin/env python3
"""
download_onchain.py — Free on-chain metrics + Fear & Greed index
Downloads:
  - Fear & Greed Index (alternative.me) → data/raw/onchain/fear_greed.json
  - Coin Metrics Community API (no key required) → data/raw/onchain/coin_metrics_daily.csv
    Metrics: CapMrktCurUSD, CapRealUSD, AdrActCnt, TxCnt, NVTAdj
    Derived: MVRV = CapMrktCurUSD / CapRealUSD
  - Computed 30-day Realized Volatility from spot → data/raw/onchain/rv_from_spot.csv

No API keys required.

Usage: python3 data_pipeline/download_onchain.py
"""

import csv
import json
import logging
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ONCHAIN_DIR = ROOT / "data" / "raw" / "onchain"
SPOT_DIR = ROOT / "data" / "raw" / "spot"
LOG_DIR = ROOT / "data" / "logs"
ONCHAIN_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "download.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("download_onchain")

SESSION = requests.Session()
# Coin Metrics community API blocks custom user agents — use curl/browser UA
SESSION.headers.update({"User-Agent": "curl/7.88.1"})


def api_get(url, params=None, max_retries=5):
    for attempt in range(max_retries):
        try:
            r = SESSION.get(url, params=params or {}, timeout=30)
            if r.status_code == 429:
                wait = 2 ** attempt
                log.warning(f"  Rate limited — waiting {wait}s …")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log.warning(f"  Request error (attempt {attempt + 1}/{max_retries}): {e}")
            time.sleep(2 ** attempt)
    return None


# ── Fear & Greed Index ────────────────────────────────────────────────────────

def download_fear_greed():
    out = ONCHAIN_DIR / "fear_greed.json"
    if out.exists() and out.stat().st_size > 50_000:
        log.info("Fear & Greed: already downloaded. Skipping.")
        with open(out) as f:
            d = json.load(f)
        return d.get("count", 0)

    log.info("Downloading Fear & Greed index (alternative.me) …")
    r = api_get("https://api.alternative.me/fng/", {"limit": 0, "format": "json"})
    if r is None:
        log.error("Failed to fetch Fear & Greed data")
        return 0

    data = r.json()
    records = data.get("data", [])

    for rec in records:
        ts = int(rec.get("timestamp", 0))
        rec["date"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    records.sort(key=lambda x: int(x.get("timestamp", 0)))

    output = {
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "alternative.me/fng",
        "description": "Fear & Greed index: 0=extreme fear, 100=extreme greed",
        "count": len(records),
        "data": records,
    }
    if records:
        output["first_date"] = records[0]["date"]
        output["last_date"] = records[-1]["date"]

    with open(out, "w") as f:
        json.dump(output, f)

    log.info(f"  Fear & Greed: {len(records):,} records ({output.get('first_date')} → {output.get('last_date')})")
    return len(records)


# ── Coin Metrics Community API ────────────────────────────────────────────────
# Base URL: https://community-api.coinmetrics.io/v4/
# No API key required for community tier.
#
# Community tier has 31 metrics. We fetch what's actually available:
#   CapMVRVCur     — MVRV ratio (market cap / realized cap) — direct signal
#   CapMrktCurUSD  — Market cap in USD
#   AdrActCnt      — Active addresses per day
#   TxCnt          — Transaction count per day
#   HashRate       — Mining hash rate (network security proxy)
#   FlowInExUSD    — USD flowing into exchanges (sell pressure)
#   FlowOutExUSD   — USD flowing out of exchanges (accumulation signal)
#   ROI30d         — 30-day return on investment
#   SplyCur        — Circulating supply
#
# Note: CapRealUSD and NVTAdj are NOT available in the community tier.
# CapMVRVCur is the direct MVRV ratio equivalent.

COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4"
COINMETRICS_METRICS = [
    "CapMVRVCur",       # MVRV ratio (primary on-chain valuation signal)
    "CapMrktCurUSD",    # Market cap USD
    "AdrActCnt",        # Active addresses
    "TxCnt",            # Transaction count
    "HashRate",         # Hash rate
    "FlowInExUSD",      # Exchange inflow USD
    "FlowOutExUSD",     # Exchange outflow USD
    "ROI30d",           # 30-day ROI
    "SplyCur",          # Circulating supply
]
COINMETRICS_START = "2017-01-01"
COINMETRICS_OUT = ONCHAIN_DIR / "coin_metrics_daily.csv"


def download_coin_metrics_daily(force: bool = False):
    """
    Download Coin Metrics community API signals in a single paginated request
    and save to coin_metrics_daily.csv.

    Uses CapMVRVCur as the MVRV ratio (equivalent to CapMrktCurUSD / CapRealUSD).
    CapRealUSD and NVTAdj are not available in the free community tier.
    """
    out = COINMETRICS_OUT

    if not force and out.exists() and out.stat().st_size > 10_000:
        log.info("Coin Metrics daily: already downloaded. Skipping.")
        with open(out, newline="") as f:
            rows = list(csv.DictReader(f))
        log.info(f"  Existing file: {len(rows):,} rows")
        return len(rows)

    log.info(f"Downloading Coin Metrics community API ({len(COINMETRICS_METRICS)} metrics) …")
    log.info(f"  Metrics: {', '.join(COINMETRICS_METRICS)}")
    log.info(f"  Source : {COINMETRICS_BASE}/timeseries/asset-metrics")
    log.info(f"  Range  : {COINMETRICS_START} → today")

    all_rows = []
    page_token = None
    page_size = 10000
    page_num = 0

    while True:
        params = {
            "assets": "btc",
            "metrics": ",".join(COINMETRICS_METRICS),
            "start_time": COINMETRICS_START,
            "frequency": "1d",
            "page_size": page_size,
        }
        if page_token:
            params["next_page_token"] = page_token

        r = api_get(f"{COINMETRICS_BASE}/timeseries/asset-metrics", params)
        if r is None:
            log.error("  Failed to fetch Coin Metrics data — aborting")
            return 0

        data = r.json()
        rows = data.get("data", [])
        all_rows.extend(rows)
        page_num += 1

        if page_num == 1:
            log.info(f"  First page: {len(rows)} rows, columns: {list(rows[0].keys()) if rows else 'none'}")

        page_token = data.get("next_page_token")
        if not page_token or not rows:
            break
        time.sleep(0.2)

    if not all_rows:
        log.warning("  No data returned from Coin Metrics API")
        return 0

    # Build output rows
    output_rows = []
    for row in all_rows:
        date = row.get("time", "")[:10]
        output_rows.append({
            "date": date,
            "asset": row.get("asset", "btc"),
            "mvrv": row.get("CapMVRVCur") or "",           # MVRV ratio (direct)
            "CapMrktCurUSD": row.get("CapMrktCurUSD") or "",
            "AdrActCnt": row.get("AdrActCnt") or "",
            "TxCnt": row.get("TxCnt") or "",
            "HashRate": row.get("HashRate") or "",
            "FlowInExUSD": row.get("FlowInExUSD") or "",
            "FlowOutExUSD": row.get("FlowOutExUSD") or "",
            "ROI30d": row.get("ROI30d") or "",
            "SplyCur": row.get("SplyCur") or "",
        })

    output_rows.sort(key=lambda x: x["date"])

    fieldnames = [
        "date", "asset", "mvrv", "CapMrktCurUSD",
        "AdrActCnt", "TxCnt", "HashRate",
        "FlowInExUSD", "FlowOutExUSD", "ROI30d", "SplyCur",
    ]
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    first_date = output_rows[0]["date"] if output_rows else "?"
    last_date = output_rows[-1]["date"] if output_rows else "?"
    mvrv_count = sum(1 for r in output_rows if r["mvrv"])

    log.info(f"  Coin Metrics daily: {len(output_rows):,} rows ({first_date} → {last_date})")
    log.info(f"  MVRV (CapMVRVCur): {mvrv_count:,} rows with data")
    log.info(f"  Saved → {out}")
    return len(output_rows)


# ── Computed 30-day Realized Volatility ──────────────────────────────────────

def compute_rv_from_spot():
    """Compute rolling 7/30/90-day realized volatility from 1h spot data."""
    out = ONCHAIN_DIR / "rv_from_spot.csv"
    if out.exists() and out.stat().st_size > 100_000:
        log.info("RV from spot: already computed. Skipping.")
        return True

    spot_file = SPOT_DIR / "btc_1h.csv"
    if not spot_file.exists():
        log.warning("btc_1h.csv not found — skipping RV computation. Run download_spot.py first.")
        return False

    log.info("Computing 30d/7d/90d realized volatility from 1h spot data …")

    rows = []
    with open(spot_file) as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            try:
                rows.append((int(row[0]) / 1000, float(row[4])))  # timestamp_s, close
            except (ValueError, IndexError):
                pass

    if len(rows) < 720:
        log.error("Not enough spot data to compute RV")
        return False

    log_rets = []
    for i in range(1, len(rows)):
        ts = rows[i][0]
        close = rows[i][1]
        prev_close = rows[i - 1][1]
        if prev_close > 0 and close > 0:
            lr = math.log(close / prev_close)
            log_rets.append((ts, lr))

    WINDOWS = {
        "rv_7d": 7 * 24,
        "rv_30d": 30 * 24,
        "rv_90d": 90 * 24,
    }
    ANNUALIZE = math.sqrt(8760)

    result_rows = []
    for i in range(max(WINDOWS.values()), len(log_rets)):
        ts = log_rets[i][0]
        date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        row = {"timestamp": ts, "datetime": date}
        for key, window in WINDOWS.items():
            window_rets = [r[1] for r in log_rets[i - window:i]]
            n = len(window_rets)
            if n < 2:
                row[key] = ""
                continue
            mean = sum(window_rets) / n
            variance = sum((r - mean) ** 2 for r in window_rets) / (n - 1)
            rv = math.sqrt(variance) * ANNUALIZE
            row[key] = f"{rv:.6f}"
        result_rows.append(row)

    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "datetime", "rv_7d", "rv_30d", "rv_90d"])
        writer.writeheader()
        writer.writerows(result_rows)

    log.info(f"  RV from spot: {len(result_rows):,} rows ({result_rows[0]['datetime']} → {result_rows[-1]['datetime']})")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("On-Chain & Supplementary Data Downloader")
    log.info("Sources: alternative.me/fng, community-api.coinmetrics.io/v4")
    log.info("No API keys required.")
    log.info("=" * 60)

    t0 = time.time()

    # 1. Fear & Greed
    log.info("\n[1/3] Fear & Greed Index …")
    fg_count = download_fear_greed()

    # 2. Coin Metrics community API (combined daily CSV)
    log.info("\n[2/3] Coin Metrics on-chain metrics (community API) …")
    cm_count = download_coin_metrics_daily()

    # 3. Computed RV from spot
    log.info("\n[3/3] Computing realized volatility from spot data …")
    compute_rv_from_spot()

    elapsed = time.time() - t0
    log.info("\n" + "=" * 60)
    log.info(f"ON-CHAIN DOWNLOAD COMPLETE ({elapsed:.0f}s)")
    log.info(f"  Fear & Greed    : {fg_count:,} records")
    log.info(f"  Coin Metrics    : {cm_count:,} daily rows (coin_metrics_daily.csv)")
    log.info("=" * 60)

    log.info("\nOutput files:")
    for p in sorted(ONCHAIN_DIR.iterdir()):
        size_kb = p.stat().st_size / 1024
        log.info(f"  {p.name}: {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
