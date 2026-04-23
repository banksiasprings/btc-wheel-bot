"""
bot_farm.py — Supervisor that runs N paper-trading bots simultaneously.

Each bot runs in its own isolated subdirectory under farm/:
    farm/bot_0/config.yaml        ← merged config (base + overrides)
    farm/bot_0/data/              ← trades, state, logs (isolated)
    farm/bot_0/KILL_SWITCH        ← per-bot kill switch

The supervisor:
  - Reads farm_config.yaml to know how many bots to launch
  - Merges each bot's overrides onto the base config.yaml
  - Launches each bot as a subprocess (python main.py --mode=paper --data-dir=...)
  - Writes farm/status.json every status_interval_seconds
  - Handles SIGTERM by sending SIGTERM to all children and waiting

Usage:
    python bot_farm.py [--config farm_config.yaml]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from readiness_validator import validate_bot, ReadinessReport

# ── Helpers ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base (returns a new dict)."""
    result = copy.deepcopy(base)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        print(f"[farm] Failed to read {path}: {exc}", flush=True)
        return {}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _read_csv_trades(path: Path) -> list[dict]:
    import csv
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


# ── Bot metrics ────────────────────────────────────────────────────────────────

def _compute_bot_metrics(bot_dir: Path, starting_equity: float) -> dict:
    """Read a bot's data files and return metrics dict."""
    trades_path = bot_dir / "data" / "trades.csv"
    trades = _read_csv_trades(trades_path)

    # Fallback: backtest trades
    if not trades:
        bt_path = bot_dir / "data" / "backtest_trades.csv"
        trades = _read_csv_trades(bt_path)

    if not trades:
        return {
            "num_trades":       0,
            "win_rate":         0.0,
            "total_return_pct": 0.0,
            "sharpe":           0.0,
            "max_drawdown":     0.0,
            "current_equity":   starting_equity,
            "starting_equity":  starting_equity,
        }

    import math

    pnls = [float(t.get("pnl_usd", 0) or 0) for t in trades]
    num_trades = len(trades)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / num_trades if num_trades > 0 else 0.0

    equity_series: list[float] = []
    for t in trades:
        v = t.get("equity_after")
        if v is not None:
            try:
                equity_series.append(float(v))
            except (ValueError, TypeError):
                pass

    current_equity = equity_series[-1] if equity_series else starting_equity
    total_return_pct = (
        (current_equity - starting_equity) / starting_equity * 100
        if starting_equity > 0 else 0.0
    )

    # Sharpe
    returns = [p / starting_equity for p in pnls if starting_equity > 0]
    sharpe = 0.0
    if len(returns) >= 2:
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(variance)
        periods_per_year = 120
        if std_r > 0:
            sharpe = (mean_r * periods_per_year) / (std_r * math.sqrt(periods_per_year))

    # Max drawdown
    max_drawdown = 0.0
    if equity_series:
        peak = starting_equity
        for eq in equity_series:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

    return {
        "num_trades":       num_trades,
        "win_rate":         round(win_rate, 4),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe":           round(sharpe, 4),
        "max_drawdown":     round(max_drawdown, 4),
        "current_equity":   round(current_equity, 2),
        "starting_equity":  round(starting_equity, 2),
    }


def _readiness_to_dict(report: ReadinessReport) -> dict:
    return {
        "score":  report.checks_passed,
        "total":  report.total_checks,
        "ready":  report.ready,
        "checks": report.checks,
    }


# ── BotProcess ────────────────────────────────────────────────────────────────

class BotProcess:
    """Manages a single bot subprocess."""

    def __init__(self, bot_cfg: dict, farm_dir: Path, base_config: dict, thresholds: dict):
        self.bot_id: str         = bot_cfg["id"]
        self.name: str           = bot_cfg.get("name", self.bot_id)
        self.description: str    = bot_cfg.get("description", "")
        self.overrides: dict     = bot_cfg.get("overrides", {})
        self.farm_dir: Path      = farm_dir
        self.bot_dir: Path       = farm_dir / self.bot_id
        self.base_config: dict   = base_config
        self.thresholds: dict    = thresholds
        self.started_at: float   = 0.0
        self.proc: subprocess.Popen | None = None

        # Derive starting equity from merged config
        merged = _deep_merge(self.base_config, self.overrides)
        self.starting_equity: float = float(
            merged.get("backtest", {}).get("starting_equity", 10_000.0)
        )

        # Summary of config keys for status.json
        strat = merged.get("strategy", {})
        sizing = merged.get("sizing", {})
        self.config_summary: dict = {
            "iv_rank_threshold":  strat.get("iv_rank_threshold"),
            "target_delta_min":   strat.get("target_delta_min"),
            "target_delta_max":   strat.get("target_delta_max"),
            "max_equity_per_leg": sizing.get("max_equity_per_leg"),
            "starting_equity":    self.starting_equity,
        }

    def setup_dir(self) -> None:
        """Create the bot's directory tree and write its merged config.yaml."""
        self.bot_dir.mkdir(parents=True, exist_ok=True)
        (self.bot_dir / "data").mkdir(exist_ok=True)
        (self.bot_dir / "logs").mkdir(exist_ok=True)

        merged = _deep_merge(self.base_config, self.overrides)
        # Force paper / testnet mode regardless of base config
        merged.setdefault("deribit", {})["testnet"] = True

        with open(self.bot_dir / "config.yaml", "w") as f:
            yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)

    def start(self) -> None:
        """Spawn the bot subprocess."""
        self.setup_dir()

        log_path = self.bot_dir / "logs" / "bot.log"
        log_file = open(log_path, "a")

        # We pass --data-dir so the bot writes its files into the bot's own data/
        # directory rather than the repo-level data/ dir.
        cmd = [
            sys.executable,
            str(BASE_DIR / "main.py"),
            "--mode=paper",
        ]

        env = os.environ.copy()
        # Point WHEEL_BOT_CONFIG to this bot's config so Config() picks it up.
        # The bot reads WHEEL_BOT_CONFIG if set, otherwise falls back to config.yaml.
        env["WHEEL_BOT_CONFIG"]    = str(self.bot_dir / "config.yaml")
        env["WHEEL_BOT_DATA_DIR"]  = str(self.bot_dir / "data")

        self.proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        self.started_at = time.time()
        print(f"[farm] Started {self.bot_id} (pid={self.proc.pid})", flush=True)

    def is_running(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def status_str(self) -> str:
        if self.proc is None:
            return "stopped"
        rc = self.proc.poll()
        if rc is None:
            return "running"
        return "error" if rc != 0 else "stopped"

    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def uptime_hours(self) -> float:
        if self.started_at == 0 or not self.is_running():
            return 0.0
        return (time.time() - self.started_at) / 3600

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            print(f"[farm] Stopped {self.bot_id}", flush=True)

    def to_status_dict(self) -> dict:
        metrics = _compute_bot_metrics(self.bot_dir, self.starting_equity)
        report  = validate_bot(self.bot_dir, self.thresholds, self.starting_equity)
        uptime  = self.uptime_hours()

        return {
            "id":             self.bot_id,
            "name":           self.name,
            "description":    self.description,
            "status":         self.status_str(),
            "pid":            self.pid(),
            "uptime_hours":   round(uptime, 2),
            "days_running":   round(uptime / 24, 2),
            "config_summary": self.config_summary,
            "metrics":        metrics,
            "readiness":      _readiness_to_dict(report),
        }


# ── BotFarm ───────────────────────────────────────────────────────────────────

class BotFarm:
    """Supervisor that owns all BotProcess instances."""

    def __init__(self, config_path: Path = BASE_DIR / "farm_config.yaml"):
        self.config_path = config_path
        self._load_config()
        self._bots: list[BotProcess] = []
        self._shutdown = False

        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT,  self._handle_sigterm)

    def _load_config(self) -> None:
        raw = _read_yaml(self.config_path)
        farm_sec         = raw.get("farm", {})
        self.farm_dir    = BASE_DIR / farm_sec.get("data_dir", "farm")
        self.status_interval = int(farm_sec.get("status_interval_seconds", 60))
        self.bot_specs   = raw.get("bots", [])
        self.thresholds  = raw.get("readiness_thresholds", {})

    def _handle_sigterm(self, signum, frame) -> None:
        print("[farm] SIGTERM received — shutting down all bots…", flush=True)
        self._shutdown = True

    def _get_base_config(self, base_config_name: str) -> dict:
        path = BASE_DIR / base_config_name
        return _read_yaml(path)

    def setup(self) -> None:
        """Prepare bot directories and BotProcess instances."""
        self.farm_dir.mkdir(parents=True, exist_ok=True)

        for spec in self.bot_specs:
            base_cfg = self._get_base_config(spec.get("base_config", "config.yaml"))
            bp = BotProcess(
                bot_cfg=spec,
                farm_dir=self.farm_dir,
                base_config=base_cfg,
                thresholds=self.thresholds,
            )
            self._bots.append(bp)

    def start_all(self) -> None:
        for bot in self._bots:
            bot.start()
            time.sleep(1)  # stagger launches slightly

    def stop_all(self) -> None:
        for bot in self._bots:
            bot.stop()

    def write_status(self) -> None:
        status = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "bots": [bot.to_status_dict() for bot in self._bots],
        }
        _write_json(self.farm_dir / "status.json", status)

    def run(self) -> None:
        """Main supervisor loop."""
        self.setup()
        self.start_all()

        print(f"[farm] {len(self._bots)} bots running. Status every {self.status_interval}s", flush=True)

        last_status = 0.0
        while not self._shutdown:
            now = time.time()

            # Restart crashed bots
            for bot in self._bots:
                if not bot.is_running() and not self._shutdown:
                    print(f"[farm] {bot.bot_id} crashed — restarting…", flush=True)
                    bot.start()

            # Write status file
            if now - last_status >= self.status_interval:
                try:
                    self.write_status()
                except Exception as exc:
                    print(f"[farm] Failed to write status: {exc}", flush=True)
                last_status = now

            time.sleep(5)

        # Graceful shutdown
        self.stop_all()
        # Final status snapshot
        try:
            self.write_status()
        except Exception:
            pass
        print("[farm] All bots stopped. Exiting.", flush=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="BTC Wheel Bot Farm Supervisor")
    parser.add_argument(
        "--config",
        default=str(BASE_DIR / "farm_config.yaml"),
        help="Path to farm_config.yaml",
    )
    args = parser.parse_args()

    farm = BotFarm(config_path=Path(args.config))
    farm.run()


if __name__ == "__main__":
    main()
