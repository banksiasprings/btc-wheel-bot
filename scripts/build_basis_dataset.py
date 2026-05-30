#!/usr/bin/env python3
"""
build_basis_dataset.py — join Deribit perp + Binance spot into the Basis Arb dataset.

Inputs:
  data/raw/deribit/btc_perp_1h.csv   (from fetch_deribit_perp_history.py)
  data/raw/binance/btc_spot_1h.csv   (from fetch_binance_spot_history.py)

Output:
  data/processed/basis_dataset_1h.csv

Schema:
    timestamp_utc, timestamp_ms,
    perp_close, deribit_index_price, binance_spot_close,
    basis_vs_deribit_index_abs, basis_vs_deribit_index_pct,
    basis_vs_binance_spot_abs,  basis_vs_binance_spot_pct,
    cross_reference_noise_bps,
    perp_volume_usd, binance_volume_usd

Definitions
  basis_vs_<ref>_abs = perp_close - <ref>                       (USD)
  basis_vs_<ref>_pct = basis_abs / <ref>                        (fraction)
  cross_reference_noise_bps
      = |deribit_index_price - binance_spot_close| / binance_spot_close × 10_000

The Basis-Arb spec asks Gate 3 to answer the spot-reference question
(open Q #2): does using Deribit's own composite index drift meaningfully
from Binance spot? cross_reference_noise_bps quantifies it per hour.

Inner-join on timestamp_ms. Rows missing any of (perp_close,
deribit_index_price, binance_spot_close) are dropped from the processed
file — they are useful in the raw files but unusable downstream.

Usage:
    python3 scripts/build_basis_dataset.py
"""

from __future__ import annotations

import csv
import logging
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PERP_PATH  = ROOT / "data" / "raw" / "deribit" / "btc_perp_1h.csv"
SPOT_PATH  = ROOT / "data" / "raw" / "binance" / "btc_spot_1h.csv"
OUT_DIR    = ROOT / "data" / "processed"
LOG_DIR    = ROOT / "data" / "logs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH   = OUT_DIR / "basis_dataset_1h.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "build_basis_dataset.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("build_basis_dataset")

OUTPUT_HEADERS = [
    "timestamp_utc", "timestamp_ms",
    "perp_close", "deribit_index_price", "binance_spot_close",
    "basis_vs_deribit_index_abs", "basis_vs_deribit_index_pct",
    "basis_vs_binance_spot_abs",  "basis_vs_binance_spot_pct",
    "cross_reference_noise_bps",
    "perp_volume_usd", "binance_volume_usd",
]


def iso_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(
        ts_ms / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_float(s: str | None) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load_perp() -> dict[int, dict[str, Any]]:
    if not PERP_PATH.exists():
        log.error(f"Perp file missing: {PERP_PATH}")
        sys.exit(1)
    rows: dict[int, dict[str, Any]] = {}
    with open(PERP_PATH, "r", newline="") as f:
        for r in csv.DictReader(f):
            try:
                ts = int(r["timestamp_ms"])
            except (KeyError, ValueError):
                continue
            rows[ts] = {
                "perp_close":          parse_float(r.get("close")),
                "deribit_index_price": parse_float(r.get("index_price")),
                "perp_volume_usd":     parse_float(r.get("volume_usd")),
            }
    log.info(f"Loaded {len(rows):,} perp bars from {PERP_PATH.relative_to(ROOT)}")
    return rows


def load_spot() -> dict[int, dict[str, Any]]:
    if not SPOT_PATH.exists():
        log.error(f"Spot file missing: {SPOT_PATH}")
        sys.exit(1)
    rows: dict[int, dict[str, Any]] = {}
    with open(SPOT_PATH, "r", newline="") as f:
        for r in csv.DictReader(f):
            try:
                ts = int(r["timestamp_ms"])
            except (KeyError, ValueError):
                continue
            rows[ts] = {
                "binance_spot_close": parse_float(r.get("close")),
                "binance_volume_usd": parse_float(r.get("volume_usd")),
            }
    log.info(f"Loaded {len(rows):,} spot bars from {SPOT_PATH.relative_to(ROOT)}")
    return rows


def main() -> int:
    perp = load_perp()
    spot = load_spot()
    if not perp or not spot:
        log.error("One or both inputs are empty.")
        return 1

    common = sorted(set(perp.keys()) & set(spot.keys()))
    log.info(f"Inner-join overlap: {len(common):,} hourly timestamps")

    out_rows: list[dict[str, Any]] = []
    dropped_missing = 0
    deribit_basis_pcts: list[float] = []
    binance_basis_pcts: list[float] = []
    cross_noise_bps:    list[float] = []

    for ts in common:
        p = perp[ts]
        s = spot[ts]
        perp_close    = p["perp_close"]
        deribit_index = p["deribit_index_price"]
        binance_spot  = s["binance_spot_close"]
        if perp_close is None or deribit_index is None or binance_spot is None:
            dropped_missing += 1
            continue
        if binance_spot <= 0 or deribit_index <= 0:
            dropped_missing += 1
            continue

        basis_d_abs = perp_close - deribit_index
        basis_d_pct = basis_d_abs / deribit_index
        basis_b_abs = perp_close - binance_spot
        basis_b_pct = basis_b_abs / binance_spot
        cross_noise = abs(deribit_index - binance_spot) / binance_spot * 10_000

        out_rows.append({
            "timestamp_utc":               iso_utc(ts),
            "timestamp_ms":                ts,
            "perp_close":                  perp_close,
            "deribit_index_price":         deribit_index,
            "binance_spot_close":          binance_spot,
            "basis_vs_deribit_index_abs":  basis_d_abs,
            "basis_vs_deribit_index_pct":  basis_d_pct,
            "basis_vs_binance_spot_abs":   basis_b_abs,
            "basis_vs_binance_spot_pct":   basis_b_pct,
            "cross_reference_noise_bps":   cross_noise,
            "perp_volume_usd":             p["perp_volume_usd"] or 0.0,
            "binance_volume_usd":          s["binance_volume_usd"] or 0.0,
        })
        deribit_basis_pcts.append(basis_d_pct)
        binance_basis_pcts.append(basis_b_pct)
        cross_noise_bps.append(cross_noise)

    if not out_rows:
        log.error("No usable joined rows produced.")
        return 1

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS)
        writer.writeheader()
        writer.writerows(out_rows)

    def summarise(name: str, xs: list[float], scale: float = 1.0) -> str:
        if not xs:
            return f"  {name}: (no data)"
        med = statistics.median(xs) * scale
        p95 = statistics.quantiles(xs, n=20)[18] * scale  # 95th
        mn  = min(xs) * scale
        mx  = max(xs) * scale
        return f"  {name}:  median={med:+.2f}  p95={p95:+.2f}  min={mn:+.2f}  max={mx:+.2f}"

    first_ts = out_rows[0]["timestamp_ms"]
    last_ts  = out_rows[-1]["timestamp_ms"]

    print("\n" + "=" * 72)
    print("BASIS DATASET BUILD COMPLETE")
    print(f"  Output      : {OUT_PATH.relative_to(ROOT)}")
    print(f"  Joined rows : {len(out_rows):,}")
    print(f"  Range       : {iso_utc(first_ts)} → {iso_utc(last_ts)}")
    print(f"  Dropped     : {dropped_missing:,} rows missing perp/index/spot")
    print()
    print("Diagnostics (the headline numbers Gate 3 cares about):")
    print(summarise("basis vs Deribit index (bps) ", deribit_basis_pcts, 10_000))
    print(summarise("basis vs Binance spot  (bps) ", binance_basis_pcts, 10_000))
    print(summarise("cross-reference noise  (bps) ", cross_noise_bps))
    print()
    print("Interpretation:")
    print("  • Healthy basis (typical funding regime):    median ~ +5 to +25 bps")
    print("  • Healthy cross-ref noise (Deribit vs Binance): median < ~5 bps")
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
