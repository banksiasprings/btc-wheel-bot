"""
income_bots.py — extra paper-bot engines for the farm, beyond the grid.

FundingBot         — delta-neutral carry: hold spot + short perp, collect the
                     perp funding fee each hour. No price risk; only question
                     is whether funding stays positive. ~6%/yr historically.

LongVolBot         — the grid's complement: a long-volatility position
                     (modelled as a delta-hedged long straddle = "long
                     variance"). Profits when price moves MORE than implied
                     vol priced in, bleeds (theta) when calm. Wins exactly
                     when the grid struggles (sharp crashes / violent moves).
                     Simplified model, not a full options+hedging sim.

FundingDynamicBot  — slope-conditioned sibling of FundingBot. Sizes the short-
                     perp leg by the 24h OLS slope of hourly funding rather
                     than the level. Specialist for funding spikes (April 2021
                     alt-season, ETF launch, etc.) where level-based cousins
                     are still sized at last-week's mean. See
                     specs/04-funding-dynamic-spec.md.

All three are pure + persistable so the farm can step them hourly and resume.
"""

from __future__ import annotations

import math
from collections import deque


class FundingBot:
    def __init__(self, capital: float = 10_000.0, positive_only: bool = False,
                 leverage: float = 1.0):
        self.capital = capital
        self.equity = capital
        self.leverage = leverage
        self.notional = capital * leverage      # leverage amplifies the carry (and the bleed)
        self.positive_only = positive_only
        self.peak = capital
        self.steps = 0
        self.liquidated = False

    def step(self, rate_1h: float):
        """rate_1h = perpetual funding rate for this hour (signed fraction)."""
        if self.liquidated:
            return
        r = 0.0 if (self.positive_only and rate_1h < 0) else rate_1h
        self.equity += self.notional * r
        if self.equity <= 0:                     # leveraged carry can erode to $0 in a long negative-funding stretch
            self.equity = 0.0
            self.liquidated = True
        self.peak = max(self.peak, self.equity)
        self.steps += 1

    def equity_now(self):
        return self.equity

    def to_dict(self):
        return {"equity": self.equity, "peak": self.peak, "steps": self.steps,
                "liquidated": self.liquidated}

    def load_dict(self, d):
        self.equity = d["equity"]
        self.peak = d.get("peak", self.equity)
        self.steps = d.get("steps", 0)
        self.liquidated = d.get("liquidated", False)


class LongVolBot:
    K = 2.0   # scale: cumulative pnl/capital ≈ K × (realised_var − implied_var) over the period

    def __init__(self, capital: float = 10_000.0, leverage: float = 1.0,
                 dvol_max: float | None = None):
        self.capital = capital
        self.equity = capital
        self.notional = capital
        self.leverage = leverage
        self.dvol_max = dvol_max          # if set, only hold vol when implied vol is below this (buy it cheap)
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
        if self.dvol_max is not None and implied_vol_annual > self.dvol_max:
            self.prev_price = price       # vol too expensive → stand aside (no position, no theta, no gain)
            self.peak = max(self.peak, self.equity)
            self.steps += 1
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


class FundingDynamicBot(FundingBot):
    """Slope-conditioned funding bot — sizes a short-perp position by the OLS
    slope of the last N hourly funding rates, not by the level. Built per the
    locked Gate 2 spec (`specs/04-funding-dynamic-spec.md`).

    Sign convention:
        current_position > 0  → SHORT  (collects positive funding)
        current_position < 0  → LONG   (only enabled if allow_long_perp=True)
        current_position == 0 → flat

    Accrual mirrors the parent's `equity += notional × rate` but signed and
    scaled by the position fraction: `equity += notional × position × rate`.
    Each rebalance > `size_increment_step` charges `trade_cost_bps` on the
    notional moved — the parent never models a trade, so the cost is new.
    """

    def __init__(self, capital: float = 10_000.0,
                 slope_lookback_hours: int = 24,
                 slope_threshold: float = 2e-7,
                 size_increment_step: float = 0.10,
                 trade_cost_bps: float = 6.0,
                 allow_long_perp: bool = False,
                 positive_only: bool = False,
                 leverage: float = 1.0,
                 negative_funding_halt_hours: int = 168,
                 slope_saturation_mult: float = 5.0,
                 slope_saturation_streak_bars: int = 6,
                 min_position: float = 0.05):
        super().__init__(capital, positive_only, leverage)
        self.slope_lookback_hours = int(slope_lookback_hours)
        self.slope_threshold = float(slope_threshold)
        self.size_increment_step = float(size_increment_step)
        self.trade_cost_bps = float(trade_cost_bps)
        self.allow_long_perp = bool(allow_long_perp)
        self.negative_funding_halt_hours = int(negative_funding_halt_hours)
        self.slope_saturation_mult = float(slope_saturation_mult)
        self.slope_saturation_streak_bars = int(slope_saturation_streak_bars)
        self.min_position = float(min_position)
        # State
        self._buf: deque[float] = deque(maxlen=self.slope_lookback_hours)
        self.current_slope: float | None = None
        self.current_position = 0.0
        self.last_size_change_step = -1
        self.negative_funding_streak = 0
        self.slope_saturation_streak = 0
        self.in_negative_halt = False
        self.positive_funding_streak = 0
        self.total_trade_cost_paid = 0.0
        self.rebalances = 0

    @property
    def trades(self):
        # Dashboard parity: every other bot surfaces .trades.
        return self.rebalances

    def warmup(self, rates):
        """Seed the slope buffer from `slope_lookback_hours` historical rates so
        slope is computable from step 1 — mirrors DCASmartBot.warmup()."""
        for r in rates[-self.slope_lookback_hours:]:
            self._buf.append(float(r))
        self._recompute_slope()

    def _recompute_slope(self):
        n = len(self._buf)
        if n < self.slope_lookback_hours:
            self.current_slope = None
            return
        # Closed-form OLS slope of y vs uniform x=0..n-1. x_mean=(n-1)/2;
        # denom = n*(n²-1)/12. The y_mean term cancels because Σ(i - x_mean) = 0.
        x_mean = (n - 1) / 2.0
        num = 0.0
        for i, y in enumerate(self._buf):
            num += (i - x_mean) * y
        denom = n * (n * n - 1) / 12.0
        self.current_slope = num / denom if denom > 0 else 0.0

    def _target_size(self) -> float:
        if self.current_slope is None:
            return 0.0
        raw = self.current_slope / self.slope_threshold
        target = max(-1.0, min(1.0, raw))
        if not self.allow_long_perp:
            target = max(0.0, target)
        # Slope-saturation guard (§4): when |slope| > mult × threshold for ≥
        # streak hours, cap |target| at 0.5 — defends venue funding-cap clipping
        # (pitfall #6: saturated slope reads flat even though pressure builds).
        if self.slope_saturation_streak >= self.slope_saturation_streak_bars:
            target = max(-0.5, min(0.5, target))
        return target

    def step(self, rate_1h: float):
        if self.liquidated:
            return

        # 1. Slope buffer + recompute.
        self._buf.append(float(rate_1h))
        self._recompute_slope()

        # 2. Guard streaks.
        if self.current_slope is not None and \
                abs(self.current_slope) > self.slope_saturation_mult * self.slope_threshold:
            self.slope_saturation_streak += 1
        else:
            self.slope_saturation_streak = 0

        # Negative-funding catastrophe halt (§4): negative funding while short for
        # negative_funding_halt_hours consecutive hours → snap flat until 24h
        # consecutive positive funding clears the halt.
        if rate_1h < 0 and self.current_position > 0:
            self.negative_funding_streak += 1
            self.positive_funding_streak = 0
        elif rate_1h > 0:
            self.negative_funding_streak = 0
            if self.in_negative_halt:
                self.positive_funding_streak += 1
                if self.positive_funding_streak >= 24:
                    self.in_negative_halt = False
                    self.positive_funding_streak = 0
        if self.negative_funding_streak >= self.negative_funding_halt_hours:
            self.in_negative_halt = True
            self.negative_funding_streak = 0
            self.positive_funding_streak = 0

        # 3. Resize decision.
        raw_target = 0.0 if self.in_negative_halt else self._target_size()
        target = 0.0 if abs(raw_target) < self.min_position else raw_target

        delta = target - self.current_position
        force_close = (target == 0.0 and self.current_position != 0.0)
        if (abs(delta) >= self.size_increment_step or force_close) and abs(delta) > 1e-12:
            d_notional_usd = abs(delta) * self.notional
            cost = d_notional_usd * self.trade_cost_bps / 10_000.0
            self.equity -= cost
            self.total_trade_cost_paid += cost
            self.current_position = target
            self.rebalances += 1
            self.last_size_change_step = self.steps

        # 4. Funding accrual on the open notional. Signed by position direction:
        # short + positive funding = receive; long + positive funding = pay.
        signed = self.current_position * rate_1h
        if self.positive_only and signed < 0:
            signed = 0.0
        self.equity += self.notional * signed

        if self.equity <= 0:
            self.equity = 0.0
            self.liquidated = True
        self.peak = max(self.peak, self.equity)
        self.steps += 1

    def to_dict(self):
        d = super().to_dict()
        d.update({
            "buf": list(self._buf),
            "current_slope": self.current_slope,
            "current_position": self.current_position,
            "last_size_change_step": self.last_size_change_step,
            "negative_funding_streak": self.negative_funding_streak,
            "slope_saturation_streak": self.slope_saturation_streak,
            "in_negative_halt": self.in_negative_halt,
            "positive_funding_streak": self.positive_funding_streak,
            "total_trade_cost_paid": self.total_trade_cost_paid,
            "rebalances": self.rebalances,
        })
        return d

    def load_dict(self, d):
        super().load_dict(d)
        self._buf = deque(d.get("buf", []), maxlen=self.slope_lookback_hours)
        self.current_slope = d.get("current_slope")
        self.current_position = d.get("current_position", 0.0)
        self.last_size_change_step = d.get("last_size_change_step", -1)
        self.negative_funding_streak = d.get("negative_funding_streak", 0)
        self.slope_saturation_streak = d.get("slope_saturation_streak", 0)
        self.in_negative_halt = d.get("in_negative_halt", False)
        self.positive_funding_streak = d.get("positive_funding_streak", 0)
        self.total_trade_cost_paid = d.get("total_trade_cost_paid", 0.0)
        self.rebalances = d.get("rebalances", 0)


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
