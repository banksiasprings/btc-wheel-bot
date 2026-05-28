"""
api.py — grid-farm web backend (FastAPI), served at bot.banksiaspringsfarm.com.

Replaces the retired options-bot API. Serves:
  - GET /health                         liveness
  - GET /farm/status   (X-API-Key)      farm + per-variant status (the Android widget reads this)
  - GET /farm/equity   (X-API-Key)      aggregate equity (the Android widget reads this)
  - GET /  and  /widget                 a clean mobile dashboard page (server-rendered, no key)

Data source: grid_farm/status.json (written hourly by grid_farm.py). Reuses the
existing WHEEL_API_KEY from .env, so the already-installed Android widget keeps
working with no APK rebuild.

Run:  python3.11 -m uvicorn api:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
STATUS = BASE_DIR / "grid_farm" / "status.json"
API_KEY = os.getenv("WHEEL_API_KEY", "").strip()   # same key the widget was built with

app = FastAPI(title="BTC Grid Farm API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


def _require_api_key(x_api_key: str = Header(None)) -> None:
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _load() -> dict | None:
    try:
        return json.loads(STATUS.read_text())
    except Exception:
        return None


def _is_fresh(updated: str, max_age_s: float = 7200) -> bool:
    """Farm counts as 'running' if status.json was written within ~2 hours."""
    try:
        t = datetime.fromisoformat(updated)
        return (datetime.now(timezone.utc) - t).total_seconds() < max_age_s
    except Exception:
        return False


@app.get("/health")
def health():
    data = _load()
    return {"status": "ok", "farm_data": data is not None}


@app.get("/farm/status", dependencies=[Depends(_require_api_key)])
def farm_status() -> dict:
    data = _load()
    if not data:
        return {"farm_running": False, "btc_price": 0, "bots": []}
    bots = [{
        "id": v["slug"],
        "name": v["name"],
        "style": v["style"],
        "status": "running",
        "has_open_position": v.get("btc_held", 0) > 1e-9,   # holding BTC = "open position"
        "equity": v["equity"],
        "profit": v["profit"],
        "return_pct": v["return_pct"],
        "max_drawdown_pct": v["max_drawdown_pct"],
        "leverage": v.get("leverage", 1.0),
        "trades": v.get("trades", 0),
        "state": v.get("state", ""),
    } for v in data.get("variants", [])]
    return {
        "farm_running": _is_fresh(data.get("updated", "")),
        "updated": data.get("updated"),
        "btc_price": data.get("btc_price", 0),
        "bots": bots,
    }


@app.get("/farm/equity", dependencies=[Depends(_require_api_key)])
def farm_equity() -> dict:
    data = _load()
    variants = (data or {}).get("variants", [])
    start_each = (data or {}).get("paper_capital", 10_000.0)
    total_current = sum(v["equity"] for v in variants)
    total_starting = start_each * len(variants)
    ret = (total_current / total_starting - 1) * 100 if total_starting else 0.0
    return {
        "total_current": round(total_current, 2),
        "total_starting": round(total_starting, 2),
        "total_return_pct": round(ret, 2),
    }


# ── Mobile dashboard page (server-rendered, no API key needed) ────────────────

def _page() -> str:
    data = _load()
    if not data:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                "<h2>BTC Grid Farm</h2><p>The farm isn't running yet. Start it on the Mac:</p>"
                "<pre>caffeinate -s python3.11 grid_farm.py</pre></body>")
    rows = sorted(data.get("variants", []), key=lambda v: v["equity"], reverse=True)
    btc = data.get("btc_price", 0)
    updated = data.get("updated", "")[:16].replace("T", " ")
    cards = []
    for i, v in enumerate(rows, 1):
        up = v["profit"] >= 0
        col = "#22c55e" if up else "#ef4444"
        sign = "+" if up else ""
        warn = " ⚠️" if v.get("leverage", 1) > 1 else ""
        cards.append(f"""
        <div style="background:#151a23;border-radius:14px;padding:14px 16px;margin:10px 0;border-left:4px solid {col}">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <span style="font-size:17px;font-weight:600">{i}. {v['name']}{warn}</span>
            <span style="font-size:18px;font-weight:700">${v['equity']:,.0f}</span>
          </div>
          <div style="color:#8b95a5;font-size:13px;margin:2px 0 8px">{v['style']}</div>
          <div style="display:flex;justify-content:space-between;font-size:14px">
            <span style="color:{col};font-weight:600">{sign}${v['profit']:,.0f} ({sign}{v['return_pct']:.1f}%)</span>
            <span style="color:#8b95a5">worst dip −{v['max_drawdown_pct']:.1f}%</span>
          </div>
          <div style="color:#6b7280;font-size:12px;margin-top:6px">{v['state']} · {v.get('trades',0)} trades</div>
        </div>""")
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60>
<title>BTC Grid Farm</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:680px;margin:auto">
  <h2 style="margin:0 0 2px">📈 BTC Grid Farm</h2>
  <div style="color:#8b95a5;font-size:14px;margin-bottom:14px">
    Bitcoin ${btc:,.0f} · {len(rows)} bots · pretend money · updated {updated} UTC
  </div>
  {''.join(cards)}
  <p style="color:#6b7280;font-size:12px;margin-top:16px">
    Each bot started with $10,000 (pretend). Bots make money in up <i>and</i> down markets by
    trading Bitcoin's wiggles. "Worst dip" = biggest temporary drop. Refreshes every minute.</p>
</body></html>"""


@app.get("/", include_in_schema=False)
def index():
    return HTMLResponse(_page())


@app.get("/widget", include_in_schema=False)
def widget():
    return HTMLResponse(_page())
