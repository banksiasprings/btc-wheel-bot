#!/usr/bin/env python3
"""
download_status.py — Live status dashboard for the Phase 1 data pipeline
Shows: instruments done/remaining, trades downloaded, file sizes, ETA

Usage: python3 data_pipeline/download_status.py
       python3 data_pipeline/download_status.py --watch   (refresh every 30s)
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG_DIR = DATA / "logs"
DERIBIT_DIR = DATA / "raw" / "deribit"
SPOT_DIR = DATA / "raw" / "spot"
PROGRESS_FILE = LOG_DIR / "bulk_progress.json"


def fmt_size(path: Path) -> str:
    if not path.exists():
        return "missing"
    size = path.stat().st_size
    if size < 1024:
        return f"{size} B"
    elif size < 1024 ** 2:
        return f"{size/1024:.1f} KB"
    elif size < 1024 ** 3:
        return f"{size/1024**2:.1f} MB"
    else:
        return f"{size/1024**3:.2f} GB"


def dir_size(path: Path) -> tuple[int, int]:
    """Returns (file_count, total_bytes)."""
    if not path.exists():
        return 0, 0
    files = list(path.rglob("*"))
    files = [f for f in files if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    return len(files), total


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}


def find_instrument_file() -> Path | None:
    files = sorted(DERIBIT_DIR.glob("instruments_*.json"), reverse=True)
    return files[0] if files else None


def count_total_instruments() -> int:
    inst_file = find_instrument_file()
    if not inst_file:
        return 0
    with open(inst_file) as f:
        data = json.load(f)
    return data.get("summary", {}).get("total_instruments", len(data.get("instruments", [])))


def print_status():
    print("\n" + "=" * 68)
    print(f"  BTC-RL v2 Data Pipeline Status  —  {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 68)

    # ── Instrument enumeration ────────────────────────────────────────────
    inst_file = find_instrument_file()
    if inst_file:
        with open(inst_file) as f:
            inst_data = json.load(f)
        summary = inst_data.get("summary", {})
        print(f"\n[Instruments]")
        print(f"  File     : {inst_file.name}")
        print(f"  Total    : {summary.get('total_instruments', '?'):,}")
        print(f"  Expired  : {summary.get('expired_count', '?'):,}")
        print(f"  Live     : {summary.get('live_count', '?'):,}")
        print(f"  Puts/Calls: {summary.get('puts', '?'):,} / {summary.get('calls', '?'):,}")
        print(f"  Date range: {summary.get('earliest_expiry', '?')[:10]} → {summary.get('latest_expiry', '?')[:10]}")
    else:
        print("\n[Instruments] ✗ Not yet downloaded — run enumerate_instruments.py")

    # ── Bulk options download progress ───────────────────────────────────
    progress = load_progress()
    if progress:
        completed = len(progress.get("completed", []))
        failed = len(progress.get("failed", []))
        total_trades = progress.get("total_trades", 0)
        total_inst = count_total_instruments() or (completed + failed)
        remaining = max(0, total_inst - completed - failed)
        pct = 100 * completed / total_inst if total_inst else 0

        print(f"\n[Bulk Options Download]")
        print(f"  Completed : {completed:,} / {total_inst:,} ({pct:.1f}%)")
        print(f"  Remaining : {remaining:,}")
        print(f"  Failed    : {failed:,}")
        print(f"  Trades    : {total_trades:,}")

        last_updated = progress.get("last_updated")
        if last_updated:
            print(f"  Updated   : {last_updated}")

        # ETA estimate from start time
        start_str = progress.get("start_time")
        if start_str and completed > 0:
            try:
                start_dt = datetime.fromisoformat(start_str)
                elapsed = (datetime.now(tz=timezone.utc) - start_dt).total_seconds()
                rate = completed / elapsed
                eta_sec = remaining / rate if rate > 0 else float("inf")
                eta_h = eta_sec / 3600
                print(f"  Rate      : {rate:.1f} inst/s")
                if eta_h < 1000:
                    print(f"  ETA       : {eta_h:.1f} hours")
                else:
                    print(f"  ETA       : very long (or download not active)")
            except Exception:
                pass
    else:
        print(f"\n[Bulk Options Download] Not yet started (or progress file missing)")

    # ── File sizes ────────────────────────────────────────────────────────
    print(f"\n[File Sizes]")

    # Spot
    for fname in ["btc_daily.csv", "btc_1h.csv"]:
        p = SPOT_DIR / fname
        print(f"  {fname:<25}: {fmt_size(p)}")

    # Deribit top-level
    for fname in ["iv_history.json", "dvol_history.json", "funding_rates.json"]:
        p = DERIBIT_DIR / fname
        print(f"  {fname:<25}: {fmt_size(p)}")

    # Bulk dirs
    for subdir_name in ["trades", "ohlcv"]:
        subdir = DERIBIT_DIR / subdir_name
        count, total = dir_size(subdir)
        size_str = f"{total/1024**2:.1f} MB" if total < 1024**3 else f"{total/1024**3:.2f} GB"
        print(f"  deribit/{subdir_name:<17}: {count:,} files, {size_str}")

    # Total data dir
    _, total_bytes = dir_size(DATA)
    total_gb = total_bytes / 1024 ** 3
    print(f"  {'TOTAL data/':<25}: {total_gb:.3f} GB")

    # ── Log tail ─────────────────────────────────────────────────────────
    log_file = LOG_DIR / "download.log"
    if log_file.exists():
        print(f"\n[Recent Log]")
        with open(log_file) as f:
            lines = f.readlines()
        for line in lines[-6:]:
            print(f"  {line.rstrip()}")

    print("=" * 68)


def main():
    parser = argparse.ArgumentParser(description="Data pipeline status dashboard")
    parser.add_argument("--watch", action="store_true", help="Refresh every 30 seconds")
    parser.add_argument("--interval", type=int, default=30, help="Watch interval in seconds")
    args = parser.parse_args()

    if args.watch:
        print("Watching... (Ctrl-C to stop)")
        while True:
            os.system("clear")
            print_status()
            time.sleep(args.interval)
    else:
        print_status()


if __name__ == "__main__":
    main()
