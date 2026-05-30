"""
infinity_grid_bot.py — open-top grid with hysteretic trend-stop and infinity tail.

Subclasses GridBot. Three behavioural changes vs the parent:

  1. The buy ladder has NO upper cap — as price walks up, the grid keeps adding
     rungs and banking each step's spread. The lower bound is a hard price floor
     (default 50% of the initial anchor), not a rung count.
  2. Every sell retains `infinity_tail_pct` of the lot as long-term BTC inventory
     — the "infinity tail". This is a structural long position that walks up the
     ladder with price; fixed-range grids forfeit this entirely.
  3. The trend-stop is hysteretic: 6 hourly bars below the 30-day MA to fire,
     12 hourly bars above MA + 1×ATR_14d to re-arm, then a 3-day cooldown before
     trading resumes. The parent fires on a single bar — the documented whipsaw
     source.

Steven's locked decisions on the three Gate 2 open questions:
  Q1: HOLD the infinity tail through a trend-stop COOLDOWN. Liquidate only the
      active grid lots; carry the structural tail through.
  Q2: After COOLDOWN ends, anchor the new lower price floor at MA + 0.5×ATR.
  Q3: `infinity_tail_pct` sweep range is {15, 30, 50, 70}% (no 0% degenerate).

This file is FOR BACKTEST ONLY at Gate 3. It is NOT wired into grid_farm.py
VARIANTS — that's a Gate 4 deploy decision.

Run:
    python3.11 infinity_grid_bot.py    # sanity-test on daily data vs Balanced + BHO
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path

from grid_bot import GridBot


class InfinityGridBot(GridBot):
    # Spec defaults (Gate 2 spec §3); Gate 3 sweep varies the high-sensitivity ones.
    SPACING = 0.015
    MAX_LOTS = 20
    MA_HOURS = 720                  # 30-day trend-stop
    LOWER_FLOOR_FRAC = 0.5          # buy floor = 50% of initial anchor
    MIN_BELOW_MA_BARS = 6           # 6-bar hysteresis on stop trigger
    MIN_ABOVE_MA_BARS = 12          # 12-bar hysteresis on re-arm
    REENTRY_BUFFER_ATR = 1.0        # re-arm threshold = MA + 1.0 × ATR_14d
    RESTART_COOLDOWN_DAYS = 3       # cooldown after re-arm before trading resumes
    INFINITY_TAIL_PCT = 0.30        # fraction of each sold lot retained as tail
    ATR_PERIOD_DAYS = 14            # ATR lookback window
    MAX_DRAWDOWN_HALT_PCT = 0.25    # hard halt if equity dips this far below peak
    REANCHOR_ATR_MULT = 0.5         # Q2: new floor after cooldown = MA + 0.5 × ATR

    def __init__(self, spacing=SPACING, max_lots=MAX_LOTS, ma_hours=MA_HOURS,
                 lower_price_floor_frac=LOWER_FLOOR_FRAC,
                 min_below_ma_bars=MIN_BELOW_MA_BARS,
                 min_above_ma_bars=MIN_ABOVE_MA_BARS,
                 reentry_buffer_atr=REENTRY_BUFFER_ATR,
                 restart_cooldown_days=RESTART_COOLDOWN_DAYS,
                 infinity_tail_pct=INFINITY_TAIL_PCT,
                 atr_period_days=ATR_PERIOD_DAYS,
                 max_drawdown_halt_pct=MAX_DRAWDOWN_HALT_PCT,
                 reanchor_atr_mult=REANCHOR_ATR_MULT,
                 capital=10_000.0, fee=0.0006):
        # Force leverage=1 per the spec; infinity grid is paper-only-unleveraged at v1.
        super().__init__(spacing=spacing, max_lots=max_lots, ma_hours=ma_hours,
                         capital=capital, fee=fee, leverage=1.0, borrow_rate=0.0)
        self.lower_price_floor_frac = lower_price_floor_frac
        self.min_below_ma_bars = min_below_ma_bars
        self.min_above_ma_bars = min_above_ma_bars
        self.reentry_buffer_atr = reentry_buffer_atr
        self.restart_cooldown_hours = int(restart_cooldown_days * 24)
        self.infinity_tail_pct = infinity_tail_pct
        self.atr_period_hours = max(1, int(atr_period_days * 24))
        self.max_drawdown_halt_pct = max_drawdown_halt_pct
        self.reanchor_atr_mult = reanchor_atr_mult
        # State machine
        self.state = "RUNNING"                  # RUNNING | STOPPED | COOLDOWN | HALTED_DRAWDOWN
        self.bars_below_ma = 0
        self.bars_above_ma = 0
        self.cooldown_bars_remaining = 0
        # Grid anchoring
        self.initial_anchor_price = None
        self.lower_price_floor = None
        # ATR (true-range proxy: abs(close - prev_close); honest at hourly granularity)
        self.atr_q = deque(maxlen=self.atr_period_hours)
        self.atr_sum = 0.0
        self.atr_value = None
        self.last_close = None
        # Drawdown halt
        self.peak_equity = capital
        # Infinity tail — long-term BTC inventory, NOT bound to any rung
        self.infinity_tail_qty = 0.0
        # Risk cap: total BTC value (active grid + tail) ≤ capital × 1.05
        # See spec §4 "Max BTC position".
        self.btc_position_cap_usd = capital * 1.05

    # ── helpers ───────────────────────────────────────────────────────────────

    def _update_atr(self, price):
        if self.last_close is not None:
            tr = abs(price - self.last_close)
            if len(self.atr_q) == self.atr_q.maxlen:
                self.atr_sum -= self.atr_q[0]
            self.atr_q.append(tr)
            self.atr_sum += tr
            self.atr_value = self.atr_sum / len(self.atr_q)
        self.last_close = price

    def _liquidate_grid(self, price, tag):
        """Sell every held rung to cash. Does NOT touch the infinity tail."""
        orders = []
        for r in list(self.held):
            qty = self.held.pop(r)
            self.cash += qty * price * (1 - self.fee)
            self.trades += 1
            orders.append((tag, round(price, 2), qty))
        return orders

    def _liquidate_tail(self, price, tag):
        if self.infinity_tail_qty <= 0:
            return []
        qty = self.infinity_tail_qty
        self.cash += qty * price * (1 - self.fee)
        self.infinity_tail_qty = 0.0
        self.trades += 1
        return [(tag, round(price, 2), qty)]

    # ── main step ─────────────────────────────────────────────────────────────

    def on_close(self, price, low=None):
        """Process one new closing price. Returns a list of executed events.

        Event tuples: ("BUY"/"SELL"/"SELL_STOP"/"TAIL_RETAIN"/"SELL_HALT"
                       /"REENTRY"/"HALTED_DRAWDOWN", price_or_pct, qty_or_pct)
        """
        # 0. ATR update first (uses prior close)
        self._update_atr(price)

        # 1. Initialize anchor + floor on the very first bar.
        if self.initial_anchor_price is None:
            self.initial_anchor_price = price
            self.lower_price_floor = self.lower_price_floor_frac * price

        # 2. MA update (parent-style: rolling deque + running sum).
        ma = None
        if self.ma_q is not None:
            if len(self.ma_q) == self.ma_q.maxlen:
                self.ma_sum -= self.ma_q[0]
            self.ma_q.append(price)
            self.ma_sum += price
            ma = self.ma_sum / len(self.ma_q)

        # 3. Anchor r_prev on the first bar (matches parent).
        if self.r_prev is None:
            self.r_prev = self._rung(price)

        # 4. Drawdown high-water mark + halt check — runs in every state.
        current_eq = self.equity(price)
        if current_eq > self.peak_equity:
            self.peak_equity = current_eq
        if self.peak_equity > 0:
            dd = (self.peak_equity - current_eq) / self.peak_equity
            if dd >= self.max_drawdown_halt_pct and self.state != "HALTED_DRAWDOWN":
                orders = self._liquidate_grid(price, "SELL_HALT")
                orders += self._liquidate_tail(price, "SELL_HALT_TAIL")
                orders.append(("HALTED_DRAWDOWN", round(price, 4), round(dd, 4)))
                self.state = "HALTED_DRAWDOWN"
                self.r_prev = self._rung(price)
                return orders

        # 5. State machine dispatch.
        if self.state == "HALTED_DRAWDOWN":
            self.r_prev = self._rung(price)
            return []                                   # no recovery without manual reset

        if self.state == "STOPPED":
            # The grid is in cash; only the tail floats with price. Watch for re-arm.
            if ma is not None and self.atr_value is not None:
                threshold = ma + self.reentry_buffer_atr * self.atr_value
                if price > threshold:
                    self.bars_above_ma += 1
                else:
                    self.bars_above_ma = 0
                if self.bars_above_ma >= self.min_above_ma_bars:
                    self.state = "COOLDOWN"
                    self.cooldown_bars_remaining = self.restart_cooldown_hours
                    self.bars_above_ma = 0
            self.r_prev = self._rung(price)
            return []

        if self.state == "COOLDOWN":
            # Wait out the cooldown; on expiry re-anchor per Q2.
            self.cooldown_bars_remaining -= 1
            if self.cooldown_bars_remaining > 0:
                self.r_prev = self._rung(price)
                return []
            # Cooldown expired: anchor new lower floor at MA + 0.5×ATR per Q2.
            if ma is not None and self.atr_value is not None:
                self.lower_price_floor = ma + self.reanchor_atr_mult * self.atr_value
            else:
                self.lower_price_floor = self.lower_price_floor_frac * price
            self.state = "RUNNING"
            self.bars_below_ma = 0
            self.r_prev = self._rung(price)
            # Fall through so this bar can still place a buy if a rung is crossed.
            return [("REENTRY", round(price, 2), round(self.lower_price_floor, 2))]

        # RUNNING: check trend-stop with hysteresis.
        if ma is not None:
            if price < ma:
                self.bars_below_ma += 1
            else:
                self.bars_below_ma = 0
            if self.bars_below_ma >= self.min_below_ma_bars:
                orders = self._liquidate_grid(price, "SELL_STOP")
                # Q1: HOLD the infinity tail through STOPPED + COOLDOWN.
                self.state = "STOPPED"
                self.bars_below_ma = 0
                self.bars_above_ma = 0
                self.r_prev = self._rung(price)
                return orders

        # RUNNING + no trend-stop: regular grid trading.
        orders = []
        r_now = self._rung(price)

        if r_now < self.r_prev:
            # Price stepped down → buy at each crossed rung (no max_lots clamp;
            # the lower_price_floor is the hard backstop).
            for r in range(self.r_prev - 1, r_now - 1, -1):
                rung_price = self._price_at(r)
                if rung_price < self.lower_price_floor:
                    continue                            # below the floor: refuse
                if r in self.held:
                    continue                            # already loaded at this rung
                if self.cash < self.buy_usd * (1 + self.fee):
                    break                               # out of cash → stop trying lower
                self.cash -= self.buy_usd * (1 + self.fee)
                self.held[r] = self.buy_usd / rung_price
                self.trades += 1
                orders.append(("BUY", round(rung_price, 2), self.held[r]))

        elif r_now > self.r_prev:
            # Price stepped up → take profit at rung+1, retain infinity_tail_pct.
            for r in sorted(self.held.keys()):
                if r + 1 > r_now:
                    continue
                qty = self.held.pop(r)
                sell_price = self._price_at(r + 1)
                # Single fill: sell (1 - tail_pct) of the lot, retain the rest as tail.
                retain_qty = qty * self.infinity_tail_pct
                sell_qty = qty - retain_qty
                self.cash += sell_qty * sell_price * (1 - self.fee)
                self.trades += 1
                orders.append(("SELL", round(sell_price, 2), sell_qty))
                if retain_qty > 0:
                    # Enforce btc-position cap: tail cannot grow beyond capital × 1.05 in USD.
                    cap_qty = max(0.0, self.btc_position_cap_usd / sell_price - self.infinity_tail_qty)
                    if cap_qty <= 0:
                        # At cap → route the would-be retain straight to cash.
                        self.cash += retain_qty * sell_price * (1 - self.fee)
                    else:
                        keep = min(retain_qty, cap_qty)
                        self.infinity_tail_qty += keep
                        orders.append(("TAIL_RETAIN", round(sell_price, 2), keep))
                        spill = retain_qty - keep
                        if spill > 0:
                            self.cash += spill * sell_price * (1 - self.fee)

        self.r_prev = r_now
        return orders

    # ── account ───────────────────────────────────────────────────────────────

    def equity(self, price):
        return (self.cash
                + sum(q * price for q in self.held.values())
                + self.infinity_tail_qty * price)

    def btc_held(self):
        return sum(self.held.values()) + self.infinity_tail_qty

    def warmup(self, prices):
        """Pre-fill the MA + ATR from history without trading, so both filters
        are active from the first live bar (matches parent's contract).
        """
        for p in prices:
            if self.ma_q is not None:
                if len(self.ma_q) == self.ma_q.maxlen:
                    self.ma_sum -= self.ma_q[0]
                self.ma_q.append(p)
                self.ma_sum += p
            if self.last_close is not None:
                tr = abs(p - self.last_close)
                if len(self.atr_q) == self.atr_q.maxlen:
                    self.atr_sum -= self.atr_q[0]
                self.atr_q.append(tr)
                self.atr_sum += tr
                self.atr_value = self.atr_sum / len(self.atr_q)
            self.last_close = p
        if prices:
            self.r_prev = self._rung(prices[-1])

    # ── persistence (extends parent; backward-compatible) ─────────────────────

    def to_dict(self):
        d = super().to_dict()
        d.update({
            "state": self.state,
            "bars_below_ma": self.bars_below_ma,
            "bars_above_ma": self.bars_above_ma,
            "cooldown_bars_remaining": self.cooldown_bars_remaining,
            "initial_anchor_price": self.initial_anchor_price,
            "lower_price_floor": self.lower_price_floor,
            "atr_q": list(self.atr_q),
            "atr_sum": self.atr_sum,
            "atr_value": self.atr_value,
            "last_close": self.last_close,
            "peak_equity": self.peak_equity,
            "infinity_tail_qty": self.infinity_tail_qty,
        })
        return d

    def load_dict(self, d):
        super().load_dict(d)
        self.state = d.get("state", "RUNNING")
        self.bars_below_ma = d.get("bars_below_ma", 0)
        self.bars_above_ma = d.get("bars_above_ma", 0)
        self.cooldown_bars_remaining = d.get("cooldown_bars_remaining", 0)
        self.initial_anchor_price = d.get("initial_anchor_price")
        self.lower_price_floor = d.get("lower_price_floor")
        self.atr_q.clear()
        for x in d.get("atr_q", []):
            self.atr_q.append(x)
        self.atr_sum = d.get("atr_sum", 0.0)
        self.atr_value = d.get("atr_value")
        self.last_close = d.get("last_close")
        self.peak_equity = d.get("peak_equity", self.capital)
        self.infinity_tail_qty = d.get("infinity_tail_qty", 0.0)


# ── sanity test on daily data (matches more_bots.py style) ───────────────────

if __name__ == "__main__":
    import csv

    rows = list(csv.DictReader(open(Path(__file__).resolve().parent.parent
                                    / "rl_agent" / "data" / "btc_daily.csv")))
    closes = [float(r["close"]) for r in rows]
    yrs = len(rows) / 365.0

    def rep(name, eq, capital, extra=""):
        print(f"{name:<32} ${eq:>10,.0f}  ({(eq/capital - 1)*100:+6.1f}% over {yrs:.1f}y){extra}")

    cap = 10_000.0

    # Infinity Grid at spec defaults — note daily bars mean the 6h/12h hysteresis
    # collapses to ~1 bar; this is a smoke test, not a Gate 3 number.
    bot = InfinityGridBot(capital=cap)
    for p in closes:
        bot.on_close(p)
    n_stops = sum(1 for s in [bot.state] if s == "STOPPED")
    rep("Infinity Grid (defaults)", bot.equity(closes[-1]), cap,
        f"  tail={bot.infinity_tail_qty:.4f} BTC  trades={bot.trades}  end-state={bot.state}")

    # Balanced grid for comparison
    bal = GridBot(spacing=0.05, max_lots=20, ma_hours=15, capital=cap)   # 15-bar MA on daily ≈ 15d
    for p in closes:
        bal.on_close(p)
    rep("GridBot Balanced (5%/20)", bal.equity(closes[-1]), cap,
        f"  trades={bal.trades}")

    # BuyHold benchmark
    bh = cap * closes[-1] / closes[0]
    rep("Buy & Hold benchmark", bh, cap)

    print(f"\n(Daily-data smoke test only. Gate 3 hourly numbers come from "
          f"infinity_grid_backtest.py.)")
