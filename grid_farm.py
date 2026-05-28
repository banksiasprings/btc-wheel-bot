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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "strategies"))

from grid_bot import GridBot                      # noqa: E402

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


def make_bot(v):
    return GridBot(spacing=v["spacing"], max_lots=v["max_lots"], ma_hours=v["ma_hours"],
                   capital=PAPER_CAPITAL, leverage=v["leverage"], borrow_rate=v["borrow_rate"])


def load_variant(v, rest):
    d = FARM / v["slug"]
    d.mkdir(parents=True, exist_ok=True)
    bot = make_bot(v)
    sf = d / "state.json"
    if sf.exists():
        st = json.loads(sf.read_text())
        bot.load_dict(st["bot"])
        return bot, st.get("peak", PAPER_CAPITAL), st.get("started", _now())
    if v["ma_hours"]:
        closes = warmup_closes(rest, v["ma_hours"])
        if closes:
            bot.warmup(closes)
            log(f"{v['name']}: warmed up from {len(closes)}h of history")
    return bot, PAPER_CAPITAL, _now()


def _now():
    return datetime.now(timezone.utc).isoformat()


def save_variant(v, bot, peak, started, price):
    d = FARM / v["slug"]
    (d / "state.json").write_text(json.dumps(
        {"bot": bot.to_dict(), "peak": peak, "started": started}))
    eq = bot.equity(price)
    ec = d / "equity.csv"
    header = not ec.exists()
    with open(ec, "a") as f:
        if header:
            f.write("timestamp,btc_price,equity,btc_held,cash,trades,liquidated\n")
        f.write(f"{_now()},{price:.2f},{eq:.2f},{bot.btc_held():.6f},"
                f"{bot.cash:.2f},{bot.trades},{int(bot.liquidated)}\n")


def step_all(state, rest):
    price, low = fetch_close_low(rest)
    if not price or price <= 0:
        log("price fetch failed — skipping this step")
        return
    rows = []
    for v in VARIANTS:
        bot, peak, started = state[v["slug"]]
        orders = bot.on_close(price, low=low)
        eq = bot.equity(price)
        peak = max(peak, eq)
        state[v["slug"]] = (bot, peak, started)
        save_variant(v, bot, peak, started, price)

        for side, p, qty in orders:
            if side in ("LIQUIDATED",):
                log(f"  {v['name']}: 💀 LIQUIDATED at ${p:,.0f}")

        if bot.liquidated:
            cur = "💀 liquidated ($0)"
        elif bot.btc_held() > 1e-9:
            cur = f"holding {bot.btc_held():.4f} BTC"
        else:
            cur = "in cash (waiting)"
        days = max(0.0, (datetime.now(timezone.utc)
                         - datetime.fromisoformat(started)).total_seconds() / 86400)
        rows.append({
            "slug": v["slug"], "name": v["name"], "style": v["style"],
            "spacing_pct": v["spacing"] * 100, "max_lots": v["max_lots"],
            "trend_stop": v["ma_hours"] > 0, "leverage": v["leverage"],
            "equity": round(eq, 2), "profit": round(eq - PAPER_CAPITAL, 2),
            "return_pct": round((eq / PAPER_CAPITAL - 1) * 100, 2),
            "max_drawdown_pct": round((peak - eq) / peak * 100 if peak > 0 else 0, 2),
            "trades": bot.trades, "btc_held": round(bot.btc_held(), 6),
            "cash": round(bot.cash, 2), "state": cur, "days_running": round(days, 2),
        })
    STATUS.write_text(json.dumps({
        "updated": _now(), "btc_price": round(price, 2),
        "paper_capital": PAPER_CAPITAL, "variants": rows,
    }, indent=2))
    leader = max(rows, key=lambda r: r["equity"])
    log(f"BTC ${price:,.0f} | leader {leader['name']} ${leader['equity']:,.0f} "
        f"({leader['return_pct']:+.1f}%)")


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
