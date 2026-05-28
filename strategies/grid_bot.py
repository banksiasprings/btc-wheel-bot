"""
grid_bot.py — the deployable income bot engine (the "pure bot").

This is the live-capable version of the validated backtest in grid_backtest.py.
It's event-driven: feed it one new price at a time via on_close(price) and it
decides what to buy/sell, tracking its own cash and Bitcoin holdings. The same
engine drives the free paper-test (replaying real prices) and, later, live
trading (a thin exchange adapter calls on_close on each new candle and places
the orders it returns).

Config = "Balanced": 5% spacing, 20 lots, 15-day (360h) trend-stop, no leverage.

Run:
    python3.11 grid_bot.py            # self-check vs backtest + paper-test demo
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import numpy as np

# Balanced preset
SPACING = 0.05
MAX_LOTS = 20
MA_HOURS = 360            # 15-day trend-stop
FEE = 0.0006
CAPITAL = 100_000.0


class GridBot:
    def __init__(self, spacing=SPACING, max_lots=MAX_LOTS, ma_hours=MA_HOURS,
                 capital=CAPITAL, fee=FEE, leverage=1.0, borrow_rate=0.0):
        self.g = math.log1p(spacing)
        self.max_lots = max_lots
        self.ma_hours = ma_hours
        self.fee = fee
        self.leverage = leverage
        self.borrow_rate = borrow_rate          # annual funding/borrow cost on negative (borrowed) cash
        self.capital = capital
        # leverage>1 borrows: each lot is bigger and cash may go negative down to borrow_limit
        self.buy_usd = capital * leverage / max_lots
        self.borrow_limit = (leverage - 1.0) * capital
        self.cash = capital
        self.held: dict[int, float] = {}        # rung -> BTC qty
        self.r_prev = None
        self.ma_q = deque(maxlen=ma_hours) if ma_hours else None
        self.ma_sum = 0.0
        self.trades = 0
        self.liquidated = False

    def _rung(self, p):
        return int(math.floor(math.log(p) / self.g))

    def _price_at(self, r):
        return math.exp(r * self.g)

    def on_close(self, price, low=None):
        """Process one new closing price. Returns a list of orders executed.

        `low` (the bar's intra-hour low) is used only for the leverage liquidation
        check — real exchanges liquidate on the wick, not the close. Pass it in
        backtests/live; if omitted, the close is used (optimistic for leverage).
        """
        if self.liquidated:                      # margin-called: account is dead, stays flat
            return []
        ma = None
        if self.ma_q is not None:
            if len(self.ma_q) == self.ma_q.maxlen:
                self.ma_sum -= self.ma_q[0]
            self.ma_q.append(price)
            self.ma_sum += price
            ma = self.ma_sum / len(self.ma_q)

        if self.r_prev is None:                  # first bar: just anchor (matches backtest)
            self.r_prev = self._rung(price)

        if self.cash < 0.0 and self.borrow_rate:  # leveraged: pay funding/borrow on the debt
            self.cash += self.cash * (self.borrow_rate / 8760.0)   # cash<0 → grows the debt

        if self.leverage > 1.0:                  # leveraged: margin call if own equity wiped out
            lp = low if low is not None else price   # liquidate on the intra-hour wick
            if self.cash + sum(q * lp for q in self.held.values()) <= 0.0:
                lost = self.btc_held()
                self.held.clear()
                self.cash = 0.0
                self.liquidated = True
                self.r_prev = self._rung(price)
                return [("LIQUIDATED", round(lp, 2), lost)]

        orders = []
        if ma is not None and price < ma:        # trend-stop: confirmed downtrend → go flat
            for r in list(self.held):
                qty = self.held.pop(r)
                self.cash += qty * price * (1 - self.fee)
                self.trades += 1
                orders.append(("SELL_STOP", round(price, 2), qty))
            self.r_prev = self._rung(price)
            return orders

        r_now = self._rung(price)
        if r_now < self.r_prev:                  # price stepped down → buy crossed rungs
            for r in range(self.r_prev - 1, r_now - 1, -1):
                if (r not in self.held and len(self.held) < self.max_lots
                        and self.cash + self.borrow_limit >= self.buy_usd * (1 + self.fee)):
                    self.cash -= self.buy_usd * (1 + self.fee)
                    self.held[r] = self.buy_usd / self._price_at(r)
                    self.trades += 1
                    orders.append(("BUY", round(self._price_at(r), 2), self.held[r]))
        elif r_now > self.r_prev:                # price stepped up → take profit one rung up
            for r in list(self.held):
                if r + 1 <= r_now:
                    qty = self.held.pop(r)
                    self.cash += qty * self._price_at(r + 1) * (1 - self.fee)
                    self.trades += 1
                    orders.append(("SELL", round(self._price_at(r + 1), 2), qty))
        self.r_prev = r_now
        return orders

    def equity(self, price):
        return self.cash + sum(q * price for q in self.held.values())

    def btc_held(self):
        return sum(self.held.values())

    def warmup(self, prices):
        """Pre-fill the 15-day average from history WITHOUT trading, so the
        trend-stop is active from the very first live bar."""
        for p in prices:
            if self.ma_q is not None:
                if len(self.ma_q) == self.ma_q.maxlen:
                    self.ma_sum -= self.ma_q[0]
                self.ma_q.append(p)
                self.ma_sum += p
        if len(prices):
            self.r_prev = self._rung(prices[-1])

    def to_dict(self):
        return {
            "cash": self.cash,
            "held": {str(r): q for r, q in self.held.items()},
            "r_prev": self.r_prev,
            "ma_q": list(self.ma_q) if self.ma_q is not None else None,
            "ma_sum": self.ma_sum,
            "trades": self.trades,
            "liquidated": self.liquidated,
        }

    def load_dict(self, d):
        self.cash = d["cash"]
        self.held = {int(r): q for r, q in d["held"].items()}
        self.r_prev = d["r_prev"]
        self.ma_sum = d["ma_sum"]
        self.trades = d["trades"]
        self.liquidated = d.get("liquidated", False)
        if self.ma_q is not None and d.get("ma_q"):
            self.ma_q.clear()
            for p in d["ma_q"]:
                self.ma_q.append(p)


# ---------------------------------------------------------------------------

def _load_closes(start=None, end=None):
    import pandas as pd
    p = Path(__file__).resolve().parent.parent / "data" / "raw" / "spot" / "btc_1h.csv"
    df = pd.read_csv(p)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
    if start:
        df = df[df["ts"] >= pd.Timestamp(start)]
    if end:
        df = df[df["ts"] < pd.Timestamp(end)]
    return df.reset_index(drop=True)


def self_check():
    """Confirm the live engine reproduces the validated backtest exactly."""
    from grid_backtest import run_ladder, CAPITAL as C
    df = _load_closes("2019-01-01", "2026-06-01")
    closes = df["close"].values
    bot = GridBot(capital=C)
    for p in closes:
        bot.on_close(p)
    bot_eq = bot.equity(closes[-1])
    bt_eq, bt_trades = run_ladder(closes, closes, closes, spacing=SPACING,
                                  max_lots=MAX_LOTS, ma_hours=MA_HOURS, capital=C)
    bt_final = bt_eq[-1]
    match = abs(bot_eq - bt_final) < 1.0
    print(f"SELF-CHECK: engine ${bot_eq:,.0f} vs backtest ${bt_final:,.0f} "
          f"-> {'MATCH' if match else 'MISMATCH'} (trades {bot.trades} vs {bt_trades})")
    return match


def paper_test(start="2025-05-01", end="2026-06-01", capital=10_000.0):
    """Replay recent real prices through the bot as if trading live (pretend money)."""
    df = _load_closes(start, end)
    bot = GridBot(capital=capital)
    print(f"\n=== PAPER TEST (pretend ${capital:,.0f}, real BTC prices {start} -> latest) ===")
    print(f"{'Month':<10} {'BTC price':>11} {'Account $':>12} {'Profit $':>10} {'BTC held':>9} {'Trades':>7}")
    cur_month = None
    last_print_tr = 0
    for _, row in df.iterrows():
        price = row["close"]
        bot.on_close(price)
        m = row["ts"].strftime("%Y-%m")
        if m != cur_month:
            cur_month = m
            eq = bot.equity(price)
            print(f"{m:<10} {price:>11,.0f} {eq:>12,.0f} {eq-capital:>+10,.0f} "
                  f"{bot.btc_held():>9.4f} {bot.trades-last_print_tr:>7}")
            last_print_tr = bot.trades
    final = bot.equity(df['close'].values[-1])
    bh = capital * df['close'].values[-1] / df['close'].values[0]
    print(f"\nFinal account: ${final:,.0f}  (profit ${final-capital:+,.0f}, "
          f"{(final/capital-1)*100:+.1f}%)  |  total trades: {bot.trades}")
    print(f"For comparison, just holding BTC over this window: ${bh:,.0f} "
          f"({(bh/capital-1)*100:+.1f}%)")
    print(f"Currently holding {bot.btc_held():.4f} BTC + ${bot.cash:,.0f} cash.")


if __name__ == "__main__":
    self_check()
    paper_test()
