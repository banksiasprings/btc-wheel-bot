"""
api.py — FastAPI server for BTC Wheel Bot mobile interface.

Run with:
    /usr/local/bin/python3.11 -m uvicorn api:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import csv
import json
import os
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

# ── Paths ────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent
_ENV_PATH = _REPO_ROOT / ".env"
_DATA_DIR = _REPO_ROOT / "data"
_CONFIG_YAML = _REPO_ROOT / "config.yaml"
_KILL_SWITCH = _REPO_ROOT / "KILL_SWITCH"
_PID_FILE = _DATA_DIR / "optimizer_pid.txt"

# ── API key setup ─────────────────────────────────────────────────────────────

load_dotenv(_ENV_PATH)

_API_KEY: str = os.getenv("WHEEL_API_KEY", "")
if not _API_KEY:
    _API_KEY = secrets.token_hex(16)  # 32 hex chars
    with open(_ENV_PATH, "a") as _f:
        _f.write(f"\n# Mobile API key (auto-generated)\nWHEEL_API_KEY={_API_KEY}\n")
    print(f"[api.py] Generated WHEEL_API_KEY={_API_KEY!r} → saved to .env")

# ── App + CORS ────────────────────────────────────────────────────────────────

app = FastAPI(title="BTC Wheel Bot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth ──────────────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str | None = Security(_api_key_header)) -> str:
    if not key or key != _API_KEY:
        raise HTTPException(
            status_code=401, detail="Invalid or missing X-API-Key header"
        )
    return key


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict | list | None:
    """Read a JSON file; return None on missing/parse error."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def _read_yaml(path: Path) -> dict | None:
    try:
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f)
    except Exception:
        pass
    return None


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v)) if v not in (None, "") else default
    except Exception:
        return default


def _write_command(command: str, extra: dict | None = None) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "command": command,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    (_DATA_DIR / "bot_commands.json").write_text(json.dumps(payload))


def _optimizer_is_running() -> bool:
    if not _PID_FILE.exists():
        return False
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True  # process exists, we just can't signal it


# ── GET /status ───────────────────────────────────────────────────────────────

@app.get("/status")
async def get_status(_: str = Depends(verify_api_key)) -> dict:
    state = _read_json(_DATA_DIR / "bot_state.json")
    heartbeat = _read_json(_REPO_ROOT / "bot_heartbeat.json")

    if state:
        running = state.get("running", False)
        mode = state.get("mode", "stopped")
        started_at = state.get("started_at")
        last_hb = state.get("last_heartbeat")
    elif heartbeat:
        hb_time = heartbeat.get("timestamp", 0)
        running = (time.time() - float(hb_time)) < 120
        mode = heartbeat.get("mode", "paper")
        started_at = None
        last_hb = (
            datetime.fromtimestamp(float(hb_time), tz=timezone.utc).isoformat()
            if hb_time
            else None
        )
    else:
        running = False
        mode = "stopped"
        started_at = None
        last_hb = None

    uptime = 0
    if started_at and running:
        try:
            start_dt = datetime.fromisoformat(started_at)
            uptime = int((datetime.now(timezone.utc) - start_dt).total_seconds())
        except Exception:
            pass

    return {
        "bot_running": running,
        "mode": mode if running else "stopped",
        "uptime_seconds": max(0, uptime),
        "last_heartbeat": last_hb,
    }


# ── GET /position ─────────────────────────────────────────────────────────────

@app.get("/position")
async def get_position(_: str = Depends(verify_api_key)) -> dict:
    pos = _read_json(_DATA_DIR / "current_position.json")
    if not pos or not isinstance(pos, dict):
        return {"open": False}

    # Inject current spot from heartbeat if missing
    if not pos.get("current_spot"):
        hb = _read_json(_REPO_ROOT / "bot_heartbeat.json")
        if hb:
            pos["current_spot"] = hb.get("btc_price")

    return pos


# ── GET /equity ───────────────────────────────────────────────────────────────

@app.get("/equity")
async def get_equity(_: str = Depends(verify_api_key)) -> dict:
    data = _read_json(_DATA_DIR / "equity_curve.json")
    empty = {
        "dates": [],
        "equity": [],
        "starting_equity": 10000.0,
        "current_equity": 10000.0,
        "total_return_pct": 0.0,
    }
    if not data or not isinstance(data, dict):
        return empty

    dates: list = data.get("dates", [])
    equity: list = data.get("equity", [])
    if not dates or not equity:
        return empty

    # Last 90 days
    if len(dates) > 90:
        dates = dates[-90:]
        equity = equity[-90:]

    starting = _safe_float(data.get("starting_equity", equity[0] if equity else 10000.0))
    current = _safe_float(equity[-1] if equity else starting)
    total_return = (current - starting) / starting * 100 if starting > 0 else 0.0

    return {
        "dates": dates,
        "equity": equity,
        "starting_equity": starting,
        "current_equity": current,
        "total_return_pct": round(total_return, 2),
    }


# ── GET /trades ───────────────────────────────────────────────────────────────

@app.get("/trades")
async def get_trades(_: str = Depends(verify_api_key)) -> list:
    csv_path = _DATA_DIR / "trades.csv"
    if not csv_path.exists():
        return []

    trades = []
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(
                    {
                        "timestamp": row.get("timestamp", ""),
                        "instrument": row.get("instrument", ""),
                        "option_type": row.get("option_type", ""),
                        "strike": _safe_float(row.get("strike")),
                        "entry_price": _safe_float(row.get("entry_price")),
                        "exit_price": _safe_float(row.get("exit_price")),
                        "contracts": _safe_float(row.get("contracts")),
                        "pnl_btc": _safe_float(row.get("pnl_btc")),
                        "pnl_usd": _safe_float(row.get("pnl_usd")),
                        "equity_before": _safe_float(row.get("equity_before")),
                        "equity_after": _safe_float(row.get("equity_after")),
                        "btc_price": _safe_float(row.get("btc_price")),
                        "dte_at_entry": _safe_int(row.get("dte_at_entry")),
                        "dte_at_close": _safe_int(row.get("dte_at_close")),
                        "reason": row.get("reason", ""),
                        "mode": row.get("mode", ""),
                    }
                )
    except Exception:
        return []

    # Newest first, capped at 50
    return list(reversed(trades))[:50]


# ── GET /optimizer/summary ────────────────────────────────────────────────────

@app.get("/optimizer/summary")
async def get_optimizer_summary(_: str = Depends(verify_api_key)) -> dict:
    opt_dir = _DATA_DIR / "optimizer"

    best_genome = _read_yaml(opt_dir / "best_genome.yaml")
    sweep = _read_json(opt_dir / "sweep_results.json")
    wf = _read_json(opt_dir / "walk_forward.json")
    mc = _read_json(opt_dir / "monte_carlo.json")
    rec = _read_json(opt_dir / "reconciliation.json")

    best_fitness: float | None = None
    if sweep and isinstance(sweep, dict):
        for param_results in sweep.values():
            if isinstance(param_results, list):
                for r in param_results:
                    f = _safe_float(r.get("fitness", r.get("sharpe")))
                    if best_fitness is None or f > best_fitness:
                        best_fitness = f

    last_run: str | None = None
    bg_path = opt_dir / "best_genome.yaml"
    if bg_path.exists():
        mtime = bg_path.stat().st_mtime
        last_run = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return {
        "last_run": last_run,
        "best_fitness": best_fitness,
        "best_genome": best_genome,
        "monte_carlo": mc,
        "walk_forward": wf,
        "reconciliation": rec,
    }


# ── POST /controls/start ──────────────────────────────────────────────────────

@app.post("/controls/start")
async def start_bot(_: str = Depends(verify_api_key)) -> dict:
    if _KILL_SWITCH.exists():
        _KILL_SWITCH.unlink()
    _write_command("start")
    return {"ok": True, "message": "Start command sent"}


# ── POST /controls/stop ───────────────────────────────────────────────────────

@app.post("/controls/stop")
async def stop_bot(_: str = Depends(verify_api_key)) -> dict:
    _KILL_SWITCH.write_text("STOP")
    _write_command("stop")
    return {"ok": True, "message": "Bot stopped (KILL_SWITCH created)"}


# ── POST /controls/close_position ────────────────────────────────────────────

@app.post("/controls/close_position")
async def close_position(_: str = Depends(verify_api_key)) -> dict:
    _write_command("close_position")
    return {"ok": True, "message": "Close position command sent"}


# ── POST /controls/set_mode ───────────────────────────────────────────────────

class ModeRequest(BaseModel):
    mode: str
    confirm: str | None = None


@app.post("/controls/set_mode")
async def set_mode(req: ModeRequest, _: str = Depends(verify_api_key)) -> dict:
    if req.mode not in ("paper", "live"):
        raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")
    if req.mode == "live" and req.confirm != "SWITCH_TO_LIVE":
        raise HTTPException(
            status_code=400,
            detail="Switching to live requires confirm='SWITCH_TO_LIVE'",
        )
    _write_command("set_mode", {"mode": req.mode})
    return {"ok": True, "message": f"Mode switch to {req.mode} command sent (takes effect on restart)"}


# ── GET /config ───────────────────────────────────────────────────────────────

@app.get("/config")
async def get_config(_: str = Depends(verify_api_key)) -> dict:
    cfg = _read_yaml(_CONFIG_YAML) or {}
    strategy = cfg.get("strategy", {})
    backtest = cfg.get("backtest", {})
    return {
        "delta_target_min": strategy.get("target_delta_min"),
        "delta_target_max": strategy.get("target_delta_max"),
        "min_dte": strategy.get("min_dte"),
        "max_dte": strategy.get("max_dte"),
        "premium_fraction_of_spot": backtest.get("premium_fraction_of_spot"),
        "starting_equity": backtest.get("starting_equity"),
        "use_regime_filter": strategy.get("use_regime_filter", False),
        "regime_ma_days": strategy.get("regime_ma_days", 200),
    }


# ── POST /config ──────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    delta_target_min: float | None = None
    delta_target_max: float | None = None
    min_dte: int | None = None
    max_dte: int | None = None
    premium_fraction_of_spot: float | None = None
    starting_equity: float | None = None


_CONFIG_RANGES: dict[str, tuple[float, float]] = {
    "delta_target_min": (0.05, 0.50),
    "delta_target_max": (0.05, 0.50),
    "min_dte": (1, 60),
    "max_dte": (1, 90),
    "premium_fraction_of_spot": (0.001, 0.10),
    "starting_equity": (1000, 1_000_000),
}


@app.post("/config")
async def update_config(update: ConfigUpdate, _: str = Depends(verify_api_key)) -> dict:
    cfg = _read_yaml(_CONFIG_YAML)
    if cfg is None:
        raise HTTPException(status_code=500, detail="Cannot read config.yaml")

    applied: list[str] = []

    def _apply(section: str, field: str, value: float | int, key: str) -> None:
        lo, hi = _CONFIG_RANGES[key]
        if not (lo <= value <= hi):
            raise HTTPException(
                status_code=400, detail=f"{key} must be between {lo} and {hi}"
            )
        cfg[section][field] = value
        applied.append(key)

    if update.delta_target_min is not None:
        _apply("strategy", "target_delta_min", update.delta_target_min, "delta_target_min")
    if update.delta_target_max is not None:
        _apply("strategy", "target_delta_max", update.delta_target_max, "delta_target_max")
    if update.min_dte is not None:
        _apply("strategy", "min_dte", update.min_dte, "min_dte")
    if update.max_dte is not None:
        _apply("strategy", "max_dte", update.max_dte, "max_dte")
    if update.premium_fraction_of_spot is not None:
        _apply(
            "backtest",
            "premium_fraction_of_spot",
            update.premium_fraction_of_spot,
            "premium_fraction_of_spot",
        )
    if update.starting_equity is not None:
        _apply("backtest", "starting_equity", update.starting_equity, "starting_equity")

    _CONFIG_YAML.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
    return {"ok": True, "updated": applied}


# ── POST /optimizer/run ───────────────────────────────────────────────────────

_OPTIMIZER_MODES = {"sweep", "evolve", "walk_forward", "monte_carlo", "reconcile"}


class OptimizerRequest(BaseModel):
    mode: str
    param: str | None = None


@app.post("/optimizer/run")
async def run_optimizer(req: OptimizerRequest, _: str = Depends(verify_api_key)) -> dict:
    if req.mode not in _OPTIMIZER_MODES:
        raise HTTPException(
            status_code=400, detail=f"mode must be one of {sorted(_OPTIMIZER_MODES)}"
        )
    if _optimizer_is_running():
        raise HTTPException(status_code=409, detail="Optimizer is already running")

    cmd = [
        "/usr/local/bin/python3.11",
        str(_REPO_ROOT / "optimizer.py"),
        "--mode",
        req.mode,
    ]
    if req.param:
        cmd += ["--param", req.param]

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(_REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(proc.pid))
    return {"ok": True, "pid": proc.pid, "mode": req.mode}


# ── GET /optimizer/running ────────────────────────────────────────────────────

@app.get("/optimizer/running")
async def optimizer_running(_: str = Depends(verify_api_key)) -> dict:
    return {"running": _optimizer_is_running()}
