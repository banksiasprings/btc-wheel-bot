#!/usr/bin/env python3
"""
download_onchain.py — Free on-chain metrics + Fear & Greed index
Downloads:
  - Fear & Greed Index (alternative.me) → data/raw/onchain/fear_greed.json
  - MVRV Z-Score, SOPR, Realized Price (Coin Metrics free API) → data/raw/onchain/coinmetrics_*.csv
  - Computed 30-day Realized Volatility from spot → data/raw/onchain/rv_from_spot.csv

No API keys required for Fear & Greed or Coin Metrics free tier.

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
SESSION.headers.update({"User-Agent": "btc-rl-v2-data-pipeline/1.0"})


def api_get(url, params=None, max_retries=5):
    for attempt in range(max_retries):
        try:
            r = SESSION.get(url, params=params or {}, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
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
    # limit=0 returns all available data
    r = api_get("https://api.alternative.me/fng/", {"limit": 0, "format": "json"})
    if r is None:
        log.error("Failed to fetch Fear & Greed data")
        return 0

    data = r.json()
    records = data.get("data", [])

    # Add readable dates
    for rec in records:
        ts = int(rec.get("timestamp", 0))
        rec["date"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    # Sort ascending by timestamp
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

    log.info(f"  ✓ Fear & Greed: {len(records):,} records ({output.get('first_date')} → {output.get('last_date')})")
    return len(records)


# ── Coin Metrics Free API ─────────────────────────────────────────────────────

COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4"

COINMETRICS_METRICS = {
    "CapMVRVFF": "mvrv_free_float",
    "SoprFree": "sopr_free_float",
    "PriceRealizedUSD": "realized_price_usd",
    "NVTAdj": "nvt_adjusted",
    "HashRate": "hash_rate",
    "AdrActCnt": "active_addresses",
}


def download_coinmetrics_metric(metric_key: str, metric_name: str, start: str = "2019-01-01"):
    out = ONCHAIN_DIR / f"coinmetrics_{metric_name}.csv"
    if out.exists() and out.stat().st_size > 5_000:
        log.info(f"  {metric_name}: already downloaded. Skipping.")
        return True

    log.info(f"  Downloading Coin Metrics: {metric_key} ({metric_name}) …")
    all_rows = []
    page_token = None
    page_size = 10000

    while True:
        params = {
            "assets": "btc",
            "metrics": metric_key,
            "start_time": start,
            "end_time": "2026-01-01",
            "frequency": "1d",
            "page_size": page_size,
        }
        if page_token:
            params["next_page_token"] = page_token

        r = api_get(f"{COINMETRICS_BASE}/timeseries/asset-metrics", params)
        if r is None:
            log.error(f"  Failed to fetch {metric_key}")
            return False

        data = r.json()
        rows = data.get("data", [])
        all_rows.extend(rows)

        # Pagination
        page_token = data.get("next_page_token")
        if not page_token or not rows:
            break
        time.sleep(0.1)

    if not all_rows:
        log.warning(f"  No data returned for {metric_key}")
        return False

    # Write CSV
    cols = ["time", "asset", metric_key]
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    log.info(f"  ✓ {metric_name}: {len(all_rows):,} rows ({all_rows[0]['time'][:10]} → {all_rows[-1]['time'][:10]})")
    return True


def download_all_coinmetrics():
    log.info("Downloading Coin Metrics on-chain metrics (free API) …")
    success = 0
    for metric_key, metric_name in COINMETRICS_METRICS.items():
        ok = download_coinmetrics_metric(metric_key, metric_name)
        if ok:
            success += 1
        time.sleep(0.3)
    return success


# ── Computed 30-day Realized Volatility ──────────────────────────────────────

def compute_rv_from_spot():
    """Compute rolling 30-day realized volatility from 1h spot data."""
    out = ONCHAIN_DIR / "rv_from_spot.csv"
    if out.exists() and out.stat().st_size > 100_000:
        log.info("RV from spot: already computed. Skipping.")
        return True

    spot_file = SPOT_DIR / "btc_1h.csv"
    if not spot_file.exists():
        log.error("btc_1h.csv not found. Run download_spot.py first.")
        return False

    log.info("Computing 30d/7d/90d realized volatility from 1h spot data …")

    # Read spot data
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

    # Compute log returns
    log_rets = []
    for i in range(1, len(rows)):
        ts = rows[i][0]
        close = rows[i][1]
        prev_close = rows[i - 1][1]
        if prev_close > 0 and close > 0:
            lr = math.log(close / prev_close)
            log_rets.append((ts, lr))

    # Rolling windows (in 1h candles)
    WINDOWS = {
        "rv_7d": 7 * 24,
        "rv_30d": 30 * 24,
        "rv_90d": 90 * 24,
    }
    ANNUALIZE = math.sqrt(8760)  # sqrt(hours per year)

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

    log.info(f"  ✓ Computed RV: {len(result_rows):,} rows ({result_rows[0]['datetime']} → {result_rows[-1]['datetime']})")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("On-Chain & Supplementary Data Downloader")
    log.info("=" * 60)

    t0 = time.time()

    # 1. Fear & Greed
    log.info("\n[1/3] Fear & Greed Index …")
    fg_count = download_fear_greed()

    # 2. Coin Metrics
    log.info("\n[2/3] Coin Metrics on-chain metrics …")
    cm_count = download_all_coinmetrics()

    # 3. Computed RV
    log.info("\n[3/3] Computing realized volatility from spot data …")
    compute_rv_from_spot()

    elapsed = time.time() - t0
    log.info("\n" + "=" * 60)
    log.info(f"ON-CHAIN DOWNLOAD COMPLETE ({elapsed:.0f}s)")
    log.info(f"  Fear & Greed  : {fg_count:,} records")
    log.info(f"  Coin Metrics  : {cm_count} metrics downloaded")
    log.info("=" * 60)

    for p in sorted(ONCHAIN_DIR.iterdir()):
        size_kb = p.stat().st_size / 1024
        log.info(f"  {p.name}: {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
