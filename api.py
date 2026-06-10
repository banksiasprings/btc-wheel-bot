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

import steven_portfolio as sp

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
    # Heartbeat: advance Steven's portfolio NAV (idempotent per farm tick). The
    # widget polls this regularly, so it keeps the tournament curve alive even when
    # nobody has the dashboard open. Side-effect only — never alters this response.
    try:
        _tick_steven()
    except Exception:
        pass
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
        tabs += (f"<a href='/farm?tab={key}' style='flex:1 1 22%;text-align:center;padding:9px 4px;"
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
<meta http-equiv=refresh content="60;url=/farm?tab={tab}">
<title>BTC Farm — all bots</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:680px;margin:auto">
  <a href="/" style="color:#60a5fa;text-decoration:none;font-size:14px">← Home (Freyr + survivors)</a>
  <h2 style="margin:8px 0 2px">📈 BTC Farm — all {len(allv)} bots</h2>
  <div style="color:#8b95a5;font-size:14px;margin-bottom:8px">
    pretend money · updated {updated} UTC
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


def _full_rows(slug: str) -> list[dict]:
    """Full (timestamp, btc_price, equity, btc_held) history — used by the
    comparison chart to mark BUY/SELL events alongside BTC price + bot equity."""
    path = BASE_DIR / "grid_farm" / slug / "equity.csv"
    rows: list[dict] = []
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    rows.append({
                        "ts": datetime.fromisoformat(r["timestamp"]),
                        "btc": float(r["btc_price"]),
                        "equity": float(r["equity"]),
                        "btc_held": float(r["btc_held"]),
                    })
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


def _detect_trades(rows: list[dict]) -> list[dict]:
    """Detect BUY/SELL events by looking for btc_held changes between rows."""
    trades: list[dict] = []
    prev = None
    for r in rows:
        if prev is not None and abs(r["btc_held"] - prev["btc_held"]) > 1e-7:
            delta = r["btc_held"] - prev["btc_held"]
            trades.append({
                "ts": r["ts"],
                "btc": r["btc"],
                "equity": r["equity"],
                "side": "BUY" if delta > 0 else "SELL",
                "qty": abs(delta),
            })
        prev = r
    return trades


def _attribute_trades(trades: list[dict], current_btc: float) -> list[dict]:
    """Walk through trades and compute per-trade profit/loss attribution.

    Each SELL is matched FIFO against the oldest open BUY at the same qty (with
    a small tolerance) to compute realized P&L = (sell_price - entry_price) × qty.
    BUYs that haven't been closed yet get a mark-to-market P&L at `current_btc`.

    Adds to each trade dict:
      - `notional`: USD value at the trade (price × qty)
      - For BUY:  `entry_price` (= self), `unrealized_pnl` at current_btc
                  `unrealized_pct` vs entry
      - For SELL: `entry_price` of the lot matched (BUY price), `realized_pnl`,
                  `realized_pct` (return on the matched-lot capital), `hold_hours`

    FIFO matching: if a SELL doesn't perfectly match the oldest BUY's qty
    (e.g. partial closes), we proportionally consume the BUY's qty.
    """
    open_lots: list[dict] = []   # list of {entry_price, qty_remaining, ts}
    out: list[dict] = []
    for t in trades:
        notional = t["btc"] * t["qty"]
        new_t = dict(t)
        new_t["notional"] = notional
        if t["side"] == "BUY":
            open_lots.append({
                "entry_price": t["btc"],
                "qty_remaining": t["qty"],
                "ts": t["ts"],
            })
            new_t["entry_price"] = t["btc"]
            # Unrealized at current price
            mtm = (current_btc - t["btc"]) * t["qty"]
            new_t["unrealized_pnl"] = mtm
            new_t["unrealized_pct"] = (current_btc / t["btc"] - 1) * 100
            new_t["status"] = "open"   # might still be in book at end-of-window
        else:  # SELL
            # FIFO match against open lots
            qty_to_sell = t["qty"]
            total_realized = 0.0
            matched_entry_prices: list[tuple[float, float]] = []  # (entry, qty_consumed)
            earliest_match_ts = None
            while qty_to_sell > 1e-9 and open_lots:
                lot = open_lots[0]
                if earliest_match_ts is None:
                    earliest_match_ts = lot["ts"]
                consume = min(qty_to_sell, lot["qty_remaining"])
                pnl = (t["btc"] - lot["entry_price"]) * consume
                total_realized += pnl
                matched_entry_prices.append((lot["entry_price"], consume))
                lot["qty_remaining"] -= consume
                qty_to_sell -= consume
                if lot["qty_remaining"] < 1e-9:
                    open_lots.pop(0)
            # Weighted-average entry price across matched lots
            if matched_entry_prices:
                tot = sum(q for _, q in matched_entry_prices) or 1.0
                weighted_entry = sum(p * q for p, q in matched_entry_prices) / tot
            else:
                weighted_entry = t["btc"]  # safety fallback
            new_t["entry_price"] = weighted_entry
            new_t["realized_pnl"] = total_realized
            new_t["realized_pct"] = (
                (t["btc"] / weighted_entry - 1) * 100 if weighted_entry > 0 else 0.0
            )
            if earliest_match_ts is not None:
                hold_secs = (t["ts"] - earliest_match_ts).total_seconds()
                new_t["hold_hours"] = hold_secs / 3600.0
            else:
                new_t["hold_hours"] = 0.0
            new_t["status"] = "closed"
        out.append(new_t)
    # Mark BUYs that are STILL in open_lots as "still_open" (cleaner UX)
    open_ts = {l["ts"]: l for l in open_lots}
    for t in out:
        if t["side"] == "BUY" and t["ts"] in open_ts:
            t["status"] = "still_open"
    return out


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


def _ann_windows(rows: list[tuple[datetime, float]]) -> dict:
    """Annualised pace — the realised trailing return scaled to a full year, the
    same formula the rest of the widget uses. rows = [(datetime, equity)] ascending.

    Not a forecast: each window is just realised_return × (365 / elapsed_days),
    i.e. 1w ≈ ×52, 1mo ≈ ×12, 1y ≈ ×1. The 1y figure is None ('TBD') until the
    track spans a full year — then it is simply the actual 365-day return (×1).
    YTD is the realised return since 1 Jan of the latest point's year (= total
    return for a bot that launched this year)."""
    out = {"w": None, "mo": None, "y": None, "ytd": None}
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
            base = rows[0]           # < window_days of history → scale up what we have
        bt, be = base
        elapsed = (now_t - bt).total_seconds() / 86400.0
        if be <= 0 or elapsed * 24 < min_hours:
            return None
        return (now_e / be - 1) * (365.0 / elapsed) * 100

    span_days = (now_t - rows[0][0]).total_seconds() / 86400.0
    out["w"] = ann(7, 12)
    out["mo"] = ann(30, 36)
    out["y"] = ann(365, 12) if span_days >= 365 else None   # else TBD — not 12 months yet
    jan1 = datetime(now_t.year, 1, 1, tzinfo=now_t.tzinfo)
    ybase = next(((t, e) for t, e in rows if t >= jan1), rows[-1])
    out["ytd"] = (now_e / ybase[1] - 1) * 100 if ybase[1] > 0 else None
    return out


# Switching cost — reuses Freyr's crypto cost model (rules/registry.yaml:
# crypto-cost-bps = 3.0 bps/side = 2 bps fee + 1 bp slippage on Hyperliquid/Binance
# majors). A round trip (fully exit + re-enter a sleeve) is 2 sides = 6.0 bps of the
# gross notional; as a fraction of NAV that scales with the bot's gross leverage:
# 1× ≈ 0.06% (cheap-exit specialist — can take narrow edges), 3× ≈ 0.18% (needs a
# fatter edge to be worth running). Methodology mirrors ~/Documents/freyr/switching.py
# (derive_round_trip_bps = 2 × per-side cost).
CRYPTO_ROUND_TRIP_BPS = 6.0


def _switch_color(pct: float) -> str:
    """Green < 0.1% · amber 0.1–0.5% · red > 0.5% of NAV."""
    return "#22c55e" if pct < 0.1 else ("#f59e0b" if pct <= 0.5 else "#ef4444")


def _switch_cost(leverage: float) -> tuple[float, str]:
    """(round-trip switching cost as % of NAV, colour) for a bot's gross leverage.
    Crypto farm bots: 6.0 bps round trip × gross leverage."""
    pct = CRYPTO_ROUND_TRIP_BPS * max(leverage or 1.0, 0.0) / 100.0
    return pct, _switch_color(pct)


def _switch_breakdown(round_trip_bps: float, gross: float, *, asset: str = "BTC",
                      last_measured: str = "") -> dict:
    """Decompose a round-trip switching cost into the parts that produce the chip,
    so the tap-panel can show its working. The cost model is a single blended
    per-side cost = fee + slippage (registry crypto-cost-bps = 2bp fee + 1bp
    slippage; etf-cost-bps = 1bp fee + 0.5bp slippage). Round trip = 2 × per-side;
    as a % of NAV it scales with the position's gross leverage."""
    g = max(gross or 1.0, 0.0)
    per_side = round_trip_bps / 2.0
    fee = per_side * 2.0 / 3.0       # 2:1 fee:slippage split, matches the registry
    slip = per_side - fee
    pct = round_trip_bps * g / 100.0
    return {
        "fee_bps": fee, "slip_bps": slip, "per_side_bps": per_side,
        "round_trip_bps": round_trip_bps, "gross": g, "pct": pct,
        "color": _switch_color(pct), "asset": asset,
        "last_measured": last_measured or "",
    }


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


def _comparison_chart_svg(rows: list[dict], trades: list[dict], start_equity: float,
                          w: int = 720, h_btc: int = 250, h_eq: int = 180) -> str:
    """Two-panel SVG: BTC price (top) with BUY/SELL markers + bot equity (bottom),
    sharing the x-axis. Each marker is a triangle on the BTC line at the price
    where the trade fired. The lower panel shows the equity curve so you can see
    cause-and-effect between bot actions and account value."""
    if len(rows) < 2:
        return ("<div style='color:#6b7280;padding:28px 0;text-align:center'>"
                "Chart fills in as this bot trades. Check back soon.</div>")

    padL, padR, padT, padB_top, padT_bot, padB = 56, 14, 22, 8, 8, 38
    h_total = padT + h_btc + padB_top + padT_bot + h_eq + padB

    btc_ys = [r["btc"] for r in rows]
    eq_ys = [r["equity"] for r in rows]
    ts = [r["ts"] for r in rows]
    btc_lo, btc_hi = min(btc_ys), max(btc_ys)
    btc_lo *= 0.998; btc_hi *= 1.002  # tiny padding
    eq_lo, eq_hi = min(min(eq_ys), start_equity), max(max(eq_ys), start_equity)
    eq_rng = (eq_hi - eq_lo) or 1.0
    eq_lo -= eq_rng * 0.05; eq_hi += eq_rng * 0.05
    btc_rng = (btc_hi - btc_lo) or 1.0
    eq_rng = (eq_hi - eq_lo) or 1.0
    tmin, tmax = ts[0], ts[-1]
    tspan = (tmax - tmin).total_seconds() or 1.0

    # Y origins for the two panels
    y_btc_top = padT
    y_btc_bot = padT + h_btc
    y_eq_top = y_btc_bot + padB_top + padT_bot
    y_eq_bot = y_eq_top + h_eq

    fx = lambda t: padL + (t - tmin).total_seconds() / tspan * (w - padL - padR)
    fy_btc = lambda v: y_btc_top + h_btc * (1 - (v - btc_lo) / btc_rng)
    fy_eq = lambda v: y_eq_top + h_eq * (1 - (v - eq_lo) / eq_rng)

    btc_pts = " ".join(f"{fx(r['ts']):.1f},{fy_btc(r['btc']):.1f}" for r in rows)
    eq_pts = " ".join(f"{fx(r['ts']):.1f},{fy_eq(r['equity']):.1f}" for r in rows)
    eq_col = "#22c55e" if eq_ys[-1] >= start_equity else "#ef4444"
    eq_base = fy_eq(start_equity)

    # Trade markers — triangles on the BTC panel.
    # Each marker is wrapped in a <g class="tm"> with data-* attributes so a
    # mobile tap opens a modal with trade detail (handled by JS at page bottom).
    # An invisible larger circle (r=18) underneath gives a generous tap target —
    # the triangle itself is only ~14px tall which is hard to hit on a phone.
    n_buys = sum(1 for t in trades if t["side"] == "BUY")
    n_sells = sum(1 for t in trades if t["side"] == "SELL")
    marker_svg = []
    for idx, t in enumerate(trades):
        x = fx(t["ts"]); y = fy_btc(t["btc"])
        ts_str = t["ts"].strftime("%d %b %H:%M UTC")
        eq_str = f"{t['equity']:,.2f}"
        # Per-trade P&L attribution attrs (computed in _attribute_trades and merged in here)
        pnl_attrs = (
            f' data-entry="{t.get("entry_price", t["btc"]):.2f}"'
            f' data-notional="{t.get("notional", 0):.2f}"'
            f' data-status="{t.get("status", "")}"'
        )
        if t["side"] == "BUY":
            pnl_attrs += (
                f' data-unrealized-pnl="{t.get("unrealized_pnl", 0):.2f}"'
                f' data-unrealized-pct="{t.get("unrealized_pct", 0):.4f}"'
            )
            colour = "#22c55e"
            tri = (f'<polygon points="{x:.1f},{y-9:.1f} {x-7:.1f},{y+4:.1f} {x+7:.1f},{y+4:.1f}" '
                   f'fill="{colour}" stroke="#0f141c" stroke-width="1" pointer-events="none"/>')
        else:
            pnl_attrs += (
                f' data-realized-pnl="{t.get("realized_pnl", 0):.2f}"'
                f' data-realized-pct="{t.get("realized_pct", 0):.4f}"'
                f' data-hold-hours="{t.get("hold_hours", 0):.2f}"'
            )
            colour = "#ef4444"
            tri = (f'<polygon points="{x:.1f},{y+9:.1f} {x-7:.1f},{y-4:.1f} {x+7:.1f},{y-4:.1f}" '
                   f'fill="{colour}" stroke="#0f141c" stroke-width="1" pointer-events="none"/>')
        marker_svg.append(
            f'<g class="tm" tabindex="0" role="button" '
            f'data-side="{t["side"]}" data-price="{t["btc"]:.2f}" data-qty="{t["qty"]:.6f}" '
            f'data-time="{ts_str}" data-equity="{eq_str}"{pnl_attrs} '
            f'style="cursor:pointer">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="18" fill="transparent" />'
            f'<title>{t["side"]} @ ${t["btc"]:,.0f} · {t["qty"]:.4f} BTC · {ts_str}</title>'
            f'{tri}</g>'
        )

    # Gain/loss shading on the equity panel — pre-clip with polygon
    gain_pts = [f"{fx(rows[0]['ts']):.1f},{eq_base:.1f}"]
    for r in rows:
        gain_pts.append(f"{fx(r['ts']):.1f},{fy_eq(r['equity']):.1f}")
    gain_pts.append(f"{fx(rows[-1]['ts']):.1f},{eq_base:.1f}")
    eq_fill_pts = " ".join(gain_pts)

    fmt = "%d %b %H:%M" if tspan < 3 * 86400 else "%d %b"
    btc_final = btc_ys[-1]
    btc_initial = btc_ys[0]
    btc_pct = (btc_final / btc_initial - 1) * 100
    eq_pct = (eq_ys[-1] / start_equity - 1) * 100

    btc_col = "#f59e0b"
    return f"""<svg viewBox="0 0 {w} {h_total}" width="100%" style="background:#0f141c;border-radius:10px;display:block">
      <!-- BTC panel background grid -->
      <line x1="{padL}" y1="{y_btc_top}" x2="{padL}" y2="{y_btc_bot}" stroke="#1f2937" stroke-width="1"/>
      <line x1="{padL}" y1="{y_btc_bot}" x2="{w - padR}" y2="{y_btc_bot}" stroke="#1f2937" stroke-width="1"/>
      <!-- BTC panel: price line -->
      <polyline points="{btc_pts}" fill="none" stroke="{btc_col}" stroke-width="2"/>
      <!-- BTC y-axis labels -->
      <text x="{padL - 6}" y="{y_btc_top + 4}" fill="#6b7280" font-size="11" text-anchor="end">${btc_hi:,.0f}</text>
      <text x="{padL - 6}" y="{y_btc_bot + 4}" fill="#6b7280" font-size="11" text-anchor="end">${btc_lo:,.0f}</text>
      <text x="13" y="{(y_btc_top + y_btc_bot) / 2:.0f}" fill="{btc_col}" font-size="11" text-anchor="middle" font-weight="bold"
            transform="rotate(-90 13 {(y_btc_top + y_btc_bot) / 2:.0f})">₿ BTC price</text>
      <!-- BTC headline (top right) -->
      <text x="{w - padR}" y="{y_btc_top + 12}" fill="{btc_col}" font-size="12" text-anchor="end" font-weight="bold">
        BTC ${btc_initial:,.0f} → ${btc_final:,.0f} ({btc_pct:+.2f}%)</text>
      <!-- Trade markers -->
      {''.join(marker_svg)}
      <!-- Legend (BTC panel) -->
      <g transform="translate({padL + 6}, {y_btc_top + 4})">
        <polygon points="0,4 -5,12 5,12" fill="#22c55e" stroke="#0f141c" stroke-width="0.8"/>
        <text x="10" y="13" fill="#9aa4b2" font-size="10">BUY × {n_buys}</text>
        <polygon points="56,12 51,4 61,4" fill="#ef4444" stroke="#0f141c" stroke-width="0.8"/>
        <text x="66" y="13" fill="#9aa4b2" font-size="10">SELL × {n_sells}</text>
      </g>

      <!-- Equity panel background grid -->
      <line x1="{padL}" y1="{y_eq_top}" x2="{padL}" y2="{y_eq_bot}" stroke="#1f2937" stroke-width="1"/>
      <line x1="{padL}" y1="{y_eq_bot}" x2="{w - padR}" y2="{y_eq_bot}" stroke="#1f2937" stroke-width="1"/>
      <!-- Starting line -->
      <line x1="{padL}" y1="{eq_base:.1f}" x2="{w - padR}" y2="{eq_base:.1f}" stroke="#3a4253" stroke-width="1" stroke-dasharray="4 4"/>
      <!-- Gain/loss fill -->
      <polygon points="{eq_fill_pts}" fill="{eq_col}" fill-opacity="0.18"/>
      <!-- Equity line -->
      <polyline points="{eq_pts}" fill="none" stroke="{eq_col}" stroke-width="2.5"/>
      <!-- Equity y-axis labels -->
      <text x="{padL - 6}" y="{y_eq_top + 4}" fill="#6b7280" font-size="11" text-anchor="end">${eq_hi:,.0f}</text>
      <text x="{padL - 6}" y="{y_eq_bot + 4}" fill="#6b7280" font-size="11" text-anchor="end">${eq_lo:,.0f}</text>
      <text x="13" y="{(y_eq_top + y_eq_bot) / 2:.0f}" fill="{eq_col}" font-size="11" text-anchor="middle" font-weight="bold"
            transform="rotate(-90 13 {(y_eq_top + y_eq_bot) / 2:.0f})">$ Account value</text>
      <!-- Equity headline -->
      <text x="{w - padR}" y="{y_eq_top + 12}" fill="{eq_col}" font-size="12" text-anchor="end" font-weight="bold">
        Bot ${start_equity:,.0f} → ${eq_ys[-1]:,.0f} ({eq_pct:+.2f}%)</text>
      <text x="{w - padR}" y="{eq_base - 4:.1f}" fill="#6b7280" font-size="10" text-anchor="end">start ${start_equity:,.0f}</text>

      <!-- Shared x-axis labels -->
      <text x="{padL}" y="{h_total - 18}" fill="#6b7280" font-size="11">{tmin.strftime(fmt)}</text>
      <text x="{w - padR}" y="{h_total - 18}" fill="#6b7280" font-size="11" text-anchor="end">{tmax.strftime(fmt)}</text>
      <text x="{(padL + w - padR) / 2:.0f}" y="{h_total - 4}" fill="#8b95a5" font-size="11" text-anchor="middle">Time (older → newer)</text>
    </svg>"""


def _chart_page(slug: str) -> str:
    """Full-screen comparison chart page — opened when the user taps the small
    chart on the bot detail page. Shows BTC + equity + trade markers."""
    data = _load() or {}
    v = next((x for x in data.get("variants", []) if x.get("slug") == slug), None)
    if v is None:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                f"<p>Bot '{slug}' not found.</p><a href='/' style='color:#60a5fa'>← back</a></body>")
    start_equity = data.get("paper_capital", 10_000.0)
    rows = _full_rows(slug)
    current_btc = rows[-1]["btc"] if rows else data.get("btc_price", 0)
    raw_trades = _detect_trades(rows)
    trades = _attribute_trades(raw_trades, current_btc)
    n_buys = sum(1 for t in trades if t["side"] == "BUY")
    n_sells = sum(1 for t in trades if t["side"] == "SELL")
    total_realized = sum(t.get("realized_pnl", 0) for t in trades if t["side"] == "SELL")
    total_unrealized = sum(
        t.get("unrealized_pnl", 0) for t in trades
        if t["side"] == "BUY" and t.get("status") == "still_open"
    )
    chart_svg = _comparison_chart_svg(rows, trades, start_equity)

    # Recent trade list for the dashboard below the chart — now with P&L column
    def _pnl_cell(t):
        if t["side"] == "SELL":
            pnl = t.get("realized_pnl", 0)
            pct = t.get("realized_pct", 0)
            colour = "#22c55e" if pnl >= 0 else "#ef4444"
            sign = "+" if pnl >= 0 else ""
            return (f"<span style='color:{colour};font-weight:bold'>{sign}${pnl:,.2f}</span>"
                    f"<br><span style='color:#6b7280;font-size:10px'>{sign}{pct:.2f}%</span>")
        # BUY — show entry / mark-to-market
        if t.get("status") == "still_open":
            mtm = t.get("unrealized_pnl", 0)
            pct = t.get("unrealized_pct", 0)
            colour = "#22c55e" if mtm >= 0 else "#ef4444"
            sign = "+" if mtm >= 0 else ""
            return (f"<span style='color:{colour}'>{sign}${mtm:,.2f}</span>"
                    f"<br><span style='color:#6b7280;font-size:10px'>open · {sign}{pct:.2f}%</span>")
        # Closed BUY — entry that was later sold; no standalone P&L
        return "<span style='color:#6b7280'>—</span><br><span style='color:#6b7280;font-size:10px'>closed</span>"

    recent = trades[-12:] if len(trades) > 12 else trades
    trade_rows_html = "".join(
        f"<tr><td style='color:#9aa4b2;padding:5px 8px;font-size:12px'>{t['ts'].strftime('%d %b %H:%M')}</td>"
        f"<td style='padding:5px 8px;font-size:12px'>"
        f"<span style='color:{'#22c55e' if t['side']=='BUY' else '#ef4444'};font-weight:bold'>{t['side']}</span></td>"
        f"<td style='padding:5px 8px;text-align:right;font-size:12px'>${t['btc']:,.0f}</td>"
        f"<td style='padding:5px 8px;text-align:right;font-size:12px;color:#9aa4b2'>{t['qty']:.4f} BTC</td>"
        f"<td style='padding:5px 8px;text-align:right;font-size:12px'>{_pnl_cell(t)}</td></tr>"
        for t in reversed(recent)
    ) or ("<tr><td colspan='5' style='color:#6b7280;text-align:center;padding:14px;font-size:12.5px'>"
          "No trades yet — this bot hasn't transacted in this window.</td></tr>")
    # P&L summary at the top of the table
    pnl_summary_colour_r = "#22c55e" if total_realized >= 0 else "#ef4444"
    pnl_summary_colour_u = "#22c55e" if total_unrealized >= 0 else "#ef4444"

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60>
<title>{v['name']} — full chart</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:14px;max-width:820px;margin:auto">
  <a href="/bot/{slug}" style="color:#60a5fa;text-decoration:none;font-size:14px">← back to {v['name']}</a>
  <h2 style="margin:10px 0 2px">{v['name']} — full chart</h2>
  <div style="color:#8b95a5;font-size:13px;margin-bottom:12px">
    BTC price on top with <span style="color:#22c55e">▲ BUY</span> /
    <span style="color:#ef4444">▼ SELL</span> markers · bot equity on bottom · same time axis
  </div>
  {chart_svg}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:14px 0">
    <div style="background:#151a23;border-radius:10px;padding:10px 8px;text-align:center">
      <div style="color:#8b95a5;font-size:11px">Realized P&L (closed trades)</div>
      <div style="font-size:20px;margin-top:2px;color:{pnl_summary_colour_r};font-weight:bold">
        {"+" if total_realized >= 0 else ""}${total_realized:,.2f}
      </div>
      <div style="color:#6b7280;font-size:10.5px">{n_sells} SELLs</div>
    </div>
    <div style="background:#151a23;border-radius:10px;padding:10px 8px;text-align:center">
      <div style="color:#8b95a5;font-size:11px">Unrealized P&L (still-open BUYs)</div>
      <div style="font-size:20px;margin-top:2px;color:{pnl_summary_colour_u};font-weight:bold">
        {"+" if total_unrealized >= 0 else ""}${total_unrealized:,.2f}
      </div>
      <div style="color:#6b7280;font-size:10.5px">at BTC ${current_btc:,.0f}</div>
    </div>
  </div>
  <div style="background:#151a23;border-radius:10px;padding:12px;margin-top:10px">
    <div style="color:#8b95a5;font-size:12px;margin-bottom:6px">RECENT TRADES (newest first, up to 12) — tap any row's matching triangle for full detail</div>
    <table style="width:100%;border-collapse:collapse">
      <thead><tr style="color:#6b7280;font-size:10.5px;text-align:left">
        <th style="padding:4px 8px;font-weight:normal">Time</th>
        <th style="padding:4px 8px;font-weight:normal">Side</th>
        <th style="padding:4px 8px;font-weight:normal;text-align:right">Price</th>
        <th style="padding:4px 8px;font-weight:normal;text-align:right">Qty</th>
        <th style="padding:4px 8px;font-weight:normal;text-align:right">P&L</th>
      </tr></thead>
      <tbody>{trade_rows_html}</tbody>
    </table>
  </div>
  <p style="color:#6b7280;font-size:12px;margin-top:12px;line-height:1.5">
    The triangles on the BTC panel mark every BUY (green ▲) and SELL (red ▼) the bot fired.
    The bottom panel shows what happened to the account value as a result.
    Pretend money on real prices. Refreshes every minute. Tap a triangle for trade detail.</p>

  <!-- Trade detail modal — slides up from the bottom when a triangle is tapped -->
  <div id="trade-modal" style="position:fixed;left:0;right:0;bottom:0;background:#151a23;
        border-top:1px solid #2a3242;border-radius:14px 14px 0 0;padding:18px 22px 26px;
        box-shadow:0 -8px 24px rgba(0,0,0,0.5);transform:translateY(110%);
        transition:transform 0.22s ease;z-index:100;font-family:system-ui">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div id="tm-title" style="font-size:18px;font-weight:bold"></div>
      <button id="tm-close" style="background:#1f2937;color:#e6e6e6;border:none;border-radius:8px;
              width:36px;height:36px;font-size:18px;cursor:pointer">×</button>
    </div>
    <div id="tm-body" style="font-size:14px;line-height:1.7"></div>
  </div>

  <script>
    (function(){{
      const modal = document.getElementById('trade-modal');
      const title = document.getElementById('tm-title');
      const body = document.getElementById('tm-body');
      const close = document.getElementById('tm-close');
      function fmtUSD(v) {{ return '$' + Number(v).toLocaleString('en-US', {{maximumFractionDigits:2, minimumFractionDigits:2}}); }}
      function fmtPct(v) {{ return Number(v).toFixed(2) + '%'; }}
      function pnlLine(label, dollar, pct) {{
        const colour = Number(dollar) >= 0 ? '#22c55e' : '#ef4444';
        const sign = Number(dollar) >= 0 ? '+' : '';
        return `<div><b>${{label}}</b> · <span style="color:${{colour}};font-weight:bold">${{sign}}${{fmtUSD(dollar)}}</span> <span style="color:${{colour}};font-size:11px">(${{sign}}${{fmtPct(pct)}})</span></div>`;
      }}
      function open(t) {{
        const side = t.dataset.side;
        const price = parseFloat(t.dataset.price);
        const qty = parseFloat(t.dataset.qty);
        const time = t.dataset.time;
        const equity = t.dataset.equity;
        const notional = parseFloat(t.dataset.notional);
        const status = t.dataset.status;
        const colour = side === 'BUY' ? '#22c55e' : '#ef4444';
        title.style.color = colour;
        title.textContent = side === 'BUY' ? '▲ BUY' : '▼ SELL';
        // Build the body in two sections: trade facts + P&L attribution
        let html = '';
        html += `<div style="margin-bottom:10px">
          <div><b>Price</b> · <span style="color:#9aa4b2">${{fmtUSD(price)}}</span></div>
          <div><b>Quantity</b> · <span style="color:#9aa4b2">${{qty.toFixed(6)}} BTC</span></div>
          <div><b>Notional</b> · <span style="color:#9aa4b2">${{fmtUSD(notional)}}</span></div>
          <div><b>When</b> · <span style="color:#9aa4b2">${{time}}</span></div>
          <div><b>Account value at trade</b> · <span style="color:#9aa4b2">$${{equity}}</span></div>
        </div>`;
        html += `<div style="border-top:1px solid #2a3242;padding-top:10px;margin-top:8px">
          <div style="color:#8b95a5;font-size:11px;margin-bottom:6px">P&L ATTRIBUTION</div>`;
        if (side === 'SELL') {{
          const entry = parseFloat(t.dataset.entry);
          const realizedPnl = parseFloat(t.dataset.realizedPnl);
          const realizedPct = parseFloat(t.dataset.realizedPct);
          const holdHours = parseFloat(t.dataset.holdHours);
          html += `<div><b>Matched entry</b> · <span style="color:#9aa4b2">${{fmtUSD(entry)}}</span></div>`;
          html += pnlLine('Realized P&L', realizedPnl, realizedPct);
          if (holdHours > 0) {{
            const days = holdHours / 24;
            const holdStr = holdHours < 24 ? holdHours.toFixed(1) + ' hours' : days.toFixed(1) + ' days';
            html += `<div><b>Held for</b> · <span style="color:#9aa4b2">${{holdStr}}</span></div>`;
          }}
        }} else {{
          // BUY
          if (status === 'still_open') {{
            const unrealizedPnl = parseFloat(t.dataset.unrealizedPnl);
            const unrealizedPct = parseFloat(t.dataset.unrealizedPct);
            html += `<div><b>Entry price</b> · <span style="color:#9aa4b2">${{fmtUSD(price)}}</span></div>`;
            html += `<div style="color:#fbbf24;font-size:11px;margin:4px 0">⚠ Still open at end of window</div>`;
            html += pnlLine('Unrealized P&L (mark-to-market)', unrealizedPnl, unrealizedPct);
          }} else {{
            // BUY that's been closed by a later SELL
            html += `<div><b>Entry price</b> · <span style="color:#9aa4b2">${{fmtUSD(price)}}</span></div>`;
            html += `<div style="color:#9aa4b2;font-size:11px;margin-top:4px">Position closed by a later SELL. See that SELL marker for realized P&L.</div>`;
          }}
        }}
        html += `</div>`;
        body.innerHTML = html;
        modal.style.transform = 'translateY(0)';
      }}
      function dismiss() {{ modal.style.transform = 'translateY(110%)'; }}
      close.addEventListener('click', dismiss);
      document.querySelectorAll('g.tm').forEach(g => {{
        g.addEventListener('click', () => open(g));
        g.addEventListener('keydown', e => {{ if (e.key === 'Enter' || e.key === ' ') open(g); }});
      }});
      // Tap outside modal closes it
      document.addEventListener('click', e => {{
        if (!modal.contains(e.target) && !e.target.closest('g.tm')) {{ dismiss(); }}
      }});
    }})();
  </script>
</body></html>"""


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
    elif t == "donchian":
        n = v.get("entry_lookback_days", 20); m = v.get("exit_lookback_days", 10)
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 f"<div>• <b>How:</b> buys all-in when today's close breaks the highest close of the last {n} days; "
                 f"sells everything when today's close drops below the lowest close of the last {m} days. "
                 "The classic Turtle-trader rule — binary, in or out, no leverage.</div>"
                 "<div>• <b>Wins:</b> sustained bull legs after a quiet stretch — captured "
                 "+31.78%/yr on the 2024-09 → 2026-05 holdout, beating both Trend bots and Buy &amp; Hold.</div>"
                 "<div>• <b>Loses:</b> in choppy markets and during the very first burst of a bull leg "
                 "(it waits for the breakout to confirm, missing the early move TrendBot catches).</div>"
                 "<div>• <b>Safety brake:</b> 35% drawdown halt blocks new entries; an open long still "
                 "exits cleanly on the M-day-low rule. No leverage. Long-only spot-equivalent — "
                 "can never be wiped out.</div>"
                 "<div style='color:#9aa4b2;margin-top:6px'>The <b>portfolio-specialist deploy</b> — Gate 3 "
                 "K1 + K2 fired on 2020-2022 history (the cousin TrendBot dominated three regimes; walk-forward "
                 "max DD 39%), but K3 corr stayed at 0.79 with TrendBot and K4 holdout passed by 27 ppt over "
                 "the floor. Steven greenlit deploy on the holdout strength + K3/K5 catastrophic resistance; "
                 "the historical kill conditions were calibrated on a regime that the 2024-2026 data doesn't "
                 "look like. Watch how it tracks alongside Trend (fast) and Trend (slow) — that's the live "
                 "K3 check.</div>")
    elif t == "rebalance":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> keeps about half in Bitcoin, half in cash; rebalances when it drifts.</div>"
                 "<div>• <b>Effect:</b> mechanically buys low and sells high; smoother ride than holding.</div>")
    elif t == "dca":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> buys a fixed amount of Bitcoin every day — classic dollar-cost averaging.</div>"
                 "<div>• <b>Effect:</b> averages your entry price; steady accumulation, no timing.</div>")
    elif t == "dca_smart":
        works = (f"<div>• <b>Right now:</b> {v['state']}</div>"
                 "<div>• <b>How:</b> buys a small amount of Bitcoin every day — but on days where the RSI says "
                 "Bitcoin is genuinely oversold, it buys 1.5× the normal amount (up to 2 dip-buys per week).</div>"
                 "<div>• <b>Wins:</b> in bear legs and sharp crashes — when fear shows up in the RSI it accumulates "
                 "more aggressively at the cheaper prices. Backtest: beat plain DCA by ~4% during the 2022 cycle "
                 "bear, with a lower average cost basis.</div>"
                 "<div>• <b>Bleeds:</b> a small amount (~5%) in clean bull legs — it deploys cash slightly slower "
                 "than plain DCA on purpose, to keep dry powder for dips.</div>"
                 "<div>• <b>Can't be wiped out:</b> spot-only, no leverage, only spends cash it has. "
                 "It can never lose more than Bitcoin itself does.</div>"
                 "<div style='color:#9aa4b2;margin-top:6px'>The <b>bear/crash specialist</b> in the Stack. "
                 "Sits next to plain DCA so the head-to-head is honest — watch them diverge during fear.</div>")
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
  <a href="/farm" style="color:#60a5fa;text-decoration:none;font-size:14px">← all bots</a>
  <h2 style="margin:10px 0 2px">{v['name']}</h2>
  <div style="color:#8b95a5;font-size:14px;margin-bottom:12px">{v['style']}</div>
  {warn}
  <div style="font-size:13px;color:#8b95a5;margin-bottom:4px">Account value ($) over time — <a href="/bot/{slug}/chart" style="color:#60a5fa;text-decoration:none">tap for full chart with BTC + buy/sell markers ›</a></div>
  <a href="/bot/{slug}/chart" style="display:block;text-decoration:none">{_svg_chart(eq_rows, start, up)}</a>
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


# ── ROI leaderboard page (/leaderboard) — every variant ranked, survival-first ──

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
  <a class=nav href="/farm">← back to the farm</a>
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
def index():
    return HTMLResponse(_home_page())


@app.get("/widget", include_in_schema=False)
def widget():
    return HTMLResponse(_home_page())


@app.get("/farm", include_in_schema=False)
def farm_view(tab: str = "grid"):
    return HTMLResponse(_page(tab))


@app.get("/freyr/{variant}", include_in_schema=False)
def freyr_detail(variant: str):
    return HTMLResponse(_freyr_detail_page(variant))


@app.get("/testnet", include_in_schema=False)
def testnet_detail():
    """Live Testnet drill-down (positions, orders, fills, equity chart)."""
    return HTMLResponse(_testnet_detail_page())


@app.get("/testnet/live", include_in_schema=False)
def testnet_live():
    """Current Live Testnet card state as JSON (read-only; never contains the key).
    The widget renders server-side off the same snapshot; this is the raw feed."""
    snap = _testnet_load()
    semoji, slabel, scol = _testnet_status(snap)
    if not snap:
        return {"status": "disconnected", "display": {"emoji": semoji, "label": slabel},
                "snapshot": None}
    return {"status": snap.get("status", "ok"),
            "display": {"emoji": semoji, "label": slabel, "color": scol},
            "snapshot": snap}


@app.get("/bot/{slug}", include_in_schema=False)
def bot_detail(slug: str):
    return HTMLResponse(_bot_page(slug))


@app.get("/bot/{slug}/chart", include_in_schema=False)
def bot_chart(slug: str):
    """Full-screen comparison chart — BTC price + bot equity + trade markers.
    Linked from the small chart on the /bot/{slug} page."""
    return HTMLResponse(_chart_page(slug))


@app.get("/leaderboard", include_in_schema=False)
def leaderboard():
    return HTMLResponse(_leaderboard_page())


@app.get("/portfolio", include_in_schema=False)
def portfolio_json():
    """Steven's portfolio config + live snapshot (no key — paper, read-only)."""
    universe, _ = _bot_universe()
    return {"snapshot": _tick_steven(universe) or sp.snapshot(universe=universe),
            "config": sp.load_config()}


@app.post("/portfolio/set", include_in_schema=False)
def portfolio_set(bot: str, action: str, name: str = ""):
    """Add/remove a bot or set its ON/OFF/AUTO override. Every call is audit-logged.
    action ∈ {ADD, REMOVE, ON, OFF, AUTO}. Paper-only, no key needed."""
    action = (action or "").upper()
    universe, _ = _bot_universe()
    if not name:
        name = (universe.get(bot) or {}).get("name", "")
    if action == "ADD":
        sp.add(bot, name)
    elif action == "REMOVE":
        sp.remove(bot)
    elif action in ("ON", "OFF", "AUTO"):
        sp.set_override(bot, action, name)
    else:
        raise HTTPException(status_code=400, detail="bad action")
    return {"ok": True, "snapshot": _tick_steven(universe) or sp.snapshot(universe=universe)}


# ══════════════════════════════════════════════════════════════════════════════
# Freyr ensemble — the PRIMARY section of the dashboard.
#
# Freyr is Steven's multi-book trading system (a sibling repo at ~/Documents/freyr).
# Its paper variants write JSON snapshots; we read the latest off disk directly
# (same Mac — no network fetch, no cross-origin). This dashboard now leads with
# Freyr; the 34-bot BTC farm lives under /farm with a tight survivor set surfaced
# here on the home page. See WIDGET_MIGRATION_2026-06-09.md.
# ══════════════════════════════════════════════════════════════════════════════

FREYR_SNAP = Path("/Users/openclaw/Documents/freyr/paper/snapshots")
FREYR_VARIANTS = ["v0.1.1", "v0.2", "v0.3", "surtr", "vidar", "thor", "idunn", "loki", "aegir", "sif", "skadi", "hermod", "mimir", "vali"]
FREYR_META = {
    # Twelfth specialist-library bot — FIRST member of the new 🌐 Cross-asset-lead bracket,
    # and the SECOND DATA-DISCOVERED specialist (Phase 2 Finding B). Card framing: the
    # major-leads-the-alt lead, the NON-bull regime correction, and the self-retiring decay.
    "vali":   ("🌐", "Cross-asset lead", "#0ea5e9", "Váli · Freyr's SECOND data-discovered specialist (Phase 2 Finding B) · LONG SOL on month-old BTC cross-venue funding dislocation — positioning stress in the major LEADS the high-beta alt by ~1 month (ρ+0.28, lag-30; lag-0 is ~zero) · CORRECTED gate: the brief's 'bull-gated' shorthand was BACKWARDS — the signal works in chop/bear/crash (ρ+0.30/+0.30/+0.35) and INVERTS in bull (ρ−0.18, bull backtests −14%) → arms NON-bull, hard bull lockout · ships a LIVE DECAY DETECTOR (Finding B decays 0.30→0.09): auto-retires when 90d ρ<0.10 for 30d — already standing down ~16% of recent days · NEVER long into a SOL crash spike (vol kill 0/81) · sized as a TILT — 3–8% SOL sleeve, NO leverage · standalone (full-notional) +5.4%/yr Sharpe 0.43 maxDD −27% (in-window +7.4%/yr deployed) · a harvest-while-it-lasts book, built to retire itself · deploys on Hyperliquid (SOL perp)"),
    # Eleventh specialist-library bot — FIRST member of the new 🦉 Contrarian bracket,
    # and the FIRST DATA-DISCOVERED specialist (born from Phase 2's correlation sweep,
    # not a prior). Card framing: the inverted posture + the honest modest edge.
    "mimir":  ("🦉", "Contrarian", "#d97706", "Mímir · Freyr's FIRST data-discovered specialist (Phase 2 Finding C) · LONG ETH into elevated equity fear (VIX>25) — the INVERSE of every VIX-as-risk-off gate (Surtr arms a crash structure on high VIX; Mímir buys the recovery that follows) · the buy-the-fear edge is REAL but MODEST (ρ≈0.22): held days earn +0.40%/day vs +0.13% baseline, fwd-1m ETH +7.65% vs +2.77% (2.8×) · NEVER long into a crypto crash spike (eth-vol kill airtight, 0/130) · sized as a TILT not a bet — 4–10% ETH sleeve, NO leverage · deployed +1.5%/yr Sharpe 0.58 maxDD −6.6%, in-elevated-VIX +8.0%/yr (full-notional +21.6%/yr, in-window +173%/yr = the signal) · switching cost +0.11bps/round-trip · deploys on Hyperliquid (ETH perp)"),
    # Seventh specialist-library bot — FIRST member of the new 📊 Options bracket.
    "skadi":  ("📊", "Options specialist", "#8b5cf6", "Skadi · DEPLOYED 12× variance notional (survivable — worst armed day −7.3% = the defined-risk wing cap, never breached) · SELLS defined-risk BTC credit spreads, collects implied-vol premium · FLEES on vol eruption (inverse of Surtr) · deployed +9.6%/yr all-time, +44%/yr active-window, maxDD −17.7% · 500% needs ~136× (ruinous) → honest Sharpe~0.85 carry, not a moonshot · LIBRARY book, deploys on Deribit/Lyra not Hyperliquid"),
    #            emoji   profile-name    accent     one-line knob summary
    "v0.1.1": ("🛡️", "Conservative", "#22c55e", "Survival-first · vol 12% · ≤2.5× cap"),
    "v0.2":   ("⚖️", "Moderate",     "#3b82f6", "Escape-governed · vol 15% · 2.0× target"),
    "v0.3":   ("🚀", "Aggressive",   "#a78bfa", "Escape-governed · vol 20% · 3.0× target"),
    # First specialist-library bot — independent paper P&L, 🔥 crash bracket.
    "surtr":  ("🔥", "Crash specialist", "#ef4444", "Surtr · gated long-gamma · flat in calm · armed on BTC 5d-vol z>2σ OR VIX>30"),
    # Second specialist-library bot — independent paper P&L, 🐂 bull bracket.
    "vidar":  ("🐂", "Bull specialist", "#f59e0b", "Vidar · gated Kelly-levered long · flat in calm · armed on 50d-mom>0 & >200d-SMA & clean trend, NOT a vol-spike"),
    # AGGRESSIVE sibling of Vidar — same 🐂 bull bracket, second bull slot, independent paper P&L.
    "thor":   ("🐂", "Bull specialist (aggressive)", "#fb923c", "Thor · DEPLOYED cap 5× (survivable ceiling — 6× LIQUIDATES at −92%) · AGGRESSIVE Vidar sibling · responsive 15d-Kelly long · TIGHT −15% trailing stop (Mjölnir returns) · deployed in-bull +614% (both eras >500% ✅) vs Vidar +267% · maxDD −82.6% (survivable, disclosed) · 4× would drop the 2023-24 era to +361% (misses target) → 5× is the honest cap · edge rests on the 15d window → paper confirms OOS"),
    # Third specialist-library bot — independent paper P&L, 😴 calm/carry bracket.
    "idunn":  ("😴", "Calm specialist", "#14b8a6", "Idunn · gated delta-neutral funding harvest · flat outside calm · low vol + paid funding + no trend"),
    # Fourth specialist-library bot — independent paper P&L, 🌪 chop bracket.
    "loki":   ("🌪", "Chop specialist", "#06b6d4", "Loki · gated overreaction-fade · flat in trend · armed on moderate vol + flat 50d mom/Sharpe + intact 30d range · KILLS on breakout"),
    # Fifth specialist-library bot — AGGRESSIVE sibling of Loki, 🌪 chop bracket.
    "aegir":  ("🌪", "Chop specialist (aggr)", "#0891b2", "Aegir · DEPLOYED conf-ramped 3×→8× bounded by the −35% DD stop (NOT flat-8×) · gated FINE-BAND GRID (12 bands) harvesting intraday wobble · deployed all-time +79.9% / maxDD −34.5% (inside the stop) · flat-8× (+452% pure-chop / −51% DD) is the intrinsic edge — bleeds past the stop in breakouts → NOT deployed · edge-stopped + KILLS on breakout"),
    # Sixth specialist-library bot — AGGRESSIVE sibling of Idunn, 😴 calm/carry bracket.
    "sif":    ("😴", "Calm specialist (aggr)", "#eab308", "Sif · DEPLOYED cap 12× (was 10–20×; 14–20× is tail-fragile, not deployed) · 12× ≈ (1−5% maint)/8% = the largest leverage that survives one documented −8% basis tail · gated delta-neutral funding harvest, calm-streak compounding ramp, low switching · deployed +43%/yr (fixed-12× ref +50%), Sharpe 7.3 · 500% needs ~55× (basis tail = ruin) → NOT reachable survivably"),
    # Aggressive 🔥 crash sibling of Surtr — independent paper P&L. HONEST MISS:
    # directional levered short loses in crashes (vol-drag/bounce symmetry); kept as the
    # counter-example proving convexity (Surtr) beats selling the asset. Do NOT deploy.
    "fenrir": ("🔥", "Crash specialist (aggr)", "#dc2626", "Fenrir · gated LEVERED SHORT 2.5–5× · armed on a DOWN crash (5d-vol z>2σ OR VIX>30 OR −10% gap + down trend) · exits on 10% rebound off low · honest finding: −50% CAGR / −99.8% DD, leverage monotonically worsens it — directional short ≠ Surtr's convexity"),
    # Tenth specialist-library bot — FIRST member of the new 🏃 Cheap-exit bracket, and
    # Freyr's FIRST market-maker. NOVELTY + SPEED mandate (Steven), not ROI.
    "hermod": ("🏃", "Cheap-exit", "#10b981", "Hermod · Freyr's FIRST market-maker · passive maker-only quotes BOTH sides of BTC perp top-of-book, inventory-neutral via spread skew · PAID to round-trip: +1.16bps net per round-trip (spread 6bps − 2×1.5bps Hyperliquid maker − 1.84bps adverse) = LOWEST switching cost in the library · always-on in CALM, flat in fast tape (makers get adversely selected) · uncorrelated calm-yield: deployed +3.2%/yr Sharpe 5.8 maxDD −0.2% (full-notional +28%/yr) · daily-bar fill PROXY, calm gate is a microstructure prior pending tick/paper — ROI is NOT the point, the strategy class + switching cost is"),
}
FREYR_NOTIONAL = 10_000.0   # show Freyr's unit-equity on the same $10k notional as the farm

# ── Live Testnet ────────────────────────────────────────────────────────────────
# The real Hyperliquid TESTNET account (real orders, fake money). A read-only
# poller in the freyr repo (data/sources/hyperliquid_testnet_live.py, every 60s)
# writes this snapshot off disk; we render it server-side like the Freyr cards and
# also expose it raw at /testnet/live. We NEVER touch the venue from here.
TESTNET_LIVE = Path("/Users/openclaw/Documents/freyr/paper/snapshots/testnet_live.json")
TESTNET_UI_URL = "https://app.hyperliquid-testnet.xyz"


def _testnet_load() -> dict | None:
    try:
        return json.loads(TESTNET_LIVE.read_text())
    except Exception:
        return None


def _testnet_status(snap: dict | None) -> tuple[str, str, str]:
    """(emoji, label, color) for the connection chip. 🟢 fresh ok · 🟡 stale /
    slow · 🔴 no data. 'Stale' kicks in after 3 missed 60s polls."""
    if not snap or snap.get("status") == "error" and not snap.get("portfolio_value"):
        return ("🔴", "Disconnected", "#ef4444")
    age = 1e9
    try:
        fetched = datetime.strptime(snap.get("fetched_at", ""), "%Y-%m-%dT%H:%M:%SZ")
        age = (datetime.utcnow() - fetched).total_seconds()
    except Exception:
        pass
    if snap.get("status") == "stale" or age > 180:
        return ("🟡", "Stale data", "#f59e0b")
    return ("🟢", "Connected", "#22c55e")

# The BTC-farm favourites kept visible on the home page. Selection (2026-06-09,
# revised): Steven's framework is "don't die ≠ don't drawdown" — high leverage and
# deep dips are fine as long as the escape works, so leveraged high-flyers are back.
# The 4 original survival-first picks (aggressive / longvol / gamma-scalp /
# funding-smart) PLUS three favourites Steven asked back: degen (3× grid),
# longvol-3x (un-gated 3× crash hedge) and longvol-3x-dvol (the "Long-Vol 65" —
# 3× crash hedge that only fires when implied vol DVOL≤65 is cheap). Ordered so
# same-bracket bots sit together (previews the specialist-bracket restructure).
# Full bots still browseable at /farm. See WIDGET_MIGRATION_2026-06-09.md.
SURVIVORS = [
    "longvol-3x", "longvol-3x-dvol", "longvol",   # 🔥 crash specialists
    "degen", "aggressive",                        # 🐂 bull / aggressive
    "gamma-scalp",                                # 🌪 chop / convex
    "funding-smart",                             # 😴 calm / carry
]

# Specialist bracket each favourite fits — the lens Steven wants surfaced: which bot
# do you hold for which kind of market. Behaviour-based, not family-based (e.g. degen
# is a grid by engine but a bull/aggressive bot by behaviour). (emoji, label, accent).
BRACKETS = {
    "longvol-3x":      ("🔥", "Crash specialist", "#ef4444"),
    "longvol-3x-dvol": ("🔥", "Crash specialist", "#ef4444"),
    "longvol":         ("🔥", "Crash specialist", "#ef4444"),
    "degen":           ("🐂", "Bull / Aggressive", "#f59e0b"),
    "aggressive":      ("🐂", "Bull / Aggressive", "#f59e0b"),
    "gamma-scalp":     ("🌪", "Chop / Convex",     "#06b6d4"),
    "funding-smart":   ("😴", "Calm / Carry",      "#14b8a6"),
}


def _freyr_load(variant: str):
    """(snapshot dict, index-summary dict) for a variant's latest day, or (None, None)."""
    base = FREYR_SNAP / variant
    try:
        idx = json.loads((base / "index.json").read_text())
        latest = idx["latest"]
        snap = json.loads((base / f"{latest}.json").read_text())
        summ = next((s for s in idx.get("summary", []) if s.get("date") == latest), {})
        return snap, summ
    except Exception:
        return None, None


def _variant_by_slug(slug: str) -> dict | None:
    for v in (_load() or {}).get("variants", []):
        if v.get("slug") == slug:
            return v
    return None


def _mini_spark(vals: list[float], up: bool, w: int = 150, h: int = 46) -> str:
    """Tiny line sparkline from a list of equity values (no axes)."""
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    fx = lambda i: i / (n - 1) * (w - 2) + 1
    fy = lambda v: 2 + (h - 4) * (1 - (v - lo) / rng)
    pts = " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in enumerate(vals))
    col = "#22c55e" if up else "#ef4444"
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' style='display:block;max-width:{w}px'>"
            f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='2'/></svg>")


def _dd_from_equity(vals: list[float]) -> list[float]:
    """Drawdown % series derived from an equity track: at each point, how far below
    the running peak (≤ 0). Honest — computed from the same equity the line shows,
    no extra data needed."""
    out, peak = [], vals[0] if vals else 1.0
    for v in vals:
        peak = max(peak, v)
        out.append((v / peak - 1) * 100 if peak else 0.0)
    return out


def _dd_spark(equity_vals: list[float], w: int = 150, h: int = 46) -> str:
    """Underwater (drawdown) sparkline: 0% pinned at the top, the trough at the
    bottom, filled red. Derived from the equity track so it always matches the
    equity sparkline beside it."""
    if len(equity_vals) < 2:
        return ""
    dd = _dd_from_equity(equity_vals)
    lo = min(dd + [0.0])          # most negative; floor at 0 so a flat track still renders
    rng = (-lo) or 1.0
    n = len(dd)
    fx = lambda i: i / (n - 1) * (w - 2) + 1
    fy = lambda v: 2 + (h - 4) * (-v / rng)   # 0% → top, trough → bottom
    line = " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in enumerate(dd))
    area = f"1,{fy(0):.1f} " + line + f" {fx(n-1):.1f},{fy(0):.1f}"
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' style='display:block;max-width:{w}px'>"
            f"<polygon points='{area}' fill='#ef444422'/>"
            f"<polyline points='{line}' fill='none' stroke='#ef4444' stroke-width='2'/></svg>")


def _spark_pair(equity_vals: list[float], days: int = 30) -> str:
    """Equity + underwater drawdown sparklines side by side, both off the same
    trailing equity track and both labelled — the farm-card visual standard."""
    vals = equity_vals[-days:]
    if len(vals) < 2:
        return ""
    up = vals[-1] >= vals[0]
    trough = min(_dd_from_equity(vals))
    cell = lambda label, sub, svg, subc: (
        f"<div style='flex:1 1 0;min-width:0'>"
        f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
        f"<span style='color:#8b95a5;font-size:10px;text-transform:uppercase;letter-spacing:.3px'>{label}</span>"
        f"<span style='color:{subc};font-size:10px;font-weight:700'>{sub}</span></div>"
        f"<div style='margin-top:2px'>{svg}</div></div>")
    eq_sub = f"{(vals[-1]/vals[0]-1)*100:+.1f}% · {days}d"
    return ("<div style='display:flex;gap:12px'>"
            + cell("Equity", eq_sub, _mini_spark(vals, up), "#22c55e" if up else "#ef4444")
            + cell("Drawdown", f"worst {trough:.1f}%", _dd_spark(vals), "#ef4444")
            + "</div>")


def _contrib_chart(books: list[dict]) -> str:
    """Diverging horizontal bar chart of every book's standalone cumulative P&L,
    sorted best→worst, green right / red left of a zero line. Book name + current
    portfolio weight on the left, P&L value at the bar tip. Lets Steven scan in a
    couple of seconds which strategy is pulling weight and which is dragging.

    Note: pnl_cum is each book's STANDALONE track (long-horizon), not a paper-period
    attribution to the portfolio — labelled as such. Per-book equity *curves over
    time* aren't in the snapshot (only the current cumulative), so a bar of the
    standalone result is the honest 'who-won-who-dragged' view."""
    rows = sorted(books, key=lambda b: b.get("pnl_cum", 0.0), reverse=True)
    if not rows:
        return ""
    maxabs = max((abs(b.get("pnl_cum", 0.0)) for b in rows), default=0.0) * 100 or 1.0
    rowH, namew, barw, pad = 30, 132, 150, 6
    W = namew + barw + 56
    zero = namew + barw / 2
    half = barw / 2 - 4
    H = len(rows) * rowH + 6
    svg = [f"<svg viewBox='0 0 {W} {H}' width='100%' style='display:block;font-family:system-ui'>",
           f"<line x1='{zero}' y1='2' x2='{zero}' y2='{H-2}' stroke='#2a3441' stroke-width='1'/>"]
    for i, b in enumerate(rows):
        pnl = b.get("pnl_cum", 0.0) * 100
        wgt = b.get("realized_weight", 0.0) * 100
        active = b.get("activation_state", "") == "active"
        name = b.get("key", "?")
        name = name if len(name) <= 18 else name[:17] + "…"
        cy = i * rowH + rowH / 2 + 3
        L = abs(pnl) / maxabs * half
        col = "#22c55e" if pnl >= 0 else "#ef4444"
        ncol = "#e6e6e6" if active else "#8b95a5"
        if pnl >= 0:
            bar = f"<rect x='{zero:.1f}' y='{cy-7:.1f}' width='{L:.1f}' height='13' rx='2' fill='{col}'/>"
            val = f"<text x='{zero+L+4:.1f}' y='{cy+4:.1f}' fill='{col}' font-size='12' font-weight='700'>{pnl:+.0f}%</text>"
        else:
            bar = f"<rect x='{zero-L:.1f}' y='{cy-7:.1f}' width='{L:.1f}' height='13' rx='2' fill='{col}'/>"
            val = f"<text x='{zero-L-4:.1f}' y='{cy+4:.1f}' text-anchor='end' fill='{col}' font-size='12' font-weight='700'>{pnl:+.0f}%</text>"
        svg.append(
            f"<text x='{namew-pad}' y='{cy:.1f}' text-anchor='end' fill='{ncol}' font-size='12' font-weight='600'>{html_escape(name)}</text>"
            f"<text x='{namew-pad}' y='{cy+11:.1f}' text-anchor='end' fill='#6b7280' font-size='9.5'>{wgt:.0f}% weight{'' if active else ' · dormant'}</text>"
            + bar + val)
    svg.append("</svg>")
    return "".join(svg)


def _chip(label: str, value: str, col: str = "#e6e6e6") -> str:
    return (f"<div style='background:#0f141c;border-radius:9px;padding:7px 9px;flex:1 1 auto;min-width:80px'>"
            f"<div style='color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:.4px'>{label}</div>"
            f"<div style='font-size:14px;font-weight:700;color:{col};margin-top:2px'>{value}</div></div>")


def _ann_strip(ann: dict, basis: str = "") -> str:
    """Four mini-cells pinned to a card: annualised pace from the trailing
    1w / 1mo / 1y (realised return × periods/yr — not a forecast), plus realised
    YTD as context. Lets Steven spot at a glance when the short-term pace is
    running hot or cold versus the longer trend."""
    def cell(label, v, is_ytd=False):
        if v is None:
            val = "<span style='color:#6b7280;font-weight:700'>TBD</span>"
        else:
            c = "#22c55e" if v >= 0 else "#ef4444"
            suf = "" if is_ytd else "/yr"
            val = f"<span style='color:{c};font-weight:700'>{v:+,.0f}%{suf}</span>"
        return (f"<div style='flex:1 1 0;min-width:58px;background:#0f141c;border-radius:8px;"
                f"padding:6px 5px;text-align:center'>"
                f"<div style='color:#6b7280;font-size:9px;text-transform:uppercase;letter-spacing:.2px'>{label}</div>"
                f"<div style='font-size:12px;margin-top:2px'>{val}</div></div>")
    note = (f"<div style='color:#6b7280;font-size:10px;margin:7px 0 3px'>Annualised pace · realised × periods/yr · {basis}</div>"
            if basis else "")
    return (note + "<div style='display:flex;gap:5px'>"
            + cell("from 1w", ann["w"]) + cell("from 1mo", ann["mo"])
            + cell("from 1y", ann["y"]) + cell("YTD real", ann["ytd"], is_ytd=True)
            + "</div>")


def _switch_chip(leverage: float | None = None, *, round_trip_bps: float | None = None,
                 gross: float | None = None, name: str = "this bot", asset: str = "BTC",
                 last_measured: str = "") -> str:
    """A tappable switching-cost badge (% of NAV to fully exit+re-enter). Tap opens
    a panel showing the calc breakdown. Drive it either by `leverage` (farm bots,
    crypto 6bp round trip) or by an explicit `round_trip_bps` + `gross` (Freyr
    books, which carry their own per-book round-trip in the snapshot)."""
    if round_trip_bps is None:
        round_trip_bps, gross = CRYPTO_ROUND_TRIP_BPS, (leverage if leverage is not None else 1.0)
    bd = _switch_breakdown(round_trip_bps, gross if gross is not None else 1.0,
                           asset=asset, last_measured=last_measured)
    col, pct = bd["color"], bd["pct"]
    # data-* carry the numbers; onclick (with stopPropagation so it works inside a
    # card link) hands them to the shared modal.
    data = (f"data-sw-name=\"{html_escape(name)}\" data-sw-fee=\"{bd['fee_bps']:.2f}\" "
            f"data-sw-slip=\"{bd['slip_bps']:.2f}\" data-sw-side=\"{bd['per_side_bps']:.2f}\" "
            f"data-sw-rt=\"{bd['round_trip_bps']:.2f}\" data-sw-gross=\"{bd['gross']:.2f}\" "
            f"data-sw-pct=\"{pct:.3f}\" data-sw-col=\"{col}\" data-sw-asset=\"{html_escape(asset)}\" "
            f"data-sw-when=\"{html_escape(bd['last_measured'])}\"")
    return (f"<span role='button' tabindex='0' onclick='openSwitch(event,this)' {data} "
            f"style='display:inline-flex;align-items:center;gap:6px;background:#0f141c;cursor:pointer;"
            f"border:1px solid {col}40;border-radius:8px;padding:4px 10px;font-size:11px;white-space:nowrap'>"
            f"<span style='width:7px;height:7px;border-radius:50%;background:{col};display:inline-block'></span>"
            f"<span style='color:#8b95a5'>Switch cost</span>"
            f"<b style='color:{col}'>{pct:.2f}% of NAV</b>"
            f"<span style='color:#6b7280;font-weight:700'>ⓘ</span></span>")


def html_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


# Shared full-screen tap-panel for the switching-cost breakdown. Injected once per
# page that renders a switch chip. Plain-English: this is what it costs (fees +
# slippage, both ways) to fully get into or out of a position.
def _switch_modal_html() -> str:
    return """
<div id="swmodal" onclick="if(event.target===this)closeSwitch()" style="display:none;position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.66);overflow-y:auto;padding:18px">
  <div style="box-sizing:border-box;width:100%;max-width:480px;margin:24px auto;background:#151a23;border-radius:16px;padding:18px 18px 22px;border:1px solid #232b39">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:6px">
      <div style="font-size:18px;font-weight:800">Switching cost <span id="sw-name" style="color:#8b95a5;font-weight:500;font-size:14px"></span></div>
      <button onclick="closeSwitch()" style="background:#1c2230;border:none;color:#9aa4b2;font-size:20px;line-height:1;border-radius:9px;padding:4px 11px;cursor:pointer">×</button>
    </div>
    <div id="sw-head" style="font-size:30px;font-weight:800;margin:2px 0 2px"></div>
    <div style="color:#8b95a5;font-size:12.5px;margin-bottom:12px">the round-trip cost (enter + exit) to fully get into or out of this position, as a % of the position's value</div>

    <div style="color:#9aa4b2;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">How it's calculated</div>
    <table style="width:100%;border-collapse:collapse;font-size:13.5px" id="sw-rows"></table>

    <div style="background:#0f141c;border-radius:10px;padding:11px 12px;margin-top:12px;color:#cbd5e1;font-size:12.5px;line-height:1.5">
      <b style="color:#e6e6e6">Why it matters.</b> Cheap-exit bots can chase tiny edges; expensive-exit bots need a fat edge to be worth running. You can't run a 0.1% edge with 0.5% switching cost — the cost eats the trade.
    </div>

    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
      <span style="font-size:11px;color:#8b95a5"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#22c55e;margin-right:4px"></span>green &lt;0.1%</span>
      <span style="font-size:11px;color:#8b95a5"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#f59e0b;margin-right:4px"></span>amber 0.1–0.5%</span>
      <span style="font-size:11px;color:#8b95a5"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#ef4444;margin-right:4px"></span>red &gt;0.5%</span>
    </div>
    <div id="sw-when" style="color:#6b7280;font-size:11px;margin-top:10px"></div>
  </div>
</div>
<script>
function swRow(label,val,strong){return '<tr style="border-top:1px solid #1c2230"><td style="padding:7px 4px;color:#8b95a5">'+label+'</td><td style="padding:7px 4px;text-align:right;font-weight:'+(strong?800:600)+';color:'+(strong?'#e6e6e6':'#cbd5e1')+'">'+val+'</td></tr>';}
function openSwitch(ev,el){
  if(ev){ev.preventDefault();ev.stopPropagation();}
  var d=el.dataset, col=d.swCol;
  document.getElementById('sw-name').textContent='· '+d.swName;
  var head=document.getElementById('sw-head');
  head.textContent=parseFloat(d.swPct).toFixed(2)+'% of NAV'; head.style.color=col;
  var rows=swRow('Top-of-book spread / slippage',parseFloat(d.swSlip).toFixed(1)+' bps / side')
    +swRow('Fee (taker-side, blended)',parseFloat(d.swFee).toFixed(1)+' bps / side')
    +swRow('Market impact at current size','≈0 bps · paper sizes small')
    +swRow('Per side total',parseFloat(d.swSide).toFixed(1)+' bps')
    +swRow('Round trip (× 2 — enter + exit)',parseFloat(d.swRt).toFixed(1)+' bps')
    +swRow('× gross leverage',parseFloat(d.swGross).toFixed(2)+'×')
    +swRow('= Switching cost',parseFloat(d.swPct).toFixed(2)+'% of NAV',true);
  document.getElementById('sw-rows').innerHTML=rows;
  var w=d.swWhen?('Asset class: '+d.swAsset+' · last measured '+d.swWhen):('Asset class: '+d.swAsset);
  document.getElementById('sw-when').textContent=w;
  document.getElementById('swmodal').style.display='block';
}
function closeSwitch(){document.getElementById('swmodal').style.display='none';}
</script>"""


def _freyr_card(variant: str) -> str:
    snap, summ = _freyr_load(variant)
    emoji, pname, accent, sub = FREYR_META.get(variant, ("•", variant, "#3b82f6", ""))
    if not snap:
        return (f"<div style='background:#151a23;border-radius:16px;padding:16px;margin:12px 0;"
                f"border-left:5px solid {accent}'><b style='font-size:18px'>{emoji} Freyr {variant}</b>"
                f"<div style='color:#6b7280;font-size:13px;margin-top:6px'>No snapshot yet — "
                f"the paper tick writes one daily.</div></div>")
    p = snap.get("portfolio", {})
    eq = p.get("paper_equity", 1.0)
    ret = (eq - 1) * 100
    dd = p.get("current_dd", 0.0) * 100
    lev = p.get("leverage", 1.0)
    regime = (p.get("regime") or "—")
    nb_a, nb = p.get("n_books_active", 0), p.get("n_books", 0)
    esc = snap.get("escape", {})
    tier, tname = esc.get("tier", 0), esc.get("tier_name", "observe")
    ereason = esc.get("reason", "")
    kill = (summ or {}).get("kill_status", "ARMED")
    hb_ok = snap.get("heartbeat", {}).get("all_ok", False)
    date = snap.get("date", "")

    rc = "#22c55e" if ret >= 0 else "#ef4444"
    ddc = "#22c55e" if dd > -5 else ("#f59e0b" if dd > -15 else "#ef4444")
    tierc = "#22c55e" if tier == 0 else ("#f59e0b" if tier == 1 else "#ef4444")
    killc = "#22c55e" if kill == "ARMED" else "#ef4444"
    hbc, hbt = (("#22c55e", "OK") if hb_ok else ("#ef4444", "STALE"))

    track = [pt["equity"] for pt in (snap.get("model_track") or [])]
    spark = _spark_pair(track, days=30)
    mt = [(datetime.fromisoformat(pt["date"]), pt["equity"])
          for pt in (snap.get("model_track") or []) if pt.get("date")]
    fc = _ann_strip(_ann_windows(mt), basis="model track (paper is days old)")
    sw = _switch_chip(lev, name=f"Freyr {variant}", last_measured=date)
    sign = "+" if ret >= 0 else ""
    chips = "".join([
        _chip("Drawdown", f"{dd:.1f}%", ddc),
        _chip("Leverage", f"{lev:.2f}×"),
        _chip("Regime", regime.title()),
        _chip("Books", f"{nb_a}/{nb}"),
        _chip("Kill switch", kill, killc),
        _chip("Escape", f"T{tier} · {tname}", tierc),
        _chip("Heartbeat", hbt, hbc),
    ])
    return f"""
    <a href="/freyr/{variant}" style="text-decoration:none;color:inherit;display:block">
    <div style="background:#151a23;border-radius:16px;padding:18px;margin:12px 0;border-left:5px solid {accent}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
        <div>
          <div style="font-size:20px;font-weight:800">{emoji} Freyr {variant}</div>
          <div style="color:#8b95a5;font-size:12.5px;margin-top:2px">{pname} · {sub}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:26px;font-weight:800;color:{rc}">{sign}{ret:.2f}%</div>
          <div style="color:#8b95a5;font-size:12px">${eq * FREYR_NOTIONAL:,.0f} on $10k</div>
        </div>
      </div>
      <div style="margin:12px 0 4px">{spark}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px">{chips}</div>
      {fc}
      <div style="margin-top:8px">{sw}</div>
      <div style="color:#6b7280;font-size:11.5px;margin-top:9px">{ereason} · as of {date} · tap for per-book ›</div>
    </div></a>"""


def _testnet_card() -> str:
    """The 🔌 Live Testnet card — the real Hyperliquid testnet account, rendered in
    the Freyr card style and pinned to the top of the Freyr tab. Tap → /testnet."""
    snap = _testnet_load()
    accent = "#38bdf8"
    semoji, slabel, scol = _testnet_status(snap)
    if not snap:
        return (f"<a href='/testnet' style='text-decoration:none;color:inherit;display:block'>"
                f"<div style='background:#151a23;border-radius:16px;padding:18px;margin:12px 0;"
                f"border-left:5px solid {accent}'>"
                f"<div style='font-size:20px;font-weight:800'>🔌 Live Testnet</div>"
                f"<div style='color:#8b95a5;font-size:12.5px;margin-top:2px'>Real orders, fake money, paper test phase.</div>"
                f"<div style='color:#6b7280;font-size:13px;margin-top:10px'>"
                f"{semoji} {slabel} — no snapshot yet. The minute poller writes one once it connects.</div>"
                f"</div></a>")

    pv = snap.get("portfolio_value", 0.0)
    pnl_abs, pnl_pct = snap.get("pnl_24h_abs"), snap.get("pnl_24h_pct")
    lev = snap.get("leverage", 0.0)
    notional = snap.get("total_notional", 0.0)
    n_pos, n_ord = snap.get("n_positions", 0), snap.get("n_orders", 0)
    ft = snap.get("fills_today", {}) or {}
    fdate = snap.get("fetched_at", "")[:16].replace("T", " ")

    # 24h P&L — TBD until we have a row ≥24h old (history grows over time).
    if pnl_abs is None:
        pnl_html = "<span style='color:#6b7280'>TBD · building history</span>"
    else:
        pc = "#22c55e" if pnl_abs >= 0 else "#ef4444"
        pnl_html = f"<span style='color:{pc}'>{pnl_abs:+,.2f} ({pnl_pct:+.2f}%)</span>"

    # equity sparkline (7d daily closes; blank until ≥2 points)
    eq_vals = [v for _, v in (snap.get("equity_spark") or [])]
    spark = _spark_pair(eq_vals, days=7) if len(eq_vals) >= 2 else (
        "<div style='color:#6b7280;font-size:11.5px;padding:6px 0'>Equity chart builds as daily snapshots accumulate.</div>")

    pos_v = f"{n_pos} · ${notional:,.0f}" if n_pos else "0 · flat"
    fills_v = ("No fills today" if ft.get("count", 0) == 0
               else f"{ft['count']} · {ft.get('realized_pnl', 0):+,.2f}")
    fillsc = "#8b95a5" if ft.get("count", 0) == 0 else (
        "#22c55e" if ft.get("realized_pnl", 0) >= 0 else "#ef4444")

    sw = snap.get("switching_cost")
    if sw:
        sw_html = _switch_chip(round_trip_bps=sw["round_trip_bps"], gross=sw["gross"],
                               name="Live Testnet", last_measured=snap.get("fetched_at", "")[:10])
    else:
        sw_html = ("<span style='display:inline-flex;align-items:center;gap:6px;background:#0f141c;"
                   "border:1px solid #232b39;border-radius:8px;padding:4px 10px;font-size:11px'>"
                   "<span style='color:#8b95a5'>Switch cost</span><b style='color:#6b7280'>— no fills yet</b></span>")

    chips = "".join([
        _chip("Positions", pos_v),
        _chip("Open orders", str(n_ord)),
        _chip("Fills today", fills_v, fillsc),
        _chip("Leverage", f"{lev:.2f}×"),
        _chip("Status", f"{semoji} {slabel}", scol),
    ])
    pvc = "#22c55e" if (pnl_abs or 0) >= 0 else "#ef4444"
    return f"""
    <a href="/testnet" style="text-decoration:none;color:inherit;display:block">
    <div style="background:#151a23;border-radius:16px;padding:18px;margin:12px 0;border-left:5px solid {accent}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
        <div>
          <div style="font-size:20px;font-weight:800">🔌 Live Testnet</div>
          <div style="color:#8b95a5;font-size:12.5px;margin-top:2px">Real orders, fake money, paper test phase.</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:26px;font-weight:800;color:{pvc}">${pv:,.2f}</div>
          <div style="color:#8b95a5;font-size:12px">portfolio · 24h {pnl_html}</div>
        </div>
      </div>
      <div style="margin:12px 0 4px">{spark}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px">{chips}</div>
      <div style="margin-top:8px">{sw_html}</div>
      <div style="color:#6b7280;font-size:11.5px;margin-top:9px">Hyperliquid testnet · as of {fdate} UTC · tap for positions, orders &amp; fills ›</div>
    </div></a>"""


def _survivor_card(v: dict) -> str:
    up = v["profit"] >= 0
    col = "#22c55e" if up else "#ef4444"
    sign = "+" if up else ""
    ann = _ann_windows(_equity_rows(v["slug"]))
    tabc = TAB_COLORS.get(_tab_of(v), "#64748b")
    bemoji, blabel, bcol = BRACKETS.get(v["slug"], ("", "", "#64748b"))
    chip = (f"<span style='display:inline-block;background:#0f141c;color:{bcol};font-size:10.5px;"
            f"font-weight:700;padding:2px 8px;border-radius:7px;border:1px solid {bcol}33;"
            f"white-space:nowrap'>{bemoji} {blabel}</span>") if blabel else ""
    return f"""
    <a href="/bot/{v['slug']}" style="text-decoration:none;color:inherit;display:block">
    <div style="background:#151a23;border-radius:12px;padding:12px 14px;margin:8px 0;border-left:3px solid {bcol}">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <span style="font-size:15px;font-weight:600">{v['name']}
          <span style="font-size:11px;color:{tabc}">· {TAB_LABELS.get(_tab_of(v), '')}</span></span>
        {chip}
      </div>
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:6px">
        <span style="color:{col};font-weight:600;font-size:13px">{sign}{v['return_pct']:.2f}% · worst dip −{v['max_drawdown_pct']:.2f}%</span>
        <span style="font-size:15px;font-weight:700">${v['equity']:,.0f}</span>
      </div>
      <div style="margin-top:8px">{_ann_strip(ann, basis="paper track")}</div>
      <div style="margin-top:8px">{_switch_chip(v.get('leverage', 1.0), name=v['name'])}</div>
    </div></a>"""


# ── Steven's manual portfolio — the human-vs-algo tournament (My Portfolio tab) ──
# Bracket emoji by family for bots not in the curated BRACKETS map, so the full
# picker still shows a "which market is this for" lens.
TAB_EMOJI = {"grid": "⚖️", "funding": "😴", "longvol": "🔥", "premium": "📉",
             "trend": "🏃", "stack": "🔄", "convex": "🌪"}
FREYR_BENCH = {"v0.1.1": "freyr_v011", "v0.2": "freyr_v02", "v0.3": "freyr_v03"}
STEVEN_COL = "#eab308"   # gold — Steven's line on the head-to-head chart


def _gate_active_farm(state: str) -> bool:
    """Best-effort 'is this farm bot deploying capital right now?' from its state
    string. In cash / trend-stopped = inactive; otherwise it's holding/working."""
    s = (state or "").lower()
    return not ("in cash" in s or "trend-stopped" in s)


def _bot_universe() -> tuple[dict, list]:
    """Unified roster across Freyr + farm + specialists for Steven's picker.
      universe[key] = {equity, active, name}     # drives the NAV sim
      meta = ordered display dicts {key,name,emoji,bracket,bcol,tab,
              equity,return_pct,dd,gate_active,leverage}
    Freyr keys are namespaced 'freyr:<variant>'; farm bots use their slug."""
    universe, meta = {}, []
    for variant in FREYR_VARIANTS:
        snap, _ = _freyr_load(variant)
        if not snap:
            continue
        p = snap.get("portfolio", {})
        eq = p.get("paper_equity", 1.0) * FREYR_NOTIONAL
        active = p.get("n_books_active", 0) > 0
        emoji, pname, accent, _ = FREYR_META[variant]
        key, name = f"freyr:{variant}", f"Freyr {variant}"
        universe[key] = {"equity": eq, "active": active, "name": name}
        meta.append({"key": key, "name": name, "emoji": emoji, "bracket": pname,
                     "bcol": accent, "tab": "freyr", "equity": eq,
                     "return_pct": (eq / FREYR_NOTIONAL - 1) * 100,
                     "dd": p.get("current_dd", 0.0) * 100, "gate_active": active,
                     "leverage": p.get("leverage", 1.0)})
    for v in (_load() or {}).get("variants", []):
        slug, tab = v["slug"], _tab_of(v)
        active = _gate_active_farm(v.get("state", ""))
        bem, blab, bcol = BRACKETS.get(slug, ("", "", ""))
        emoji = bem or TAB_EMOJI.get(tab, "•")
        bracket = blab or TAB_LABELS.get(tab, tab)
        col = bcol or TAB_COLORS.get(tab, "#64748b")
        universe[slug] = {"equity": v["equity"], "active": active, "name": v["name"]}
        meta.append({"key": slug, "name": v["name"], "emoji": emoji, "bracket": bracket,
                     "bcol": col, "tab": tab, "equity": v["equity"],
                     "return_pct": v["return_pct"], "dd": -v.get("max_drawdown_pct", 0.0),
                     "gate_active": active, "leverage": v.get("leverage", 1.0)})
    return universe, meta


def _tick_steven(universe: dict | None = None):
    """Advance Steven's paper portfolio one farm tick (idempotent per farm stamp)
    and return its snapshot. Records the three Freyr variants alongside so the
    head-to-head chart is aligned from launch. Never raises into a request."""
    data = _load() or {}
    if universe is None:
        universe, _ = _bot_universe()
    bench = {col: universe[f"freyr:{var}"]["equity"]
             for var, col in FREYR_BENCH.items() if f"freyr:{var}" in universe}
    try:
        return sp.tick(universe, data.get("btc_price", 0.0), data.get("updated", ""), bench)
    except Exception:
        try:
            return sp.snapshot(universe=universe)
        except Exception:
            return None


def _series_overlay(series: list[tuple[str, str, list]], start: float) -> str:
    """Generic multi-line account-value chart. series = [(name, colour, rows)],
    rows = [(datetime, equity)] ascending. Shared time + $ axes."""
    series = [(n, c, r) for n, c, r in series if len(r) >= 2]
    if not series:
        return ("<div style='color:#6b7280;padding:26px 0;text-align:center'>"
                "Your equity curve fills in from launch — it grows each hour as the "
                "farm ticks. Check back soon.</div>")
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
        wide = "3" if name.startswith("👤") else "2"
        polys.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{wide}"/>')
        legend.append(f"<span style='color:{col};font-size:12px;white-space:nowrap'>● {name}</span>")
    base_y = fy(start)
    fmt = "%d %b %H:%M" if tspan < 3 * 86400 else "%d %b %y"
    svg = (f'<svg viewBox="0 0 {w} {h}" width="100%" style="background:#0f141c;border-radius:10px">'
           f'<line x1="{padL}" y1="{base_y:.1f}" x2="{w - padR}" y2="{base_y:.1f}" '
           f'stroke="#3a4253" stroke-dasharray="4 4"/>' + "".join(polys) +
           f'<text x="{padL - 6}" y="{padT + 4}" fill="#6b7280" font-size="11" text-anchor="end">${emax:,.0f}</text>'
           f'<text x="{padL - 6}" y="{h - padB + 3:.0f}" fill="#6b7280" font-size="11" text-anchor="end">${emin:,.0f}</text>'
           f'<text x="{w - padR}" y="{base_y - 4:.1f}" fill="#8b95a5" font-size="10.5" text-anchor="end">start ${start:,.0f}</text>'
           f'<text x="{padL}" y="{h - 17}" fill="#6b7280" font-size="11">{tmin.strftime(fmt)}</text>'
           f'<text x="{w - padR}" y="{h - 17}" fill="#6b7280" font-size="11" text-anchor="end">{tmax.strftime(fmt)}</text>'
           f'<text x="{(padL + w - padR) / 2:.0f}" y="{h - 4}" fill="#8b95a5" font-size="11" '
           f'text-anchor="middle">You vs Freyr — from launch (paper)</text></svg>')
    return svg + f"<div style='display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 2px'>{''.join(legend)}</div>"


def _portfolio_overlay() -> str:
    """Steven's NAV vs the three Freyr profiles, aligned from t0 (read off the
    tournament equity CSV the tick writes)."""
    bench = sp.benchmark_series()
    names = {"equity": ("👤 You", STEVEN_COL)}
    for var, col in FREYR_BENCH.items():
        emoji, _pn, accent, _ = FREYR_META[var]
        names[col] = (f"{emoji} Freyr {var}", accent)
    series = [(names[c][0], names[c][1], rows) for c, rows in bench.items() if c in names]
    series.sort(key=lambda s: 0 if s[0].startswith("👤") else 1)
    return _series_overlay(series, sp.INITIAL_NAV)


def _combined_leaderboard() -> str:
    """Freyr variants + farm survivors, head-to-head, by annualised pace.
    Freyr pace = model-track CAGR (paper history is only days old); survivor pace =
    30-day annualised from the paper equity curve. Mixed bases — labelled as such."""
    rows = []
    for variant in FREYR_VARIANTS:
        snap, _ = _freyr_load(variant)
        if not snap:
            continue
        p = snap.get("portfolio", {})
        emoji, _pn, accent, _ = FREYR_META[variant]
        rows.append({"pace": p.get("cagr", 0.0) * 100, "name": f"{emoji} Freyr {variant}",
                     "ret": (p.get("paper_equity", 1.0) - 1) * 100, "dd": p.get("current_dd", 0.0) * 100,
                     "accent": accent, "href": f"/freyr/{variant}"})
    for slug in SURVIVORS:
        v = _variant_by_slug(slug)
        if not v:
            continue
        rows.append({"pace": _annualised(slug)["monthly"], "name": v["name"],
                     "ret": v["return_pct"], "dd": -v["max_drawdown_pct"],
                     "accent": TAB_COLORS.get(_tab_of(v), "#64748b"), "href": f"/bot/{v['slug']}"})
    # Steven's manual portfolio races the algos head-to-head.
    try:
        snap = sp.snapshot()
        if snap["n_bots"] > 0:
            srows = sp.equity_rows()
            pace = _ann_windows(srows)["mo"] if len(srows) >= 2 else None
            rows.append({"pace": pace, "name": "👤 Steven's Portfolio",
                         "ret": snap["return_pct"], "dd": snap["drawdown_pct"],
                         "accent": STEVEN_COL, "href": "/#portfolio"})
    except Exception:
        pass
    rows.sort(key=lambda r: (r["pace"] is None, -(r["pace"] or 0)))
    out = []
    for i, r in enumerate(rows, 1):
        rc = "#22c55e" if r["ret"] >= 0 else "#ef4444"
        pace = "—" if r["pace"] is None else f"{r['pace']:+,.0f}%/yr"
        pacec = "#6b7280" if r["pace"] is None else ("#22c55e" if r["pace"] >= 0 else "#ef4444")
        out.append(
            f"<a href='{r['href']}' style='text-decoration:none;color:inherit;display:flex;align-items:center;"
            f"gap:10px;background:#151a23;border-left:3px solid {r['accent']};border-radius:10px;"
            f"padding:10px 12px;margin:6px 0'>"
            f"<span style='color:#6b7280;font-weight:700;width:18px'>{i}</span>"
            f"<span style='flex:1;font-size:14px;font-weight:600'>{r['name']}</span>"
            f"<span style='color:{rc};font-size:13px;font-weight:600;width:70px;text-align:right'>{r['ret']:+.2f}%</span>"
            f"<span style='color:{pacec};font-size:13px;font-weight:700;width:84px;text-align:right'>{pace}</span>"
            f"</a>")
    return "".join(out)


def _portfolio_row(m: dict, snap_bot: dict | None) -> str:
    """One bot row in the picker. snap_bot is non-None when the bot is in Steven's
    portfolio (carries override + slice value); None means it's still available."""
    chip = (f"<span style='display:inline-block;background:#0f141c;color:{m['bcol']};font-size:10px;"
            f"font-weight:700;padding:1px 7px;border-radius:6px;border:1px solid {m['bcol']}33;"
            f"white-space:nowrap'>{m['emoji']} {m['bracket']}</span>")
    gate = ("<span style='color:#22c55e'>● firing</span>" if m["gate_active"]
            else "<span style='color:#6b7280'>○ flat</span>")
    rc = "#22c55e" if m["return_pct"] >= 0 else "#ef4444"
    head = (f"<div style='display:flex;justify-content:space-between;align-items:center;gap:8px'>"
            f"<span style='font-size:14px;font-weight:600'>{m['name']}</span>{chip}</div>"
            f"<div style='display:flex;justify-content:space-between;align-items:baseline;margin-top:4px;font-size:11.5px'>"
            f"<span style='color:#8b95a5'>auto-gate: {gate}</span>"
            f"<span style='color:{rc};font-weight:600'>{m['return_pct']:+.2f}% · ${m['equity']:,.0f}</span></div>")

    if snap_bot is None:
        body = (f"<button onclick=\"pset('{m['key']}','ADD')\" style='margin-top:8px;width:100%;padding:7px;"
                f"border:1px dashed #2d3850;border-radius:8px;background:#10151e;color:#60a5fa;"
                f"font-size:12px;font-weight:600;font-family:inherit;cursor:pointer'>+ Add to my portfolio</button>")
        bar = "#1c2230"
    else:
        cur = snap_bot["override"]
        seg = []
        for opt, col in [("ON", "#22c55e"), ("AUTO", "#3b82f6"), ("OFF", "#ef4444")]:
            on = cur == opt
            seg.append(f"<button onclick=\"pset('{m['key']}','{opt}')\" style='flex:1;padding:6px 2px;border:none;"
                       f"border-radius:7px;background:{col if on else '#1c2230'};color:{'#fff' if on else '#9aa4b2'};"
                       f"font-size:11px;font-weight:700;font-family:inherit;cursor:pointer'>{opt}</button>")
        eff = ("<span style='color:#22c55e'>in market</span>" if snap_bot["active"]
               else "<span style='color:#6b7280'>parked (cash)</span>")
        body = (f"<div style='display:flex;gap:4px;margin-top:8px'>{''.join(seg)}</div>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;margin-top:7px;font-size:11.5px'>"
                f"<span style='color:#8b95a5'>now: {eff} · ${snap_bot['value']:,.0f}</span>"
                f"<button onclick=\"pset('{m['key']}','REMOVE')\" style='border:none;background:none;color:#ef4444;"
                f"font-size:11.5px;cursor:pointer;font-family:inherit'>✕ remove</button></div>")
        bar = STEVEN_COL
    return (f"<div style='background:#151a23;border-left:3px solid {bar};border-radius:11px;"
            f"padding:11px 13px;margin:7px 0'>{head}{body}</div>")


def _steven_panel(snap: dict, meta: list) -> str:
    """The 'My Portfolio' tab — Steven hand-picks bots and races the algos."""
    by_key = {b["key"]: b for b in snap["bots"]}
    nav, ret, dd = snap["nav"], snap["return_pct"], snap["drawdown_pct"]
    rc = "#22c55e" if ret >= 0 else "#ef4444"
    ddc = "#22c55e" if dd > -5 else ("#f59e0b" if dd > -15 else "#ef4444")
    pace = _ann_strip(_ann_windows(sp.equity_rows()), basis="your live track")
    summary = (
        f"<div style='background:#151a23;border-radius:16px;padding:16px;margin:6px 0 12px;border-left:5px solid {STEVEN_COL}'>"
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start'>"
        f"<div><div style='font-size:18px;font-weight:800'>👤 Steven's Portfolio</div>"
        f"<div style='color:#8b95a5;font-size:12px;margin-top:2px'>{snap['n_active']}/{snap['n_bots']} bots in market now · you vs the algos</div></div>"
        f"<div style='text-align:right'><div style='font-size:24px;font-weight:800;color:{rc}'>{ret:+.2f}%</div>"
        f"<div style='color:#8b95a5;font-size:12px'>${nav:,.0f} on $10k</div></div></div>"
        f"<div style='display:flex;gap:6px;margin-top:10px'>"
        + _chip("Drawdown", f"{dd:.1f}%", ddc) + _chip("In market", f"{snap['n_active']}/{snap['n_bots']}")
        + _chip("Peak", f"${snap['peak']:,.0f}") + "</div>"
        f"<div style='margin-top:9px'>{pace}</div></div>")

    chart = _portfolio_overlay()
    included = [m for m in meta if m["key"] in by_key]
    available = [m for m in meta if m["key"] not in by_key]
    inc_html = "".join(_portfolio_row(m, by_key[m["key"]]) for m in included)
    avail_html = "".join(_portfolio_row(m, None) for m in available)
    if not included:
        inc_html = ("<div style='color:#6b7280;font-size:12.5px;padding:10px 2px'>"
                    "No bots yet — add some below, then set each to <b style='color:#22c55e'>ON</b> "
                    "(force trade), <b style='color:#ef4444'>OFF</b> (park in cash) or "
                    "<b style='color:#3b82f6'>AUTO</b> (follow the bot's own gate).</div>")

    return f"""
    <div style="color:#8b95a5;font-size:12px;margin:2px 0 4px">
      Your hand-picked book vs the Freyr auto-portfolios. Pick bots, then call each one:
      <b style="color:#22c55e">ON</b> = force it to trade, <b style="color:#ef4444">OFF</b> = park it in cash,
      <b style="color:#3b82f6">AUTO</b> = defer to the bot's own gate. Every flip is logged. Starts at $10k, like the farm.
    </div>
    {summary}
    <div style="margin:6px 0 10px">{chart}</div>
    <div style="font-size:13px;font-weight:700;color:#e6e6e6;margin:14px 0 2px">Your portfolio · {snap['n_bots']} picked</div>
    {inc_html}
    <div style="font-size:13px;font-weight:700;color:#e6e6e6;margin:16px 0 2px">Add bots · {len(available)} available</div>
    <div style="color:#6b7280;font-size:11px;margin-bottom:2px">All bots across Freyr + farm + specialists.</div>
    {avail_html}"""


def _home_page() -> str:
    data = _load() or {}
    btc = data.get("btc_price", 0)
    updated = data.get("updated", "")[:16].replace("T", " ")
    n_all = len(data.get("variants", []))

    spark = ""
    wk = _btc_history("1W", allow_fetch=False)
    if len(wk) >= 2:
        spark = ("<div style='width:110px;flex:0 0 auto'>"
                 + _btc_chart_svg(wk, "1W", wk[-1][1] >= wk[0][1], w=110, h=40, mini=True) + "</div>")
    btc_banner = (
        "<a href='/btc' style='display:flex;align-items:center;gap:12px;text-decoration:none;"
        "background:#11203a;border:1px solid #1d3a66;border-radius:12px;padding:12px 14px;margin-bottom:14px'>"
        "<div style='flex:1'>"
        "<div style='color:#8b95a5;font-size:12px'>₿ Bitcoin price · tap for full chart</div>"
        f"<div style='font-size:23px;font-weight:800;color:#e6e6e6'>${btc:,.0f} "
        "<span style='font-size:13px;color:#60a5fa;font-weight:600'>1W·1M·1Y·5Y ›</span></div>"
        f"</div>{spark}</a>")

    freyr_cards = _testnet_card() + "".join(_freyr_card(v) for v in FREYR_VARIANTS)
    surv_cards = "".join(_survivor_card(v) for v in (_variant_by_slug(s) for s in SURVIVORS) if v)
    combined = _combined_leaderboard()
    universe, meta = _bot_universe()
    snap = _tick_steven(universe) or sp.snapshot(universe=universe)
    portfolio_html = _steven_panel(snap, meta)
    mine_n = snap["n_bots"]
    freyr_n, farm_n = len(FREYR_VARIANTS), len(SURVIVORS)
    board_n = freyr_n + farm_n

    # Leaderboard is now the always-visible HERO at the top of the page (Steven's
    # call — it's the most important view, so it's no longer hidden behind a tab).
    # The three detail panels (Freyr / Farm / Mine) are rendered below it and JS
    # toggles visibility so swapping is instant (no round-trip). The active tab is
    # kept in the URL #hash so the 60s soft refresh (location.reload, which
    # preserves the hash) lands the user back on the tab they were reading. A stale
    # '#leaderboard' hash from an old bookmark harmlessly falls back to 'freyr'.
    tab_js = """<script>
var TABS=['freyr','farm','portfolio'];
function showTab(name){
  TABS.forEach(function(t){
    var p=document.getElementById('panel-'+t), b=document.getElementById('tab-'+t);
    if(!p||!b)return;
    p.style.display=(t===name)?'block':'none';
    b.style.background=(t===name)?'#2563eb':'#1c2230';
    b.style.color=(t===name)?'#fff':'#9aa4b2';
  });
  if(location.hash!=='#'+name)history.replaceState(null,'','#'+name);
}
function pset(bot,action){
  fetch('/portfolio/set?bot='+encodeURIComponent(bot)+'&action='+action,{method:'POST'})
   .then(function(){location.reload();})
   .catch(function(){location.reload();});
}
(function(){
  var h=(location.hash||'').replace('#','');
  if(TABS.indexOf(h)<0)h='freyr';
  showTab(h);
  setTimeout(function(){location.reload();},60000);
})();
</script>"""

    btnbase = ("flex:1 1 0;min-width:0;padding:12px 2px;border:none;border-radius:11px;font-family:inherit;"
               "font-size:12.5px;font-weight:700;cursor:pointer;white-space:nowrap")
    cnt = "<span style='opacity:.65;font-weight:600'>"

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Banksia Springs — Live Bots</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:680px;margin:auto">
  <h2 style="margin:0 0 2px">🌱 Banksia Springs — Live Bots</h2>
  <div style="color:#8b95a5;font-size:13.5px;margin-bottom:12px">
    Freyr ensemble (live paper track to the 2026-06-30 deployment) + BTC farm survivors · all pretend money
  </div>
  {btc_banner}

  <div style="background:#10151e;border:1px solid #1d3a66;border-radius:14px;padding:13px 13px 6px;margin-bottom:12px">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:2px">
      <span style="font-size:17px;font-weight:800">🏆 Leaderboard</span>
      <span style="color:#6b7280;font-size:11.5px">{board_n} + yours · annualised pace</span>
    </div>
    <div style="color:#8b95a5;font-size:11.5px;margin-bottom:8px">
      Everyone head-to-head: Freyr ensemble &amp; specialists, farm survivors, and your picks.
      Freyr = model-track CAGR; survivors = 30-day paper pace. <a href="/leaderboard" style="color:#60a5fa;text-decoration:none">full sortable board ›</a>
    </div>
    <div style="max-height:340px;overflow-y:auto;-webkit-overflow-scrolling:touch">{combined}</div>
  </div>

  <div id="tabbar" style="position:sticky;top:0;z-index:30;background:#0b0e14;display:flex;gap:6px;padding:10px 0;margin-bottom:8px;border-bottom:1px solid #161c27">
    <button id="tab-freyr" onclick="showTab('freyr')" style="{btnbase};background:#2563eb;color:#fff">⚡ Freyr {cnt}· {freyr_n}</span></button>
    <button id="tab-farm" onclick="showTab('farm')" style="{btnbase};background:#1c2230;color:#9aa4b2">🌾 Farm {cnt}· {farm_n}</span></button>
    <button id="tab-portfolio" onclick="showTab('portfolio')" style="{btnbase};background:#1c2230;color:#9aa4b2">👤 Mine {cnt}· {mine_n}</span></button>
  </div>

  <div id="panel-freyr">
    <div style="color:#8b95a5;font-size:12px;margin:2px 0 2px">
      Multi-book system with an escape-tier risk governor &amp; portfolio kill-switch · 3 risk profiles, 12 books each.
      Each card carries its annualised pace and switching cost. Tap for the per-book breakdown.
    </div>
    {freyr_cards}
  </div>

  <div id="panel-farm" style="display:none">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin:2px 0 2px">
      <span style="color:#8b95a5;font-size:12px">{farm_n} survivors by <b>specialist bracket</b> — which bot for which market.</span>
      <a href="/farm" style="color:#60a5fa;text-decoration:none;font-size:12.5px">all {n_all} ›</a>
    </div>
    <div style="color:#6b7280;font-size:11.5px;margin-bottom:4px">
      <span style="color:#f59e0b">⚠️ leveraged = paper-only.</span> Green switch cost = cheap-exit (can take narrow edges).
    </div>
    {surv_cards}
  </div>

  <div id="panel-portfolio" style="display:none">
    {portfolio_html}
  </div>

  <p style="color:#6b7280;font-size:12px;margin-top:18px">
    Everything here is paper (pretend money). Updated {updated} UTC · refreshes each minute.</p>
  {_switch_modal_html()}
  {tab_js}
</body></html>"""


# One-line plain-English purpose for each Freyr book (keyed by snapshot book key).
FREYR_BOOK_DESC = {
    "ts_momentum_qqq": "Rides Nasdaq (QQQ) momentum — long while it trends up, flat when it rolls over.",
    "atr_breakout_qqq": "Buys Nasdaq breakouts past a volatility band; rides the move, stops out on reversal.",
    "ts_momentum_btc": "Rides Bitcoin momentum — directional trend-follow on BTC.",
    "atr_breakout_btc": "Buys Bitcoin breakouts past a volatility band; trend-capture with a vol stop.",
    "breakout_specialist": "Dedicated breakout-capture — only fires on a confirmed range break.",
    "dxy_momentum": "Trades the US-dollar index trend (DXY) as a macro diversifier.",
    "basket_rs": "Relative-strength rotation — holds the strongest names, drops the weak.",
    "funding_carry": "Market-neutral carry — collects perp funding with no directional bet. The safe floor.",
    "tail_hedge": "Crash insurance — bleeds a little in calm, pays off big in a crash. The portfolio airbag.",
    "crash_short": "Short-side crash play — profits when the market falls hard.",
    "infinity_grid": "Buy-low/sell-high grid that harvests sideways chop.",
    "panic_fade": "Mean-reversion — fades panic spikes, betting overreactions snap back.",
}


def _freyr_events(bot: str, n: int = 5) -> list[dict]:
    """Last n events for a standalone specialist bot, newest first. Events live in
    ~/freyr/paper/events/<bot>/<date>.jsonl (one file per simulated date)."""
    base = Path(f"/Users/openclaw/Documents/freyr/paper/events/{bot}")
    try:
        files = sorted(base.glob("*.jsonl"))
    except Exception:
        return []
    evs: list[dict] = []
    for f in reversed(files):
        try:
            lines = [ln for ln in f.read_text().splitlines() if ln.strip()]
        except Exception:
            continue
        for ln in reversed(lines):
            try:
                evs.append(json.loads(ln))
            except Exception:
                continue
            if len(evs) >= n:
                return evs
    return evs


def _events_html(events: list[dict]) -> str:
    """Compact last-N event list (newest first) for a specialist bot."""
    if not events:
        return ("<div style='color:#6b7280;font-size:12px;padding:6px 2px'>No events logged yet — "
                "this book writes one each time it arms, sizes, or escapes.</div>")
    rows = []
    for e in events:
        when = e.get("date", e.get("ts", "")[:10])
        ev = (e.get("event") or "event").replace("_", " ")
        why = (e.get("gate", {}) or {}).get("why", "") or (e.get("escape", {}) or {}).get("policy", "")
        sz = e.get("size", {}) or {}
        before, after = sz.get("before"), sz.get("after")
        szc = ""
        if before is not None and after is not None:
            arr = "▲" if after > before else ("▼" if after < before else "→")
            szc = f" · size {before:.3f}{arr}{after:.3f}"
        rows.append(
            f"<div style='border-top:1px solid #1c2230;padding:7px 2px'>"
            f"<div style='font-size:12.5px'><b>{when}</b> · <span style='color:#a78bfa'>{ev}</span>{szc}</div>"
            f"<div style='color:#8b95a5;font-size:11.5px;margin-top:1px'>{html_escape(why)}</div></div>")
    return "".join(rows)


def _book_falsification(b: dict) -> tuple[str, str, str]:
    """Three-state falsification status from the book's backtest 95% CI vs today's
    realised P&L: untested / validated / flagged. (label, colour, plain explanation)."""
    ci = b.get("backtest_ci95")
    if not ci:
        return ("active · not yet tested", "#6b7280",
                "No falsification band computed for this book yet — it runs, but live hasn't been "
                "scored against backtest.")
    band = f"{ci[0] * 100:+.2f}%…{ci[1] * 100:+.2f}%"
    if b.get("pnl_day_in_ci", True):
        return ("active · validated", "#22c55e",
                f"Today's P&amp;L sits inside its backtest 95% band ({band}) — behaving as modelled.")
    return ("flagged for revision", "#f59e0b",
            f"Today's P&amp;L fell OUTSIDE its backtest 95% band ({band}) — live is diverging from backtest.")


def _book_panel(b: dict, snap_date: str) -> str:
    """Hidden detail panel for one Freyr portfolio book — shown by openBook()."""
    key = b.get("key", "?")
    pretty = key.replace("_", " ").title()
    cat = b.get("category", "")
    tier = b.get("tier", "?")
    desc = FREYR_BOOK_DESC.get(key, f"A {cat} book.")
    st = b.get("activation_state", "—")
    stc = "#22c55e" if st == "active" else ("#6b7280" if st == "dormant" else "#f59e0b")
    armed = b.get("armed", True)
    weight = b.get("realized_weight", 0) * 100
    pnl = b.get("pnl_cum", 0) * 100
    pday = b.get("pnl_day", 0) * 100
    pc = "#22c55e" if pnl >= 0 else "#ef4444"
    sharpe = b.get("standalone_sharpe", 0)
    bdd = b.get("book_dd", 0) * 100

    state_chips = "".join([
        _chip("State", f"{st}{'' if armed else ' ⨯'}", stc),
        _chip("Size (of portfolio)", f"{weight:.1f}%"),
        _chip("P&amp;L contribution", f"{pnl:+.1f}%", pc),
        _chip("Standalone Sharpe", f"{sharpe:.2f}"),
        _chip("Book drawdown", f"{bdd:.1f}%"),
        _chip("Today", f"{pday:+.2f}%", "#22c55e" if pday >= 0 else "#ef4444"),
    ])

    # Switching — drive the shared switch modal off this book's own round-trip cost.
    sw = b.get("switching", {}) or {}
    rt = sw.get("round_trip_cost_bps", 3.0)
    is_btc = "btc" in key
    sw_chip = _switch_chip(round_trip_bps=rt, gross=b.get("own_gross", 1.0),
                           name=pretty, asset="BTC" if is_btc else "ETF / equity",
                           last_measured=b.get("vol_last_updated", snap_date))

    # Rules — the book's per-book switching/hysteresis registry params (the actual
    # rules that govern it, per the rule-registry principle: per-book, not global).
    exitr = sw.get("exit_strategy", "hard_close").replace("_", " ")
    dwell = sw.get("min_dwell_bars", 0)
    cool = sw.get("cool_down_bars", 0)
    rules_html = (
        "<div style='color:#cbd5e1;font-size:12.5px;line-height:1.6'>"
        f"<b>Round-trip cost</b> {rt:.1f} bps · <b>min dwell</b> {dwell} bars · "
        f"<b>cool-down</b> {cool} bars · <b>exit</b> {exitr}"
        "<div style='color:#6b7280;font-size:11px;margin-top:4px'>Per-book registry overrides "
        "(rules.registry) — not a global law. Dwell/cool-down stop a one-bar regime flicker "
        "whipping the book on and off.</div></div>")

    flabel, fcol, fwhy = _book_falsification(b)

    return f"""<div id="book-{key}" class="bookpanel" style="display:none">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:4px">
        <div><div style="font-size:18px;font-weight:800">{pretty}</div>
          <div style="color:#8b95a5;font-size:12px">{cat} · tier {tier}</div></div>
        <button onclick="closeBook()" style="background:#1c2230;border:none;color:#9aa4b2;font-size:20px;line-height:1;border-radius:9px;padding:4px 11px;cursor:pointer">×</button>
      </div>
      <div style="color:#cbd5e1;font-size:13px;margin-bottom:12px">{desc}</div>

      <div style="display:flex;flex-wrap:wrap;gap:6px">{state_chips}</div>

      <div style="color:#9aa4b2;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin:14px 0 5px">Annualised pace</div>
      <div style="color:#8b95a5;font-size:12.5px">Cumulative since paper start <b style="color:{pc}">{pnl:+.1f}%</b> · today {pday:+.2f}%. <span style="color:#6b7280">Windowed 1w/1mo/1y pace isn't tracked per-book yet — the snapshot carries cumulative P&amp;L + Sharpe only.</span></div>

      <div style="color:#9aa4b2;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin:14px 0 5px">Switching cost</div>
      <div>{sw_chip}</div>

      <div style="color:#9aa4b2;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin:14px 0 5px">Recent events</div>
      <div style="color:#6b7280;font-size:12px">No per-book event log yet — the portfolio writes fills at the ensemble level. Standalone specialist bots (Surtr 🔥, Bull, Calm, Chop) carry their own event log.</div>

      <div style="color:#9aa4b2;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin:14px 0 5px">Rules</div>
      {rules_html}

      <div style="color:#9aa4b2;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin:14px 0 5px">Falsification status</div>
      <div style="display:inline-flex;align-items:center;gap:7px;background:#0f141c;border:1px solid {fcol}40;border-radius:8px;padding:5px 11px">
        <span style="width:8px;height:8px;border-radius:50%;background:{fcol}"></span>
        <b style="color:{fcol};font-size:12.5px">{flabel}</b></div>
      <div style="color:#8b95a5;font-size:12px;margin-top:6px;line-height:1.5">{fwhy}</div>
    </div>"""


def _book_modal_html(books: list[dict], snap_date: str) -> str:
    """Overlay holding every book's hidden panel; openBook(key) reveals one."""
    panels = "".join(_book_panel(b, snap_date) for b in books)
    return f"""
<div id="bookmodal" onclick="if(event.target===this)closeBook()" style="display:none;position:fixed;inset:0;z-index:150;background:rgba(0,0,0,.66);overflow-y:auto;padding:18px">
  <div style="box-sizing:border-box;width:100%;max-width:520px;margin:18px auto;background:#151a23;border-radius:16px;padding:18px;border:1px solid #232b39">{panels}</div>
</div>
<script>
function openBook(key){{
  document.querySelectorAll('.bookpanel').forEach(function(p){{p.style.display='none';}});
  var el=document.getElementById('book-'+key); if(!el)return;
  el.style.display='block';
  document.getElementById('bookmodal').style.display='block';
}}
function closeBook(){{document.getElementById('bookmodal').style.display='none';}}
</script>"""


def _freyr_detail_page(variant: str) -> str:
    snap, summ = _freyr_load(variant)
    emoji, pname, accent, sub = FREYR_META.get(variant, ("•", variant, "#3b82f6", ""))
    if not snap:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                f"<p>No snapshot for Freyr '{variant}'.</p>"
                "<a href='/' style='color:#60a5fa'>← back</a></body>")
    p = snap.get("portfolio", {})
    esc = snap.get("escape", {})
    eq = p.get("paper_equity", 1.0)
    ret = (eq - 1) * 100
    rc = "#22c55e" if ret >= 0 else "#ef4444"
    track = [pt["equity"] for pt in (snap.get("model_track") or [])[-60:]]
    spark = _mini_spark(track, track[-1] >= track[0] if len(track) >= 2 else True, w=620, h=120)
    dd_spark = _dd_spark(track, w=620, h=70)

    def stat(label, value, c="#e6e6e6"):
        return (f"<div style='background:#151a23;border-radius:10px;padding:10px 8px;text-align:center;flex:1 1 28%;min-width:90px'>"
                f"<div style='color:#8b95a5;font-size:11px'>{label}</div>"
                f"<div style='font-size:16px;font-weight:700;color:{c};margin-top:2px'>{value}</div></div>")

    kill = (summ or {}).get("kill_status", "ARMED")
    killc = "#22c55e" if kill == "ARMED" else "#ef4444"
    tier = esc.get("tier", 0)
    tierc = "#22c55e" if tier == 0 else ("#f59e0b" if tier == 1 else "#ef4444")
    stats = "".join([
        stat("Equity (on $10k)", f"${eq * FREYR_NOTIONAL:,.0f}", rc),
        stat("Return", f"{ret:+.2f}%", rc),
        stat("Drawdown", f"{p.get('current_dd', 0) * 100:.1f}%"),
        stat("Leverage", f"{p.get('leverage', 1.0):.2f}×"),
        stat("Regime", (p.get("regime") or "—").title()),
        stat("Sharpe", f"{p.get('sharpe', 0):.2f}"),
        stat("Model CAGR", f"{p.get('cagr', 0) * 100:.1f}%"),
        stat("Kill switch", kill, killc),
        stat("Escape", f"T{tier} {esc.get('tier_name', '')}", tierc),
    ])

    # Per-book breakdown
    books = snap.get("books", []) or []
    book_rows = []
    for b in sorted(books, key=lambda x: x.get("realized_weight", 0), reverse=True):
        st = b.get("activation_state", "—")
        stc = "#22c55e" if st == "active" else ("#6b7280" if st == "dormant" else "#f59e0b")
        pnl = b.get("pnl_cum", 0) * 100
        pc = "#22c55e" if pnl >= 0 else "#ef4444"
        bdd = b.get("book_dd", 0) * 100
        armed = b.get("armed", True)
        armc = "#22c55e" if armed else "#ef4444"
        wgt = b.get("realized_weight", 0) * 100
        shp = b.get("standalone_sharpe", 0)
        book_rows.append(
            f"<tr onclick=\"openBook('{b.get('key', '')}')\" style='border-top:1px solid #1c2230;cursor:pointer'>"
            f"<td data-v='{html_escape(b.get('key','?'))}' style='padding:8px 6px;font-weight:600'>{b.get('key', '?')}"
            f"<div style='color:#6b7280;font-size:11px'>{b.get('category', '')} · tier {b.get('tier', '?')}</div></td>"
            f"<td data-v='{wgt:.4f}' style='padding:8px 6px;text-align:right'>{wgt:.1f}%</td>"
            f"<td data-v='{pnl:.4f}' style='padding:8px 6px;text-align:right;color:{pc};font-weight:600'>{pnl:+.1f}%</td>"
            f"<td data-v='{shp:.4f}' style='padding:8px 6px;text-align:right'>{shp:.2f}</td>"
            f"<td data-v='{bdd:.4f}' style='padding:8px 6px;text-align:right'>{bdd:.1f}%</td>"
            f"<td data-v='{st}' style='padding:8px 6px;text-align:center;color:{stc};font-size:12px'>{st}"
            f"{'' if armed else ' <span style=color:#ef4444>⨯</span>'}"
            f" <span style='color:#6b7280'>›</span></td>"
            f"</tr>")
    def _th(label, i, align, num=True):
        return (f"<th onclick=\"sortFB({i},{'1' if num else '0'})\" "
                f"style='padding:6px;text-align:{align};cursor:pointer;user-select:none;white-space:nowrap'>"
                f"{label}<span style='color:#475569'> ⇅</span></th>")
    book_table = (
        "<div style='overflow-x:auto;-webkit-overflow-scrolling:touch'>"
        "<table id='fbtbl' style='width:100%;border-collapse:collapse;font-size:13px;margin-top:8px;min-width:340px'>"
        "<thead><tr style='color:#9aa4b2;font-size:11px;text-align:left'>"
        + _th("Book", 0, "left", num=False) + _th("Weight", 1, "right")
        + _th("Cum P&amp;L", 2, "right") + _th("Sharpe", 3, "right")
        + _th("Book DD", 4, "right") + _th("State", 5, "center", num=False)
        + "</tr></thead><tbody>" + "".join(book_rows) + "</tbody></table></div>"
        "<script>(function(){var dir={};window.sortFB=function(c,num){"
        "var tb=document.querySelector('#fbtbl tbody');var rs=[].slice.call(tb.rows);"
        "dir[c]=-(dir[c]||1);var d=dir[c];rs.sort(function(a,b){"
        "var x=a.cells[c].getAttribute('data-v'),y=b.cells[c].getAttribute('data-v');"
        "if(num){x=parseFloat(x);y=parseFloat(y);}else{x=(''+x).toLowerCase();y=(''+y).toLowerCase();}"
        "return x<y?d:x>y?-d:0;});rs.forEach(function(r){tb.appendChild(r);});};})();</script>") if books else ""

    contrib = (f"<h3 style=\"margin:18px 0 2px;font-size:15px\">Who's pulling weight, who's dragging</h3>"
               f"<div style=\"color:#8b95a5;font-size:12px;margin-bottom:6px\">Each book's standalone cumulative P&amp;L "
               f"(its own track, best→worst) · the bigger the bar, the bigger the win or loss. Left label shows its current "
               f"portfolio weight. <span style=\"color:#6b7280\">Standalone result, not paper-period attribution.</span></div>"
               f"<div style=\"background:#0f141c;border-radius:10px;padding:10px 8px\">{_contrib_chart(books)}</div>"
               if books else "")

    # Specialist bots (Surtr 🔥, and Bull/Calm/Chop in flight) carry no books list —
    # they ARE a single book, with their own event log. Surface switching + events.
    if not books:
        evhtml = _events_html(_freyr_events(variant))
        spec_sw = _switch_chip(p.get("leverage", 1.0), name=f"Freyr {variant}",
                               last_measured=snap.get("date", ""))
        book_section = f"""
  <h3 style="margin:16px 0 2px;font-size:15px">Switching cost</h3>
  <div style="margin:4px 0 8px">{spec_sw}</div>
  <h3 style="margin:18px 0 2px;font-size:15px">Recent events</h3>
  <div style="color:#8b95a5;font-size:12px;margin-bottom:2px">Last 5 — when this book armed, resized, or escaped.</div>
  {evhtml}"""
    else:
        book_section = f"""
  {contrib}
  <h3 style="margin:18px 0 2px;font-size:15px">Books ({len(books)}) · tap a column to sort, a row to drill in</h3>
  <div style="color:#8b95a5;font-size:12px">Each book is one strategy. "State" = active (trading) / dormant (flat, awaiting signal). ⨯ = disarmed by its drawdown trip. Tap a row for its size, P&amp;L, switching cost, rules &amp; falsification status.</div>
  {book_table}
  {_book_modal_html(sorted(books, key=lambda x: x.get('realized_weight', 0), reverse=True), snap.get('date', ''))}"""

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Freyr {variant}</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:720px;margin:auto">
  <a href="/" style="color:#60a5fa;text-decoration:none;font-size:14px">← Home</a>
  <h2 style="margin:8px 0 2px">{emoji} Freyr {variant} <span style="font-size:14px;color:#8b95a5;font-weight:500">· {pname}</span></h2>
  <div style="color:#8b95a5;font-size:13px;margin-bottom:10px">{snap.get('label', sub)} · as of {snap.get('date', '')}</div>
  <div style="background:#0f141c;border-radius:10px;padding:8px;margin-bottom:12px">{spark}
    <div style="color:#6b7280;font-size:11px;text-align:center;margin-top:2px">model equity, last 60 days (paper live since {p.get('paper_start', '—')})</div>
    <div style="border-top:1px solid #1c2230;margin:8px 4px 4px"></div>
    <div style="color:#8b95a5;font-size:10px;text-transform:uppercase;letter-spacing:.3px;margin:2px 0 0 2px">Drawdown (underwater)</div>
    {dd_spark}
    <div style="color:#6b7280;font-size:11px;text-align:center;margin-top:2px">how far below its peak, same 60 days</div>
  </div>
  <div style="display:flex;flex-wrap:wrap;gap:6px">{stats}</div>
  <div style="color:#6b7280;font-size:12px;margin:12px 0 2px">{esc.get('reason', '')}</div>
  {book_section}
  <p style="color:#6b7280;font-size:12px;margin-top:16px">Paper (pretend money). Refresh on the home page.</p>
  {_switch_modal_html()}
</body></html>"""


def _testnet_detail_page() -> str:
    snap = _testnet_load()
    semoji, slabel, scol = _testnet_status(snap)
    back = "<a href='/' style='color:#60a5fa;text-decoration:none;font-size:14px'>← Home</a>"
    if not snap:
        return ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='background:#0b0e14;color:#e6e6e6;font-family:system-ui;padding:24px'>"
                f"{back}<h2>🔌 Live Testnet</h2>"
                f"<p style='color:#8b95a5'>{semoji} {slabel} — no snapshot on disk yet. The minute "
                "poller writes one once it reaches the testnet API.</p></body>")

    pv = snap.get("portfolio_value", 0.0)
    pnl_abs, pnl_pct = snap.get("pnl_24h_abs"), snap.get("pnl_24h_pct")
    fdate = snap.get("fetched_at", "")[:16].replace("T", " ")

    def stat(label, value, c="#e6e6e6"):
        return (f"<div style='background:#151a23;border-radius:10px;padding:10px 8px;text-align:center;flex:1 1 28%;min-width:90px'>"
                f"<div style='color:#8b95a5;font-size:11px'>{label}</div>"
                f"<div style='font-size:16px;font-weight:700;color:{c};margin-top:2px'>{value}</div></div>")

    if pnl_abs is None:
        pnl_v, pnl_c = "TBD", "#6b7280"
    else:
        pnl_c = "#22c55e" if pnl_abs >= 0 else "#ef4444"
        pnl_v = f"{pnl_abs:+,.2f} ({pnl_pct:+.2f}%)"
    stats = "".join([
        stat("Portfolio (USDC)", f"${pv:,.2f}"),
        stat("24h P&L", pnl_v, pnl_c),
        stat("Spot USDC", f"${snap.get('spot_usdc', 0):,.2f}"),
        stat("Perp value", f"${snap.get('perp_account_value', 0):,.2f}"),
        stat("Notional", f"${snap.get('total_notional', 0):,.0f}"),
        stat("Leverage", f"{snap.get('leverage', 0):.2f}×"),
        stat("Margin used", f"${snap.get('margin_used', 0):,.2f}"),
        stat("Status", f"{semoji} {slabel}", scol),
    ])

    # live equity chart (7d, intraday granularity)
    series = [v for _, v in (snap.get("equity_series") or [])]
    if len(series) >= 2:
        chart = _mini_spark(series, series[-1] >= series[0], w=660, h=120)
        chart_note = "portfolio value · last 7 days (intraday, grows over time)"
    else:
        chart = "<div style='color:#6b7280;font-size:12px;padding:18px;text-align:center'>Equity chart builds as the minute poller accumulates snapshots.</div>"
        chart_note = ""

    def _table(title, head, rows, empty):
        body = ("".join(rows) if rows
                else f"<tr><td colspan='{len(head)}' style='padding:14px 6px;color:#6b7280;text-align:center'>{empty}</td></tr>")
        ths = "".join(f"<th style='padding:6px;text-align:left;white-space:nowrap'>{h}</th>" for h in head)
        return (f"<h3 style='margin:18px 0 4px;font-size:15px'>{title}</h3>"
                "<div style='overflow-x:auto;-webkit-overflow-scrolling:touch'>"
                "<table style='width:100%;border-collapse:collapse;font-size:13px;min-width:360px'>"
                f"<thead><tr style='color:#9aa4b2;font-size:11px'>{ths}</tr></thead>"
                f"<tbody>{body}</tbody></table></div>")

    def _sc(side):  # side colour
        return "#22c55e" if side in ("buy", "long") else "#ef4444"

    def _td(v, c="#e6e6e6", align="left", bold=False):
        return (f"<td style='padding:7px 6px;text-align:{align};color:{c};"
                f"font-weight:{700 if bold else 400};white-space:nowrap'>{v}</td>")

    def _book(b):
        # Map the book key the poller attributes (e.g. "surtr") to its bracket emoji
        # + capitalised name in the bracket's accent colour → "🔥 Surtr". Unknown
        # keys fall back to the raw string; no attribution renders as an em-dash.
        if not b:
            return "<span style='color:#475569'>—</span>"
        meta = FREYR_META.get(b)
        if meta:
            emoji, _name, accent, _sub = meta
            label = f"{emoji} {b[:1].upper()}{b[1:]}"
            return f"<span style='color:{accent};font-weight:600'>{html_escape(label)}</span>"
        return f"<span style='color:#8b95a5'>{html_escape(b)}</span>"

    # positions
    prows = []
    for p in snap.get("positions", []):
        upnl = p.get("unrealized_pnl", 0)
        prows.append("<tr style='border-top:1px solid #1c2230'>"
                     + _td(p.get("coin"), bold=True) + _td(p.get("side"), _sc(p.get("side")))
                     + _td(f"{p.get('size', 0):g}", align="right")
                     + _td(f"${p.get('entry_px', 0):,.1f}", align="right")
                     + _td(f"${p.get('mark_px', 0):,.1f}", align="right")
                     + _td(f"{upnl:+,.2f}", "#22c55e" if upnl >= 0 else "#ef4444", "right", True)
                     + _td(_book(p.get("book"))) + "</tr>")
    pos_table = _table(f"Open positions ({snap.get('n_positions', 0)})",
                       ["Symbol", "Side", "Size", "Entry", "Mark", "uP&L", "Book"],
                       prows, "No open positions.")

    # orders
    orows = []
    for o in snap.get("orders", []):
        orows.append("<tr style='border-top:1px solid #1c2230'>"
                     + _td(o.get("coin"), bold=True) + _td(o.get("side"), _sc(o.get("side")))
                     + _td(f"{o.get('size', 0):g}", align="right")
                     + _td(f"${o.get('limit_px', 0):,.1f}", align="right")
                     + _td(o.get("age_str", "—"), "#8b95a5", "right")
                     + _td(_book(o.get("book"))) + "</tr>")
    ord_table = _table(f"Open orders ({snap.get('n_orders', 0)})",
                       ["Symbol", "Side", "Size", "Limit", "Age", "Book"],
                       orows, "No open orders.")

    # fills
    frows = []
    for f in snap.get("recent_fills", []):
        cp = f.get("closed_pnl", 0)
        cpc = "#6b7280" if cp == 0 else ("#22c55e" if cp > 0 else "#ef4444")
        frows.append("<tr style='border-top:1px solid #1c2230'>"
                     + _td(f.get("coin"), bold=True) + _td(f.get("side"), _sc(f.get("side")))
                     + _td(f"{f.get('size', 0):g}", align="right")
                     + _td(f"${f.get('px', 0):,.1f}", align="right")
                     + _td(f.get("time_str", "—"), "#8b95a5")
                     + _td(f"{cp:+,.2f}" if cp else "—", cpc, "right")
                     + _td(_book(f.get("book"))) + "</tr>")
    ft = snap.get("fills_today", {}) or {}
    fills_caption = (f"None today · {snap.get('n_fills_total', 0)} all-time" if ft.get("count", 0) == 0
                     else f"{ft['count']} today ({ft.get('realized_pnl', 0):+,.2f}) · last 20 shown")
    fill_table = _table(f"Recent fills · {fills_caption}",
                        ["Symbol", "Side", "Size", "Price", "Time", "P&L", "Book"],
                        frows, "No fills yet — the account is funded but hasn't traded.")

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Live Testnet</title></head>
<body style="background:#0b0e14;color:#e6e6e6;font-family:system-ui;margin:0;padding:18px;max-width:720px;margin:auto">
  {back}
  <h2 style="margin:8px 0 2px">🔌 Live Testnet <span style="font-size:14px;color:#8b95a5;font-weight:500">· {semoji} {slabel}</span></h2>
  <div style="color:#8b95a5;font-size:13px;margin-bottom:10px">Real orders, fake money — Freyr's actual Hyperliquid testnet account · as of {fdate} UTC</div>
  <div style="background:#0f141c;border-radius:10px;padding:8px;margin-bottom:12px">{chart}
    <div style="color:#6b7280;font-size:11px;text-align:center;margin-top:2px">{chart_note}</div>
  </div>
  <div style="display:flex;flex-wrap:wrap;gap:6px">{stats}</div>
  {pos_table}
  {ord_table}
  {fill_table}
  <div style="background:#11203a;border:1px solid #1d3a66;border-radius:12px;padding:13px 14px;margin-top:18px;color:#cbd5e1;font-size:12.5px;line-height:1.5">
    <b style="color:#e6e6e6">What "testnet" means.</b> This is the real Hyperliquid exchange's test network — live order matching, real market data, but the money is fake faucet USDC. It's the dress rehearsal before any real capital: we prove the plumbing (orders, fills, reconciliation) works against a live venue with nothing at stake.
    <div style="margin-top:8px">Authoritative source: <a href="{TESTNET_UI_URL}" style="color:#60a5fa">{TESTNET_UI_URL}</a> ›</div>
  </div>
  <p style="color:#6b7280;font-size:12px;margin-top:14px">Read-only view · polled every minute · this dashboard never places or cancels orders.</p>
  {_switch_modal_html()}
</body></html>"""
