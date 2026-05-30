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
    ("premium", "Premium", "Sells volatility — earns in calm, loses in big moves (the wheel's spirit)."),
    ("trend", "Trend", "Bets on direction — rides uptrends, dodges downtrends. The 'predict' contrast."),
    ("stack", "Stack", "Accumulation & benchmarks — DCA, 50/50 rebalancing, plain buy & hold."),
    ("convex", "Convex", "Options 'big payoff' bets — crash insurance, gamma scalping, backspreads. Pay a little, win big on a crash or huge move. Simplified models."),
]
_TAB_KEYS = [t[0] for t in TAB_INFO]
TAB_COLORS = {                     # restrained palette for the leaderboard tab badges
    "grid": "#3b82f6", "funding": "#14b8a6", "longvol": "#a78bfa",
    "premium": "#f59e0b", "trend": "#ec4899", "stack": "#64748b", "convex": "#06b6d4",
}
TAB_LABELS = {k: lbl for k, lbl, _ in TAB_INFO}


def _tab_of(v):
    return v.get("tab", v.get("type", "grid"))


def _page(tab: str = "grid") -> str:
    if tab not in _TAB_KEYS:
        tab = "grid"
    data = _load()
    if not data:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                "<h2>BTC Bot Farm</h2><p>The farm isn't running yet. Start it on the Mac:</p>"
                "<pre>caffeinate -s python3.11 grid_farm.py</pre></body>")
    allv = data.get("variants", [])
    rows = sorted([v for v in allv if _tab_of(v) == tab],
                  key=lambda v: v["equity"], reverse=True)
    btc = data.get("btc_price", 0)
    updated = data.get("updated", "")[:16].replace("T", " ")
    tabs = ""
    for key, label, _ in TAB_INFO:
        cnt = sum(1 for v in allv if _tab_of(v) == key)
        on = key == tab
        st = "background:#2563eb;color:#fff" if on else "background:#1c2230;color:#9aa4b2"
        tabs += (f"<a href='/?tab={key}' style='flex:1 1 22%;text-align:center;padding:9px 4px;"
                 f"border-radius:9px;text-decoration:none;font-size:13px;font-weight:600;{st}'>{label} ({cnt})</a>")
    tab_bar = (
        f"<div style='display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 6px'>{tabs}</div>"
        "<a href='/leaderboard' style='display:block;text-align:center;padding:9px 4px;border-radius:9px;"
        "text-decoration:none;font-size:13px;font-weight:600;background:#1c2230;color:#9aa4b2;"
        f"border:1px dashed #2d3850;margin-bottom:10px'>🏆 ROI leaderboard — all {len(allv)}, head-to-head ›</a>"
    )
    intro = next(t[2] for t in TAB_INFO if t[0] == tab)
    # tappable Bitcoin-price banner (sparkline only if 1W data is already cached — never blocks)
    spark = ""
    wk = _btc_history("1W", allow_fetch=False)
    if len(wk) >= 2:
        spark = ("<div style='width:120px;flex:0 0 auto'>"
                 + _btc_chart_svg(wk, "1W", wk[-1][1] >= wk[0][1], w=120, h=42, mini=True) + "</div>")
    btc_banner = (
        "<a href='/btc' style='display:flex;align-items:center;gap:12px;text-decoration:none;"
        "background:#11203a;border:1px solid #1d3a66;border-radius:12px;padding:12px 14px;margin-bottom:10px'>"
        "<div style='flex:1'>"
        "<div style='color:#8b95a5;font-size:12px'>₿ Bitcoin price · tap for full chart</div>"
        f"<div style='font-size:23px;font-weight:800;color:#e6e6e6'>${btc:,.0f} "
        "<span style='font-size:13px;color:#60a5fa;font-weight:600'>1W·1M·1Y·5Y ›</span></div>"
        f"</div>{spark}</a>")
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
    {len(allv)} bots · pretend money · updated {updated} UTC
  </div>
  {btc_banner}
  {tab_bar}
  <div style="color:#8b95a5;font-size:13px;margin-bottom:8px">{intro}</div>
  <a href="/btc" style="text-decoration:none;color:inherit;display:block">
    <div style="font-size:13px;color:#8b95a5;margin-bottom:4px">Each line = one bot's account value ($) over time (all started at ${data.get('paper_capital', 10000):,.0f}) · tap for the Bitcoin price chart ›</div>
    {_overlay_chart(rows, data.get('paper_capital', 10000.0))}
  </a>
  <div style="margin-top:14px">{''.join(cards)}</div>
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


PALETTE =["#22c55e", "#60a5fa", "#f59e0b", "#ef4444", "#a78bfa", "#ec4899", "#14b8a6", "#eab308"]


def _overlay_chart(variants, start):
    """All of a tab's bots' account curves on one chart (shared time + $ axes)."""
    series = []
    for i, v in enumerate(variants):
        rows = _equity_rows(v["slug"])
        if len(rows) >= 2:
            series.append((v["name"], PALETTE[i % len(PALETTE)], rows))
    if not series:
        return ("<div style='color:#6b7280;padding:26px 0;text-align:center'>"
                "Chart fills in as the bots trade (hourly) — check back in a bit.</div>")
    all_ts = [t for _, _, rows in series for t, _ in rows]
    all_eq = [e for _, _, rows in series for _, e in rows] + [start]
    tmin, tmax = min(all_ts), max(all_ts)
    emin, emax = min(all_eq), max(all_eq)
    tspan = (tmax - tmin).total_seconds() or 1.0
    erng = (emax - emin) or 1.0
    w, h = 620, 216
    padL, padR, padT, padB = 52, 12, 18, 34
    fx = lambda t: padL + (t - tmin).total_seconds() / tspan * (w - padL - padR)
    fy = lambda e: padT + (h - padT - padB) * (1 - (e - emin) / erng)
    polys, legend = [], []
    for name, col, rows in series:
        pts = " ".join(f"{fx(t):.1f},{fy(e):.1f}" for t, e in rows)
        polys.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>')
        legend.append(f"<span style='color:{col};font-size:12px;white-space:nowrap'>● {name}</span>")
    base_y = fy(start)
    fmt = "%d %b %H:%M" if tspan < 3 * 86400 else "%d %b %y"
    cy = (padT + h - padB) / 2
    svg = (f'<svg viewBox="0 0 {w} {h}" width="100%" style="background:#0f141c;border-radius:10px">'
           f'<line x1="{padL}" y1="{base_y:.1f}" x2="{w - padR}" y2="{base_y:.1f}" '
           f'stroke="#3a4253" stroke-dasharray="4 4"/>' + "".join(polys) +
           f'<text x="13" y="{cy:.0f}" fill="#8b95a5" font-size="11" text-anchor="middle" '
           f'transform="rotate(-90 13 {cy:.0f})">Account value ($)</text>'
           f'<text x="{padL - 6}" y="{padT + 4}" fill="#6b7280" font-size="11" text-anchor="end">${emax:,.0f}</text>'
           f'<text x="{padL - 6}" y="{h - padB + 3:.0f}" fill="#6b7280" font-size="11" text-anchor="end">${emin:,.0f}</text>'
           f'<text x="{w - padR}" y="{base_y - 4:.1f}" fill="#8b95a5" font-size="10.5" text-anchor="end">start ${start:,.0f}</text>'
           f'<text x="{padL}" y="{h - 17}" fill="#6b7280" font-size="11">{tmin.strftime(fmt)}</text>'
           f'<text x="{w - padR}" y="{h - 17}" fill="#6b7280" font-size="11" text-anchor="end">{tmax.strftime(fmt)}</text>'
           f'<text x="{(padL + w - padR) / 2:.0f}" y="{h - 4}" fill="#8b95a5" font-size="11" '
           f'text-anchor="middle">Time (older → newer)</text></svg>')
    return svg + f"<div style='display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 2px'>{''.join(legend)}</div>"


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


def _svg_chart(rows: list[tuple[datetime, float]], start: float, up: bool) -> str:
    """One bot's account value (vertical, $) over time (horizontal). rows=[(dt, equity)]."""
    if len(rows) < 2:
        return ("<div style='color:#6b7280;padding:28px 0;text-align:center'>"
                "Graph fills in as this bot trades (updates hourly). Check back soon.</div>")
    ys = [e for _, e in rows]
    ts = [t for t, _ in rows]
    w, h = 620, 216
    padL, padR, padT, padB = 52, 14, 18, 34
    lo, hi = min(min(ys), start), max(max(ys), start)
    rng = (hi - lo) or 1.0
    tmin, tmax = ts[0], ts[-1]
    tspan = (tmax - tmin).total_seconds() or 1.0
    fx = lambda t: padL + (t - tmin).total_seconds() / tspan * (w - padL - padR)
    fy = lambda v: padT + (h - padT - padB) * (1 - (v - lo) / rng)
    pts = " ".join(f"{fx(t):.1f},{fy(v):.1f}" for t, v in rows)
    col = "#22c55e" if up else "#ef4444"
    base_y = fy(start)
    fmt = "%d %b %H:%M" if tspan < 3 * 86400 else "%d %b %y"
    cy = (padT + h - padB) / 2
    return f"""<svg viewBox="0 0 {w} {h}" width="100%" style="background:#0f141c;border-radius:10px">
      <line x1="{padL}" y1="{base_y:.1f}" x2="{w - padR}" y2="{base_y:.1f}" stroke="#3a4253" stroke-width="1" stroke-dasharray="4 4"/>
      <polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.5"/>
      <text x="13" y="{cy:.0f}" fill="#8b95a5" font-size="11" text-anchor="middle" transform="rotate(-90 13 {cy:.0f})">Account value ($)</text>
      <text x="{padL - 6}" y="{padT + 4}" fill="#6b7280" font-size="11" text-anchor="end">${hi:,.0f}</text>
      <text x="{padL - 6}" y="{h - padB + 3:.0f}" fill="#6b7280" font-size="11" text-anchor="end">${lo:,.0f}</text>
      <text x="{w - padR}" y="{base_y - 5:.1f}" fill="#8b95a5" font-size="11" text-anchor="end">start ${start:,.0f}</text>
      <text x="{padL}" y="{h - 17}" fill="#6b7280" font-size="11">{tmin.strftime(fmt)}</text>
      <text x="{w - padR}" y="{h - 17}" fill="#6b7280" font-size="11" text-anchor="end">{tmax.strftime(fmt)}</text>
      <text x="{(padL + w - padR) / 2:.0f}" y="{h - 4}" fill="#8b95a5" font-size="11" text-anchor="middle">Time (older → newer)</text>
    </svg>"""


def _bot_page(slug: str) -> str:
    data = _load() or {}
    v = next((x for x in data.get("variants", []) if x.get("slug") == slug), None)
    if v is None:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                f"<p>Bot '{slug}' not found.</p><a href='/' style='color:#60a5fa'>← back</a></body>")
    start = data.get("paper_capital", 10_000.0)
    eq_rows = _equity_rows(slug)
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
    elif t == "shortvol":
        lv = "Leveraged (2×) — can be wiped out in a crash. " if v.get("leverage", 1) > 1 else ""
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> sells volatility (the options-wheel's spirit) — pockets premium, wants calm.</div>"
                 "<div>• <b>Wins:</b> in quiet, range-bound markets.</div>"
                 f"<div>• <b>Loses:</b> in big moves / crashes. {lv}</div>"
                 "<div style='color:#9aa4b2;margin-top:4px'>The exact opposite side of the Long-Vol bot (simplified model).</div>")
    elif t == "tailhedge":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> owns 'crash insurance' (far out-of-the-money put options).</div>"
                 "<div>• <b>Wins:</b> only in a sharp CRASH — and then it pays off HUGE (very convex).</div>"
                 "<div>• <b>Bleeds:</b> a small premium almost every day it doesn't crash — like paying an insurance bill.</div>"
                 "<div>• <b>Ignores:</b> price going up — it only cares about big drops.</div>"
                 "<div style='color:#9aa4b2;margin-top:4px'>Expect it mostly red. The point is the one day everything else "
                 "crashes, THIS is the bot that spikes green. Simplified model.</div>")
    elif t == "gammascalp":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> long volatility, but it actively TRADES — every time price swings it locks in a little profit.</div>"
                 "<div>• <b>Wins:</b> when the market is choppy / moving a lot (up OR down).</div>"
                 "<div>• <b>Bleeds:</b> a small cost when the market sits dead still.</div>"
                 "<div style='color:#9aa4b2;margin-top:4px'>Same engine idea as Long-Vol, but it shows real trades — "
                 "the busy, hands-on cousin. Simplified model.</div>")
    elif t == "backspread":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> a cheap options spread — costs almost nothing to hold.</div>"
                 "<div>• <b>Wins:</b> big on a LARGE move in either direction.</div>"
                 "<div>• <b>Loses:</b> a small, limited amount if price drifts a moderate distance (the 'dead zone').</div>"
                 "<div style='color:#9aa4b2;margin-top:4px'>A cheaper lottery ticket than buying volatility outright. Simplified model.</div>")
    elif t == "trend":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> holds Bitcoin while price is above its moving average, sits in cash below it.</div>"
                 "<div>• <b>Wins:</b> in strong, sustained trends — rides the ups, dodges the downs.</div>"
                 "<div>• <b>Loses:</b> in choppy markets (whipsaws in and out, paying fees).</div>"
                 "<div style='color:#9aa4b2;margin-top:4px'>This one DOES bet on direction — the contrast to the neutral bots.</div>")
    elif t == "rebalance":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> keeps about half in Bitcoin, half in cash; rebalances when it drifts.</div>"
                 "<div>• <b>Effect:</b> mechanically buys low and sells high; smoother ride than holding.</div>")
    elif t == "dca":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> buys a fixed amount of Bitcoin every day — classic dollar-cost averaging.</div>"
                 "<div>• <b>Effect:</b> averages your entry price; steady accumulation, no timing.</div>")
    elif t == "buyhold":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> bought Bitcoin once and holds. No trading at all.</div>"
                 "<div>• <b>Why it's here:</b> the benchmark — every other bot is trying to beat this.</div>")
    elif t == "infinity_grid":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 f"<div>• <b>Trades when price moves about:</b> {v.get('spacing_pct', '?')}% (open-top — no upper cap)</div>"
                 "<div>• <b>Keeps a slice on each sell:</b> ~15% of each lot is held as a long-term 'tail' "
                 "that rides the bull leg up.</div>"
                 "<div>• <b>Safety brake:</b> 45-day moving average — pulls to cash on a confirmed downtrend (slow but patient).</div>"
                 "<div>• <b>Borrowing:</b> none — your own money only.</div>"
                 "<div style='color:#9aa4b2;margin-top:6px'>This is the <b>bull-leg specialist</b>. It's designed to "
                 "outperform the other grids in long uptrends and to give back 30–45% through cycle transitions. "
                 "That's normal — do not panic-restart it on a big dip. Only the 50% emergency halt is a real wipe signal.</div>")
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
    dd = v.get("max_drawdown_pct", 0)
    smooth = f"{v['return_pct'] / dd:.1f}×" if dd > 0.1 else "—"
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
  <div style="font-size:13px;color:#8b95a5;margin-bottom:4px">Account value ($) over time — the line above the dashed start line means profit</div>
  {_svg_chart(eq_rows, start, up)}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:14px 0">{stats}</div>
  <div style="color:#9aa4b2;font-size:12.5px;margin:-2px 0 2px">Smoothness: <b>{smooth}</b>
    <span style="color:#6b7280">return per 1% dip — higher = smoother ride</span></div>
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


# ── ROI leaderboard page (/leaderboard) — all 25 bots ranked, survival-first ──

def _freshness(updated: str) -> tuple[str, str, str]:
    """(status_label, hex_colour, 'Xm ago') for the leaderboard subtitle.

    Matches the widget's 3-state freshness so a single skipped hourly tick
    doesn't look alarming: fresh ≤ 75 min, stale 75-180 min, offline > 180 min.
    """
    try:
        t = datetime.fromisoformat(updated)
        secs = (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return ("unknown", "#6b7280", "unknown")
    if secs < 60:
        ago = "just now"
    elif secs < 3600:
        ago = f"{int(secs / 60)}m ago"
    elif secs < 86400:
        ago = f"{secs / 3600:.1f}h ago"
    else:
        ago = f"{int(secs / 86400)}d ago"
    if secs < 75 * 60:
        return ("live", "#22c55e", ago)
    if secs < 180 * 60:
        return ("recent", "#f59e0b", ago)
    return ("stale", "#ef4444", ago)


def _leaderboard_page() -> str:
    data = _load()
    if not data:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                "<h2>BSF Bot Farm — Leaderboard</h2><p>The farm isn't running yet.</p>"
                "<a href='/' style='color:#60a5fa'>← back</a></body>")
    allv = data.get("variants", [])
    fresh_label, fresh_col, fresh_ago = _freshness(data.get("updated", ""))

    # Survival score = return_pct − max_drawdown_pct (the weekly digest's ranking metric).
    # Rank the medals by survival, independent of the user's chosen column sort.
    enriched = []
    for v in allv:
        ret = float(v.get("return_pct", 0.0))
        dd = float(v.get("max_drawdown_pct", 0.0))
        days = float(v.get("days_running", 0.0))
        ann = ret * (365.0 / days) if days >= 1 else None
        enriched.append({**v, "_ann": ann, "_survival": ret - dd})
    medal_rank = {v["slug"]: i for i, v in enumerate(
        sorted(enriched, key=lambda x: x["_survival"], reverse=True))}
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}

    # Default render: sorted by survival score descending (the survival-first ranking).
    rows = sorted(enriched, key=lambda x: x["_survival"], reverse=True)

    def numfmt(v, suffix="", dash="—"):
        if v is None:
            return f"<span style='color:#6b7280'>{dash}</span>"
        c = "#22c55e" if v >= 0 else "#ef4444"
        sign = "+" if v >= 0 else ""
        return f"<span style='color:{c};font-weight:600'>{sign}{v:.1f}{suffix}</span>"

    body_rows = []
    for i, v in enumerate(rows, 1):
        slug = v["slug"]
        tab = _tab_of(v)
        tab_col = TAB_COLORS.get(tab, "#6b7280")
        tab_lbl = TAB_LABELS.get(tab, tab)
        medal = medals.get(medal_rank[slug], "")
        warn = " ⚠️" if v.get("leverage", 1) > 1 else ""
        ann = v["_ann"]
        surv = v["_survival"]
        ret = float(v["return_pct"])
        dd = float(v["max_drawdown_pct"])
        days = float(v.get("days_running", 0.0))
        trades = int(v.get("trades", 0))
        equity = float(v["equity"])
        # data-sort attrs are numeric so client-side JS can sort without parsing display strings
        body_rows.append(
            f"<tr data-slug='{slug}'>"
            f"<td class='c-rank' data-sort='{i}'>{i}<span class='medal'>{medal}</span></td>"
            f"<td class='c-name'>"
            f"<a href='/bot/{slug}'><span class='dot' style='background:{tab_col}'></span>"
            f"{v['name']}{warn}</a>"
            f"<span class='tabbadge' style='background:{tab_col}22;color:{tab_col};"
            f"border:1px solid {tab_col}66'>{tab_lbl}</span></td>"
            f"<td class='c-state hide-mob' data-sort='{slug}'>{v.get('state', '')}</td>"
            f"<td class='c-days hide-mob' data-sort='{days}'>{days:.1f}d</td>"
            f"<td class='c-ret hide-mob' data-sort='{ret}'>{numfmt(ret, '%')}</td>"
            f"<td class='c-ann' data-sort='{ann if ann is not None else 0}' "
            f"data-null='{1 if ann is None else 0}'>{numfmt(ann, '%')}</td>"
            f"<td class='c-dd' data-sort='{-dd}'>"
            f"<span style='color:#ef4444;font-weight:600'>−{dd:.1f}%</span></td>"
            f"<td class='c-surv' data-sort='{surv}'>{numfmt(surv, '%')}</td>"
            f"<td class='c-trades hide-mob' data-sort='{trades}'>{trades}</td>"
            f"<td class='c-eq' data-sort='{equity}'>${equity:,.0f}</td>"
            f"</tr>"
        )

    # Columns: (label, css class, default direction, optional tooltip).
    # `hide-mob` ⇒ column is collapsed on ≤640px until "Show all columns" toggle.
    # Annualised is the meaningful cross-bot comparison (days_running varies wildly), so it
    # takes Return's mobile-default slot; raw Return moves behind the toggle.
    headers = [
        ("#", "c-rank", "asc", ""),
        ("Bot", "c-name", "asc", ""),
        ("State", "c-state hide-mob", "asc", ""),
        ("Days", "c-days hide-mob", "desc", ""),
        ("Return", "c-ret hide-mob", "desc", ""),
        ("Annualised", "c-ann", "desc",
         "Annualised return — raw return × (365/days). "
         "Shown as '—' for bots with <1 day of data."),
        ("Drawdown", "c-dd", "desc", ""),
        ("Survival", "c-surv", "desc", ""),
        ("Trades", "c-trades hide-mob", "desc", ""),
        ("Equity", "c-eq", "desc", ""),
    ]

    def _th(lbl, cls, d, tip):
        attrs = f"class='{cls}' data-default-dir='{d}'"
        if tip:
            safe = tip.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
            attrs += f' title="{safe}" aria-label="{safe}"'
        return f"<th {attrs}>{lbl}<span class='caret'></span></th>"

    head_cells = "".join(_th(*h) for h in headers)

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60>
<title>Leaderboard — BSF Bot Farm</title>
<style>
  body{{background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:760px;margin:auto}}
  a{{color:inherit;text-decoration:none}}
  h2{{margin:0 0 2px}}
  .sub{{color:#8b95a5;font-size:13px;margin-bottom:10px}}
  .nav{{color:#60a5fa;font-size:14px}}
  .toolbar{{display:flex;justify-content:space-between;align-items:center;margin:10px 0 8px;gap:8px}}
  .toolbar button{{background:#1c2230;color:#9aa4b2;border:1px solid #2d3850;border-radius:8px;
    padding:7px 11px;font-size:12.5px;font-weight:600;cursor:pointer}}
  .toolbar button.on{{background:#2563eb;color:#fff;border-color:#2563eb}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  thead th{{position:sticky;top:0;background:#11161f;color:#9aa4b2;font-weight:600;
    font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;
    padding:8px 4px;text-align:right;cursor:pointer;user-select:none;
    border-bottom:1px solid #1f2733;white-space:nowrap}}
  thead th:first-child,thead th.c-name{{text-align:left}}
  thead th .caret{{display:inline-block;width:8px;margin-left:3px;color:#3b82f6}}
  thead th.active{{color:#e6e6e6}}
  tbody td{{padding:8px 4px;text-align:right;border-bottom:1px solid #151a23;white-space:nowrap}}
  tbody td.c-rank,tbody td.c-name{{text-align:left}}
  tbody tr:hover{{background:#11161f}}
  td.c-rank{{color:#8b95a5;width:34px;font-variant-numeric:tabular-nums}}
  td.c-rank .medal{{margin-left:2px}}
  td.c-name a{{color:#e6e6e6;font-weight:600}}
  td.c-name .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}}
  td.c-name .tabbadge{{display:inline-block;font-size:10px;font-weight:600;padding:1px 6px;
    border-radius:7px;margin-left:6px;vertical-align:middle;text-transform:uppercase;letter-spacing:.3px}}
  td.c-state{{color:#9aa4b2;font-size:12px;max-width:140px;overflow:hidden;text-overflow:ellipsis}}
  td.c-eq{{font-weight:600;font-variant-numeric:tabular-nums}}
  .legend{{color:#6b7280;font-size:12px;margin-top:14px;line-height:1.55}}
  @media (max-width:640px){{
    body{{padding:14px}}
    .hide-mob{{display:none}}
    thead th,tbody td{{padding:7px 2px;font-size:12.5px}}
    td.c-name a{{font-size:13px}}
    td.c-name .tabbadge{{display:none}}     /* colored dot already encodes the tab on mobile */
  }}
  body.show-all .hide-mob{{display:table-cell}}
</style>
</head>
<body>
  <a class=nav href="/">← back to dashboard</a>
  <h2 style="margin:10px 0 2px">🏆 BSF Bot Farm — Leaderboard</h2>
  <div class=sub>Survival-first scorecard · sortable · <span style="color:{fresh_col};font-weight:600">●</span> Updated {fresh_ago}</div>
  <div class=toolbar>
    <div style="color:#8b95a5;font-size:12.5px">{len(rows)} bots · tap a header to sort · tap a bot for its graph</div>
    <button id=allcols>Show all columns</button>
  </div>
  <table id=lb>
    <thead><tr>{head_cells}</tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
  <p class=legend>
    <b>Survival score</b> = Return % − Drawdown %. The weekly digest ranks by this — a bot that earned
    +5% with a −20% dip scores worse than one that earned +3% with a −2% dip. Default sort.
    <br><b>Annualised</b> = recent pace projected forward (Return × 365 ÷ days). Shown as "—" until a bot has run a full day.
    <br>Pretend money on real Bitcoin prices. Refreshes every minute.
  </p>
<script>
(function(){{
  var tbl = document.getElementById('lb');
  var tbody = tbl.tBodies[0];
  var ths = tbl.tHead.rows[0].cells;
  var state = {{col: -1, dir: 'desc'}};

  function sortBy(idx) {{
    var th = ths[idx];
    var defaultDir = th.getAttribute('data-default-dir') || 'desc';
    var dir = (state.col === idx) ? (state.dir === 'asc' ? 'desc' : 'asc') : defaultDir;
    state.col = idx; state.dir = dir;
    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function(a, b) {{
      // data-null='1' cells (e.g. Annualised '—' for <1 day bots) always sort last,
      // regardless of direction — by returning unsigned cmp before the dir flip.
      var aNull = a.cells[idx].getAttribute('data-null') === '1';
      var bNull = b.cells[idx].getAttribute('data-null') === '1';
      if (aNull && !bNull) return 1;
      if (!aNull && bNull) return -1;
      var av = a.cells[idx].getAttribute('data-sort');
      var bv = b.cells[idx].getAttribute('data-sort');
      var an = parseFloat(av), bn = parseFloat(bv);
      var cmp;
      if (!isNaN(an) && !isNaN(bn)) cmp = an - bn;
      else cmp = (av || '').localeCompare(bv || '');
      return dir === 'asc' ? cmp : -cmp;
    }});
    rows.forEach(function(r, i) {{
      tbody.appendChild(r);
      r.cells[0].firstChild.nodeValue = (i + 1);
    }});
    for (var k = 0; k < ths.length; k++) {{
      ths[k].classList.remove('active');
      ths[k].querySelector('.caret').textContent = '';
    }}
    th.classList.add('active');
    th.querySelector('.caret').textContent = dir === 'asc' ? '▲' : '▼';
  }}

  for (var i = 0; i < ths.length; i++) (function(i){{
    ths[i].addEventListener('click', function(){{ sortBy(i); }});
  }})(i);

  // Mark the survival column as the default-sorted one (already server-sorted desc).
  var survIdx = -1;
  for (var k = 0; k < ths.length; k++) if (ths[k].classList.contains('c-surv')) survIdx = k;
  if (survIdx >= 0) {{
    state.col = survIdx; state.dir = 'desc';
    ths[survIdx].classList.add('active');
    ths[survIdx].querySelector('.caret').textContent = '▼';
  }}

  var btn = document.getElementById('allcols');
  function setShowAll(on) {{
    document.body.classList.toggle('show-all', on);
    btn.classList.toggle('on', on);
    btn.textContent = on ? 'Compact view' : 'Show all columns';
  }}
  try {{ setShowAll(localStorage.getItem('lb-show-all') === '1'); }} catch (e) {{}}
  btn.addEventListener('click', function(){{
    var on = !document.body.classList.contains('show-all');
    setShowAll(on);
    try {{ localStorage.setItem('lb-show-all', on ? '1' : '0'); }} catch (e) {{}}
  }});
}})();
</script>
</body></html>"""


# ── Bitcoin price chart page (/btc) — live candles from Deribit, several ranges ─

# range key → (deribit resolution [valid: 1/3/5/15/30/60/120/180/360/720/1D],
#              lookback seconds, cache TTL seconds, headline label)
BTC_RANGES = [
    ("1D",  "5",   1 * 86_400,        120, "Past 24 hours"),
    ("1W",  "60",  7 * 86_400,        300, "Past week"),
    ("1M",  "360", 30 * 86_400,       600, "Past month"),
    ("1Y",  "1D",  366 * 86_400,     1800, "Past year"),
    ("5Y",  "1D",  5 * 366 * 86_400, 3600, "Past 5 years"),
    ("Max", "1D",  9 * 366 * 86_400, 3600, "Since 2019"),
]
_BTC_RANGE_KEYS = [r[0] for r in BTC_RANGES]
_btc_cache: dict[str, tuple[float, list]] = {}   # range → (fetched_at, [(ts_ms, close), ...])


def _btc_history(range_key: str, allow_fetch: bool = True) -> list[tuple[int, float]]:
    """(timestamp_ms, close) candles for a range. In-process cached, with stale fallback.

    allow_fetch=False only ever returns already-cached data (never blocks on the
    network) — used by the main dashboard so it stays fully decoupled from Deribit.
    """
    spec = next((r for r in BTC_RANGES if r[0] == range_key), None)
    if spec is None:
        return []
    _, res, secs, ttl, _ = spec
    import time as _t
    now = _t.time()
    cached = _btc_cache.get(range_key)
    if cached and (now - cached[0] < ttl or not allow_fetch):
        return cached[1]
    if not allow_fetch:
        return cached[1] if cached else []
    try:
        from deribit_client import DeribitPublicREST
        rest = DeribitPublicREST()
        end = int(now)
        candles = rest.get_tradingview_chart_data("BTC-PERPETUAL", res, end - secs, end)
        pts = [(int(c["timestamp"]), float(c["close"])) for c in candles
               if c.get("close") and c.get("timestamp")]
        if pts:
            _btc_cache[range_key] = (now, pts)
            return pts
    except Exception:
        pass
    return cached[1] if cached else []   # stale data beats no data


def _btc_chart_svg(pts: list[tuple[int, float]], range_key: str, up: bool,
                   w: float = 680.0, h: float = 260.0, mini: bool = False) -> str:
    if len(pts) < 2:
        return ("<div style='color:#6b7280;padding:40px 0;text-align:center'>"
                "Couldn't load the price right now — try again in a moment.</div>")
    closes = [c for _, c in pts]
    hi, lo = max(closes), min(closes)
    hi_i = max(range(len(pts)), key=lambda i: pts[i][1])
    lo_i = min(range(len(pts)), key=lambda i: pts[i][1])
    n = len(pts)
    step = max(1, n // 600)                      # thin the drawn line on long ranges
    dpts = pts[::step]
    if dpts[-1][0] != pts[-1][0]:
        dpts.append(pts[-1])
    padL, padR = 6, 6
    padT, padB = (4, 4) if mini else (14, 22)
    tmin, tmax = pts[0][0], pts[-1][0]
    tspan = (tmax - tmin) or 1
    span = (hi - lo) or 1.0
    loP, hiP = lo - span * 0.07, hi + span * 0.07
    rng = (hiP - loP) or 1.0
    fx = lambda t: padL + (t - tmin) / tspan * (w - padL - padR)
    fy = lambda v: padT + (h - padT - padB) * (1 - (v - loP) / rng)
    line = " ".join(f"{fx(t):.1f},{fy(v):.1f}" for t, v in dpts)
    area = (f"{fx(dpts[0][0]):.1f},{h - padB:.1f} " + line +
            f" {fx(dpts[-1][0]):.1f},{h - padB:.1f}")
    col = "#22c55e" if up else "#ef4444"
    fill = "rgba(34,197,94,0.15)" if up else "rgba(239,68,68,0.15)"
    parts = [f'<svg viewBox="0 0 {w:.0f} {h:.0f}" width="100%" '
             f'style="background:#0f141c;border-radius:12px;display:block">',
             f'<polygon points="{area}" fill="{fill}" stroke="none"/>']
    if not mini and len(dpts) > 20:              # dashed moving-average (trend) line
        win = max(3, len(dpts) // 12)
        ma = []
        for i, (t, _v) in enumerate(dpts):
            seg = dpts[max(0, i - win + 1):i + 1]
            ma.append(f"{fx(t):.1f},{fy(sum(x for _, x in seg) / len(seg)):.1f}")
        parts.append(f'<polyline points="{" ".join(ma)}" fill="none" stroke="#8b95a5" '
                     f'stroke-width="1.3" stroke-dasharray="5 4" opacity="0.7"/>')
    parts.append(f'<polyline points="{line}" fill="none" stroke="{col}" '
                 f'stroke-width="{1.6 if mini else 2.2}"/>')
    if not mini:
        from datetime import datetime as _dt, timezone as _tz
        def dl(ms):
            d = _dt.fromtimestamp(ms / 1000, tz=_tz.utc)
            return d.strftime("%H:%M %d %b") if range_key in ("1D", "1W") else d.strftime("%d %b %y")
        # hi / lo markers
        hx, hy = fx(pts[hi_i][0]), fy(pts[hi_i][1])
        lx, ly = fx(pts[lo_i][0]), fy(pts[lo_i][1])
        parts.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="3" fill="#22c55e"/>'
                     f'<text x="{hx:.1f}" y="{hy - 6:.1f}" fill="#9aa4b2" font-size="10.5" '
                     f'text-anchor="middle">${pts[hi_i][1]:,.0f}</text>')
        parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="#ef4444"/>'
                     f'<text x="{lx:.1f}" y="{ly + 14:.1f}" fill="#9aa4b2" font-size="10.5" '
                     f'text-anchor="middle">${pts[lo_i][1]:,.0f}</text>')
        # last-price dot
        ex, ey = fx(pts[-1][0]), fy(pts[-1][1])
        parts.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3.5" fill="{col}"/>')
        # date axis
        parts.append(f'<text x="{padL}" y="{h - 6:.0f}" fill="#6b7280" font-size="10.5">{dl(tmin)}</text>'
                     f'<text x="{w - padR}" y="{h - 6:.0f}" fill="#6b7280" font-size="10.5" '
                     f'text-anchor="end">{dl(tmax)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _btc_page(range_key: str = "1M") -> str:
    if range_key not in _BTC_RANGE_KEYS:
        range_key = "1M"
    label = next(r[4] for r in BTC_RANGES if r[0] == range_key)
    pts = _btc_history(range_key)
    data = _load() or {}
    cur = (pts[-1][1] if pts else 0) or data.get("btc_price", 0)
    if pts:
        first, last = pts[0][1], pts[-1][1]
        chg = (last / first - 1) * 100 if first else 0.0
        chg_usd = last - first
        hi = max(c for _, c in pts)
        lo = min(c for _, c in pts)
        from_hi = (last / hi - 1) * 100 if hi else 0.0
    else:
        first = last = chg = chg_usd = hi = lo = from_hi = 0.0
    up = chg >= 0
    col = "#22c55e" if up else "#ef4444"
    sign = "+" if up else ""
    d1 = _btc_history("1D")
    chg24 = (d1[-1][1] / d1[0][1] - 1) * 100 if len(d1) >= 2 else None

    btns = ""
    for k in _BTC_RANGE_KEYS:
        on = k == range_key
        st = "background:#2563eb;color:#fff" if on else "background:#1c2230;color:#9aa4b2"
        btns += (f"<a href='/btc?range={k}' style='flex:1;text-align:center;padding:10px 4px;"
                 f"border-radius:9px;text-decoration:none;font-size:14px;font-weight:700;{st}'>{k}</a>")

    def stat(lbl, val, c="#e6e6e6"):
        return ("<div style='background:#151a23;border-radius:10px;padding:10px 12px'>"
                f"<div style='color:#8b95a5;font-size:12px'>{lbl}</div>"
                f"<div style='font-size:17px;font-weight:600;color:{c}'>{val}</div></div>")

    c24 = "#22c55e" if (chg24 or 0) >= 0 else "#ef4444"
    stats = "".join([
        stat(f"{range_key} high", f"${hi:,.0f}"),
        stat(f"{range_key} low", f"${lo:,.0f}"),
        stat("24-hour change", f"{'+' if (chg24 or 0) >= 0 else ''}{chg24:.1f}%" if chg24 is not None else "—", c24),
        stat(f"Down from {range_key} high", f"{from_hi:.1f}%" if from_hi < -0.05 else "at the high", col),
    ])
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content="60;url=/btc?range={range_key}">
<title>Bitcoin price — BTC Bot Farm</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:680px;margin:auto">
  <a href="/" style="color:#60a5fa;text-decoration:none;font-size:14px">← back to bots</a>
  <h2 style="margin:10px 0 0">₿ Bitcoin price</h2>
  <div style="display:flex;align-items:baseline;gap:12px;margin:4px 0 2px">
    <span style="font-size:34px;font-weight:800">${cur:,.0f}</span>
    <span style="color:{col};font-size:17px;font-weight:700">{sign}{chg:.1f}%</span>
  </div>
  <div style="color:#8b95a5;font-size:13px;margin-bottom:10px">{label} · {sign}${chg_usd:,.0f}</div>
  <div style="display:flex;gap:6px;margin-bottom:12px">{btns}</div>
  {_btc_chart_svg(pts, range_key, up)}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px">{stats}</div>
  <p style="color:#6b7280;font-size:12px;margin-top:16px">
    Live Bitcoin price from Deribit. Green line = up over the period, red = down.
    The dashed line is the trend (moving average). Dots mark the period high & low.
    Refreshes every minute.</p>
</body></html>"""


@app.get("/btc", include_in_schema=False)
def btc_chart(range: str = "1M"):
    return HTMLResponse(_btc_page(range))


@app.get("/", include_in_schema=False)
def index(tab: str = "grid"):
    return HTMLResponse(_page(tab))


@app.get("/widget", include_in_schema=False)
def widget():
    return HTMLResponse(_page())


@app.get("/bot/{slug}", include_in_schema=False)
def bot_detail(slug: str):
    return HTMLResponse(_bot_page(slug))


@app.get("/leaderboard", include_in_schema=False)
def leaderboard():
    return HTMLResponse(_leaderboard_page())
