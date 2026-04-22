"""
api.py — FastAPI REST server for mobile app access to the BTC Wheel Bot.

Run with:
    /usr/local/bin/python3.11 -m uvicorn api:app --host 0.0.0.0 --port 8765

Auth: X-API-Key header required on all endpoints.
WHEEL_API_KEY is loaded from .env; auto-generated and saved if absent.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests as _requests
import yaml
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OPT_DIR  = DATA_DIR / "optimizer"

load_dotenv(BASE_DIR / ".env")


# ── API key bootstrap ─────────────────────────────────────────────────────────

def _ensure_api_key() -> str:
    """Return WHEEL_API_KEY from env, or auto-generate and persist one."""
    key = os.getenv("WHEEL_API_KEY", "").strip()
    if key:
        return key
    key = secrets.token_hex(16)  # 32 hex chars
    env_path = BASE_DIR / ".env"
    existing = env_path.read_text() if env_path.exists() else ""
    with open(env_path, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"WHEEL_API_KEY={key}\n")
    os.environ["WHEEL_API_KEY"] = key
    print(f"[api] Generated API key: {key}  (saved to .env)")
    return key


API_KEY = _ensure_api_key()

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="BTC Wheel Bot API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_api_key(x_api_key: str = Header(...)) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return None


def _write_command(command: str, extra: dict | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "command":   command,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if extra:
        record.update(extra)
    (DATA_DIR / "bot_commands.json").write_text(json.dumps(record))


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/status", dependencies=[Depends(_require_api_key)])
def get_status() -> dict:
    from datetime import datetime, timezone
    state = _read_json(DATA_DIR / "bot_state.json") or {}
    # Compute uptime from started_at if uptime_seconds not directly stored
    uptime = state.get("uptime_seconds")
    if uptime is None and state.get("started_at") and state.get("running"):
        try:
            started = datetime.fromisoformat(state["started_at"])
            uptime = (datetime.now(timezone.utc) - started).total_seconds()
        except Exception:
            pass

    # Dead-bot detection: if the heartbeat is older than 3 minutes and state
    # still says running, the bot has silently died — report it as not running.
    bot_running = state.get("running", False)
    if bot_running and state.get("last_heartbeat"):
        try:
            last_hb = datetime.fromisoformat(state["last_heartbeat"])
            age_seconds = (datetime.now(timezone.utc) - last_hb).total_seconds()
            if age_seconds > 180:
                bot_running = False
        except Exception:
            pass

    return {
        "bot_running":    bot_running,
        "paused":         state.get("paused", False),
        "mode":           state.get("mode", "unknown"),
        "uptime_seconds": uptime,
        "last_heartbeat": state.get("last_heartbeat"),
    }


# ── BTC price (cached 30s) ────────────────────────────────────────────────────

_btc_price_cache: dict = {}

@app.get("/market/btc_price", dependencies=[Depends(_require_api_key)])
def get_btc_price() -> dict:
    now = time.time()
    if _btc_price_cache.get("expires", 0) > now:
        return _btc_price_cache["data"]
    try:
        r = _requests.get(
            "https://www.deribit.com/api/v2/public/get_index_price",
            params={"index_name": "btc_usd"},
            timeout=5,
        )
        r.raise_for_status()
        price = r.json()["result"]["index_price"]
        result = {"price": price, "timestamp": datetime.utcnow().isoformat()}
        _btc_price_cache["data"] = result
        _btc_price_cache["expires"] = now + 30
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Deribit unreachable: {e}")


# ── Position ──────────────────────────────────────────────────────────────────

@app.get("/position", dependencies=[Depends(_require_api_key)])
def get_position() -> dict:
    pos = _read_json(DATA_DIR / "current_position.json")
    if pos is None:
        return {"open": False}
    return pos


# ── Hedge ─────────────────────────────────────────────────────────────────────

@app.get("/hedge", dependencies=[Depends(_require_api_key)])
def get_hedge() -> dict:
    """Return the current delta-hedge (BTC-PERPETUAL) position and P&L."""
    hedge_state = _read_json(DATA_DIR / "hedge_state.json")
    if hedge_state is None:
        return {
            "enabled": False,
            "perp_position_btc": 0.0,
            "avg_entry_price": 0.0,
            "unrealised_pnl_usd": None,
            "realised_pnl_usd": 0.0,
            "funding_paid_usd": 0.0,
            "rebalance_count": 0,
        }
    # Enrich with current spot for unrealised P&L
    btc_price: float | None = None
    try:
        r = _requests.get(
            "https://www.deribit.com/api/v2/public/get_index_price",
            params={"index_name": "btc_usd"},
            timeout=3,
        )
        btc_price = r.json()["result"]["index_price"]
    except Exception:
        pass

    pos_btc = hedge_state.get("perp_position_btc", 0.0)
    entry   = hedge_state.get("avg_entry_price", 0.0)
    unrealised = None
    if btc_price and pos_btc != 0.0 and entry > 0.0:
        unrealised = round(pos_btc * (btc_price - entry), 2)

    return {
        "enabled": True,
        "perp_position_btc":  hedge_state.get("perp_position_btc", 0.0),
        "avg_entry_price":    hedge_state.get("avg_entry_price", 0.0),
        "unrealised_pnl_usd": unrealised,
        "realised_pnl_usd":   hedge_state.get("realised_pnl_usd", 0.0),
        "funding_paid_usd":   hedge_state.get("funding_paid_usd", 0.0),
        "rebalance_count":    hedge_state.get("rebalance_count", 0),
    }


# ── Equity ────────────────────────────────────────────────────────────────────

@app.get("/equity", dependencies=[Depends(_require_api_key)])
def get_equity() -> dict:
    curve = _read_json(DATA_DIR / "equity_curve.json") or []
    if not curve:
        return {
            "dates": [], "equity": [],
            "starting_equity": None, "current_equity": None, "total_return_pct": None,
        }
    dates    = [r["date"] for r in curve]
    equities = [r["equity"] for r in curve]
    start    = equities[0] if equities else None
    current  = equities[-1] if equities else None
    return {
        "dates":            dates,
        "equity":           equities,
        "starting_equity":  start,
        "current_equity":   current,
        "total_return_pct": round((current - start) / start * 100, 2)
                            if start and start > 0 else None,
    }


# ── Trades ────────────────────────────────────────────────────────────────────

@app.get("/trades", dependencies=[Depends(_require_api_key)])
def get_trades() -> list:
    trades = _read_json(DATA_DIR / "paper_trades" / "paper_trades.json") or []
    return sorted(trades, key=lambda t: t.get("entry_date", ""), reverse=True)[:50]


# ── Optimizer summary ─────────────────────────────────────────────────────────

@app.get("/optimizer/summary", dependencies=[Depends(_require_api_key)])
def get_optimizer_summary() -> dict:
    # best_genome.yaml stores as YAML
    best_genome = _read_yaml(OPT_DIR / "best_genome.yaml")

    mc    = _read_json(OPT_DIR / "monte_carlo_results.json")
    wf    = _read_json(OPT_DIR / "walk_forward_results.json")
    recon = _read_json(OPT_DIR / "reconcile_results.json")

    # Extract best fitness — prefer evolution results, fall back to sweep best
    best_fitness: float | None = None
    if wf and "in_sample" in wf:
        best_fitness = wf["in_sample"].get("fitness")
    if best_fitness is None:
        _sweep = _read_json(OPT_DIR / "sweep_results.json")
        if _sweep:
            for param_rows in _sweep.values():
                if isinstance(param_rows, list):
                    for row in param_rows:
                        f = row.get("fitness")
                        if f is not None and (best_fitness is None or f > best_fitness):
                            best_fitness = f

    # Monte Carlo summary
    mc_summary = None
    if mc and "summary" in mc:
        s = mc["summary"]
        mc_summary = {
            "pct_profitable":  s.get("pct_profitable"),
            "median_return":   s.get("median_return"),
            "p5_sharpe":       s.get("p5_sharpe"),
            "verdict":         (
                "robust" if (s.get("p5_sharpe") or -1) > 0.5
                else "marginal" if (s.get("p5_sharpe") or -1) >= 0.0
                else "fails under stress"
            ),
        }

    # Walk-forward summary
    wf_summary = None
    if wf and "robustness_score" in wf:
        rob = wf["robustness_score"]
        wf_summary = {
            "robustness_score": rob,
            "verdict": (
                "robust" if rob >= 0.70
                else "marginal" if rob >= 0.40
                else "likely overfit"
            ),
        }

    # Reconciliation summary
    recon_summary = None
    if recon and "metrics" in recon:
        m = recon["metrics"]
        recon_summary = {
            "accuracy":     m.get("accuracy"),
            "premium_rmse": m.get("premium_rmse_usd"),
            "premium_bias": m.get("premium_bias_usd"),
        }

    # Sweep metadata
    sweep_raw = _read_json(OPT_DIR / "sweep_results.json")  # type: ignore[assignment]
    last_sweep_ts = None
    sweep_params_count = 0
    if sweep_raw:
        sweep_params_count = len(sweep_raw)
        try:
            last_sweep_ts = datetime.fromtimestamp(
                (OPT_DIR / "sweep_results.json").stat().st_mtime
            ).isoformat()
        except Exception:
            pass

    return {
        "best_fitness":        best_fitness,
        "best_genome":         best_genome,
        "monte_carlo":         mc_summary,
        "walk_forward":        wf_summary,
        "reconciliation":      recon_summary,
        "last_sweep_timestamp": last_sweep_ts,
        "sweep_params_count":  sweep_params_count,
    }


@app.get("/optimizer/sweep_results", dependencies=[Depends(_require_api_key)])
def get_sweep_results() -> dict:
    """Return sweep results in a clean, mobile-friendly format."""
    raw = _read_json(OPT_DIR / "sweep_results.json")
    if not raw:
        return {"params": [], "results": {}, "best_per_param": {}, "timestamp": None}

    results: dict[str, list[dict]] = {}
    best_per_param: dict[str, dict] = {}

    for param, rows in raw.items():
        clean: list[dict] = []
        best_row: dict | None = None
        for r in rows:
            if r.get("error"):
                continue
            val = r.get("params", {}).get(param)
            if val is None:
                continue
            entry = {
                "value":      round(float(val), 5),
                "fitness":    round(float(r.get("fitness", 0)), 4),
                "sharpe":     round(float(r.get("sharpe_ratio", 0)), 3),
                "return_pct": round(float(r.get("total_return_pct", 0)), 2),
                "win_rate":   round(float(r.get("win_rate_pct", 0)), 1),
                "drawdown":   round(float(r.get("max_drawdown_pct", 0)), 2),
            }
            clean.append(entry)
            if best_row is None or entry["fitness"] > best_row["fitness"]:
                best_row = entry
        results[param] = clean
        if best_row:
            best_per_param[param] = {"value": best_row["value"], "fitness": best_row["fitness"]}

    timestamp = None
    try:
        timestamp = datetime.fromtimestamp(
            (OPT_DIR / "sweep_results.json").stat().st_mtime
        ).isoformat()
    except Exception:
        pass

    return {
        "params":        list(results.keys()),
        "results":       results,
        "best_per_param": best_per_param,
        "timestamp":     timestamp,
    }


@app.get("/optimizer/evolve_results", dependencies=[Depends(_require_api_key)])
def get_evolve_results() -> dict:
    """Return top 10 genomes from evolution leaderboard."""
    import csv as _csv

    # Prefer the leaderboard CSV (pre-sorted by fitness)
    csv_path = OPT_DIR / "evolution_leaderboard.csv"
    rows: list[dict] = []

    if csv_path.exists():
        try:
            with open(csv_path, newline="") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    try:
                        rows.append({
                            "fitness":    round(float(row.get("fitness", 0)), 4),
                            "sharpe":     round(float(row.get("sharpe_ratio", 0)), 3),
                            "return_pct": round(float(row.get("total_return_pct", 0)), 2),
                            "win_rate":   round(float(row.get("win_rate_pct", 0)), 1),
                            "drawdown":   round(float(row.get("max_drawdown_pct", 0)), 2),
                            "num_cycles": int(float(row.get("num_cycles", 0))),
                        })
                    except (ValueError, TypeError):
                        continue
        except Exception:
            pass

    # Fallback: derive leaderboard from evolution_results.json
    if not rows:
        evo_raw = _read_json(OPT_DIR / "evolution_results.json")
        if evo_raw:
            all_bots: list[dict] = [b for gen in evo_raw for b in gen]
            all_bots.sort(key=lambda r: r.get("fitness", 0), reverse=True)
            for b in all_bots:
                try:
                    rows.append({
                        "fitness":    round(float(b.get("fitness", 0)), 4),
                        "sharpe":     round(float(b.get("sharpe_ratio", 0)), 3),
                        "return_pct": round(float(b.get("total_return_pct", 0)), 2),
                        "win_rate":   round(float(b.get("win_rate_pct", 0)), 1),
                        "drawdown":   round(float(b.get("max_drawdown_pct", 0)), 2),
                        "num_cycles": int(float(b.get("num_cycles", 0))),
                    })
                except (ValueError, TypeError):
                    continue

    # Already sorted; take top 10
    top10 = rows[:10]

    timestamp = None
    try:
        path = csv_path if csv_path.exists() else OPT_DIR / "evolution_results.json"
        if path.exists():
            timestamp = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:
        pass

    return {
        "top_genomes": top10,
        "total_evaluated": len(rows),
        "timestamp": timestamp,
    }


@app.get("/optimizer/walk_forward_results", dependencies=[Depends(_require_api_key)])
def get_walk_forward_results() -> dict:
    """Return walk-forward validation results."""
    path = OPT_DIR / "walk_forward_results.json"
    if not path.exists():
        return {"available": False}
    try:
        data = _read_json(path)
        data["available"] = True
        return data
    except Exception:
        return {"available": False}


@app.get("/optimizer/monte_carlo_results", dependencies=[Depends(_require_api_key)])
def get_monte_carlo_results() -> dict:
    """Return Monte Carlo simulation results."""
    path = OPT_DIR / "monte_carlo_results.json"
    if not path.exists():
        return {"available": False}
    try:
        data = _read_json(path)
        data["available"] = True
        return data
    except Exception:
        return {"available": False}


# ── Controls ──────────────────────────────────────────────────────────────────

BOT_PID_FILE = DATA_DIR / "bot_pid.txt"


def _bot_is_running() -> bool:
    """Return True if the bot process is alive and not a zombie."""
    if not BOT_PID_FILE.exists():
        return False
    try:
        pid = int(BOT_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        stat = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                              capture_output=True, text=True)
        if "Z" in stat.stdout:
            BOT_PID_FILE.unlink(missing_ok=True)
            return False
        return True
    except (ValueError, OSError):
        BOT_PID_FILE.unlink(missing_ok=True)
        return False


def _write_bot_state(running: bool) -> None:
    """Immediately update bot_state.json so /status reflects the new state."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state_path = DATA_DIR / "bot_state.json"
    state = _read_json(state_path) or {}
    state["running"] = running
    state["paused"] = False
    if not running:
        state["uptime_seconds"] = None
    state_path.write_text(json.dumps(state))


@app.post("/controls/start", dependencies=[Depends(_require_api_key)])
def control_start() -> dict:
    kill_path = BASE_DIR / "KILL_SWITCH"
    kill_path.unlink(missing_ok=True)

    if _bot_is_running():
        _write_command("start")
        _write_bot_state(running=True)
        return {"ok": True, "action": "resumed", "message": "Bot resumed"}

    # Not running — spawn fresh process in paper mode
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "main.py"), "--mode=paper"],
        cwd=str(BASE_DIR),
        stdout=open(BASE_DIR / "logs" / "btc-wheel-bot.log", "a"),
        stderr=subprocess.STDOUT,
    )
    BOT_PID_FILE.write_text(str(proc.pid))
    _write_bot_state(running=True)
    return {"ok": True, "action": "started", "pid": proc.pid, "message": "Bot started"}


class StopRequest(BaseModel):
    confirm: str = ""


@app.post("/controls/stop", dependencies=[Depends(_require_api_key)])
def control_stop(body: StopRequest = StopRequest()) -> dict:
    if body.confirm != "STOP_BOT":
        raise HTTPException(
            status_code=400,
            detail="Stopping the bot requires confirm='STOP_BOT' in the request body.",
        )
    # Write kill-switch file so a resumed bot halts on next tick
    kill_path = BASE_DIR / "KILL_SWITCH"
    kill_path.write_text("STOP")
    _write_command("stop")

    # Send SIGTERM to kill the process immediately rather than waiting for
    # it to poll the kill-switch file on its next tick
    terminated = False
    if BOT_PID_FILE.exists():
        try:
            pid = int(BOT_PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            BOT_PID_FILE.unlink(missing_ok=True)
            terminated = True
        except (ValueError, OSError):
            BOT_PID_FILE.unlink(missing_ok=True)

    _write_bot_state(running=False)

    try:
        import notifier as _notifier
        _notifier.notify_bot_stopped()
    except Exception:
        pass

    msg = "Bot stopped" if terminated else "Stop signal sent"
    return {"ok": True, "message": msg}


class ClosePositionRequest(BaseModel):
    confirm: str = ""


@app.post("/controls/close_position", dependencies=[Depends(_require_api_key)])
def control_close_position(body: ClosePositionRequest = ClosePositionRequest()) -> dict:
    if body.confirm != "CLOSE_POSITION":
        raise HTTPException(
            status_code=400,
            detail="Closing the position requires confirm='CLOSE_POSITION' in the request body.",
        )
    _write_command("close_position")
    return {"ok": True}


class SetModeRequest(BaseModel):
    mode: str
    confirm: str = ""


@app.post("/controls/set_mode", dependencies=[Depends(_require_api_key)])
def control_set_mode(body: SetModeRequest) -> dict:
    if body.mode not in ("live", "paper"):
        raise HTTPException(status_code=400, detail="mode must be 'live' or 'paper'")
    if body.mode == "live" and body.confirm != "SWITCH_TO_LIVE":
        raise HTTPException(
            status_code=400,
            detail="Switching to live requires confirm='SWITCH_TO_LIVE'",
        )
    _write_command("set_mode", {"mode": body.mode})
    return {"ok": True}


# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_KEYS: dict[str, list[str]] = {
    "strategy": [
        "iv_rank_threshold", "target_delta_min", "target_delta_max",
        "min_dte", "max_dte", "use_regime_filter", "regime_ma_days",
    ],
    "backtest": ["premium_fraction_of_spot", "starting_equity"],
    "sizing":   ["max_equity_per_leg", "min_free_equity_fraction"],
}


@app.get("/config", dependencies=[Depends(_require_api_key)])
def get_config() -> dict:
    raw = _read_yaml(BASE_DIR / "config.yaml") or {}
    result: dict = {}
    for section, keys in _CONFIG_KEYS.items():
        for k in keys:
            result[k] = raw.get(section, {}).get(k)
    return result


class ConfigUpdateRequest(BaseModel):
    params: dict


# ── Config presets ─────────────────────────────────────────────────────────────

_PRESET_SECTIONS: dict[str, str] = {
    "iv_rank_threshold":        "strategy",
    "target_delta_min":         "strategy",
    "target_delta_max":         "strategy",
    "min_dte":                  "strategy",
    "max_dte":                  "strategy",
    "max_equity_per_leg":       "sizing",
    "min_free_equity_fraction": "sizing",
    "approx_otm_offset":        "backtest",
    "premium_fraction_of_spot": "backtest",
    "starting_equity":          "backtest",
}


def _round_param(v: Any) -> Any:
    return round(v, 6) if isinstance(v, float) else v


def _sweep_preset_params() -> dict | None:
    raw = _read_json(OPT_DIR / "sweep_results.json")
    if not raw:
        return None
    params: dict = {}
    for key in _PRESET_SECTIONS:
        rows = raw.get(key)
        if not isinstance(rows, list):
            continue
        valid = [r for r in rows if not r.get("error") and isinstance(r.get("params"), dict)]
        if not valid:
            continue
        best = max(valid, key=lambda r: r.get("fitness", 0.0))
        val = best["params"].get(key)
        if val is not None:
            params[key] = _round_param(val)
    return params or None


def _sweep_best_fitness() -> float | None:
    raw = _read_json(OPT_DIR / "sweep_results.json")
    if not raw:
        return None
    best: float | None = None
    for rows in raw.values():
        if not isinstance(rows, list):
            continue
        for r in rows:
            f = r.get("fitness")
            if f is not None and (best is None or f > best):
                best = f
    return best


_EVOLVE_GOALS = ("balanced", "max_yield", "safest", "sharpe")


def _evolve_preset_for_goal(goal: str) -> tuple[dict | None, float | None]:
    """Return (params, fitness) for a specific evolution goal, or (None, None) if not found."""
    path = OPT_DIR / f"best_genome_{goal}.yaml"
    if not path.exists() and goal == "balanced":
        path = OPT_DIR / "best_genome.yaml"  # backwards compat
    genome = _read_yaml(path) if path.exists() else None
    if not genome:
        return None, None
    fitness = genome.get("fitness")
    params = {k: _round_param(genome[k]) for k in _PRESET_SECTIONS if k in genome}
    return params or None, fitness


def _current_preset_params() -> dict:
    raw = _read_yaml(BASE_DIR / "config.yaml") or {}
    return {k: raw.get(sec, {}).get(k) for k, sec in _PRESET_SECTIONS.items()}


def _params_close(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.001
    except (TypeError, ValueError):
        return a == b


def _detect_active(current: dict, sweep: dict | None, evolve_goals: dict[str, dict | None]) -> str:
    if sweep and all(
        current.get(k) is not None and _params_close(current[k], v)
        for k, v in sweep.items()
    ):
        return "sweep"
    for goal, params in evolve_goals.items():
        if params and all(
            current.get(k) is not None and _params_close(current[k], v)
            for k, v in params.items()
        ):
            return f"evolve_{goal}"
    return "custom"


def _file_ts(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:
        return None


def _evolve_goal_ts(goal: str) -> str | None:
    path = OPT_DIR / f"best_genome_{goal}.yaml"
    if not path.exists() and goal == "balanced":
        path = OPT_DIR / "best_genome.yaml"
    return _file_ts(path)


@app.get("/config/presets", dependencies=[Depends(_require_api_key)])
def get_presets() -> dict:
    sweep_params = _sweep_preset_params()
    current = _current_preset_params()

    evolve_params_by_goal: dict[str, dict | None] = {}
    evolve_section: dict[str, dict] = {}
    for goal in _EVOLVE_GOALS:
        params, fitness = _evolve_preset_for_goal(goal)
        evolve_params_by_goal[goal] = params
        evolve_section[f"evolve_{goal}"] = {
            "available": params is not None,
            "fitness":   fitness,
            "timestamp": _evolve_goal_ts(goal),
            "params":    params or {},
        }

    active = _detect_active(current, sweep_params, evolve_params_by_goal)
    return {
        "active": active,
        "sweep": {
            "available": sweep_params is not None,
            "fitness":   _sweep_best_fitness(),
            "timestamp": _file_ts(OPT_DIR / "sweep_results.json"),
            "params":    sweep_params or {},
        },
        **evolve_section,
        "current": {"params": current},
    }


_VALID_PRESET_NAMES = {"sweep"} | {f"evolve_{g}" for g in _EVOLVE_GOALS}

_CONFIG_HISTORY_PATH = DATA_DIR / "config_history.json"


def _append_config_history(preset: str, params: dict) -> None:
    try:
        history = _read_json(_CONFIG_HISTORY_PATH) or []
        history.insert(0, {
            "timestamp": datetime.utcnow().isoformat(),
            "preset": preset,
            "params": {k: _round_param(v) for k, v in params.items()},
        })
        _CONFIG_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_HISTORY_PATH.write_text(json.dumps(history[:50], indent=2))
    except Exception:
        pass


class LoadPresetRequest(BaseModel):
    preset: str


@app.post("/config/load_preset", dependencies=[Depends(_require_api_key)])
def load_preset(body: LoadPresetRequest) -> dict:
    if body.preset not in _VALID_PRESET_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"preset must be one of: {sorted(_VALID_PRESET_NAMES)}"
        )
    if body.preset == "sweep":
        params = _sweep_preset_params()
        if not params:
            raise HTTPException(status_code=404, detail="No sweep results found — run Parameter Sweep first")
    else:
        goal = body.preset[len("evolve_"):]  # strip "evolve_" prefix
        params, _ = _evolve_preset_for_goal(goal)
        if not params:
            raise HTTPException(
                status_code=404,
                detail=f"No genome for goal '{goal}' — run Evolve with that fitness goal first"
            )
    raw = _read_yaml(BASE_DIR / "config.yaml") or {}
    for key, val in params.items():
        raw.setdefault(_PRESET_SECTIONS[key], {})[key] = val
    with open(BASE_DIR / "config.yaml", "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
    _append_config_history(body.preset, params)
    return {"ok": True, "preset": body.preset, "params_updated": list(params.keys())}


@app.post("/config", dependencies=[Depends(_require_api_key)])
def update_config(body: ConfigUpdateRequest) -> dict:
    raw = _read_yaml(BASE_DIR / "config.yaml") or {}
    for section, keys in _CONFIG_KEYS.items():
        for k in keys:
            if k in body.params:
                raw.setdefault(section, {})[k] = body.params[k]
    with open(BASE_DIR / "config.yaml", "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
    _append_config_history("custom", body.params)
    return {"ok": True}


@app.get("/config/history", dependencies=[Depends(_require_api_key)])
def config_history() -> list:
    return _read_json(_CONFIG_HISTORY_PATH) or []


# ── Optimizer ─────────────────────────────────────────────────────────────────

class OptimizerRunRequest(BaseModel):
    mode: str
    param: str | None = None
    fitness_goal: str = "balanced"


_VALID_OPT_MODES = {"sweep", "evolve", "walk_forward", "monte_carlo", "reconcile"}
# Modes not yet implemented in optimizer.py — return a clear error rather than crashing
_UNIMPLEMENTED_MODES = {"reconcile"}


@app.post("/optimizer/run", dependencies=[Depends(_require_api_key)])
def optimizer_run(body: OptimizerRunRequest) -> dict:
    if body.mode not in _VALID_OPT_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {_VALID_OPT_MODES}")
    if body.mode in _UNIMPLEMENTED_MODES:
        raise HTTPException(
            status_code=501,
            detail=f"'{body.mode}' requires paper trade history — run the bot in paper mode first to collect data."
        )

    cmd = [sys.executable, str(BASE_DIR / "optimizer.py"), "--mode", body.mode]
    if body.param:
        cmd += ["--param", body.param]
    if body.mode == "evolve" and body.fitness_goal:
        cmd += ["--fitness-goal", body.fitness_goal]

    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "optimizer_pid.txt").write_text(str(proc.pid))
    return {"ok": True, "pid": proc.pid}


@app.get("/optimizer/running", dependencies=[Depends(_require_api_key)])
def optimizer_running() -> dict:
    pid_path = DATA_DIR / "optimizer_pid.txt"
    if not pid_path.exists():
        return {"running": False}
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)   # raises OSError if process doesn't exist
        # Check for zombie (process exited but not reaped)
        stat = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True, text=True
        )
        if "Z" in stat.stdout:
            pid_path.unlink(missing_ok=True)
            return {"running": False}
        return {"running": True, "pid": pid}
    except (ValueError, OSError):
        pid_path.unlink(missing_ok=True)
        return {"running": False}


@app.get("/optimizer/progress", dependencies=[Depends(_require_api_key)])
def optimizer_progress() -> dict:
    path = OPT_DIR / "evolution_progress.json"
    data = _read_json(path) or {}
    return data


# ── Notifications ─────────────────────────────────────────────────────────────

_NOTIFIER_CONFIG_PATH = DATA_DIR / "notifier_config.json"


class NotifySetupRequest(BaseModel):
    bot_token: str
    chat_id: str


@app.post("/notifications/setup", dependencies=[Depends(_require_api_key)])
def notifications_setup(body: NotifySetupRequest) -> dict:
    _NOTIFIER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _NOTIFIER_CONFIG_PATH.write_text(
        json.dumps({"bot_token": body.bot_token, "chat_id": body.chat_id})
    )
    return {"ok": True}


@app.get("/notifications/config", dependencies=[Depends(_require_api_key)])
def notifications_config() -> dict:
    data = _read_json(_NOTIFIER_CONFIG_PATH) or {}
    token = data.get("bot_token", "")
    return {
        "configured": bool(token and data.get("chat_id")),
        "chat_id": data.get("chat_id", ""),
        "bot_token_hint": f"...{token[-6:]}" if len(token) > 6 else "",
    }


@app.post("/notifications/test", dependencies=[Depends(_require_api_key)])
def notifications_test() -> dict:
    try:
        import notifier as _notifier
        _notifier._send("🔔 Test message from BTC Wheel Bot — notifications are working!")
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── PWA static file serving ────────────────────────────────────────────────────
_STATIC_DIR = BASE_DIR / "mobile-app" / "dist"

if _STATIC_DIR.exists():
    # Serve the SPA index for all unmatched paths so client-side routing works
    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        file = _STATIC_DIR / full_path
        if file.exists() and file.is_file():
            return FileResponse(file)
        return FileResponse(_STATIC_DIR / "index.html")

    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")
