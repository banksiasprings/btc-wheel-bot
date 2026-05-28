#!/usr/bin/env python3.11
"""
grid_farm.py — runs a farm of grid-bot VARIANTS on live BTC prices, pretend money.

Seven styles (safest → super-aggressive → a leveraged "Degen" for kicks) all trade
the same live price each hour with NO real orders and NO API keys. Each variant
keeps its own pretend $10k account; results feed the dashboard (grid_farm/status.json).

    python3.11 grid_farm.py            # leave running; steps every hour
    python3.11 grid_farm.py --once     # one step (e.g. cron)

Per-variant state persists under grid_farm/<slug>/ so it resumes after a restart.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "strategies"))

from grid_bot import GridBot                      # noqa: E402
from income_bots import FundingBot, LongVolBot    # noqa: E402

FARM = ROOT / "grid_farm"
STATUS = FARM / "status.json"
LOG = FARM / "farm.log"
PAPER_CAPITAL = 10_000.0
INSTRUMENT = "BTC-PERPETUAL"

# spacing, max_lots, ma_hours (0 = no trend-stop), leverage, borrow_rate
VARIANTS = [
    {"slug": "vault",      "name": "Vault",      "style": "safest — tiny dips",
     "spacing": 0.05, "max_lots": 100, "ma_hours": 360, "leverage": 1.0, "borrow_rate": 0.0},
    {"slug": "steady",     "name": "Steady",     "style": "conservative",
     "spacing": 0.05, "max_lots": 50,  "ma_hours": 360, "leverage": 1.0, "borrow_rate": 0.0},
    {"slug": "balanced",   "name": "Balanced",   "style": "the recommended pick",
     "spacing": 0.05, "max_lots": 20,  "ma_hours": 360, "leverage": 1.0, "borrow_rate": 0.0},
    {"slug": "brisk",      "name": "Brisk",      "style": "more trades, tighter grid",
     "spacing": 0.03, "max_lots": 20,  "ma_hours": 360, "leverage": 1.0, "borrow_rate": 0.0},
    {"slug": "aggressive", "name": "Aggressive", "style": "no safety brake",
     "spacing": 0.05, "max_lots": 20,  "ma_hours": 0,   "leverage": 1.0, "borrow_rate": 0.0},
    {"slug": "wild",       "name": "Wild",       "style": "tight grid, no brake",
     "spacing": 0.02, "max_lots": 20,  "ma_hours": 0,   "leverage": 1.0, "borrow_rate": 0.0},
    {"slug": "degen",      "name": "Degen ⚠️", "style": "3x leverage, FOR KICKS — can blow up to $0",
     "spacing": 0.05, "max_lots": 20,  "ma_hours": 0,   "leverage": 3.0, "borrow_rate": 0.15},
    # ── Funding: market-neutral carry (collect funding, no price risk) ──
    {"slug": "funding",       "name": "Funding Carry", "type": "funding",
     "style": "market-neutral — collects funding, no price bet", "positive_only": False},
    {"slug": "funding-smart", "name": "Funding (smart)", "type": "funding",
     "style": "market-neutral — only collects when funding pays", "positive_only": True},
    {"slug": "funding-2x",    "name": "Funding 2×", "type": "funding", "leverage": 2.0,
     "style": "leveraged carry — ~2× the funding income (and the bleed)"},
    {"slug": "funding-3x",    "name": "Funding 3× ⚠️", "type": "funding", "leverage": 3.0,
     "style": "aggressive leveraged carry — erodes fast if funding turns negative"},
    # ── Long-Vol: the grid's complement — wins on big moves / crashes ──
    {"slug": "longvol",       "name": "Long-Vol",    "type": "longvol", "leverage": 1.0,
     "style": "big-moves bot — profits from chaos, bleeds when calm"},
    {"slug": "longvol-2x",    "name": "Long-Vol 2×", "type": "longvol", "leverage": 2.0,
     "style": "big-moves, double size — bigger swings"},
    {"slug": "longvol-3x",    "name": "Long-Vol 3× ⚠️", "type": "longvol", "leverage": 3.0,
     "style": "big-moves, triple size — can be wiped out"},
    {"slug": "longvol-cheap", "name": "Long-Vol (cheap)", "type": "longvol", "leverage": 1.0,
     "dvol_max": 45.0, "style": "only buys volatility when it's cheap — less bleed"},
]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] {msg}"
    print(line, flush=True)
    FARM.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _rest():
    from deribit_client import DeribitPublicREST
    return DeribitPublicREST()


def fetch_close_low(rest):
    """Most recent hourly candle: (close, low). Low feeds the leverage liquidation check."""
    try:
        end = int(time.time())
        candles = rest.get_tradingview_chart_data(INSTRUMENT, 60, end - 3 * 3600, end)
        if candles:
            c = candles[-1]
            return float(c["close"]), float(c["low"])
    except Exception as exc:
        log(f"candle fetch failed ({exc}); trying ticker")
    t = rest.get_ticker(INSTRUMENT)
    if t and t.mark_price:
        return t.mark_price, t.mark_price
    return None, None


def warmup_closes(rest, hours):
    try:
        end = int(time.time())
        candles = rest.get_tradingview_chart_data(INSTRUMENT, 60, end - hours * 3600, end)
        return [float(c["close"]) for c in candles if c.get("close")]
    except Exception as exc:
        log(f"warmup fetch failed ({exc})")
        return []


MIN_ORDER_USD = 15.0   # safe floor per order on Deribit (perp min ~$10 + buffer)


def min_capital(v):
    """Approx. smallest live stake that lets every order clear exchange minimums."""
    t = v.get("type", "grid")
    if t == "grid":
        raw = v["max_lots"] * MIN_ORDER_USD / v.get("leverage", 1.0)
        return int(math.ceil(raw / 50.0) * 50)
    if t == "funding":
        return int(max(100, round(200 / v.get("leverage", 1.0) / 50) * 50))
    if t == "longvol":
        return int(max(200, round(500 / v.get("leverage", 1.0) / 50) * 50))
    return 100


def make_bot(v):
    t = v.get("type", "grid")
    if t == "funding":
        return FundingBot(capital=PAPER_CAPITAL, positive_only=v.get("positive_only", False),
                          leverage=v.get("leverage", 1.0))
    if t == "longvol":
        return LongVolBot(capital=PAPER_CAPITAL, leverage=v.get("leverage", 1.0),
                          dvol_max=v.get("dvol_max"))
    return GridBot(spacing=v["spacing"], max_lots=v["max_lots"], ma_hours=v["ma_hours"],
                   capital=PAPER_CAPITAL, leverage=v.get("leverage", 1.0),
                   borrow_rate=v.get("borrow_rate", 0.0))


def load_variant(v, rest):
    d = FARM / v["slug"]
    d.mkdir(parents=True, exist_ok=True)
    bot = make_bot(v)
    sf = d / "state.json"
    if sf.exists():
        st = json.loads(sf.read_text())
        bot.load_dict(st["bot"])
        return bot, st.get("peak", PAPER_CAPITAL), st.get("started", _now())
    if v.get("ma_hours"):                        # only grid variants warm up an MA
        closes = warmup_closes(rest, v["ma_hours"])
        if closes:
            bot.warmup(closes)
            log(f"{v['name']}: warmed up from {len(closes)}h of history")
    return bot, PAPER_CAPITAL, _now()


def _now():
    return datetime.now(timezone.utc).isoformat()


def fetch_funding_1h(rest):
    """Perpetual funding rate for one hour (8h rate / 8)."""
    try:
        t = rest._get("ticker", {"instrument_name": INSTRUMENT})
        return float(t.get("funding_8h", 0.0)) / 8.0
    except Exception as exc:
        log(f"funding fetch failed ({exc})")
        return 0.0


def fetch_dvol(rest):
    """Current BTC implied-vol index (DVOL, %). Used by the long-vol bots."""
    try:
        end = int(time.time())
        r = rest._get("get_volatility_index_data", {
            "currency": "BTC", "resolution": "3600",
            "start_timestamp": (end - 6 * 3600) * 1000, "end_timestamp": end * 1000})
        data = r.get("data", [])
        if data:
            return float(data[-1][4])              # last candle close = current DVOL
    except Exception as exc:
        log(f"dvol fetch failed ({exc})")
    return 60.0                                     # fallback implied vol


def _state_label(v, bot):
    t = v.get("type", "grid")
    if getattr(bot, "liquidated", False):
        return "💀 liquidated ($0)"
    if t == "funding":
        return "collecting funding (market-neutral)"
    if t == "longvol":
        return "long volatility (waiting for big moves)"
    if bot.btc_held() > 1e-9:
        return f"holding {bot.btc_held():.4f} BTC"
    return "in cash (waiting)"


def save_variant(v, bot, peak, started, price, eq, btc, cash):
    d = FARM / v["slug"]
    (d / "state.json").write_text(json.dumps(
        {"bot": bot.to_dict(), "peak": peak, "started": started}))
    ec = d / "equity.csv"
    header = not ec.exists()
    with open(ec, "a") as f:
        if header:
            f.write("timestamp,btc_price,equity,btc_held,cash,trades,liquidated\n")
        f.write(f"{_now()},{price:.2f},{eq:.2f},{btc:.6f},{cash:.2f},"
                f"{getattr(bot, 'trades', 0)},{int(getattr(bot, 'liquidated', False))}\n")


def step_all(state, rest):
    price, low = fetch_close_low(rest)
    if not price or price <= 0:
        log("price fetch failed — skipping this step")
        return
    funding_1h = fetch_funding_1h(rest)
    dvol = fetch_dvol(rest)
    rows = []
    for v in VARIANTS:
        t = v.get("type", "grid")
        bot, peak, started = state[v["slug"]]
        if t == "funding":
            bot.step(funding_1h)
        elif t == "longvol":
            bot.step(price, dvol)
        else:
            for side, p, qty in bot.on_close(price, low=low):
                if side == "LIQUIDATED":
                    log(f"  {v['name']}: 💀 LIQUIDATED at ${p:,.0f}")
        eq = bot.equity(price) if t == "grid" else bot.equity_now()
        peak = max(peak, eq)
        state[v["slug"]] = (bot, peak, started)
        btc = bot.btc_held() if t == "grid" else 0.0
        cash = bot.cash if t == "grid" else eq
        save_variant(v, bot, peak, started, price, eq, btc, cash)
        days = max(0.0, (datetime.now(timezone.utc)
                         - datetime.fromisoformat(started)).total_seconds() / 86400)
        rows.append({
            "slug": v["slug"], "name": v["name"], "style": v["style"], "type": t,
            "spacing_pct": v.get("spacing", 0) * 100, "max_lots": v.get("max_lots"),
            "trend_stop": v.get("ma_hours", 0) > 0, "leverage": v.get("leverage", 1.0),
            "equity": round(eq, 2), "profit": round(eq - PAPER_CAPITAL, 2),
            "return_pct": round((eq / PAPER_CAPITAL - 1) * 100, 2),
            "max_drawdown_pct": round((peak - eq) / peak * 100 if peak > 0 else 0, 2),
            "trades": getattr(bot, "trades", 0), "btc_held": round(btc, 6),
            "cash": round(cash, 2), "state": _state_label(v, bot), "days_running": round(days, 2),
            "min_capital": min_capital(v),
        })
    STATUS.write_text(json.dumps({
        "updated": _now(), "btc_price": round(price, 2),
        "paper_capital": PAPER_CAPITAL, "variants": rows,
    }, indent=2))
    leader = max(rows, key=lambda r: r["equity"])
    log(f"BTC ${price:,.0f} | {len(rows)} bots | leader {leader['name']} ${leader['equity']:,.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single step then exit (for cron)")
    args = ap.parse_args()
    FARM.mkdir(exist_ok=True)
    rest = _rest()
    state = {v["slug"]: load_variant(v, rest) for v in VARIANTS}
    log(f"grid farm: {len(VARIANTS)} variants, ${PAPER_CAPITAL:,.0f} pretend each")
    if args.once:
        step_all(state, rest)
        return
    log("grid farm running — stepping every hour. Leave running; Ctrl-C to stop.")
    while True:
        step_all(state, rest)
        time.sleep(3600 - (time.time() % 3600))


if __name__ == "__main__":
    main()
