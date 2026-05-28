"""
paper_live.py — forward paper-test on LIVE Bitcoin prices, with pretend money.

Runs the income bot (GridBot) against real Deribit prices, but places NO real
orders and needs NO API keys — it's a simulation you can leave running to watch
the bot work going forward, with zero money at risk.

    python3.11 paper_live.py            # leave running; steps once per hour
    python3.11 paper_live.py --once     # one step (e.g. from a cron job)

State is saved to paper_state.json each step, so it resumes after a restart.
Everything is logged to paper_live.log.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))            # for deribit_client

from grid_bot import GridBot                     # noqa: E402

STATE = HERE / "paper_state.json"
LOG = HERE / "paper_live.log"
PAPER_CAPITAL = 10_000.0
INSTRUMENT = "BTC-PERPETUAL"


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _rest():
    from deribit_client import DeribitPublicREST
    return DeribitPublicREST()


def fetch_price(rest):
    t = rest.get_ticker(INSTRUMENT)
    if t is None:
        return None
    return t.underlying_price or t.mark_price     # index (true BTC price), fall back to mark


def warmup_closes(rest, hours=360):
    end = int(time.time())
    start = end - hours * 3600
    try:
        candles = rest.get_tradingview_chart_data(INSTRUMENT, 60, start, end)
        return [c["close"] for c in candles if c.get("close")]
    except Exception as exc:
        log(f"warmup fetch failed ({exc}); 15-day average will fill over the next ~15 days")
        return []


def load_bot(rest):
    bot = GridBot(capital=PAPER_CAPITAL)
    if STATE.exists():
        bot.load_dict(json.loads(STATE.read_text()))
        log(f"resumed paper state ({bot.trades} trades so far)")
    else:
        closes = warmup_closes(rest)
        if closes:
            bot.warmup(closes)
            log(f"warmed up the 15-day average from {len(closes)} historical hours")
        log(f"fresh paper account: ${PAPER_CAPITAL:,.0f}")
    return bot


def step(bot, rest):
    price = fetch_price(rest)
    if not price or price <= 0:
        log("price fetch failed — skipping this step (will retry next hour)")
        return
    orders = bot.on_close(price)
    STATE.write_text(json.dumps(bot.to_dict()))
    for side, p, qty in orders:
        log(f"  {side:9} {qty:.5f} BTC @ ${p:,.0f}")
    eq = bot.equity(price)
    log(f"BTC ${price:,.0f} | account ${eq:,.0f} ({(eq / PAPER_CAPITAL - 1) * 100:+.1f}%) | "
        f"holding {bot.btc_held():.5f} BTC + ${bot.cash:,.0f} cash | {bot.trades} trades total")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single step then exit (for cron)")
    args = ap.parse_args()

    rest = _rest()
    bot = load_bot(rest)

    if args.once:
        step(bot, rest)
        return

    log("paper-live started — stepping once per hour. Leave this running; Ctrl-C to stop.")
    while True:
        step(bot, rest)
        time.sleep(3600 - (time.time() % 3600))   # sleep to the top of the next hour


if __name__ == "__main__":
    main()
