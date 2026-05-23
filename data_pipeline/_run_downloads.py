#!/usr/bin/env python3
"""
_run_downloads.py — Run all fast downloads sequentially.
Designed to complete within 40 seconds total.
Handles: DVOL, spot daily, spot 1h, funding rates.
"""

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DERIBIT_DIR = ROOT / "data/raw/deribit"
SPOT_DIR = ROOT / "data/raw/spot"
LOG_DIR = ROOT / "data/logs"

for d in [DERIBIT_DIR, SPOT_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "btc-rl-v2/1.0"


def api_get(url, params, retries=5):
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(2 ** i); continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            time.sleep(2 ** i)
    return None


# ── DVOL ──────────────────────────────────────────────────────────────────────
def download_dvol():
    out = DERIBIT_DIR / "dvol_history.json"
    existing = {}
    if out.exists():
        with open(out) as f:
            existing = json.load(f)
    if existing.get("count", 0) > 1000:
        print(f"  DVOL: already have {existing['count']:,} candles — skipping")
        return existing["count"]

    print("  DVOL: downloading from 2021-04-01 …")
    all_data = []
    RESOLUTION = 3600
    PAGE_CANDLES = 1000
    STEP_MS = RESOLUTION * 1000 * PAGE_CANDLES

    start_ms = int(datetime(2021, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(time.time() * 1000)
    cur = start_ms
    pages = 0

    while cur < end_ms:
        result = api_get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                         {"currency": "BTC", "start_timestamp": cur,
                          "end_timestamp": min(cur + STEP_MS, end_ms), "resolution": RESOLUTION})
        data = (result or {}).get("result", {}).get("data", [])
        if not data:
            cur += STEP_MS
            time.sleep(0.05)
            continue
        all_data.extend(data)
        pages += 1
        cur = data[-1][0] + RESOLUTION * 1000
        time.sleep(0.08)

    record = {
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "currency": "BTC", "resolution": "1h",
        "count": len(all_data),
        "first_timestamp": datetime.fromtimestamp(all_data[0][0] / 1000, tz=timezone.utc).isoformat() if all_data else None,
        "last_timestamp": datetime.fromtimestamp(all_data[-1][0] / 1000, tz=timezone.utc).isoformat() if all_data else None,
        "data": all_data,
    }
    with open(out, "w") as f:
        json.dump(record, f)
    print(f"  DVOL: {len(all_data):,} candles ({record['first_timestamp']} → {record['last_timestamp']})")
    return len(all_data)


# ── Spot OHLCV ────────────────────────────────────────────────────────────────
KLINES_URL = "https://api.binance.com/api/v3/klines"
CSV_COLS = ["timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "num_trades",
            "taker_buy_base", "taker_buy_quote", "ignore"]


def interval_ms(iv: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return int(iv[:-1]) * units[iv[-1]]


def download_spot(interval: str, start_dt: datetime, label: str, out_path: Path):
    if out_path.exists() and out_path.stat().st_size > 50_000:
        print(f"  {label}: already exists ({out_path.stat().st_size / 1024:.0f} KB) — skipping")
        return

    print(f"  {label}: downloading from {start_dt.date()} …")
    iv_ms = interval_ms(interval)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(time.time() * 1000)
    cur = start_ms
    total = 0

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        while cur < end_ms:
            result = api_get(KLINES_URL, {
                "symbol": "BTCUSDT", "interval": interval,
                "startTime": cur, "endTime": min(cur + iv_ms * 1000, end_ms), "limit": 1000
            })
            if not result:
                cur += iv_ms * 1000
                continue
            w.writerows(result)
            total += len(result)
            cur = result[-1][0] + iv_ms
            time.sleep(0.04)

    size_kb = out_path.stat().st_size / 1024
    print(f"  {label}: {total:,} candles saved ({size_kb:.0f} KB)")


# ── Funding Rates ─────────────────────────────────────────────────────────────
def download_funding_rates():
    out = DERIBIT_DIR / "funding_rates.json"
    if out.exists() and out.stat().st_size > 10_000:
        with open(out) as f:
            d = json.load(f)
        print(f"  Funding rates: already have {d.get('count', '?')} records — skipping")
        return

    print("  Funding rates: downloading BTC-PERPETUAL history …")
    # Deribit funding rate history - paginate backward
    all_records = []
    end_ms = int(time.time() * 1000)
    start_ms = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    # Use start/end timestamp pagination
    cur_start = start_ms
    while cur_start < end_ms:
        cur_end = min(cur_start + 90 * 24 * 3600 * 1000, end_ms)  # 90 days per page
        result = api_get(
            "https://www.deribit.com/api/v2/public/get_funding_rate_history",
            {"instrument_name": "BTC-PERPETUAL",
             "start_timestamp": cur_start,
             "end_timestamp": cur_end}
        )
        records = (result or {}) if isinstance(result, list) else []
        if not records:
            cur_start = cur_end
            time.sleep(0.1)
            continue
        all_records.extend(records)
        last_ts = max(r.get("timestamp", 0) for r in records) if records and isinstance(records[0], dict) else cur_end
        cur_start = last_ts + 1
        time.sleep(0.08)

    data = {
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        "instrument_name": "BTC-PERPETUAL",
        "count": len(all_records),
        "data": all_records,
    }
    if all_records and isinstance(all_records[0], dict):
        data["first_timestamp"] = datetime.fromtimestamp(
            min(r["timestamp"] for r in all_records) / 1000, tz=timezone.utc).isoformat()
        data["last_timestamp"] = datetime.fromtimestamp(
            max(r["timestamp"] for r in all_records) / 1000, tz=timezone.utc).isoformat()

    with open(out, "w") as f:
        json.dump(data, f)
    print(f"  Funding rates: {len(all_records)} records saved")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Phase 1 Fast Downloads ===")
    t0 = time.time()

    print("\n[1/4] DVOL history …")
    download_dvol()

    print("\n[2/4] BTC daily spot (2017→now) …")
    download_spot("1d", datetime(2017, 1, 1, tzinfo=timezone.utc),
                  "btc_daily", SPOT_DIR / "btc_daily.csv")

    print("\n[3/4] BTC 1h spot (2019→now) …")
    download_spot("1h", datetime(2019, 1, 1, tzinfo=timezone.utc),
                  "btc_1h", SPOT_DIR / "btc_1h.csv")

    print("\n[4/4] Funding rates …")
    download_funding_rates()

    elapsed = time.time() - t0
    print(f"\n=== All done in {elapsed:.1f}s ===")
    for p in [SPOT_DIR / "btc_daily.csv", SPOT_DIR / "btc_1h.csv",
              DERIBIT_DIR / "dvol_history.json", DERIBIT_DIR / "funding_rates.json"]:
        size = p.stat().st_size / 1024 if p.exists() else 0
        print(f"  {p.name}: {size:.0f} KB")
