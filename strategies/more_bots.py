"""
more_bots.py — extra paper-bot engines, to run more strategies in the farm.

  ShortVolBot   — sells volatility (short variance): earns in calm, loses in big
                  moves. The premium-seller / wheel spirit. Can liquidate if leveraged.
  TrendBot      — directional: holds BTC while price is above its moving average,
                  sits in cash below it. Rides uptrends, dodges downtrends (it bets).
  RebalanceBot  — holds a target % in BTC (e.g. 50/50) and rebalances on drift,
                  mechanically buying low and selling high.
  DCABot        — buys a fixed $ of BTC every interval until cash runs out, then holds.
  BuyHoldBot    — buys once and holds. The benchmark everything is measured against.

Uniform interface so the farm can drive them: step(...), equity_now(), and for the
price-holding ones equity(price) + btc_held() + .cash; to_dict/load_dict for resume.
"""

from __future__ import annotations

import math
from collections import deque

FEE = 0.0006


class ShortVolBot:
    K = 2.0

    def __init__(self, capital: float = 10_000.0, leverage: float = 1.0):
        self.capital = capital
        self.equity = capital
        self.notional = capital
        self.leverage = leverage
        self.prev_price = None
        self.peak = capital
        self.steps = 0
        self.liquidated = False

    def step(self, price: float, implied_vol_annual: float, periods_per_year: float = 365 * 24):
        if self.liquidated:
            return
        if self.prev_price is None or price <= 0:
            self.prev_price = price
            return
        r = math.log(price / self.prev_price)
        self.prev_price = price
        implied_var = (implied_vol_annual / 100.0) ** 2 / periods_per_year
        pnl = self.notional * self.K * self.leverage * (implied_var - r * r)   # + when calm
        self.equity += pnl
        if self.equity <= 0:
            self.equity = 0.0
            self.liquidated = True
        self.peak = max(self.peak, self.equity)
        self.steps += 1

    def equity_now(self):
        return self.equity

    def to_dict(self):
        return {"equity": self.equity, "peak": self.peak, "steps": self.steps,
                "prev_price": self.prev_price, "liquidated": self.liquidated}

    def load_dict(self, d):
        self.equity = d["equity"]
        self.peak = d.get("peak", self.equity)
        self.steps = d.get("steps", 0)
        self.prev_price = d.get("prev_price")
        self.liquidated = d.get("liquidated", False)


class _SpotBot:
    """Shared base for bots that hold BTC + cash (trend / rebalance / dca / buyhold)."""

    def __init__(self, capital=10_000.0, fee=FEE):
        self.capital = capital
        self.cash = capital
        self.btc = 0.0
        self.fee = fee
        self.last_price = None
        self.peak = capital
        self.steps = 0
        self.trades = 0

    def equity(self, price):
        return self.cash + self.btc * price

    def equity_now(self):
        return self.equity(self.last_price or 0.0)

    def btc_held(self):
        return self.btc

    def _bump(self, price):
        self.last_price = price
        self.peak = max(self.peak, self.equity(price))
        self.steps += 1

    def to_dict(self):
        return {"cash": self.cash, "btc": self.btc, "last_price": self.last_price,
                "peak": self.peak, "steps": self.steps, "trades": self.trades}

    def load_dict(self, d):
        self.cash = d["cash"]
        self.btc = d["btc"]
        self.last_price = d.get("last_price")
        self.peak = d.get("peak", self.capital)
        self.steps = d.get("steps", 0)
        self.trades = d.get("trades", 0)


class TrendBot(_SpotBot):
    def __init__(self, capital=10_000.0, ma_hours=168, fee=FEE):
        super().__init__(capital, fee)
        self.ma_q = deque(maxlen=ma_hours)
        self.ma_sum = 0.0

    def warmup(self, prices):
        for p in prices:
            if len(self.ma_q) == self.ma_q.maxlen:
                self.ma_sum -= self.ma_q[0]
            self.ma_q.append(p)
            self.ma_sum += p
        if prices:
            self.last_price = prices[-1]

    def step(self, price):
        if len(self.ma_q) == self.ma_q.maxlen:
            self.ma_sum -= self.ma_q[0]
        self.ma_q.append(price)
        self.ma_sum += price
        ma = self.ma_sum / len(self.ma_q)
        if price > ma and self.btc == 0.0 and self.cash > 0:        # turn long
            self.btc = self.cash / price * (1 - self.fee)
            self.cash = 0.0
            self.trades += 1
        elif price <= ma and self.btc > 0:                          # go to cash
            self.cash = self.btc * price * (1 - self.fee)
            self.btc = 0.0
            self.trades += 1
        self._bump(price)

    def to_dict(self):
        d = super().to_dict()
        d.update({"ma_q": list(self.ma_q), "ma_sum": self.ma_sum})
        return d

    def load_dict(self, d):
        super().load_dict(d)
        self.ma_sum = d.get("ma_sum", 0.0)
        self.ma_q.clear()
        for p in d.get("ma_q", []):
            self.ma_q.append(p)


class RebalanceBot(_SpotBot):
    def __init__(self, capital=10_000.0, target=0.5, band=0.1, fee=FEE):
        super().__init__(capital, fee)
        self.target = target
        self.band = band
        self._pending = capital * target     # buy initial BTC slice on first step

    def step(self, price):
        if self._pending > 0:
            self.btc = self._pending / price * (1 - self.fee)
            self.cash -= self._pending
            self._pending = 0.0
            self.trades += 1
        eq = self.equity(price)
        btc_val = self.btc * price
        frac = btc_val / eq if eq > 0 else 0.0
        if abs(frac - self.target) > self.band:                     # drifted → rebalance to target
            delta = eq * self.target - btc_val
            if delta > 0:
                buy = min(delta, self.cash)
                self.btc += buy / price * (1 - self.fee)
                self.cash -= buy
            else:
                self.btc -= (-delta) / price
                self.cash += (-delta) * (1 - self.fee)
            self.trades += 1
        self._bump(price)

    def to_dict(self):
        d = super().to_dict()
        d["_pending"] = self._pending
        return d

    def load_dict(self, d):
        super().load_dict(d)
        self._pending = d.get("_pending", 0.0)


class DCABot(_SpotBot):
    def __init__(self, capital=10_000.0, interval_hours=24, fee=FEE):
        super().__init__(capital, fee)
        self.interval = interval_hours
        self.buy_usd = capital / 30.0        # ~30 buys then hold

    def step(self, price):
        if self.steps % self.interval == 0 and self.cash >= self.buy_usd:
            self.btc += self.buy_usd / price * (1 - self.fee)
            self.cash -= self.buy_usd
            self.trades += 1
        self._bump(price)

    def to_dict(self):
        d = super().to_dict()
        d["buy_usd"] = self.buy_usd
        return d

    def load_dict(self, d):
        super().load_dict(d)
        self.buy_usd = d.get("buy_usd", self.capital / 30.0)


class BuyHoldBot(_SpotBot):
    def step(self, price):
        if self.btc == 0.0 and self.cash > 0:
            self.btc = self.cash / price * (1 - self.fee)
            self.cash = 0.0
            self.trades = 1
        self._bump(price)


# ── sanity test on daily data ─────────────────────────────────────────────────

if __name__ == "__main__":
    import csv
    from pathlib import Path

    rows = list(csv.DictReader(open(Path(__file__).resolve().parent.parent
                                    / "rl_agent" / "data" / "btc_daily.csv")))
    closes = [float(r["close"]) for r in rows]
    ivs = [float(r["iv"]) for r in rows]
    yrs = len(rows) / 365.0

    def rep(name, eq):
        print(f"{name:<22} ${eq:>9,.0f}  ({(eq/10000-1)*100:+5.0f}% over {yrs:.1f}y)")

    sv = ShortVolBot()
    for p, iv in zip(closes, ivs):
        sv.step(p, iv, periods_per_year=365)
    rep("ShortVol (premium)", sv.equity)

    for label, ma in (("Trend fast (7d)", 7), ("Trend slow (50d)", 50)):
        b = TrendBot(ma_hours=ma)
        for p in closes:
            b.step(p)
        rep(label, b.equity(closes[-1]))

    for cls, name in ((RebalanceBot, "Rebalance 50/50"), (DCABot, "DCA"), (BuyHoldBot, "Buy & Hold")):
        b = cls()
        for p in closes:
            b.step(p)
        rep(name, b.equity(closes[-1]))
