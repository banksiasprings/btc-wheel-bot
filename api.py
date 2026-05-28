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

import csv
import json
import os
from datetime import datetime, timedelta, timezone
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

TAB_INFO = [
    ("grid", "Grid", "Buy-low / sell-high on Bitcoin's wiggles — no direction bet."),
    ("funding", "Funding", "Market-neutral — earns the funding fee, almost no price risk."),
    ("longvol", "Long-Vol", "Profits from BIG moves; wins when the grid struggles. Simplified model."),
]


def _page(tab: str = "grid") -> str:
    if tab not in ("grid", "funding", "longvol"):
        tab = "grid"
    data = _load()
    if not data:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                "<h2>BTC Bot Farm</h2><p>The farm isn't running yet. Start it on the Mac:</p>"
                "<pre>caffeinate -s python3.11 grid_farm.py</pre></body>")
    allv = data.get("variants", [])
    rows = sorted([v for v in allv if v.get("type", "grid") == tab],
                  key=lambda v: v["equity"], reverse=True)
    btc = data.get("btc_price", 0)
    updated = data.get("updated", "")[:16].replace("T", " ")
    tabs = ""
    for key, label, _ in TAB_INFO:
        cnt = sum(1 for v in allv if v.get("type", "grid") == key)
        on = key == tab
        st = "background:#2563eb;color:#fff" if on else "background:#1c2230;color:#9aa4b2"
        tabs += (f"<a href='/?tab={key}' style='flex:1;text-align:center;padding:9px 4px;"
                 f"border-radius:9px;text-decoration:none;font-size:13.5px;font-weight:600;{st}'>{label} ({cnt})</a>")
    tab_bar = f"<div style='display:flex;gap:7px;margin:8px 0 10px'>{tabs}</div>"
    intro = next(t[2] for t in TAB_INFO if t[0] == tab)
    cards = []
    for i, v in enumerate(rows, 1):
        up = v["profit"] >= 0
        col = "#22c55e" if up else "#ef4444"
        sign = "+" if up else ""
        warn = " ⚠️" if v.get("leverage", 1) > 1 else ""
        a = _annualised(v["slug"])
        cards.append(f"""
        <a href="/bot/{v['slug']}" style="text-decoration:none;color:inherit;display:block">
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
          <div style="font-size:12.5px;color:#9aa4b2;margin-top:7px">
            Annualised pace · day {_ann_span(a['daily'])} · week {_ann_span(a['weekly'])} · month {_ann_span(a['monthly'])}
          </div>
          <div style="display:flex;justify-content:space-between;color:#6b7280;font-size:12px;margin-top:6px">
            <span>{v['state']} · min ${v.get('min_capital', 0):,} to run</span><span>see graph ›</span>
          </div>
        </div></a>""")
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content="60;url=/?tab={tab}">
<title>BTC Bot Farm</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:680px;margin:auto">
  <h2 style="margin:0 0 2px">📈 BTC Bot Farm</h2>
  <div style="color:#8b95a5;font-size:14px;margin-bottom:8px">
    Bitcoin ${btc:,.0f} · {len(allv)} bots · pretend money · updated {updated} UTC
  </div>
  {tab_bar}
  <div style="color:#8b95a5;font-size:13px;margin-bottom:10px">{intro}</div>
  {''.join(cards)}
  <p style="color:#6b7280;font-size:12px;margin-top:16px">
    Each bot started with $10,000 (pretend). "Worst dip" = biggest temporary drop.
    Tap a bot for its graph. Refreshes every minute.</p>
</body></html>"""


def _equity_rows(slug: str) -> list[tuple[datetime, float]]:
    """A variant's (timestamp, equity) history from grid_farm/<slug>/equity.csv."""
    path = BASE_DIR / "grid_farm" / slug / "equity.csv"
    rows: list[tuple[datetime, float]] = []
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    rows.append((datetime.fromisoformat(r["timestamp"]), float(r["equity"])))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


def _equity_series(slug: str) -> list[float]:
    return [e for _, e in _equity_rows(slug)][-500:]   # cap points for a clean chart


def _annualised(slug: str) -> dict:
    """Annualised run-rate ROI from the last day / week / month of equity history.

    Linear projection: period_return × (365 / elapsed_days). Returns None per
    window until there's enough history (short windows are pure noise otherwise).
    """
    rows = _equity_rows(slug)
    out = {"daily": None, "weekly": None, "monthly": None}
    if len(rows) < 2:
        return out
    now_t, now_e = rows[-1]

    def ann(window_days: float, min_hours: float):
        cutoff = now_t - timedelta(days=window_days)
        base = None
        for t, e in rows:
            if t <= cutoff:
                base = (t, e)
            else:
                break
        if base is None:
            base = rows[0]           # less than `window_days` of history → use earliest
        bt, be = base
        elapsed = (now_t - bt).total_seconds() / 86400.0
        if be <= 0 or elapsed * 24 < min_hours:
            return None
        return (now_e / be - 1) * (365.0 / elapsed) * 100

    out["daily"] = ann(1, 2)
    out["weekly"] = ann(7, 12)
    out["monthly"] = ann(30, 48)
    return out


def _ann_span(v) -> str:
    if v is None:
        return "<span style='color:#6b7280'>—</span>"
    c = "#22c55e" if v >= 0 else "#ef4444"
    return f"<span style='color:{c};font-weight:600'>{v:+,.0f}%</span>"


def _svg_chart(ys: list[float], start: float, up: bool) -> str:
    if len(ys) < 2:
        return ("<div style='color:#6b7280;padding:28px 0;text-align:center'>"
                "Graph fills in as this bot trades (updates hourly). Check back soon.</div>")
    w, h, pad = 620, 180, 12
    lo, hi = min(min(ys), start), max(max(ys), start)
    rng = (hi - lo) or 1.0
    n = len(ys)
    fx = lambda i: pad + i * (w - 2 * pad) / (n - 1)
    fy = lambda v: pad + (h - 2 * pad) * (1 - (v - lo) / rng)
    pts = " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in enumerate(ys))
    col = "#22c55e" if up else "#ef4444"
    base_y = fy(start)
    return f"""<svg viewBox="0 0 {w} {h}" width="100%" style="background:#0f141c;border-radius:10px">
      <line x1="{pad}" y1="{base_y:.1f}" x2="{w - pad}" y2="{base_y:.1f}" stroke="#3a4253" stroke-width="1" stroke-dasharray="4 4"/>
      <polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.5"/>
      <text x="{pad}" y="14" fill="#6b7280" font-size="11">${hi:,.0f}</text>
      <text x="{pad}" y="{h - 5}" fill="#6b7280" font-size="11">${lo:,.0f}</text>
      <text x="{w - pad}" y="{base_y - 5:.1f}" fill="#6b7280" font-size="11" text-anchor="end">start ${start:,.0f}</text>
    </svg>"""


def _bot_page(slug: str) -> str:
    data = _load() or {}
    v = next((x for x in data.get("variants", []) if x.get("slug") == slug), None)
    if v is None:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                f"<p>Bot '{slug}' not found.</p><a href='/' style='color:#60a5fa'>← back</a></body>")
    start = data.get("paper_capital", 10_000.0)
    ys = _equity_series(slug)
    up = v["profit"] >= 0
    col = "#22c55e" if up else "#ef4444"
    sign = "+" if up else ""
    t = v.get("type", "grid")
    brake = ("ON — steps aside (goes to cash) in a sustained downturn"
             if v.get("trend_stop") else "OFF — always trading, even in a crash")
    lev = ("none — your own money only (can't be wiped out)" if v.get("leverage", 1) == 1
           else f"{v['leverage']:.0f}× borrowed — amplifies gains AND losses; can be wiped to $0")
    warn = ("<div style='background:#3a1212;border:1px solid #ef4444;border-radius:10px;padding:10px 12px;"
            "margin:10px 0;font-size:13px;color:#fca5a5'>⚠️ The 'for kicks' leveraged bot — it can "
            "multiply gains, but a sharp crash can wipe it to $0. Not for real money.</div>"
            if (t == "grid" and v.get("leverage", 1) > 1) else "")
    if t == "funding":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> holds Bitcoin + a matching short, so price moves cancel out.</div>"
                 "<div>• <b>Earns:</b> the funding fee traders pay each hour (positive most of the time).</div>"
                 "<div>• <b>Risk:</b> tiny — no price bet; only dips if funding turns negative for a stretch.</div>")
    elif t == "longvol":
        extra = "Double-sized (2×). " if v.get("leverage", 1) > 1 else ""
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> a 'long volatility' bet — profits when Bitcoin moves MORE than priced for.</div>"
                 f"<div>• <b>Wins:</b> in sharp crashes & violent swings, when the grid bots struggle. {extra}</div>"
                 "<div>• <b>Bleeds:</b> slowly in calm, quiet markets.</div>"
                 "<div style='color:#9aa4b2;margin-top:4px'>Note: a simplified model, not a full options simulation.</div>")
    else:
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 f"<div>• <b>Trades when price moves about:</b> {v.get('spacing_pct', '?')}%</div>"
                 f"<div>• <b>Safety brake:</b> {brake}</div>"
                 f"<div>• <b>Borrowing:</b> {lev}</div>")

    def stat(label, value, c="#e6e6e6"):
        return (f"<div style='background:#151a23;border-radius:10px;padding:10px 12px'>"
                f"<div style='color:#8b95a5;font-size:12px'>{label}</div>"
                f"<div style='font-size:17px;font-weight:600;color:{c}'>{value}</div></div>")

    stats = "".join([
        stat("Account now", f"${v['equity']:,.0f}"),
        stat("Profit", f"{sign}${v['profit']:,.0f} ({sign}{v['return_pct']:.1f}%)", col),
        stat("Worst dip", f"−{v['max_drawdown_pct']:.1f}%"),
        stat("Trades", f"{v.get('trades', 0)}"),
    ])
    ann = _annualised(slug)

    def acell(label, val):
        return ("<div style='background:#151a23;border-radius:10px;padding:10px 8px;text-align:center'>"
                f"<div style='color:#8b95a5;font-size:11px'>{label}</div>"
                f"<div style='font-size:16px;margin-top:2px'>{_ann_span(val)}</div></div>")

    ann_block = (
        "<div style='font-size:13px;color:#8b95a5;margin:6px 0 4px'>Annualised return — at recent pace</div>"
        "<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px'>"
        f"{acell('Daily pace', ann['daily'])}{acell('Weekly pace', ann['weekly'])}{acell('Monthly pace', ann['monthly'])}"
        "</div>"
        "<div style='color:#6b7280;font-size:11.5px;margin-top:6px'>“At pace” = if the last day / "
        "week / month repeated all year. Shorter windows swing a lot, especially early on.</div>"
    )
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60>
<title>{v['name']} — BTC Grid Farm</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:680px;margin:auto">
  <a href="/" style="color:#60a5fa;text-decoration:none;font-size:14px">← all bots</a>
  <h2 style="margin:10px 0 2px">{v['name']}</h2>
  <div style="color:#8b95a5;font-size:14px;margin-bottom:12px">{v['style']}</div>
  {warn}
  <div style="font-size:13px;color:#8b95a5;margin-bottom:4px">Account value over time</div>
  {_svg_chart(ys, start, up)}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:14px 0">{stats}</div>
  {ann_block}
  <div style="background:#11203a;border:1px solid #1d3a66;border-radius:10px;padding:11px 14px;margin-top:14px;font-size:14px">
    💵 <b>Minimum to run live: ~${v.get('min_capital', 0):,}</b>
    <div style="color:#8b95a5;font-size:12px;margin-top:3px">The smallest real stake where every order
      still clears the exchange's minimum size. Approximate — we'll confirm exact figures at go-live.</div>
  </div>
  <div style="background:#151a23;border-radius:10px;padding:12px 14px;font-size:14px;line-height:1.7;margin-top:14px">
    <div style="color:#8b95a5;font-size:12px;margin-bottom:4px">HOW THIS BOT WORKS</div>
    {works}
  </div>
  <p style="color:#6b7280;font-size:12px;margin-top:14px">Pretend money on real Bitcoin prices.
    The dashed line is the $10,000 starting point — the line above it means profit. Refreshes every minute.</p>
</body></html>"""


@app.get("/", include_in_schema=False)
def index(tab: str = "grid"):
    return HTMLResponse(_page(tab))


@app.get("/widget", include_in_schema=False)
def widget():
    return HTMLResponse(_page())


@app.get("/bot/{slug}", include_in_schema=False)
def bot_detail(slug: str):
    return HTMLResponse(_bot_page(slug))
