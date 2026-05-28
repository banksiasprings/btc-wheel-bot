"""
readiness_validator.py — Go/no-go checklist for bot farm bots.

Each bot's paper-trading data is inspected and scored against 8 checks.
Only a bot that passes all 8 is considered ready to promote to live trading.

Usage:
    from readiness_validator import validate_bot, validate_all_bots

    report = validate_bot("farm/bot_0", thresholds={})
    print(report.recommendation)

    reports = validate_all_bots("farm")
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Default thresholds (overridden by farm_config.yaml readiness_thresholds) ──

DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_trades":                   20,
    "min_days":                     30,
    "min_sharpe":                   0.8,
    "max_drawdown":                 0.15,
    "min_win_rate":                 0.55,
    "min_walk_forward_robustness":  0.75,
    "min_reconcile_accuracy":       0.80,
}


@dataclass
class ReadinessReport:
    bot_id: str
    ready: bool
    checks_passed: int
    total_checks: int
    checks: dict[str, bool]
    metrics: dict[str, float]
    recommendation: str   # "READY FOR LIVE" | "KEEP TESTING" | "FAILED — REVIEW CONFIG"
    blocking_issues: list[str] = field(default_factory=list)


# ── CSV / JSON helpers ─────────────────────────────────────────────────────────

def _read_trades_csv(path: Path) -> list[dict]:
    """Return all rows from trades.csv as dicts. Returns [] if missing."""
    if not path.exists():
        return []
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ── Metric calculations ────────────────────────────────────────────────────────

def _compute_metrics(trades: list[dict], starting_equity: float) -> dict[str, float]:
    """Derive all metrics needed for the checklist from the closed-trade list."""
    if not trades:
        return {
            "num_trades": 0,
            "win_rate": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "total_return_pct": 0.0,
            "current_equity": starting_equity,
            "starting_equity": starting_equity,
            "days_running": 0.0,
        }

    # --- basic counts ---
    num_trades = len(trades)
    wins = sum(1 for t in trades if float(t.get("pnl_usd", 0) or 0) > 0)
    win_rate = wins / num_trades if num_trades > 0 else 0.0

    # --- PnL series ---
    pnls = [float(t.get("pnl_usd", 0) or 0) for t in trades]

    # --- equity curve ---
    equity_after_values = []
    for t in trades:
        v = t.get("equity_after")
        if v is not None:
            try:
                equity_after_values.append(float(v))
            except (ValueError, TypeError):
                pass

    current_equity = equity_after_values[-1] if equity_after_values else starting_equity
    total_return_pct = (
        (current_equity - starting_equity) / starting_equity * 100
        if starting_equity > 0 else 0.0
    )

    # --- Sharpe ratio (annualised, using daily PnL proxy) ---
    # We treat each trade PnL as a "period" return on starting equity
    import math
    returns = [p / starting_equity for p in pnls if starting_equity > 0]
    if len(returns) >= 2:
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(variance)
        # Scale to annual: assume ~10 trades / month → 120 per year
        periods_per_year = 120
        sharpe = (mean_r * periods_per_year) / (std_r * math.sqrt(periods_per_year)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # --- max drawdown ---
    max_drawdown = 0.0
    if equity_after_values:
        peak = starting_equity
        for eq in equity_after_values:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

    # --- days running ---
    timestamps = []
    for t in trades:
        ts_str = t.get("timestamp") or t.get("entry_date") or ""
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                timestamps.append(ts)
            except ValueError:
                pass

    days_running = 0.0
    if len(timestamps) >= 2:
        timestamps.sort()
        days_running = (timestamps[-1] - timestamps[0]).total_seconds() / 86_400
    elif timestamps:
        now = datetime.now(timezone.utc)
        days_running = (now - timestamps[0].replace(tzinfo=timezone.utc)
                        if timestamps[0].tzinfo is None
                        else now - timestamps[0]).total_seconds() / 86_400

    return {
        "num_trades":       float(num_trades),
        "win_rate":         win_rate,
        "sharpe":           round(sharpe, 4),
        "max_drawdown":     round(max_drawdown, 4),
        "total_return_pct": round(total_return_pct, 4),
        "current_equity":   round(current_equity, 2),
        "starting_equity":  round(starting_equity, 2),
        "days_running":     round(days_running, 2),
    }


# ── Core validator ─────────────────────────────────────────────────────────────

def validate_bot(
    bot_dir: str | Path,
    thresholds: dict[str, float] | None = None,
    starting_equity: float = 10_000.0,
) -> ReadinessReport:
    """
    Run the 8-check go/no-go checklist for a single bot directory.

    bot_dir:  path to e.g. farm/bot_0/ — must contain data/trades.csv
    thresholds: override DEFAULT_THRESHOLDS (typically from farm_config.yaml)
    starting_equity: fallback equity if not readable from trades/config
    """
    bot_dir = Path(bot_dir)
    bot_id = bot_dir.name

    thr = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    # --- Load data files ---
    trades_path   = bot_dir / "data" / "trades.csv"
    state_path    = bot_dir / "data" / "bot_state.json"
    opt_dir       = bot_dir / "data" / "optimizer"
    wf_path       = opt_dir / "walk_forward_results.json"
    recon_path    = opt_dir / "reconcile_results.json"

    # Try reading starting_equity from bot config
    bot_cfg_path = bot_dir / "config.yaml"
    if bot_cfg_path.exists():
        try:
            import yaml
            raw = yaml.safe_load(bot_cfg_path.read_text()) or {}
            starting_equity = float(raw.get("backtest", {}).get("starting_equity", starting_equity))
        except Exception:
            pass

    trades = _read_trades_csv(trades_path)

    # Also load backtest trades if live trades are empty
    if not trades:
        bt_path = bot_dir / "data" / "backtest_trades.csv"
        trades = _read_trades_csv(bt_path)

    metrics = _compute_metrics(trades, starting_equity)

    # --- Walk-forward robustness ---
    wf_data = _read_json(wf_path)
    wf_robustness: float | None = None
    if wf_data and "robustness_score" in wf_data:
        wf_robustness = float(wf_data["robustness_score"])

    # --- Reconcile accuracy ---
    recon_data = _read_json(recon_path)
    recon_accuracy: float | None = None
    if recon_data and "metrics" in recon_data:
        recon_accuracy = recon_data["metrics"].get("accuracy")
        if recon_accuracy is not None:
            recon_accuracy = float(recon_accuracy)

    # --- Kill switch check ---
    kill_switch_absent = not (bot_dir / "KILL_SWITCH").exists()

    # ── Run 8 checks ─────────────────────────────────────────────────────────

    checks: dict[str, bool] = {}
    blocking_issues: list[str] = []

    # 1. Min trades
    checks["min_trades"] = metrics["num_trades"] >= thr["min_trades"]
    if not checks["min_trades"]:
        remaining = int(thr["min_trades"] - metrics["num_trades"])
        blocking_issues.append(
            f"Need {remaining} more closed trades (have {int(metrics['num_trades'])}, need {int(thr['min_trades'])})"
        )

    # 2. Min days
    checks["min_days"] = metrics["days_running"] >= thr["min_days"]
    if not checks["min_days"]:
        remaining_days = thr["min_days"] - metrics["days_running"]
        blocking_issues.append(
            f"Need {remaining_days:.0f} more days running (have {metrics['days_running']:.1f}d, need {thr['min_days']:.0f}d)"
        )

    # 3. Sharpe ratio
    checks["sharpe"] = metrics["sharpe"] >= thr["min_sharpe"]
    if not checks["sharpe"]:
        blocking_issues.append(
            f"Sharpe ratio too low ({metrics['sharpe']:.2f}, need ≥ {thr['min_sharpe']:.2f})"
        )

    # 4. Max drawdown
    checks["drawdown"] = metrics["max_drawdown"] < thr["max_drawdown"]
    if not checks["drawdown"]:
        blocking_issues.append(
            f"Max drawdown too high ({metrics['max_drawdown']*100:.1f}%, limit {thr['max_drawdown']*100:.0f}%)"
        )

    # 5. Win rate
    checks["win_rate"] = metrics["win_rate"] >= thr["min_win_rate"]
    if not checks["win_rate"]:
        blocking_issues.append(
            f"Win rate too low ({metrics['win_rate']*100:.1f}%, need ≥ {thr['min_win_rate']*100:.0f}%)"
        )

    # 6. Walk-forward robustness
    if wf_robustness is not None:
        checks["walk_forward"] = wf_robustness >= thr["min_walk_forward_robustness"]
        if not checks["walk_forward"]:
            blocking_issues.append(
                f"Walk-forward robustness too low ({wf_robustness:.2f}, need ≥ {thr['min_walk_forward_robustness']:.2f})"
            )
    else:
        checks["walk_forward"] = False
        blocking_issues.append("Walk-forward test not yet run — run optimizer walk_forward mode")

    # 7. Reconcile accuracy
    if recon_accuracy is not None:
        checks["reconcile"] = recon_accuracy >= thr["min_reconcile_accuracy"]
        if not checks["reconcile"]:
            blocking_issues.append(
                f"Reconcile accuracy too low ({recon_accuracy*100:.1f}%, need ≥ {thr['min_reconcile_accuracy']*100:.0f}%)"
            )
    else:
        checks["reconcile"] = False
        blocking_issues.append("Reconciliation not yet run — run optimizer reconcile mode")

    # 8. No kill switch
    checks["no_kill_switch"] = kill_switch_absent
    if not checks["no_kill_switch"]:
        blocking_issues.append("KILL_SWITCH file is present — bot is halted")

    # ── Score and recommendation ──────────────────────────────────────────────

    checks_passed = sum(1 for v in checks.values() if v)
    total_checks  = len(checks)
    ready         = checks_passed == total_checks

    if ready:
        recommendation = "READY FOR LIVE"
    elif checks_passed >= 6:
        recommendation = "KEEP TESTING"
    else:
        recommendation = "FAILED — REVIEW CONFIG"

    return ReadinessReport(
        bot_id=bot_id,
        ready=ready,
        checks_passed=checks_passed,
        total_checks=total_checks,
        checks=checks,
        metrics=metrics,
        recommendation=recommendation,
        blocking_issues=blocking_issues,
    )


def validate_all_bots(
    farm_dir: str | Path,
    thresholds: dict[str, float] | None = None,
) -> list[ReadinessReport]:
    """
    Validate every bot subdirectory found in farm_dir.

    Returns a list of ReadinessReport, one per bot.
    """
    farm_dir = Path(farm_dir)
    reports: list[ReadinessReport] = []

    if not farm_dir.exists():
        return reports

    # Collect subdirectories that look like bot dirs (bot_0, bot_1, …)
    bot_dirs = sorted(
        [d for d in farm_dir.iterdir() if d.is_dir() and d.name.startswith("bot_")],
        key=lambda d: d.name,
    )

    for bot_dir in bot_dirs:
        report = validate_bot(bot_dir, thresholds=thresholds)
        reports.append(report)

    return reports


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Validate bot farm readiness")
    parser.add_argument("--farm-dir", default="farm", help="Farm root directory")
    parser.add_argument("--bot-id",   help="Only validate this bot (e.g. bot_0)")
    args = parser.parse_args()

    farm_path = Path(args.farm_dir)

    if args.bot_id:
        bot_path = farm_path / args.bot_id
        if not bot_path.exists():
            print(f"Bot directory not found: {bot_path}")
            sys.exit(1)
        reports = [validate_bot(bot_path)]
    else:
        reports = validate_all_bots(farm_path)

    if not reports:
        print(f"No bot directories found in {farm_path}")
        sys.exit(0)

    for r in reports:
        status_icon = "✅" if r.ready else ("🟡" if r.checks_passed >= 6 else "❌")
        print(f"\n{status_icon} {r.bot_id}  [{r.checks_passed}/{r.total_checks}]  {r.recommendation}")
        print(f"   Trades: {int(r.metrics['num_trades'])}  "
              f"Days: {r.metrics['days_running']:.1f}  "
              f"Win: {r.metrics['win_rate']*100:.1f}%  "
              f"Sharpe: {r.metrics['sharpe']:.2f}  "
              f"DD: {r.metrics['max_drawdown']*100:.1f}%  "
              f"Return: {r.metrics['total_return_pct']:.1f}%")
        for issue in r.blocking_issues:
            print(f"   - {issue}")
