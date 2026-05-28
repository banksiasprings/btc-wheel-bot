"""
income_bots.py — two extra paper-bot engines for the farm, beyond the grid.

FundingBot  — delta-neutral carry: hold spot + short perp so price cancels out,
              collect the perpetual funding fee each hour. No price risk; the only
              question is whether funding stays positive. ~6%/yr historically.

LongVolBot  — the grid's complement: a long-volatility position (modelled as a
              delta-hedged long straddle = "long variance"). Profits when price
              moves MORE than its implied vol priced in, bleeds (theta) when calm.
              Wins exactly when the grid struggles (sharp crashes / violent moves).
              This is a simplified model, not a full options+hedging simulation.

Both are pure + persistable so the farm can step them hourly and resume.
"""

from __future__ import annotations

import math


class FundingBot:
    def __init__(self, capital: float = 10_000.0, positive_only: bool = False):
        self.capital = capital
        self.equity = capital
        self.notional = capital          # 1x, no leverage
        self.positive_only = positive_only
        self.peak = capital
        self.steps = 0

    def step(self, rate_1h: float):
        """rate_1h = perpetual funding rate for this hour (signed fraction)."""
        r = 0.0 if (self.positive_only and rate_1h < 0) else rate_1h
        self.equity += self.notional * r
        self.peak = max(self.peak, self.equity)
        self.steps += 1

    def equity_now(self):
        return self.equity

    def to_dict(self):
        return {"equity": self.equity, "peak": self.peak, "steps": self.steps}

    def load_dict(self, d):
        self.equity = d["equity"]
        self.peak = d.get("peak", self.equity)
        self.steps = d.get("steps", 0)


class LongVolBot:
    K = 2.0   # scale: cumulative pnl/capital ≈ K × (realised_var − implied_var) over the period

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
        """price = current BTC price; implied_vol_annual = DVOL-style implied vol (%)."""
        if self.liquidated:
            return
        if self.prev_price is None or price <= 0:
            self.prev_price = price
            return
        r = math.log(price / self.prev_price)
        self.prev_price = price
        implied_var_step = (implied_vol_annual / 100.0) ** 2 / periods_per_year
        pnl = self.notional * self.K * self.leverage * (r * r - implied_var_step)
        self.equity += pnl
        if self.equity <= 0:                 # leveraged long-vol can (rarely) be wiped out
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


# ── sanity tests on historical data ───────────────────────────────────────────

if __name__ == "__main__":
    import json
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent

    # Funding: step over historical 1h funding rates
    fr = json.load(open(ROOT / "data" / "raw" / "deribit" / "funding_rates.json"))
    rows = sorted(fr["data"], key=lambda r: r["timestamp"])
    rates = [r["interest_1h"] for r in rows]
    for label, po in (("always-on", False), ("positive-only", True)):
        b = FundingBot(positive_only=po)
        for r in rates:
            b.step(r)
        ann = (sum(0.0 if (po and r < 0) else r for r in rates) / len(rates)) * 24 * 365 * 100
        print(f"FundingBot ({label:<13}): final ${b.equity:,.0f}  | deployed-APR ~{ann:.1f}%")

    # Long-vol: step over daily close + real Deribit IV (ppy=365 for daily bars)
    import csv
    rows = list(csv.DictReader(open(ROOT / "rl_agent" / "data" / "btc_daily.csv")))
    for lev in (1.0, 2.0):
        b = LongVolBot(leverage=lev)
        for row in rows:
            b.step(float(row["close"]), float(row["iv"]), periods_per_year=365)
        yrs = len(rows) / 365.0
        ret = b.equity / b.capital - 1
        apr = (b.equity / b.capital) ** (1 / yrs) - 1 if b.equity > 0 else -1
        print(f"LongVolBot  ({lev:.0f}x          ): final ${b.equity:,.0f}  "
              f"({ret*100:+.0f}% over {yrs:.1f}y = {apr*100:+.0f}%/yr){'  💀LIQUIDATED' if b.liquidated else ''}")
