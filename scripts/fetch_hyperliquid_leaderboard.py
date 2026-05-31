#!/usr/bin/env python3
"""
fetch_hyperliquid_leaderboard.py — Daily snapshot of Hyperliquid's public
leaderboard + HLP vault.

Writes one JSONL file per UTC date at
`data/raw/hyperliquid/leaderboard_YYYY-MM-DD.jsonl`. Each line is either a
trader-snapshot row (`kind = "trader"`) or a single HLP-vault row
(`kind = "hlp_vault"`).

Pipeline per run:
  1. GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard
     (full leaderboard JSON, ~30 MB, ~38k rows).
  2. Filter to active traders: `month_vlm > $1M`. The spike found the naïve
     top-100 is corrupted by HYPE bagholders with $0 monthly volume —
     see ~/Documents/bsf-research-briefs/hyperliquid-spike.md §1.4.
  3. Sort by 30d PnL desc, take top-N (default 100).
  4. For each trader, POST to /info {type:"clearinghouseState"} to get the
     perp account snapshot (positions + margin summary).
  5. POST {type:"vaultDetails", vaultAddress:HLP} once for the HLP row.

Idempotent: if today's file exists, exit without re-fetching unless --force.

Rate limit (per spike §1.2 / Hyperliquid docs):
  - 1,200 weight units/min/IP.
  - clearinghouseState = 2 weight; vaultDetails = 20 weight.
  - Top-100 run: ~220 weight units → well under budget.
  - Per-call sleep below is conservative.

References:
  - Spike: ~/Documents/bsf-research-briefs/hyperliquid-spike.md §1 (data surface).
  - Info endpoint: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
  - Rate limits:   https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits

Usage:
    python3.11 scripts/fetch_hyperliquid_leaderboard.py             # top 100, today
    python3.11 scripts/fetch_hyperliquid_leaderboard.py --top 30    # smoke-test
    python3.11 scripts/fetch_hyperliquid_leaderboard.py --force     # overwrite today
    python3.11 scripts/fetch_hyperliquid_leaderboard.py --date 2026-05-31
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "raw" / "hyperliquid"
LOG_DIR = ROOT / "data" / "logs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "fetch_hyperliquid.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("fetch_hyperliquid")

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"
HLP_VAULT = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"

# Filter that converts the raw 37k leaderboard into the ~7.5k active-trader
# universe. Spike §1.4: 17 of the raw top-20 by 30d PnL are HYPE token holders
# with under $1M monthly volume — they are not traders. month_vlm > $1M is the
# mandatory floor.
ACTIVE_VLM_FLOOR_USD = 1_000_000.0

# Conservative per-call sleep — clearinghouseState is weight 2, so 600 calls/min
# is allowed even before counting the other weight in budget. 0.15s ≈ 400/min.
PER_CALL_SLEEP_S = 0.15

SESSION = requests.Session()
SESSION.headers.update(
    {"User-Agent": "btc-wheel-bot/hyperliquid-snapshot/1.0",
     "Content-Type": "application/json"}
)


def fetch_leaderboard() -> list[dict]:
    """Pull the full prebuilt leaderboard JSON. ~30 MB."""
    log.info("Fetching leaderboard from %s …", LEADERBOARD_URL)
    resp = SESSION.get(LEADERBOARD_URL, timeout=120)
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("leaderboardRows", [])
    log.info("  → %d total leaderboard rows", len(rows))
    return rows


def info_post(body: dict, max_retries: int = 6) -> Any:
    """POST a /info request with exponential backoff on 429 / network errors."""
    for attempt in range(max_retries):
        try:
            resp = SESSION.post(INFO_URL, data=json.dumps(body), timeout=30)
            if resp.status_code == 429:
                wait = min(60, 2 ** attempt)
                log.warning("429 rate-limit on %s; sleeping %ds",
                            body.get("type"), wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            wait = min(30, 2 ** attempt)
            log.warning("info %s error: %s; retry %d/%d in %ds",
                        body.get("type"), exc, attempt + 1, max_retries, wait)
            time.sleep(wait)
    raise RuntimeError(f"info {body.get('type')} failed after {max_retries} retries")


def window_perfs_to_dict(window_performances: list) -> dict[str, dict]:
    """
    `windowPerformances` is a list of [window_name, {pnl, roi, vlm}] pairs.
    Returns a flat dict keyed by window name for filter convenience.
    Strings are kept as strings — they are decimal-typed on the wire and
    parsing them lossily here would discard precision.
    """
    out: dict[str, dict] = {}
    for entry in window_performances or []:
        if isinstance(entry, list) and len(entry) == 2:
            name, body = entry
            if isinstance(body, dict):
                out[name] = body
    return out


def filter_active_top_n(rows: list[dict], top_n: int) -> list[dict]:
    """Active = month_vlm > floor. Rank by 30d PnL desc."""
    scored: list[tuple[float, dict]] = []
    for r in rows:
        perfs = window_perfs_to_dict(r.get("windowPerformances", []))
        month = perfs.get("month")
        if not month:
            continue
        try:
            vlm = float(month.get("vlm", 0))
            pnl = float(month.get("pnl", 0))
        except (TypeError, ValueError):
            continue
        if vlm <= ACTIVE_VLM_FLOOR_USD:
            continue
        scored.append((pnl, r))
    scored.sort(key=lambda t: -t[0])
    universe = len(scored)
    chosen = [r for _pnl, r in scored[:top_n]]
    log.info("  → %d active traders (month_vlm > $%s); taking top %d by 30d PnL",
             universe, f"{ACTIVE_VLM_FLOOR_USD:,.0f}", len(chosen))
    return chosen


def trim_clearinghouse(state: dict) -> dict:
    """
    Keep the fields we actually care about for downstream analysis:
      - marginSummary / crossMarginSummary (account value totals)
      - assetPositions (open perp positions — size + side per asset)
      - withdrawable, crossMaintenanceMarginUsed (risk indicators)
      - time (server timestamp at snapshot)
    Discard nothing useful, but don't blow up the JSONL with debug fields.
    """
    if not isinstance(state, dict):
        return {}
    keys = (
        "marginSummary",
        "crossMarginSummary",
        "assetPositions",
        "withdrawable",
        "crossMaintenanceMarginUsed",
        "time",
    )
    return {k: state[k] for k in keys if k in state}


def build_trader_row(
    rank: int,
    lb_row: dict,
    chs: dict,
    snapshot_date: str,
    snapshot_ts: int,
) -> dict:
    """
    One JSONL row per trader. Captures both leaderboard-side and
    clearinghouse-side account values so the master-vs-agent-wallet
    misalignment (spike §2.3 / §6.4) is preserved in the time series.
    """
    perfs = window_perfs_to_dict(lb_row.get("windowPerformances", []))
    month = perfs.get("month", {})
    return {
        "kind": "trader",
        "snapshot_date": snapshot_date,
        "snapshot_ts_ms": snapshot_ts,
        "rank_by_30d_pnl_active": rank,
        "address": lb_row.get("ethAddress"),
        "display_name": lb_row.get("displayName"),
        "lb_account_value": lb_row.get("accountValue"),
        "month_vlm": month.get("vlm"),
        "month_pnl": month.get("pnl"),
        "month_roi": month.get("roi"),
        "window_performances": perfs,
        "clearinghouse": chs,
    }


def build_hlp_row(
    vault_details: dict,
    snapshot_date: str,
    snapshot_ts: int,
) -> dict:
    """
    HLP vault snapshot. Pulls the headline numbers (NAV, APR, lifetime PnL
    point) inline for ease of downstream querying, plus retains the full
    portfolio array (8 windows × accountValueHistory/pnlHistory) for any
    later replay of the lifetime tape.

    Spike §1.5: lifetime PnL ≈ $137M on ~$224M average NAV → lifetime CAGR
    ~17%. The headline `apr` is a trailing-day-annualised figure, not
    lifetime, so we keep both.
    """
    # Pull the last point of the "allTime" series for an at-a-glance PnL.
    portfolio = {name: body for name, body in vault_details.get("portfolio", [])
                 if isinstance(body, dict)}
    all_time = portfolio.get("allTime", {})
    pnl_hist = all_time.get("pnlHistory") or []
    nav_hist = all_time.get("accountValueHistory") or []
    last_pnl = pnl_hist[-1][1] if pnl_hist else None
    last_nav = nav_hist[-1][1] if nav_hist else None
    last_ts = pnl_hist[-1][0] if pnl_hist else None

    return {
        "kind": "hlp_vault",
        "snapshot_date": snapshot_date,
        "snapshot_ts_ms": snapshot_ts,
        "vault_address": HLP_VAULT,
        "name": vault_details.get("name"),
        "apr_trailing_day_annualised": vault_details.get("apr"),
        "max_distributable_usd": vault_details.get("maxDistributable"),
        "max_withdrawable_usd": vault_details.get("maxWithdrawable"),
        "is_closed": vault_details.get("isClosed"),
        "allow_deposits": vault_details.get("allowDeposits"),
        "follower_count": len(vault_details.get("followers", [])),
        "lifetime_pnl_usd": last_pnl,
        "current_nav_usd": last_nav,
        "lifetime_last_point_ts_ms": last_ts,
        "portfolio_windows": list(portfolio.keys()),
        "portfolio_raw": vault_details.get("portfolio"),
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Hyperliquid daily snapshot.")
    ap.add_argument("--top", type=int, default=100,
                    help="Top-N active traders to fetch positions for (default 100).")
    ap.add_argument("--date", default=None,
                    help="UTC snapshot date YYYY-MM-DD (default: today UTC).")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite today's file even if it exists.")
    ap.add_argument("--sleep", type=float, default=PER_CALL_SLEEP_S,
                    help=f"Sleep between per-trader API calls (default {PER_CALL_SLEEP_S}s).")
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    if args.date:
        snapshot_dt = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        snapshot_dt = datetime.now(tz=timezone.utc)
    snapshot_date = snapshot_dt.strftime("%Y-%m-%d")
    snapshot_ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    out_path = OUT_DIR / f"leaderboard_{snapshot_date}.jsonl"
    if out_path.exists() and not args.force:
        log.info("Snapshot already exists at %s — exiting (use --force to overwrite).",
                 out_path.relative_to(ROOT))
        return 0

    t0 = time.time()

    # 1. Leaderboard
    rows = fetch_leaderboard()

    # 2 + 3. Active filter + top-N by 30d PnL
    chosen = filter_active_top_n(rows, args.top)

    # 4. Per-trader clearinghouseState
    log.info("Fetching clearinghouseState for %d traders (sleep=%.2fs/call) …",
             len(chosen), args.sleep)
    trader_rows: list[dict] = []
    misaligned = 0
    for i, lb in enumerate(chosen, start=1):
        addr = lb.get("ethAddress")
        try:
            state = info_post({"type": "clearinghouseState", "user": addr})
        except Exception as exc:
            log.warning("  [%d/%d] %s: clearinghouseState failed: %s",
                        i, len(chosen), addr, exc)
            state = {}
        chs = trim_clearinghouse(state)

        # Misalignment heuristic for the run summary (>50% gap).
        try:
            lb_val = float(lb.get("accountValue", 0))
            chs_val = float(chs.get("marginSummary", {}).get("accountValue", 0))
            if lb_val > 0 and (chs_val / lb_val) < 0.5:
                misaligned += 1
        except (TypeError, ValueError):
            pass

        trader_rows.append(
            build_trader_row(i, lb, chs, snapshot_date, snapshot_ts_ms)
        )

        if i % 25 == 0:
            log.info("  … %d/%d traders fetched", i, len(chosen))
        time.sleep(args.sleep)

    # 5. HLP vault
    log.info("Fetching HLP vaultDetails …")
    try:
        vault = info_post({"type": "vaultDetails", "vaultAddress": HLP_VAULT})
    except Exception as exc:
        log.warning("HLP vaultDetails failed: %s — writing stub row", exc)
        vault = {}
    hlp_row = build_hlp_row(vault, snapshot_date, snapshot_ts_ms)

    # Write JSONL atomically: write to .tmp then rename.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp, "w") as f:
        for r in trader_rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
        f.write(json.dumps(hlp_row, separators=(",", ":")) + "\n")
    tmp.replace(out_path)

    elapsed = time.time() - t0
    bytes_written = out_path.stat().st_size

    print("\n" + "=" * 72)
    print("HYPERLIQUID SNAPSHOT COMPLETE")
    print(f"  Date              : {snapshot_date} UTC")
    print(f"  Output            : {out_path.relative_to(ROOT)}")
    print(f"  Trader rows       : {len(trader_rows)}")
    print(f"  HLP rows          : 1 ({'OK' if vault else 'STUB — vaultDetails failed'})")
    print(f"  HLP lifetime PnL  : {hlp_row.get('lifetime_pnl_usd')}")
    print(f"  HLP current NAV   : {hlp_row.get('current_nav_usd')}")
    print(f"  Misaligned (>50%) : {misaligned} (master-vs-agent-wallet, expect ~⅓)")
    print(f"  Bytes written     : {bytes_written:,}")
    print(f"  Elapsed           : {elapsed:.1f}s")
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
