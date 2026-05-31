#!/usr/bin/env python3
"""
build_hyperliquid_dataset.py — Stitch daily Hyperliquid JSONL snapshots into a
single long-format CSV for downstream analysis.

Reads:  data/raw/hyperliquid/leaderboard_YYYY-MM-DD.jsonl  (one or many)
Writes: data/processed/hyperliquid_leaderboard_history.csv

Output schema (long format — one row per (date, address, asset_or_aggregate)):

  date                 UTC snapshot date, YYYY-MM-DD
  snapshot_ts_ms       wall-clock ms at fetch time (run timestamp, not bar time)
  kind                 "trader_aggregate" | "trader_position" | "hlp_vault"
  address              ethAddress (HLP rows use the vault address)
  display_name         leaderboard displayName (may be null)
  rank_by_30d_pnl      rank within that day's active top-N (trader rows only)
  asset                "_AGGREGATE_" for kind=trader_aggregate,
                       coin symbol (e.g. "BTC", "HYPE") for kind=trader_position,
                       "_HLP_"      for kind=hlp_vault

  # Trader-aggregate fields (filled on trader_aggregate rows)
  lb_account_value     accountValue from the leaderboard payload
  chs_account_value    accountValue from clearinghouseState marginSummary
  total_ntl_pos        crossMarginSummary.totalNtlPos
  withdrawable         clearinghouseState withdrawable
  day_pnl, day_roi, day_vlm
  week_pnl, week_roi, week_vlm
  month_pnl, month_roi, month_vlm
  alltime_pnl, alltime_roi, alltime_vlm

  # Per-position fields (filled on trader_position rows)
  position_szi         signed size, base units (negative = short)
  position_side        "long" | "short" | "flat"
  position_value_usd   positionValue
  entry_px             entryPx
  leverage_value       leverage.value
  leverage_type        leverage.type ("cross" | "isolated")
  unrealized_pnl       unrealizedPnl
  liquidation_px       liquidationPx
  margin_used          marginUsed

  # HLP fields (filled on hlp_vault rows)
  hlp_lifetime_pnl     last point of allTime pnlHistory
  hlp_current_nav      last point of allTime accountValueHistory
  hlp_apr_daily        apr field (trailing-day-annualised, not lifetime)
  hlp_follower_count   number of follower wallets

Idempotent: each rebuild scans all daily files in OUT_DIR/raw/hyperliquid and
rewrites the CSV from scratch. Cheap — at ~100 traders × 365 days × maybe-5
positions each ≈ 200k rows/year, full rebuild is sub-second.

Sanity report on completion:
  - number of snapshot dates ingested
  - total unique traders observed
  - HLP trailing-30d PnL (delta from the oldest-NAV-in-window to latest)

Usage:
    python3.11 scripts/build_hyperliquid_dataset.py
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "hyperliquid"
OUT_DIR = ROOT / "data" / "processed"
LOG_DIR = ROOT / "data" / "logs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "hyperliquid_leaderboard_history.csv"

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "build_hyperliquid_dataset.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("build_hyperliquid_dataset")

CSV_HEADERS = [
    "date", "snapshot_ts_ms", "kind", "address", "display_name",
    "rank_by_30d_pnl", "asset",
    # trader aggregate
    "lb_account_value", "chs_account_value", "total_ntl_pos", "withdrawable",
    "day_pnl", "day_roi", "day_vlm",
    "week_pnl", "week_roi", "week_vlm",
    "month_pnl", "month_roi", "month_vlm",
    "alltime_pnl", "alltime_roi", "alltime_vlm",
    # per-position
    "position_szi", "position_side", "position_value_usd", "entry_px",
    "leverage_value", "leverage_type",
    "unrealized_pnl", "liquidation_px", "margin_used",
    # HLP
    "hlp_lifetime_pnl", "hlp_current_nav", "hlp_apr_daily", "hlp_follower_count",
]


def _safe_float(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _side(szi_str: Any) -> str:
    s = _safe_float(szi_str)
    if s is None or s == 0:
        return "flat"
    return "long" if s > 0 else "short"


def _window(perfs: dict, name: str, field: str) -> Any:
    body = perfs.get(name)
    if not isinstance(body, dict):
        return None
    return body.get(field)


def yield_rows_from_trader(rec: dict) -> Iterable[dict]:
    """Emit one aggregate row + one per-position row from a trader record."""
    perfs = rec.get("window_performances", {}) or {}
    chs = rec.get("clearinghouse", {}) or {}
    margin = chs.get("marginSummary", {}) or {}
    cross = chs.get("crossMarginSummary", {}) or {}

    base = {h: "" for h in CSV_HEADERS}
    base.update({
        "date": rec.get("snapshot_date"),
        "snapshot_ts_ms": rec.get("snapshot_ts_ms"),
        "address": rec.get("address"),
        "display_name": rec.get("display_name") or "",
        "rank_by_30d_pnl": rec.get("rank_by_30d_pnl_active"),
    })

    # ── aggregate row ─────────────────────────────────────────────────────────
    agg = dict(base)
    agg.update({
        "kind": "trader_aggregate",
        "asset": "_AGGREGATE_",
        "lb_account_value": rec.get("lb_account_value") or "",
        "chs_account_value": margin.get("accountValue") or "",
        "total_ntl_pos": cross.get("totalNtlPos") or margin.get("totalNtlPos") or "",
        "withdrawable": chs.get("withdrawable") or "",
        "day_pnl":     _window(perfs, "day",     "pnl") or "",
        "day_roi":     _window(perfs, "day",     "roi") or "",
        "day_vlm":     _window(perfs, "day",     "vlm") or "",
        "week_pnl":    _window(perfs, "week",    "pnl") or "",
        "week_roi":    _window(perfs, "week",    "roi") or "",
        "week_vlm":    _window(perfs, "week",    "vlm") or "",
        "month_pnl":   _window(perfs, "month",   "pnl") or "",
        "month_roi":   _window(perfs, "month",   "roi") or "",
        "month_vlm":   _window(perfs, "month",   "vlm") or "",
        "alltime_pnl": _window(perfs, "allTime", "pnl") or "",
        "alltime_roi": _window(perfs, "allTime", "roi") or "",
        "alltime_vlm": _window(perfs, "allTime", "vlm") or "",
    })
    yield agg

    # ── per-position rows ─────────────────────────────────────────────────────
    for ap in chs.get("assetPositions", []) or []:
        pos = ap.get("position", {}) if isinstance(ap, dict) else {}
        if not pos:
            continue
        coin = pos.get("coin") or ""
        lev = pos.get("leverage", {}) or {}
        row = dict(base)
        row.update({
            "kind": "trader_position",
            "asset": coin,
            "position_szi":       pos.get("szi") or "",
            "position_side":      _side(pos.get("szi")),
            "position_value_usd": pos.get("positionValue") or "",
            "entry_px":           pos.get("entryPx") or "",
            "leverage_value":     lev.get("value") or "",
            "leverage_type":      lev.get("type") or "",
            "unrealized_pnl":     pos.get("unrealizedPnl") or "",
            "liquidation_px":     pos.get("liquidationPx") or "",
            "margin_used":        pos.get("marginUsed") or "",
        })
        yield row


def yield_rows_from_hlp(rec: dict) -> Iterable[dict]:
    base = {h: "" for h in CSV_HEADERS}
    base.update({
        "date": rec.get("snapshot_date"),
        "snapshot_ts_ms": rec.get("snapshot_ts_ms"),
        "kind": "hlp_vault",
        "address": rec.get("vault_address"),
        "display_name": rec.get("name") or "",
        "asset": "_HLP_",
        "hlp_lifetime_pnl":  rec.get("lifetime_pnl_usd") or "",
        "hlp_current_nav":   rec.get("current_nav_usd") or "",
        "hlp_apr_daily":     rec.get("apr_trailing_day_annualised") if rec.get("apr_trailing_day_annualised") is not None else "",
        "hlp_follower_count": rec.get("follower_count") or "",
    })
    yield base


def process_one_file(path: Path) -> list[dict]:
    out: list[dict] = []
    with open(path) as f:
        for line_num, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                log.warning("  %s:%d skipping malformed JSON line: %s",
                            path.name, line_num, exc)
                continue
            kind = rec.get("kind")
            if kind == "trader":
                out.extend(yield_rows_from_trader(rec))
            elif kind == "hlp_vault":
                out.extend(yield_rows_from_hlp(rec))
            else:
                log.warning("  %s:%d unknown kind=%r — skipping",
                            path.name, line_num, kind)
    return out


def hlp_trailing_30d_pnl(rows: list[dict]) -> float | None:
    """Δ(lifetime_pnl) between today's HLP row and the row ~30d earlier."""
    hlp = [r for r in rows if r["kind"] == "hlp_vault"]
    if not hlp:
        return None
    hlp.sort(key=lambda r: r["date"])
    latest = hlp[-1]
    latest_date = datetime.strptime(latest["date"], "%Y-%m-%d")
    cutoff = latest_date - timedelta(days=30)
    # Earliest HLP row on or after the cutoff.
    earlier = next((r for r in hlp
                    if datetime.strptime(r["date"], "%Y-%m-%d") >= cutoff
                    and r is not latest), None)
    if earlier is None:
        return None
    a = _safe_float(latest["hlp_lifetime_pnl"])
    b = _safe_float(earlier["hlp_lifetime_pnl"])
    if a is None or b is None:
        return None
    return a - b


def main() -> int:
    daily_files = sorted(RAW_DIR.glob("leaderboard_*.jsonl"))
    if not daily_files:
        log.warning("No daily JSONL files in %s — nothing to do.",
                    RAW_DIR.relative_to(ROOT))
        return 0

    log.info("Ingesting %d daily file(s) from %s",
             len(daily_files), RAW_DIR.relative_to(ROOT))
    t0 = time.time()

    all_rows: list[dict] = []
    for path in daily_files:
        rows = process_one_file(path)
        log.info("  %s → %d rows", path.name, len(rows))
        all_rows.extend(rows)

    # Deterministic sort: date, then kind (aggregates first), then rank, then asset.
    kind_order = {"trader_aggregate": 0, "trader_position": 1, "hlp_vault": 2}
    all_rows.sort(key=lambda r: (
        r.get("date") or "",
        kind_order.get(r.get("kind", ""), 9),
        (r.get("rank_by_30d_pnl") if isinstance(r.get("rank_by_30d_pnl"), int) else 9_999),
        r.get("asset") or "",
    ))

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for r in all_rows:
            writer.writerow({k: r.get(k, "") for k in CSV_HEADERS})

    elapsed = time.time() - t0

    # ── sanity report ─────────────────────────────────────────────────────────
    dates = sorted({r["date"] for r in all_rows if r.get("date")})
    addrs = {r["address"] for r in all_rows
             if r.get("kind") in ("trader_aggregate", "trader_position") and r.get("address")}
    trailing_30d = hlp_trailing_30d_pnl(all_rows)

    print("\n" + "=" * 72)
    print("HYPERLIQUID DATASET BUILD COMPLETE")
    print(f"  Output           : {OUT_PATH.relative_to(ROOT)}")
    print(f"  Source files     : {len(daily_files)}")
    print(f"  Total rows       : {len(all_rows):,}")
    print(f"  Snapshot dates   : {len(dates)}"
          + (f"  ({dates[0]} → {dates[-1]})" if dates else ""))
    print(f"  Unique traders   : {len(addrs):,}")
    print(f"  HLP trailing-30d PnL: "
          + ("(need ≥2 snapshots ≥30d apart)" if trailing_30d is None
             else f"${trailing_30d:,.0f}"))
    print(f"  Elapsed          : {elapsed:.2f}s")
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
