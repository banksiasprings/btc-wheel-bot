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
import asyncio
import httpx
import websockets as _websockets
from fastapi import Depends, FastAPI, HTTPException, Header, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config_store as _cs

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

# ── Prometheus metrics ────────────────────────────────────────────────────────

try:
    from prometheus_client import Gauge, Counter, generate_latest, CONTENT_TYPE_LATEST
    _PROM_ENABLED = True
    _prom_open_positions  = Gauge("btc_bot_open_positions",  "Number of open option positions")
    _prom_equity_usd      = Gauge("btc_bot_equity_usd",      "Current account equity in USD")
    _prom_pnl_usd         = Gauge("btc_bot_pnl_usd_total",   "Cumulative realised P&L in USD")
    _prom_total_trades    = Gauge("btc_bot_total_trades",     "Total closed trades")
    _prom_iv_rank         = Gauge("btc_bot_iv_rank",          "Current IV rank (0-1)")
    _prom_drawdown        = Gauge("btc_bot_drawdown_pct",     "Current drawdown from peak equity")
except ImportError:
    _PROM_ENABLED = False

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


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0"}


# ── Prometheus metrics ────────────────────────────────────────────────────────

@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    """
    Prometheus-compatible metrics endpoint.
    No API key required (standard for Prometheus scrape configs).
    """
    if not _PROM_ENABLED:
        return Response(content="# prometheus-client not installed\n", media_type="text/plain")

    # Refresh gauges from current bot state files
    try:
        equity_data = _read_json(DATA_DIR / "equity_curve.json") or []
        if equity_data:
            current_eq = equity_data[-1] if isinstance(equity_data[0], (int, float)) else equity_data[-1].get("equity", 0)
            peak_eq = max(
                (v if isinstance(v, (int, float)) else v.get("equity", 0))
                for v in equity_data
            )
            _prom_equity_usd.set(current_eq)
            if peak_eq > 0:
                _prom_drawdown.set((peak_eq - current_eq) / peak_eq)

        pos_data = _read_json(DATA_DIR / "current_position.json") or {}
        _prom_open_positions.set(1 if pos_data.get("open") else 0)

        state_data = _read_json(DATA_DIR / "bot_state.json") or {}
        _prom_total_trades.set(state_data.get("total_cycles", 0))
        _prom_pnl_usd.set(state_data.get("total_pnl_usd", 0))
        _prom_iv_rank.set(state_data.get("iv_rank", 0))
    except Exception:
        pass

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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

    # Monte Carlo summary — read from actual results structure (no nested "summary" key)
    mc_summary = None
    if mc:
        # Filter zero-cycle windows before computing stats (they produce -inf / sentinel Sharpe)
        valid_runs = [r for r in mc.get("runs", []) if r.get("num_cycles", 0) > 0]
        valid_sharpes = sorted([
            r["sharpe"] for r in valid_runs
            if r.get("sharpe") is not None and abs(r["sharpe"]) < 1e10
        ])
        median_sharpe: float | None = valid_sharpes[len(valid_sharpes) // 2] if valid_sharpes else None
        p5_idx = max(0, int(len(valid_sharpes) * 0.05))
        # p5_sharpe available for future use
        # p5_sharpe = valid_sharpes[p5_idx] if valid_sharpes else None

        dists = mc.get("distributions", {})
        ret = dists.get("return_pct", {})
        prob_profit_raw = mc.get("prob_profit_pct", 0)

        mc_summary = {
            "prob_profit":   round(prob_profit_raw / 100.0, 4) if prob_profit_raw is not None else None,
            "median_sharpe": round(median_sharpe, 3) if median_sharpe is not None else None,
            "p5":            ret.get("p5"),
            "p50":           ret.get("p50"),
            "p95":           ret.get("p95"),
            "verdict": (
                "robust" if (median_sharpe or -1) > 1.0
                else "marginal" if (median_sharpe or -1) >= 0.5
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

    def _safe_float(v: Any, default: float = 0.0) -> float:
        """Tolerant float coercion — empty strings, None, NaN all map to default."""
        try:
            f = float(v)
            return f if f == f else default   # NaN check
        except (TypeError, ValueError):
            return default

    def _row_to_genome(row: dict) -> dict:
        """Map an optimizer-emitted dict (CSV row or JSON entry) onto the API
        EvolveGenome shape, including capital-efficiency metrics. Defensive
        about missing fields so old leaderboards (pre-fix) still load."""
        return {
            "fitness":               round(_safe_float(row.get("fitness")), 4),
            "sharpe":                round(_safe_float(row.get("sharpe_ratio")), 3),
            "return_pct":            round(_safe_float(row.get("total_return_pct")), 2),
            "win_rate":              round(_safe_float(row.get("win_rate_pct")), 1),
            "drawdown":              round(_safe_float(row.get("max_drawdown_pct")), 2),
            "num_cycles":            int(_safe_float(row.get("num_cycles"))),
            "trades_per_year":       round(_safe_float(row.get("trades_per_year")), 1),
            "avg_pnl_per_trade_usd": round(_safe_float(row.get("avg_pnl_per_trade_usd")), 2),
            "annualised_margin_roi": round(_safe_float(row.get("annualised_margin_roi")), 4),
            "premium_on_margin":     round(_safe_float(row.get("premium_on_margin")), 4),
            "min_viable_capital":    round(_safe_float(row.get("min_viable_capital")), 2),
            "avg_margin_utilization": round(_safe_float(row.get("avg_margin_utilization")), 4),
        }

    if csv_path.exists():
        try:
            with open(csv_path, newline="") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    try:
                        rows.append(_row_to_genome(row))
                    except Exception:
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
                    rows.append(_row_to_genome(b))
                except Exception:
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


@app.get("/optimizer/evolve_results_all", dependencies=[Depends(_require_api_key)])
def get_evolve_results_all() -> dict:
    """Return per-goal evolution results with version history and delta vs previous run."""
    result: dict[str, dict] = {}
    for goal in ("balanced", "max_yield", "safest", "sharpe", "capital_roi", "daily_trader", "small_bot_specialist"):
        genome = _read_yaml(OPT_DIR / f"best_genome_{goal}.yaml") or _read_yaml(OPT_DIR / "best_genome.yaml")
        ts = _evolve_goal_ts(goal)

        history_path = OPT_DIR / f"evolve_history_{goal}.json"
        history: list = []
        try:
            if history_path.exists():
                with open(history_path) as _hf:
                    history = json.load(_hf)
        except Exception:
            pass

        version = len(history)
        current  = history[-1]  if len(history) >= 1 else None
        previous = history[-2]  if len(history) >= 2 else None

        delta: dict | None = None
        if current and previous:
            delta = {
                "fitness":    round(
                    (current.get("fitness", 0) or 0) - (previous.get("fitness", 0) or 0), 4
                ),
                "return_pct": round(
                    (current.get("return_pct", 0) or 0) - (previous.get("return_pct", 0) or 0), 2
                ),
                "sharpe": round(
                    (current.get("sharpe", 0) or 0) - (previous.get("sharpe", 0) or 0), 3
                ),
            }

        result[goal] = {
            "version":   version,
            "timestamp": ts,
            "current":   current,
            "previous":  previous,
            "delta":     delta,
            "history":   history[-10:],   # last 10 runs
            "available": genome is not None,
        }
    return result


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


# ── Named Config Store ────────────────────────────────────────────────────────

class SaveConfigRequest(BaseModel):
    name: str
    params: dict = {}
    source: str = "manual"
    # Legacy nested metadata dict OR flat fields (frontend sends flat)
    metadata: dict | None = None
    # Flat metadata fields accepted from frontend
    notes: str | None = None
    fitness: float | None = None
    total_return_pct: float | None = None
    sharpe: float | None = None
    goal: str | None = None


@app.get("/configs", dependencies=[Depends(_require_api_key)])
def list_named_configs(include_archived: bool = False) -> list:
    """List all saved named configs with metadata. Archived configs hidden by default."""
    return _cs.list_configs(include_archived=include_archived)


@app.post("/configs", dependencies=[Depends(_require_api_key)])
def create_named_config(body: SaveConfigRequest) -> dict:
    """Save a named config. Merges params over master config.yaml.
    Accepts either a nested 'metadata' dict or flat fields (notes, fitness, etc.).
    """
    try:
        # Merge flat fields into metadata dict
        metadata = dict(body.metadata or {})
        if body.notes is not None:
            metadata.setdefault("notes", body.notes)
        if body.fitness is not None:
            metadata.setdefault("fitness", body.fitness)
        if body.total_return_pct is not None:
            metadata.setdefault("total_return_pct", body.total_return_pct)
        if body.sharpe is not None:
            metadata.setdefault("sharpe", body.sharpe)
        if body.goal is not None:
            metadata.setdefault("goal", body.goal)
        result = _cs.save_config(
            name=body.name,
            params=body.params,
            source=body.source,
            metadata=metadata or None,
        )
        return {"ok": True, "name": body.name, "config": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/configs/{name}", dependencies=[Depends(_require_api_key)])
def get_named_config(name: str) -> dict:
    """Load a named config merged over master config.yaml."""
    try:
        return _cs.load_config_by_name(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")


@app.delete("/configs/{name}", dependencies=[Depends(_require_api_key)])
def delete_named_config(name: str) -> dict:
    """Delete a named config. Refuses if status is 'live'."""
    try:
        deleted = _cs.delete_config(name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    return {"ok": True, "deleted": name}


# ── Config lifecycle endpoints ────────────────────────────────────────────────

class SetStatusRequest(BaseModel):
    status: str


class RenameRequest(BaseModel):
    new_name: str


class NotesRequest(BaseModel):
    notes: str


class ParamsRequest(BaseModel):
    params: dict


class DuplicateRequest(BaseModel):
    new_name: str


@app.patch("/configs/{name}/status", dependencies=[Depends(_require_api_key)])
def set_config_status(name: str, body: SetStatusRequest) -> dict:
    """Update a config's lifecycle status."""
    try:
        return _cs.set_status(name, body.status)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/configs/{name}/rename", dependencies=[Depends(_require_api_key)])
def rename_named_config(name: str, body: RenameRequest) -> dict:
    """Rename a config file and update _meta.name inside it."""
    try:
        return _cs.rename_config(name, body.new_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.patch("/configs/{name}/notes", dependencies=[Depends(_require_api_key)])
def update_config_notes(name: str, body: NotesRequest) -> dict:
    """Update the notes field in _meta."""
    try:
        return _cs.update_config_notes(name, body.notes)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")


@app.patch("/configs/{name}/params", dependencies=[Depends(_require_api_key)])
def update_config_params(name: str, body: ParamsRequest) -> dict:
    """Update the strategy/sizing/risk params of a config (merges, doesn't replace)."""
    try:
        return _cs.update_config_params(name, body.params)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")


@app.post("/configs/{name}/duplicate", dependencies=[Depends(_require_api_key)])
def duplicate_named_config(name: str, body: DuplicateRequest) -> dict:
    """Create a copy of a config with a new name, status reset to draft."""
    try:
        return _cs.duplicate_config(name, body.new_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/configs/{name}/archive", dependencies=[Depends(_require_api_key)])
def archive_named_config(name: str) -> dict:
    """Set config status to 'archived'."""
    try:
        return _cs.archive_config(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")


@app.post("/configs/{name}/start-paper", dependencies=[Depends(_require_api_key)])
def start_paper_testing(name: str) -> dict:
    """Set config status to 'paper' — farm supervisor will pick it up and start a bot."""
    try:
        return _cs.set_status(name, "paper")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/configs/{name}/stop-paper", dependencies=[Depends(_require_api_key)])
def stop_paper_testing(name: str) -> dict:
    """Set config status back to 'validated' — farm supervisor will stop the bot."""
    try:
        # Try to read current status to decide what to revert to
        import yaml as _yaml
        from pathlib import Path as _Path
        cfg_path = _Path(_cs.get_config_yaml_path(name))
        if cfg_path.exists():
            data = _yaml.safe_load(cfg_path.read_text()) or {}
            current = data.get("_meta", {}).get("status", "draft")
            # Revert to validated if possible, else draft
            target = "validated" if current in ("paper", "ready") else "draft"
        else:
            target = "draft"
        return _cs.set_status(name, target)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class PromoteRequest(BaseModel):
    starting_equity: float  # actual deposit amount in USD


@app.post("/configs/{name}/promote", dependencies=[Depends(_require_api_key)])
def promote_named_config(name: str, req: PromoteRequest) -> dict:
    """
    Promote a named config to live (overwrites config.yaml).
    - Forces testnet=false so real money is traded on mainnet
    - Sets starting_equity to the provided deposit amount
    - Backs up current config.yaml to config.yaml.bak first
    - Logs the promotion event to data/promotion_log.json
    """
    if req.starting_equity <= 0:
        raise HTTPException(status_code=400, detail="starting_equity must be > 0")
    try:
        result = _cs.promote_to_live(name, req.starting_equity)
        log_entry = result["promotion_log"]
        return {
            "ok": True,
            "promoted": name,
            "message": "config.yaml updated; restart bot to apply",
            "starting_equity": log_entry["starting_equity"],
            "timestamp": log_entry["timestamp"],
            "backup_path": log_entry["previous_config_backup_path"],
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/configs/{name}/download", dependencies=[Depends(_require_api_key)])
def download_named_config(name: str) -> Response:
    """Download a named config's YAML file."""
    path = Path(_cs.get_config_yaml_path(name))
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config '{name}' not found")
    return Response(
        content=path.read_bytes(),
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{name}.yaml"'},
    )


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


_EVOLVE_GOALS = ("balanced", "max_yield", "safest", "sharpe", "capital_roi", "small_bot_specialist")


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
    config_name: str | None = None        # named config to use as starting parameters
    seed_config_name: str | None = None   # evolve: seed population from this config


_VALID_OPT_MODES = {"sweep", "evolve", "walk_forward", "monte_carlo", "reconcile"}
# Modes not yet implemented in optimizer.py — return a clear error rather than crashing
_UNIMPLEMENTED_MODES: set[str] = set()  # reconcile is now implemented


def _apply_named_config_to_env(config_name: str) -> dict:
    """
    Load a named config and write it to a temp YAML file so the optimizer
    subprocess can read it via WHEEL_BOT_CONFIG.
    Returns the env dict to pass to the subprocess (or {} on failure).
    """
    import tempfile
    try:
        cfg_data = _cs.load_config_by_name(config_name)
        cfg_data.pop("_meta", None)   # strip metadata before writing as config
        tmp = tempfile.NamedTemporaryFile(
            suffix=".yaml", delete=False, dir=str(BASE_DIR), prefix=f"_named_cfg_{config_name}_"
        )
        yaml.dump(cfg_data, tmp, default_flow_style=False, allow_unicode=True)
        tmp.close()
        return {"WHEEL_BOT_CONFIG": tmp.name}
    except Exception:
        return {}


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
    if body.mode == "evolve" and body.seed_config_name:
        cmd += ["--seed-config", body.seed_config_name]

    env = os.environ.copy()
    if body.config_name:
        named_env = _apply_named_config_to_env(body.config_name)
        env.update(named_env)

    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "optimizer_pid.txt").write_text(str(proc.pid))
    result: dict = {"ok": True, "pid": proc.pid}
    if body.config_name:
        result["config_name"] = body.config_name
    if body.seed_config_name:
        result["seed_config_name"] = body.seed_config_name
    return result


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


# ── Chart data ─────────────────────────────────────────────────────────────────

_chart_cache: dict = {}

@app.get("/chart/btc_history", dependencies=[Depends(_require_api_key)])
def get_btc_history(days: int = 30, bot_id: str | None = None) -> dict:
    """Return BTC OHLC candles + strategy overlay values for the chart."""
    cache_key = f"chart_{days}_{bot_id or 'main'}"
    now = time.time()
    if _chart_cache.get(cache_key, {}).get("expires", 0) > now:
        return _chart_cache[cache_key]["data"]

    # Resolution: 360-min (6h) candles for 7d, daily for 30d/90d
    # Deribit valid resolutions: 1,3,5,10,15,30,60,120,180,360,720,1D
    resolution = "360" if days <= 7 else "1D"
    end_ts = int(now * 1000)
    start_ts = end_ts - days * 24 * 60 * 60 * 1000

    try:
        r = _requests.get(
            "https://www.deribit.com/api/v2/public/get_tradingview_chart_data",
            params={
                "instrument_name": "BTC-PERPETUAL",
                "start_timestamp": start_ts,
                "end_timestamp":   end_ts,
                "resolution":      resolution,
            },
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()["result"]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Deribit OHLC unavailable: {e}")

    ticks  = result.get("ticks",  [])
    opens  = result.get("open",   [])
    highs  = result.get("high",   [])
    lows   = result.get("low",    [])
    closes = result.get("close",  [])

    candles = [
        {"time": ticks[i] // 1000, "open": opens[i], "high": highs[i],
         "low": lows[i], "close": closes[i]}
        for i in range(len(ticks))
    ]

    # Live spot price (overrides last close so current candle is accurate)
    current_price: float | None = closes[-1] if closes else None
    try:
        lp = _requests.get(
            "https://www.deribit.com/api/v2/public/get_index_price",
            params={"index_name": "btc_usd"}, timeout=3,
        )
        current_price = lp.json()["result"]["index_price"]
        # Update last candle's close with live price
        if candles:
            last = candles[-1]
            candles[-1] = {
                **last,
                "close": current_price,
                "high":  max(last["high"], current_price),
                "low":   min(last["low"],  current_price),
            }
    except Exception:
        pass

    # Config overlays
    if bot_id:
        effective_data_dir = FARM_DIR / bot_id / "data"
        cfg = _read_yaml(FARM_DIR / bot_id / "config.yaml") or {}
        pos = _read_json(effective_data_dir / "current_position.json") or {}
    else:
        effective_data_dir = DATA_DIR
        cfg = _read_yaml(BASE_DIR / "config.yaml") or {}
        pos = _read_json(DATA_DIR / "current_position.json") or {}

    otm_offset       = cfg.get("backtest",  {}).get("approx_otm_offset",        0.05)
    delta_min        = cfg.get("strategy",  {}).get("target_delta_min",          0.15)
    delta_max        = cfg.get("strategy",  {}).get("target_delta_max",          0.35)
    min_dte          = cfg.get("strategy",  {}).get("min_dte",                   14)
    max_dte          = cfg.get("strategy",  {}).get("max_dte",                   45)
    max_eq_per_leg   = cfg.get("sizing",    {}).get("max_equity_per_leg",        0.10)
    iv_threshold     = cfg.get("strategy",  {}).get("iv_rank_threshold",         30)
    premium_fraction = cfg.get("backtest",  {}).get("premium_fraction_of_spot",  0.02)
    starting_equity  = cfg.get("backtest",  {}).get("starting_equity",           0)

    # Target zone where bot would place puts (±50% of offset, centered on otm strike)
    zone_center = round(current_price * (1 - otm_offset), 0)          if current_price else None
    zone_upper  = round(current_price * (1 - otm_offset * 0.5), 0)    if current_price else None
    zone_lower  = round(current_price * (1 - otm_offset * 1.5), 0)    if current_price else None

    # Active position
    is_open        = bool(pos.get("open"))
    active_strike  = pos.get("strike")            if is_open else None
    contracts      = pos.get("contracts", 0)      if is_open else 0
    prem_collected = pos.get("premium_collected", 0) if is_open else 0
    breakeven: float | None = None
    if active_strike and contracts and contracts > 0:
        prem_per_btc = prem_collected / contracts
        breakeven = round(active_strike - prem_per_btc, 0)

    expiry_ts: int | None = None
    if is_open and pos.get("expiry"):
        try:
            expiry_ts = int(datetime.strptime(pos["expiry"], "%Y-%m-%d").timestamp())
        except Exception:
            pass

    # Trade history markers
    import csv as _csv
    trade_markers = []
    if bot_id:
        trades_path = effective_data_dir / "trades.csv"
        if trades_path.exists():
            with open(trades_path, newline="") as f:
                for row in _csv.DictReader(f):
                    try:
                        exit_ts = int(datetime.fromisoformat(row["timestamp"]).timestamp())
                        strike = float(row.get("strike") or 0)
                        pnl = float(row.get("pnl_usd") or 0)
                        trade_markers.append({
                            "entry_time": exit_ts,
                            "exit_time": exit_ts,
                            "strike": strike,
                            "pnl_usd": pnl,
                            "won": pnl >= 0,
                            "reason": row.get("reason", ""),
                        })
                    except Exception:
                        continue
    else:
        raw_trades = _read_json(DATA_DIR / "paper_trades" / "paper_trades.json") or []
        for t in raw_trades:
            try:
                entry_ts = int(datetime.fromisoformat(t["entry_date"]).timestamp())
                exit_ts_val: int | None = None
                if t.get("exit_date"):
                    exit_ts_val = int(datetime.fromisoformat(t["exit_date"]).timestamp())
                pnl = t.get("pnl_usd") or 0
                trade_markers.append({
                    "entry_time": entry_ts,
                    "exit_time":  exit_ts_val,
                    "strike":     t.get("strike"),
                    "pnl_usd":    pnl,
                    "won":        pnl >= 0,
                    "reason":     t.get("reason", ""),
                })
            except Exception:
                continue

    payload = {
        "candles":      candles,
        "current_price": current_price,
        "resolution":   resolution,
        "overlays": {
            "zone_upper":    zone_upper,
            "zone_center":   zone_center,
            "zone_lower":    zone_lower,
            "active_strike": active_strike,
            "breakeven":     breakeven,
            "expiry_ts":     expiry_ts,
        },
        "config": {
            "otm_offset":       otm_offset,
            "target_delta_min": delta_min,
            "target_delta_max": delta_max,
            "min_dte":          min_dte,
            "max_dte":          max_dte,
            "max_equity_per_leg": max_eq_per_leg,
            "iv_rank_threshold":  iv_threshold,
            "premium_fraction":   premium_fraction,
            "starting_equity":    starting_equity,
        },
        "trade_markers": trade_markers,
    }
    # Cache 60s for 30d/90d, 20s for 7d (more real-time feel)
    _chart_cache[cache_key] = {"data": payload, "expires": now + (20 if days <= 7 else 60)}
    return payload


# ── Streamlit Dashboard Proxy ─────────────────────────────────────────────────
#
# Streamlit binds to 0.0.0.0:8501 but rejects connections that arrive via the
# Mac's own LAN IP (192.168.x.x) with ERR_EMPTY_RESPONSE — a known macOS
# self-connection quirk combined with Streamlit's Host-header check.
#
# Fix: proxy all dashboard traffic through FastAPI (already LAN-accessible at
# :8765).  The browser visits http://<lan-ip>:8765/dashboard; that page's HTML
# references Streamlit-internal paths like /_stcore/*, /static/*, /media/*,
# /component/* which FastAPI forwards to localhost:8501.
#
# ─────────────────────────────────────────────────────────────────────────────

_SL_HTTP = "http://127.0.0.1:8501"
_SL_WS   = "ws://127.0.0.1:8501"

# Headers that must not be forwarded between proxy hops
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})

_sl_client: httpx.AsyncClient | None = None


def _get_sl_client() -> httpx.AsyncClient:
    global _sl_client
    if _sl_client is None:
        _sl_client = httpx.AsyncClient(
            base_url=_SL_HTTP,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
    return _sl_client


async def _sl_http_proxy(request: Request, target_path: str) -> Response:
    """Forward one HTTP request to Streamlit and return the response."""
    client = _get_sl_client()
    url = target_path or "/"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }
    body = await request.body()
    try:
        resp = await client.request(request.method, url, headers=headers, content=body)
    except httpx.ConnectError:
        return Response(
            content=b"Streamlit dashboard is not running (start it with: streamlit run dashboard_ui.py)",
            status_code=503,
            media_type="text/plain",
        )
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)


# /dashboard  →  Streamlit root
@app.api_route("/dashboard", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
               include_in_schema=False)
async def proxy_dashboard_root(request: Request) -> Response:
    return await _sl_http_proxy(request, "/")


# /dashboard/<anything>  →  Streamlit /<anything>
@app.api_route("/dashboard/{path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
               include_in_schema=False)
async def proxy_dashboard_path(request: Request, path: str) -> Response:
    return await _sl_http_proxy(request, f"/{path}")


# Streamlit's HTML references /_stcore/*, /static/*, /media/*, /component/*
# at the root of the origin — proxy those through as well.

@app.api_route("/_stcore/{path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
               include_in_schema=False)
async def proxy_sl_stcore(request: Request, path: str) -> Response:
    return await _sl_http_proxy(request, f"/_stcore/{path}")


@app.api_route("/static/{path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
               include_in_schema=False)
async def proxy_sl_static(request: Request, path: str) -> Response:
    return await _sl_http_proxy(request, f"/static/{path}")


@app.api_route("/media/{path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
               include_in_schema=False)
async def proxy_sl_media(request: Request, path: str) -> Response:
    return await _sl_http_proxy(request, f"/media/{path}")


@app.api_route("/component/{path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
               include_in_schema=False)
async def proxy_sl_component(request: Request, path: str) -> Response:
    return await _sl_http_proxy(request, f"/component/{path}")


# WebSocket proxy — Streamlit's real-time stream lives at /_stcore/stream
@app.websocket("/_stcore/stream")
async def proxy_sl_ws(client_ws: WebSocket):
    """Bi-directional WebSocket bridge between the browser and Streamlit."""
    qs = client_ws.url.query
    target = f"{_SL_WS}/_stcore/stream" + (f"?{qs}" if qs else "")

    await client_ws.accept()
    try:
        async with _websockets.connect(target) as sl_ws:

            async def browser_to_sl():
                try:
                    while True:
                        msg = await client_ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if msg.get("bytes") is not None:
                            await sl_ws.send(msg["bytes"])
                        elif msg.get("text") is not None:
                            await sl_ws.send(msg["text"])
                except Exception:
                    pass

            async def sl_to_browser():
                try:
                    async for msg in sl_ws:
                        if isinstance(msg, bytes):
                            await client_ws.send_bytes(msg)
                        else:
                            await client_ws.send_text(msg)
                except Exception:
                    pass

            tasks = [
                asyncio.create_task(browser_to_sl()),
                asyncio.create_task(sl_to_browser()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

    except Exception as exc:
        print(f"[dashboard-proxy] WS error: {exc}")
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass


# ── Bot Farm endpoints ────────────────────────────────────────────────────────

FARM_DIR    = BASE_DIR / "farm"
FARM_PID_FILE = DATA_DIR / "farm_pid.txt"
FARM_STATUS = FARM_DIR / "status.json"


def _farm_is_running() -> bool:
    if not FARM_PID_FILE.exists():
        return False
    try:
        pid = int(FARM_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        stat = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                              capture_output=True, text=True)
        if "Z" in stat.stdout:
            FARM_PID_FILE.unlink(missing_ok=True)
            return False
        return True
    except (ValueError, OSError):
        FARM_PID_FILE.unlink(missing_ok=True)
        return False


@app.get("/farm/status", dependencies=[Depends(_require_api_key)])
def get_farm_status() -> dict:
    """Return farm/status.json content, or generate on-demand if stale."""
    if not FARM_STATUS.exists():
        raise HTTPException(status_code=404, detail="Farm has not been started")
    data = _read_json(FARM_STATUS)
    if data is None:
        raise HTTPException(status_code=404, detail="Farm status file unreadable")
    data["farm_running"] = _farm_is_running()

    # ── Risk-transition Telegram alerts ──────────────────────────────────────
    # Fire a Telegram notification the first time a bot crosses into caution or
    # danger.  We track the last-known level in data/farm_risk_levels.json so
    # we only notify on transitions, not every poll cycle.
    try:
        risk_path  = DATA_DIR / "farm_risk_levels.json"
        last_known = (_read_json(risk_path) or {}) if risk_path.exists() else {}
        current    = {}
        changed    = False

        import notifier as _notifier

        for bot in data.get("bots", []):
            bot_id = bot.get("id", "")
            risk   = bot.get("position_risk", "ok")
            current[bot_id] = risk

            prev = last_known.get(bot_id, "ok")
            # Notify on ok→caution, ok→danger, caution→danger transitions
            if risk in ("caution", "danger") and prev != risk:
                changed = True
                pos = bot.get("open_position") or {}
                try:
                    _notifier.notify_position_risk(
                        bot.get("name", bot_id), risk, pos
                    )
                except Exception:
                    pass
            # Also notify if position cleared (danger/caution → ok = relief)
            if risk == "ok" and prev in ("caution", "danger"):
                changed = True
                try:
                    _notifier._send(
                        f"✅ <b>{bot.get('name', bot_id)}</b> — position risk cleared\n"
                        f"Back to normal levels."
                    )
                except Exception:
                    pass

        if changed or not risk_path.exists():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            risk_path.write_text(json.dumps(current))
    except Exception:
        pass

    return data


@app.get("/farm/bot/{bot_id}/readiness", dependencies=[Depends(_require_api_key)])
def get_bot_readiness(bot_id: str) -> dict:
    """Return the ReadinessReport for a specific bot as JSON."""
    bot_dir = FARM_DIR / bot_id
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot directory not found: {bot_id}")
    try:
        from readiness_validator import validate_bot
        import yaml as _yaml
        # Load thresholds from farm_config.yaml if available
        farm_cfg_path = BASE_DIR / "farm_config.yaml"
        thresholds: dict = {}
        starting_equity: float = 10_000.0
        if farm_cfg_path.exists():
            fc = _read_yaml(farm_cfg_path) or {}
            thresholds = fc.get("readiness_thresholds", {})
            # Find starting equity for this bot
            for spec in fc.get("bots", []):
                if spec.get("id") == bot_id:
                    starting_equity = float(
                        spec.get("overrides", {}).get("backtest", {}).get("starting_equity", 10_000.0)
                    )
                    break
        report = validate_bot(bot_dir, thresholds=thresholds, starting_equity=starting_equity)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Validation error: {exc}")

    return {
        "bot_id":         report.bot_id,
        "ready":          report.ready,
        "checks_passed":  report.checks_passed,
        "total_checks":   report.total_checks,
        "checks":         report.checks,
        "metrics":        report.metrics,
        "recommendation": report.recommendation,
        "blocking_issues": report.blocking_issues,
    }


@app.post("/farm/start", dependencies=[Depends(_require_api_key)])
def farm_start() -> dict:
    """Start the bot farm supervisor (bot_farm.py) as a subprocess."""
    if _farm_is_running():
        pid = int(FARM_PID_FILE.read_text().strip())
        return {"status": "already_running", "pid": pid}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FARM_DIR.mkdir(parents=True, exist_ok=True)

    log_path = BASE_DIR / "logs" / "farm.log"
    log_path.parent.mkdir(exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "bot_farm.py")],
        cwd=str(BASE_DIR),
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
    )
    FARM_PID_FILE.write_text(str(proc.pid))

    # Notify via Telegram — count bots and any that are live-ready
    try:
        import notifier as _notifier
        bot_dirs = [d for d in FARM_DIR.iterdir() if d.is_dir() and (d / "config.yaml").exists()]
        live_count = 0
        for bd in bot_dirs:
            try:
                import yaml as _yaml
                cfg = _yaml.safe_load((bd / "config.yaml").read_text()) or {}
                if cfg.get("_meta", {}).get("status") in ("live", "ready"):
                    live_count += 1
            except Exception:
                pass
        _notifier.notify_farm_started(len(bot_dirs), live_count)
    except Exception:
        pass

    return {"status": "started", "pid": proc.pid}


@app.post("/farm/stop", dependencies=[Depends(_require_api_key)])
def farm_stop() -> dict:
    """Send SIGTERM to the bot_farm.py process."""
    if not FARM_PID_FILE.exists():
        return {"status": "not_running"}

    # Gather farm state BEFORE killing so we can include it in the notification
    try:
        import notifier as _notifier
        bot_dirs = [d for d in FARM_DIR.iterdir() if d.is_dir() and (d / "config.yaml").exists()]
        open_positions = 0
        for bd in bot_dirs:
            try:
                pos_path = bd / "data" / "current_position.json"
                if pos_path.exists():
                    pos = _read_json(pos_path) or {}
                    if pos.get("instrument") or pos.get("strike"):
                        open_positions += 1
            except Exception:
                pass
        _notifier.notify_farm_stopped(len(bot_dirs), open_positions, manual=True)
    except Exception:
        pass

    try:
        pid = int(FARM_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        FARM_PID_FILE.unlink(missing_ok=True)
        return {"status": "stopped", "pid": pid}
    except (ValueError, OSError) as exc:
        FARM_PID_FILE.unlink(missing_ok=True)
        return {"status": "not_running", "detail": str(exc)}


@app.post("/farm/bot/{bot_id}/close_position", dependencies=[Depends(_require_api_key)])
def farm_bot_close_position(bot_id: str) -> dict:
    """
    Write a close_position command to the bot's command file.
    The bot subprocess picks this up on its next poll cycle and closes the open option.
    """
    bot_dir = FARM_DIR / bot_id
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot directory not found: {bot_id}")

    data_dir = bot_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cmd_path = data_dir / "bot_commands.json"

    record = {
        "command":   "close_position",
        "timestamp": datetime.utcnow().isoformat(),
        "source":    "emergency_close",
    }
    cmd_path.write_text(json.dumps(record))

    try:
        import notifier as _notifier
        pos_path = data_dir / "current_position.json"
        pos = (_read_json(pos_path) or {}) if pos_path.exists() else {}
        opt  = (pos.get("type") or "option").replace("short_", "").upper()
        strike = pos.get("strike", "?")
        _notifier._send(
            f"🛑 <b>Emergency Close — {bot_id}</b>\n"
            f"Short {opt} @ ${strike:,} — close command sent.\n"
            f"Bot will execute the buy-back on its next cycle."
        )
    except Exception:
        pass

    return {"ok": True, "bot_id": bot_id, "command": "close_position"}


class AssignConfigRequest(BaseModel):
    config_name: str


@app.post("/farm/bot/{bot_id}/assign-config", dependencies=[Depends(_require_api_key)])
def farm_bot_assign_config(bot_id: str, body: AssignConfigRequest) -> dict:
    """
    Assign a named config to a specific farm bot.
    Copies the named config's merged params to farm/bot_N/config.yaml.
    The farm supervisor will detect the change and restart the bot on its next tick.
    """
    bot_dir = FARM_DIR / bot_id
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot directory not found: {bot_id}")

    try:
        cfg_data = _cs.load_config_by_name(body.config_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{body.config_name}' not found")

    # Extract _meta, preserve a minimal version so bot_farm can read config_name
    meta = cfg_data.pop("_meta", {})

    # Force paper/testnet mode
    cfg_data.setdefault("deribit", {})["testnet"] = True

    # Write back a minimal _meta so _current_config_name() works
    cfg_data["_meta"] = {
        "name":       meta.get("name", body.config_name),
        "source":     meta.get("source", "assigned"),
        "assigned_at": datetime.utcnow().isoformat(),
    }

    # Write to bot directory
    bot_config_path = bot_dir / "config.yaml"
    with open(bot_config_path, "w") as f:
        yaml.dump(cfg_data, f, default_flow_style=False, allow_unicode=True)

    # Write a restart sentinel the farm supervisor can poll for
    (bot_dir / "RESTART_REQUESTED").write_text(body.config_name)

    return {
        "ok": True,
        "bot_id": bot_id,
        "config_name": body.config_name,
        "config_label": meta.get("name", body.config_name),
        "message": f"Config assigned to {bot_id}; bot will restart on next supervisor tick",
    }


@app.get("/farm/bot/{bot_id}/trades", dependencies=[Depends(_require_api_key)])
def get_farm_bot_trades(bot_id: str) -> list:
    """Return trades.csv content for a specific farm bot as a JSON array."""
    import csv as _csv
    bot_dir = FARM_DIR / bot_id
    trades_path = bot_dir / "data" / "trades.csv"
    if not trades_path.exists():
        return []
    try:
        with open(trades_path, newline="") as f:
            rows = list(_csv.DictReader(f))
        # Convert numeric-looking fields
        numeric_fields = {
            "pnl_usd", "pnl_btc", "equity_before", "equity_after",
            "entry_price", "exit_price", "contracts", "strike",
            "btc_price", "dte_at_entry", "dte_at_close",
        }
        result = []
        for row in rows:
            clean: dict = {}
            for k, v in row.items():
                if k in numeric_fields:
                    try:
                        clean[k] = float(v)
                    except (ValueError, TypeError):
                        clean[k] = v
                else:
                    clean[k] = v
            result.append(clean)
        return sorted(result, key=lambda t: t.get("timestamp", ""), reverse=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading trades: {exc}")


@app.get("/farm/bot/{bot_id}/state", dependencies=[Depends(_require_api_key)])
def get_farm_bot_state(bot_id: str) -> dict:
    """
    Return the live state for a specific farm bot:
      - current open position (from current_position.json)
      - bot_state summary (mode, iv_rank, last heartbeat)
      - kill_switch active flag
      - last 5 trades for quick activity view
    """
    import csv as _csv

    bot_dir = FARM_DIR / bot_id
    if not bot_dir.exists():
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")

    data_dir = bot_dir / "data"

    # Current position
    pos = _read_json(data_dir / "current_position.json") or {}

    # Bot state
    state = _read_json(data_dir / "bot_state.json") or {}

    # Kill switch
    kill_switch_active = (bot_dir / "KILL_SWITCH").exists()

    # Heartbeat age
    heartbeat = _read_json(data_dir / "bot_heartbeat.json") or {}
    heartbeat_ts = heartbeat.get("timestamp")
    heartbeat_age_s: float | None = None
    if heartbeat_ts:
        try:
            from datetime import datetime, timezone
            hb = datetime.fromisoformat(heartbeat_ts.replace("Z", "+00:00"))
            heartbeat_age_s = (datetime.now(timezone.utc) - hb).total_seconds()
        except Exception:
            pass

    # Last 5 trades
    recent_trades: list = []
    trades_path = data_dir / "trades.csv"
    if trades_path.exists():
        try:
            with open(trades_path, newline="") as f:
                rows = list(_csv.DictReader(f))
            numeric_fields = {"pnl_usd", "equity_after", "strike", "entry_price", "exit_price", "contracts", "dte_at_close"}
            clean_rows = []
            for row in rows:
                clean: dict = {}
                for k, v in row.items():
                    if k in numeric_fields:
                        try:
                            clean[k] = float(v)
                        except (ValueError, TypeError):
                            clean[k] = v
                    else:
                        clean[k] = v
                clean_rows.append(clean)
            recent_trades = sorted(clean_rows, key=lambda t: t.get("timestamp", ""), reverse=True)[:5]
        except Exception:
            pass

    return {
        "bot_id": bot_id,
        "kill_switch_active": kill_switch_active,
        "heartbeat_age_seconds": heartbeat_age_s,
        "position": pos,
        "state": {
            "mode":          state.get("mode"),
            "config_name":   state.get("config_name"),
            "iv_rank":       state.get("iv_rank"),
            "total_cycles":  state.get("total_cycles"),
            "total_pnl_usd": state.get("total_pnl_usd"),
            "equity_usd":    state.get("equity_usd"),
        },
        "recent_trades": recent_trades,
    }


# ── Black Swan stress test ─────────────────────────────────────────────────────

import threading as _threading

_BLACK_SWAN_JOBS: dict[str, dict] = {}   # job_id → {status, config_name, started_at}
_BLACK_SWAN_LOCK = _threading.Lock()


class _BlackSwanRunRequest(BaseModel):
    config_name: str
    bot_id: str | None = None
    skip_prereqs: bool = False


@app.get("/black_swan/prereqs/{config_name}", dependencies=[Depends(_require_api_key)])
def black_swan_prereqs(config_name: str):
    """Check whether prerequisites are met before running the black swan test."""
    import black_swan as _bs
    met, missing = _bs.check_prerequisites(config_name)
    return {"met": met, "missing": missing}


@app.post("/black_swan/run", dependencies=[Depends(_require_api_key)])
def black_swan_run(req: _BlackSwanRunRequest):
    """
    Start an async black-swan stress test for the given config.
    Returns a job_id to poll with GET /black_swan/status/{job_id}.
    Only one run per config_name is allowed at a time.
    """
    import uuid
    import black_swan as _bs

    # Reject duplicate runs for the same config
    with _BLACK_SWAN_LOCK:
        running = [
            j for j in _BLACK_SWAN_JOBS.values()
            if j["config_name"] == req.config_name and j["status"] == "running"
        ]
        if running:
            return {"job_id": running[0]["job_id"], "status": "already_running"}

        job_id = str(uuid.uuid4())[:8]
        _BLACK_SWAN_JOBS[job_id] = {
            "job_id":      job_id,
            "config_name": req.config_name,
            "status":      "running",
            "started_at":  datetime.utcnow().isoformat() + "Z",
            "error":       None,
        }

    def _worker():
        try:
            report = _bs.run_black_swan(
                config_name=req.config_name,
                bot_id=req.bot_id,
                skip_prereq_check=req.skip_prereqs,
            )
            _bs.save_report(report)
            with _BLACK_SWAN_LOCK:
                _BLACK_SWAN_JOBS[job_id]["status"] = "done"
                _BLACK_SWAN_JOBS[job_id]["verdict"] = report.verdict
        except Exception as exc:
            with _BLACK_SWAN_LOCK:
                _BLACK_SWAN_JOBS[job_id]["status"] = "error"
                _BLACK_SWAN_JOBS[job_id]["error"] = str(exc)[:300]

    t = _threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"job_id": job_id, "status": "running"}


@app.get("/black_swan/status/{job_id}", dependencies=[Depends(_require_api_key)])
def black_swan_status(job_id: str):
    """Poll the status of a black swan run."""
    with _BLACK_SWAN_LOCK:
        job = _BLACK_SWAN_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/black_swan/results/{config_name}", dependencies=[Depends(_require_api_key)])
def black_swan_results(config_name: str):
    """Return the most recent black swan report for the given config."""
    import black_swan as _bs
    report = _bs.load_report(config_name)
    if not report:
        raise HTTPException(status_code=404, detail="No results found for this config")
    return report


# ── PWA static file serving ────────────────────────────────────────────────────
_STATIC_DIR = BASE_DIR / "mobile-app" / "dist"

# Files that must never be cached so the browser always fetches the latest SW
_NO_CACHE_FILES = {"sw.js", "registerSW.js", "workbox-8c29f6e4.js", "index.html", "manifest.webmanifest"}

def _static_response(path) -> FileResponse:
    """Return a FileResponse with appropriate cache headers."""
    name = path.name if hasattr(path, "name") else str(path).split("/")[-1]
    headers = (
        {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
        if name in _NO_CACHE_FILES
        else {}
    )
    return FileResponse(path, headers=headers)

if _STATIC_DIR.exists():
    # Serve the SPA index for all unmatched paths so client-side routing works
    @app.get("/", include_in_schema=False)
    async def serve_root():
        return _static_response(_STATIC_DIR / "index.html")

    # The app is built with base="/btc-wheel-bot/", so all asset URLs include
    # that prefix.  Strip it before resolving against the dist directory.
    _PWA_PREFIX = "btc-wheel-bot/"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        rel = full_path[len(_PWA_PREFIX):] if full_path.startswith(_PWA_PREFIX) else full_path
        file = _STATIC_DIR / rel
        if file.exists() and file.is_file():
            return _static_response(file)
        return _static_response(_STATIC_DIR / "index.html")

    app.mount("/btc-wheel-bot/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")
