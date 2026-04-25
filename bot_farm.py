"""
bot_farm.py — Supervisor that runs paper-trading bots for all configs with status='paper'.

Each bot runs in its own isolated subdirectory under farm/:
    farm/{slug}/config.yaml        ← merged config (base + overrides)
    farm/{slug}/data/              ← trades, state, logs (isolated)
    farm/{slug}/KILL_SWITCH        ← per-bot kill switch

The supervisor:
  - Calls get_paper_configs() every 60 seconds to discover which configs are paper-status
  - Starts new bots for any paper config not yet running
  - Stops bots whose config status changed away from 'paper'
  - Writes farm/status.json every status_interval_seconds
  - Handles SIGTERM by sending SIGTERM to all children and waiting

DEPRECATED: farm_config.yaml-based bot setup is still supported for backward compatibility
but new bots are discovered dynamically from configs/ with status='paper'.

Usage:
    python bot_farm.py [--config farm_config.yaml]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
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


def _slugify(name: str) -> str:
    """Convert a config name to a filesystem-safe slug for bot directory naming."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "config"


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


# ── Position risk helper ──────────────────────────────────────────────────────

def _position_risk_level(pos: dict) -> str:
    """
    Compute risk level for an open short option position.

    Returns 'ok', 'caution', or 'danger'.

    Caution triggers (any one sufficient):
      - BTC spot within 8% of strike (put: spot < strike + 8%; call: spot > strike - 8%)
      - |delta| > 0.35 (option approaching ATM)
      - Unrealized loss > 20% of premium collected

    Danger triggers (any one sufficient):
      - BTC spot has crossed the strike (option is ITM)
      - |delta| > 0.45
      - Unrealized loss > 50% of premium collected
    """
    strike   = pos.get("strike") or 0.0
    spot     = pos.get("current_spot") or 0.0
    delta    = abs(pos.get("current_delta") or 0.0)
    pnl_usd  = pos.get("unrealized_pnl_usd") or 0.0
    premium  = pos.get("premium_collected") or 0.0
    opt_type = (pos.get("type") or "").lower()

    if strike <= 0 or spot <= 0:
        return "ok"

    loss_pct = (-pnl_usd / premium * 100) if premium > 0 else 0.0  # positive = losing

    if "put" in opt_type:
        distance_pct = (spot - strike) / strike * 100  # negative means ITM
        itm = distance_pct < 0
        near = distance_pct < 8
    elif "call" in opt_type:
        distance_pct = (strike - spot) / strike * 100  # negative means ITM
        itm = distance_pct < 0
        near = distance_pct < 8
    else:
        return "ok"

    if itm or delta > 0.45 or loss_pct > 50:
        return "danger"
    if near or delta > 0.35 or loss_pct > 20:
        return "caution"
    return "ok"


# ── BotProcess ────────────────────────────────────────────────────────────────

class BotProcess:
    """Manages a single bot subprocess."""

    def __init__(self, bot_cfg: dict, farm_dir: Path, base_config: dict, thresholds: dict):
        self.bot_id: str         = bot_cfg["id"]
        self.name: str           = bot_cfg.get("name", self.bot_id)
        self.description: str    = bot_cfg.get("description", "")
        self.overrides: dict     = bot_cfg.get("overrides", {})
        self.config_path: str | None = bot_cfg.get("config_path")  # direct path for paper configs
        self.farm_dir: Path      = farm_dir
        self.bot_dir: Path       = farm_dir / self.bot_id
        self.base_config: dict   = base_config
        self.thresholds: dict    = thresholds
        self.started_at: float   = 0.0
        self.proc: subprocess.Popen | None = None

        # Derive starting equity from merged config
        if self.config_path and Path(self.config_path).exists():
            source_cfg = _read_yaml(Path(self.config_path))
            # Strip _meta so it doesn't interfere with config structure
            source_cfg.pop("_meta", None)
            merged = source_cfg
        else:
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

        if self.config_path and Path(self.config_path).exists():
            # For paper configs: use the named config file directly (via env var)
            # No need to copy — WHEEL_BOT_CONFIG env var points to the source
            # But we do need a local config.yaml for readiness_validator
            source = _read_yaml(Path(self.config_path))
            # Force paper/testnet mode
            source.setdefault("deribit", {})["testnet"] = True
            with open(self.bot_dir / "config.yaml", "w") as f:
                yaml.dump(source, f, default_flow_style=False, allow_unicode=True)
        else:
            # Legacy: preserve existing _meta if present
            existing_meta = {}
            existing_config_path = self.bot_dir / "config.yaml"
            if existing_config_path.exists():
                try:
                    existing_raw = _read_yaml(existing_config_path)
                    existing_meta = existing_raw.get("_meta", {})
                except Exception:
                    pass

            merged = _deep_merge(self.base_config, self.overrides)
            # Force paper / testnet mode regardless of base config
            merged.setdefault("deribit", {})["testnet"] = True

            # Restore _meta so config_name is preserved across restarts
            if existing_meta:
                merged["_meta"] = existing_meta

            with open(self.bot_dir / "config.yaml", "w") as f:
                yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)

    def start(self) -> None:
        """Spawn the bot subprocess."""
        self.setup_dir()

        log_path = self.bot_dir / "logs" / "bot.log"
        log_file = open(log_path, "a")

        cmd = [
            sys.executable,
            str(BASE_DIR / "main.py"),
            "--mode=paper",
        ]

        env = os.environ.copy()
        if self.config_path and Path(self.config_path).exists():
            # Paper config: point bot at the named config file directly
            env["WHEEL_BOT_CONFIG"]   = self.config_path
        else:
            # Legacy: use the per-bot config.yaml
            env["WHEEL_BOT_CONFIG"]   = str(self.bot_dir / "config.yaml")
        env["WHEEL_BOT_DATA_DIR"] = str(self.bot_dir / "data")

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

    def _current_config_name(self) -> str:
        """Read config_name from _meta in the bot's config.yaml, else 'custom'."""
        try:
            raw = _read_yaml(self.bot_dir / "config.yaml")
            return raw.get("_meta", {}).get("name") or "custom"
        except Exception:
            return "custom"

    def _current_config_status(self) -> str | None:
        """Read status from _meta in the source config file."""
        try:
            cfg_file = Path(self.config_path) if self.config_path else self.bot_dir / "config.yaml"
            raw = _read_yaml(cfg_file)
            return raw.get("_meta", {}).get("status")
        except Exception:
            return None

    def check_restart_requested(self) -> bool:
        """
        If RESTART_REQUESTED sentinel exists, reload the bot's config summary,
        remove the sentinel, stop then start the bot, and return True.
        """
        sentinel = self.bot_dir / "RESTART_REQUESTED"
        if not sentinel.exists():
            return False
        try:
            assigned_name = sentinel.read_text().strip()
            print(f"[farm] {self.bot_id}: restart requested (config={assigned_name!r})", flush=True)
            sentinel.unlink(missing_ok=True)
            # Reload config summary from updated config.yaml
            merged = _read_yaml(self.bot_dir / "config.yaml")
            strat  = merged.get("strategy", {})
            sizing = merged.get("sizing", {})
            self.starting_equity = float(merged.get("backtest", {}).get("starting_equity", self.starting_equity))
            self.config_summary = {
                "iv_rank_threshold":  strat.get("iv_rank_threshold"),
                "target_delta_min":   strat.get("target_delta_min"),
                "target_delta_max":   strat.get("target_delta_max"),
                "max_equity_per_leg": sizing.get("max_equity_per_leg"),
                "starting_equity":    self.starting_equity,
            }
            if self.is_running():
                self.stop()
            self.start()
        except Exception as exc:
            print(f"[farm] {self.bot_id}: restart failed: {exc}", flush=True)
        return True

    def to_status_dict(self) -> dict:
        metrics = _compute_bot_metrics(self.bot_dir, self.starting_equity)
        report  = validate_bot(self.bot_dir, self.thresholds, self.starting_equity)
        uptime  = self.uptime_hours()

        # Check for an open position (lightweight read of current_position.json)
        has_open_position = False
        open_position_summary: dict = {}
        position_risk = "ok"
        try:
            pos_path = self.bot_dir / "data" / "current_position.json"
            if pos_path.exists():
                import json as _json
                pos = _json.loads(pos_path.read_text())
                if pos.get("open"):
                    has_open_position = True
                    open_position_summary = {
                        "type":             pos.get("type"),
                        "strike":           pos.get("strike"),
                        "expiry":           pos.get("expiry"),
                        "dte":              pos.get("days_to_expiry"),
                        "pnl_usd":          pos.get("unrealized_pnl_usd"),
                        "pnl_pct":          pos.get("unrealized_pnl_pct"),
                        "current_spot":     pos.get("current_spot"),
                        "current_delta":    pos.get("current_delta"),
                        "premium_collected": pos.get("premium_collected"),
                    }
                    position_risk = _position_risk_level(pos)
        except Exception:
            pass

        return {
            "id":                   self.bot_id,
            "name":                 self.name,
            "description":          self.description,
            "status":               self.status_str(),
            "pid":                  self.pid(),
            "uptime_hours":         round(uptime, 2),
            "days_running":         round(uptime / 24, 2),
            "config_name":          self._current_config_name(),
            "config_status":        self._current_config_status(),
            "config_summary":       self.config_summary,
            "metrics":              metrics,
            "readiness":            _readiness_to_dict(report),
            "has_open_position":    has_open_position,
            "open_position":        open_position_summary if has_open_position else None,
            "position_risk":        position_risk,
        }


# ── BotFarm ───────────────────────────────────────────────────────────────────

class BotFarm:
    """Supervisor that owns all BotProcess instances."""

    def __init__(self, config_path: Path = BASE_DIR / "farm_config.yaml"):
        self.config_path = config_path
        self._load_config()
        self._bots: dict[str, BotProcess] = {}  # keyed by bot_id
        self._shutdown = False

        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT,  self._handle_sigterm)

    def _load_config(self) -> None:
        # DEPRECATED: farm_config.yaml is used only for readiness thresholds and
        # backward-compatible legacy bot specs. New bots are discovered dynamically
        # from configs/ with status='paper'.
        raw = _read_yaml(self.config_path)
        farm_sec         = raw.get("farm", {})
        self.farm_dir    = BASE_DIR / farm_sec.get("data_dir", "farm")
        self.status_interval = int(farm_sec.get("status_interval_seconds", 60))
        self.legacy_bot_specs = raw.get("bots", [])
        self.thresholds  = raw.get("readiness_thresholds", {})

    def _handle_sigterm(self, signum, frame) -> None:
        print("[farm] SIGTERM received — shutting down all bots…", flush=True)
        self._shutdown = True

    def _get_base_config(self, base_config_name: str) -> dict:
        path = BASE_DIR / base_config_name
        return _read_yaml(path)

    def _discover_paper_bots(self) -> list[dict]:
        """
        Scan configs/ directory for configs with status='paper'.
        Returns list of bot specs: {id, name, config_path, data_dir}
        Bot ID is derived from config name (slugified):
        'High-IV Aggressive' -> 'high-iv-aggressive'
        """
        try:
            from config_store import get_paper_configs
            paper_configs = get_paper_configs()
        except Exception as exc:
            print(f"[farm] Failed to discover paper configs: {exc}", flush=True)
            return []

        specs = []
        for cfg in paper_configs:
            name = cfg.get("name", "")
            slug = _slugify(name)
            specs.append({
                "id":          slug,
                "name":        name,
                "description": f"Paper trading: {name}",
                "config_path": cfg.get("config_path", ""),
                "data_dir":    str(self.farm_dir / slug / "data"),
            })
        return specs

    def _sync_paper_bots(self) -> None:
        """
        Reconcile running bots against current paper configs.
        - Start new bots for any paper config not yet running.
        - Stop bots whose config status changed away from 'paper'.
        """
        paper_specs = self._discover_paper_bots()
        paper_ids   = {spec["id"] for spec in paper_specs}

        # Start new paper bots
        for spec in paper_specs:
            bot_id = spec["id"]
            if bot_id not in self._bots:
                print(f"[farm] Discovered new paper config: {spec['name']!r} -> {bot_id}", flush=True)
                bp = BotProcess(
                    bot_cfg    = spec,
                    farm_dir   = self.farm_dir,
                    base_config= {},
                    thresholds = self.thresholds,
                )
                self._bots[bot_id] = bp
                bp.start()
                time.sleep(1)  # stagger launches

        # Stop bots that are no longer paper
        for bot_id in list(self._bots.keys()):
            bot = self._bots[bot_id]
            # Only manage dynamically-discovered bots (those with config_path set)
            if not bot.config_path:
                continue
            if bot_id not in paper_ids:
                print(f"[farm] Config {bot.name!r} no longer paper — stopping bot {bot_id}", flush=True)
                bot.stop()
                del self._bots[bot_id]

    def setup(self) -> None:
        """Prepare bot directories and BotProcess instances from legacy farm_config.yaml."""
        self.farm_dir.mkdir(parents=True, exist_ok=True)

        # Legacy: load bots from farm_config.yaml
        for spec in self.legacy_bot_specs:
            bot_id = spec.get("id")
            if not bot_id or bot_id in self._bots:
                continue
            base_cfg = self._get_base_config(spec.get("base_config", "config.yaml"))
            bp = BotProcess(
                bot_cfg    = spec,
                farm_dir   = self.farm_dir,
                base_config= base_cfg,
                thresholds = self.thresholds,
            )
            self._bots[bot_id] = bp

    def start_all(self) -> None:
        for bot in self._bots.values():
            bot.start()
            time.sleep(1)  # stagger launches slightly

    def stop_all(self) -> None:
        for bot in self._bots.values():
            bot.stop()

    def write_status(self) -> None:
        status = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "bots": [bot.to_status_dict() for bot in self._bots.values()],
        }
        _write_json(self.farm_dir / "status.json", status)

    def run(self) -> None:
        """Main supervisor loop."""
        self.setup()

        # If we have legacy farm_config.yaml bots, start them
        if self._bots:
            self.start_all()
            print(f"[farm] {len(self._bots)} legacy bots started. Switching to dynamic discovery.", flush=True)
        else:
            print("[farm] No legacy bots. Running in dynamic discovery mode.", flush=True)

        # Initial paper config sync
        self._sync_paper_bots()

        print(f"[farm] Supervisor running. Status every {self.status_interval}s", flush=True)

        last_status = 0.0
        last_sync   = 0.0

        while not self._shutdown:
            now = time.time()

            # Sync paper bots every 60 seconds
            if now - last_sync >= 60:
                self._sync_paper_bots()
                last_sync = now

            # Check for config-assignment restart requests
            for bot in list(self._bots.values()):
                if not self._shutdown:
                    bot.check_restart_requested()

            # Restart crashed bots
            for bot in list(self._bots.values()):
                if not bot.is_running() and not self._shutdown:
                    # Don't restart if status changed away from paper
                    if bot.config_path:
                        current_status = bot._current_config_status()
                        if current_status != "paper":
                            continue
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
        help="Path to farm_config.yaml (deprecated — bots now discovered from configs/ dynamically)",
    )
    args = parser.parse_args()

    farm = BotFarm(config_path=Path(args.config))
    farm.run()


if __name__ == "__main__":
    main()
