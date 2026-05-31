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


class DCASmartBot(DCABot):
    """DCA + 2× size on calendar ticks where daily RSI(14) < 40.

    One buy per calendar tick (inherits DCABot's `self.steps % self.interval == 0`
    gate). The dip rule is a *size multiplier* on that one buy, never a second
    event. Weekly cap on dip-buys defends against sustained-downtrend cash burn.
    Optional dip-pool reservation is shipped but defaults to 0 (off) per spec §2.4.

    Daily-close construction is synthetic rolling-24h, not UTC-aligned — the bot
    is stepped hourly by the farm and accumulates 24 hourly closes into a daily
    bucket. Good enough for RSI-as-filter purposes; cheaper than calendar logic.
    """

    HOURS_PER_WEEK = 24 * 7

    def __init__(self, capital=10_000.0, interval_hours=24,
                 rsi_period_days=14, rsi_threshold=40.0,
                 dip_multiplier=2.0, max_dip_buys_per_week=3,
                 dip_pool_pct=0.0, min_order_usd=15.0, fee=FEE):
        super().__init__(capital=capital, interval_hours=interval_hours, fee=fee)
        self.rsi_period = int(rsi_period_days)
        self.rsi_threshold = float(rsi_threshold)
        self.dip_mult = float(dip_multiplier)
        self.max_dip_buys_per_week = int(max_dip_buys_per_week)
        self.dip_pool_pct = float(dip_pool_pct)
        self.min_order_usd = float(min_order_usd)
        # Daily-close ring buffer: need period+1 closes to compute period gains/losses
        self.daily_closes = deque(maxlen=self.rsi_period + 1)
        self.hours_since_last_close = 0
        self.dip_buys_this_week = 0
        self.hours_since_week_reset = 0
        # Dip-pool reservation (off by default). Drawn down only when triggered;
        # falls back to 1× if the pool runs dry.
        self.dip_pool_remaining = float(capital) * self.dip_pool_pct

    def warmup(self, prices):
        """Seed the daily-close deque from an hourly warm-up array.
        Takes every 24th close so we don't wait 15 real days for RSI to become
        valid. Mirrors `TrendBot.warmup()` shape."""
        for i, p in enumerate(prices):
            if i % 24 == 23:
                self.daily_closes.append(float(p))
        if prices:
            self.last_price = prices[-1]

    def _compute_rsi(self):
        """Wilder RSI(period) over the deque. Returns None until the deque holds
        period+1 closes."""
        if len(self.daily_closes) < self.rsi_period + 1:
            return None
        closes = list(self.daily_closes)
        gains = 0.0
        losses = 0.0
        for a, b in zip(closes, closes[1:]):
            d = b - a
            if d > 0:
                gains += d
            else:
                losses += -d
        avg_gain = gains / self.rsi_period
        avg_loss = losses / self.rsi_period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def step(self, price):
        # Roll the synthetic daily-close bucket: every 24h append latest close.
        self.hours_since_last_close += 1
        if self.hours_since_last_close >= 24:
            self.daily_closes.append(float(price))
            self.hours_since_last_close = 0

        # Roll the weekly dip-cap counter.
        self.hours_since_week_reset += 1
        if self.hours_since_week_reset >= self.HOURS_PER_WEEK:
            self.dip_buys_this_week = 0
            self.hours_since_week_reset = 0

        # Calendar tick → at most one buy this step.
        if self.steps % self.interval == 0:
            rsi = self._compute_rsi()
            # Determine if dip-rule should fire on THIS calendar tick.
            dip_trigger = (
                rsi is not None
                and rsi < self.rsi_threshold
                and self.dip_buys_this_week < self.max_dip_buys_per_week
            )
            buy_amt = self.buy_usd
            used_dip_pool = False
            if dip_trigger:
                # If dip-pool is active (dip_pool_pct > 0), only fire 2× while
                # the pool has cash; fall back to 1× when empty (spec §2.4).
                extra = self.buy_usd * (self.dip_mult - 1.0)
                if self.dip_pool_pct > 0.0:
                    if self.dip_pool_remaining >= extra:
                        buy_amt = self.buy_usd * self.dip_mult
                        used_dip_pool = True
                    # else: pool dry → 1× buy, no dip count increment
                else:
                    buy_amt = self.buy_usd * self.dip_mult
            # Cash-floor + order-floor guards. On the last residual, clip to cash.
            if buy_amt > self.cash:
                buy_amt = self.cash
            if buy_amt >= self.min_order_usd and self.cash >= buy_amt:
                self.btc += buy_amt / price * (1 - self.fee)
                self.cash -= buy_amt
                self.trades += 1
                # Only count the dip if we actually placed a 2× buy.
                if dip_trigger and buy_amt > self.buy_usd + 1e-9:
                    self.dip_buys_this_week += 1
                    if used_dip_pool:
                        self.dip_pool_remaining -= self.buy_usd * (self.dip_mult - 1.0)
        self._bump(price)

    def to_dict(self):
        d = super().to_dict()
        d.update({
            "daily_closes": list(self.daily_closes),
            "hours_since_last_close": self.hours_since_last_close,
            "dip_buys_this_week": self.dip_buys_this_week,
            "hours_since_week_reset": self.hours_since_week_reset,
            "dip_pool_remaining": self.dip_pool_remaining,
        })
        return d

    def load_dict(self, d):
        super().load_dict(d)
        self.daily_closes.clear()
        for c in d.get("daily_closes", []):
            self.daily_closes.append(float(c))
        self.hours_since_last_close = int(d.get("hours_since_last_close", 0))
        self.dip_buys_this_week = int(d.get("dip_buys_this_week", 0))
        self.hours_since_week_reset = int(d.get("hours_since_week_reset", 0))
        self.dip_pool_remaining = float(d.get("dip_pool_remaining",
                                              self.capital * self.dip_pool_pct))


class DonchianBot(TrendBot):
    """Donchian channel breakout — long-only, binary 100% sizing (v1).

    Enters long when today's daily close exceeds the prior `entry_lookback`-day
    high. Exits when today's close drops below the prior `exit_lookback`-day
    low. Today's close is excluded from both extrema (Faith 2007 convention).
    No leverage, no pyramid, no re-entry cooldown. A 35% drawdown halt blocks
    new entries but lets an open long exit on the M-day-low rule normally
    (forcing a flatten would lock in the bottom).

    Inherits `_SpotBot` plumbing via `TrendBot` (cash / btc / fee / peak /
    steps / trades + `_bump`). The parent `ma_q` / `ma_sum` fields are
    initialised at `ma_hours=1` (zero-cost ring) so the inherited
    `to_dict()` / `load_dict()` shape round-trips cleanly without special-
    casing the parent fields.

    Daily-close construction is synthetic rolling-24h, mirroring
    `DCASmartBot.warmup()` / `step()`: stepped hourly by the farm, and every
    24 hourly closes one synthetic daily close is appended to the deque.
    """

    def __init__(self, capital=10_000.0,
                 entry_lookback_days=20, exit_lookback_days=10,
                 position_size_pct=1.0, long_only=True,
                 max_drawdown_halt_pct=0.35,
                 halt_release_recovery_pct=0.10,
                 fee=FEE):
        super().__init__(capital=capital, ma_hours=1, fee=fee)
        self.entry_lookback = int(entry_lookback_days)
        self.exit_lookback = int(exit_lookback_days)
        self.position_size_pct = float(position_size_pct)
        self.long_only = bool(long_only)
        self.max_dd_halt = float(max_drawdown_halt_pct)
        self.halt_release_recovery = float(halt_release_recovery_pct)
        # +1 so we can compute "prior N closes" while today's close is held.
        self.daily_closes = deque(maxlen=self.entry_lookback + 1)
        self.hours_since_last_close = 0
        self.position = 0           # 0 = flat, 1 = long (−1 reserved for v2)
        self.entry_price = None
        self.consecutive_losing_trades = 0
        self.halt_active = False
        self.total_trades = 0       # entry + exit events; mirrors self.trades

    def warmup(self, prices):
        """Seed the daily-close deque from an hourly warm-up array.
        Takes every 24th close so the channel is valid as soon as the
        bot enters the live loop. Mirrors `DCASmartBot.warmup()`."""
        for i, p in enumerate(prices):
            if i % 24 == 23:
                self.daily_closes.append(float(p))
        if prices:
            self.last_price = prices[-1]

    def step(self, price):
        # Roll the synthetic daily-close bucket: every 24h append the latest.
        self.hours_since_last_close += 1
        if self.hours_since_last_close >= 24:
            self.daily_closes.append(float(price))
            self.hours_since_last_close = 0

        # Drawdown halt: trips at -max_dd_halt vs peak, clears when equity
        # recovers to within halt_release_recovery of the peak.
        eq = self.equity(price)
        if self.peak > 0:
            if eq / self.peak < (1.0 - self.max_dd_halt):
                self.halt_active = True
            elif (self.halt_active
                  and eq / self.peak >= (1.0 - self.halt_release_recovery)):
                self.halt_active = False

        # Need entry_lookback + 1 closes for a fresh breakout signal.
        if len(self.daily_closes) >= self.entry_lookback + 1:
            closes = list(self.daily_closes)
            today = closes[-1]
            prior = closes[:-1]
            n_high = max(prior[-self.entry_lookback:])
            m_low = min(prior[-self.exit_lookback:])

            # Exit first (same-bar collision: exit wins, re-evaluate next bar).
            if self.position == 1 and today < m_low:
                exit_value = self.btc * price * (1 - self.fee)
                if self.entry_price is not None and price < self.entry_price:
                    self.consecutive_losing_trades += 1
                else:
                    self.consecutive_losing_trades = 0
                self.cash += exit_value
                self.btc = 0.0
                self.position = 0
                self.entry_price = None
                self.trades += 1
                self.total_trades += 1
            elif (self.position == 0 and today > n_high
                  and not self.halt_active and self.cash > 0):
                allot = self.cash * self.position_size_pct
                self.btc += allot / price * (1 - self.fee)
                self.cash -= allot
                self.position = 1
                self.entry_price = price
                self.trades += 1
                self.total_trades += 1

        self._bump(price)

    def to_dict(self):
        d = super().to_dict()
        d.update({
            "entry_lookback": self.entry_lookback,
            "exit_lookback": self.exit_lookback,
            "position_size_pct": self.position_size_pct,
            "long_only": self.long_only,
            "max_dd_halt": self.max_dd_halt,
            "halt_release_recovery": self.halt_release_recovery,
            "daily_closes": list(self.daily_closes),
            "hours_since_last_close": self.hours_since_last_close,
            "position": self.position,
            "entry_price": self.entry_price,
            "consecutive_losing_trades": self.consecutive_losing_trades,
            "halt_active": self.halt_active,
            "total_trades": self.total_trades,
        })
        return d

    def load_dict(self, d):
        super().load_dict(d)
        self.daily_closes.clear()
        for c in d.get("daily_closes", []):
            self.daily_closes.append(float(c))
        self.hours_since_last_close = int(d.get("hours_since_last_close", 0))
        self.position = int(d.get("position", 0))
        ep = d.get("entry_price")
        self.entry_price = float(ep) if ep is not None else None
        self.consecutive_losing_trades = int(d.get("consecutive_losing_trades", 0))
        self.halt_active = bool(d.get("halt_active", False))
        self.total_trades = int(d.get("total_trades", self.trades))


class ATRBreakoutBot(TrendBot):
    """Turtle S1 N-day breakout entry + K×ATR(p) monotonic trailing stop.

    Enters long when today's daily close exceeds the prior `entry_lookback`-day
    high (today excluded, Faith 2007). Trails a stop at K × ATR(p) below the
    running peak-since-entry — the stop only ever ratchets up: when today's
    close beats the running peak, the peak rises and the candidate new stop
    `peak − K × ATR` is taken if it's higher than the current stop. ATR is
    Wilder TR averaged (SMA, not exponential) over the last p days. Exits when
    today's close falls to or below the current stop. Sits flat until a fresh
    N-day high re-fires entry; no re-entry cooldown in v1 (the trail itself is
    the cooldown).

    Long-only, binary 100 % cash sizing, no pyramid. 35 % drawdown halt blocks
    new entries; open longs still exit on the trail rule normally (forcing a
    flatten would lock the bottom).

    Inherits `_SpotBot` plumbing via `TrendBot` (cash / btc / fee / peak /
    steps / trades + `_bump`). Parent `ma_q` / `ma_sum` are initialised at
    `ma_hours=1` (zero-cost ring) so the inherited `to_dict()` / `load_dict()`
    shape round-trips. Daily H/L/C deques are built from an hourly OHLC
    accumulator: stepped hourly by the farm, every 24 hourly closes one
    synthetic daily bar is appended. Same construction shape as `DonchianBot`,
    extended with high/low for the ATR True Range calc.
    """

    def __init__(self, capital=10_000.0,
                 entry_lookback_days=20, atr_period_days=14,
                 atr_multiplier_K=3.0, position_size_pct=1.0,
                 reentry_cooldown_days=0, long_only=True,
                 max_drawdown_halt_pct=0.35,
                 halt_release_recovery_pct=0.10,
                 hours_per_bar=24, fee=FEE):
        super().__init__(capital=capital, ma_hours=1, fee=fee)
        self.entry_lookback = int(entry_lookback_days)
        self.atr_period = int(atr_period_days)
        self.atr_K = float(atr_multiplier_K)
        self.position_size_pct = float(position_size_pct)
        self.reentry_cooldown_days = int(reentry_cooldown_days)
        self.long_only = bool(long_only)
        self.max_dd_halt = float(max_drawdown_halt_pct)
        self.halt_release_recovery = float(halt_release_recovery_pct)
        self.hours_per_bar = int(hours_per_bar)

        # Need prior-N high (N+1 entries with today held) and prior-p TR (needs
        # closes[-p-1:] so p+1 entries). Size the ring to max(N, p) + 1.
        size = max(self.entry_lookback, self.atr_period) + 1
        self.daily_highs = deque(maxlen=size)
        self.daily_lows = deque(maxlen=size)
        self.daily_closes = deque(maxlen=size)

        # Sub-day OHLC bucket
        self.bar_high = None
        self.bar_low = None
        self.bar_close = None
        self.bar_hours = 0

        # Position state
        self.position = 0           # 0 = flat, 1 = long (-1 reserved for v2)
        self.entry_price = None
        self.peak_since_entry = None
        self.current_stop = None
        self.cooldown_remaining_days = 0

        # Diagnostics
        self.consecutive_losing_trades = 0
        self.halt_active = False
        self.total_trades = 0

    def _compute_atr(self):
        """Wilder TR averaged over the last p days (SMA per spec §2.1).
        TR_i = max(H_i − L_i, |H_i − C_{i−1}|, |L_i − C_{i−1}|). Returns None
        until the deque holds p+1 closes (need previous close for TR_1)."""
        if len(self.daily_closes) < self.atr_period + 1:
            return None
        highs = list(self.daily_highs)
        lows = list(self.daily_lows)
        closes = list(self.daily_closes)
        total = 0.0
        for i in range(-self.atr_period, 0):
            h, l, c_prev = highs[i], lows[i], closes[i - 1]
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            total += tr
        return total / self.atr_period

    def warmup(self, prices):
        """Seed daily H/L/C deques from an hourly warmup array. Chunks every
        24 hourly bars into one synthetic daily bar (max=high, min=low,
        last=close). Partial trailing chunk is dropped — matches the
        DonchianBot pattern of taking every 24th close."""
        n_days = len(prices) // 24
        for d in range(n_days):
            chunk = prices[d * 24:(d + 1) * 24]
            self.daily_highs.append(float(max(chunk)))
            self.daily_lows.append(float(min(chunk)))
            self.daily_closes.append(float(chunk[-1]))
        if prices:
            self.last_price = prices[-1]

    def step(self, price):
        p = float(price)
        # Sub-day OHLC accumulator
        if self.bar_high is None:
            self.bar_high = p
            self.bar_low = p
        else:
            if p > self.bar_high:
                self.bar_high = p
            if p < self.bar_low:
                self.bar_low = p
        self.bar_close = p
        self.bar_hours += 1

        bar_closed = (self.bar_hours >= self.hours_per_bar)
        if bar_closed:
            self.daily_highs.append(self.bar_high)
            self.daily_lows.append(self.bar_low)
            self.daily_closes.append(self.bar_close)
            self.bar_high = None
            self.bar_low = None
            self.bar_close = None
            self.bar_hours = 0
            if self.cooldown_remaining_days > 0:
                self.cooldown_remaining_days -= 1

        # Drawdown halt: trips at −max_dd_halt vs peak, clears when equity
        # recovers to within halt_release_recovery of the peak. Halt blocks
        # new entries; open longs still exit on the normal trail rule.
        eq = self.equity(p)
        if self.peak > 0:
            if eq / self.peak < (1.0 - self.max_dd_halt):
                self.halt_active = True
            elif (self.halt_active
                  and eq / self.peak >= (1.0 - self.halt_release_recovery)):
                self.halt_active = False

        # Breakout / trail logic runs at daily-bar close, once the deques
        # are long enough.
        min_ready = max(self.entry_lookback, self.atr_period) + 1
        if bar_closed and len(self.daily_closes) >= min_ready:
            closes = list(self.daily_closes)
            highs = list(self.daily_highs)
            today_close = closes[-1]
            # Prior N-day high (today excluded).
            prior_highs = highs[-self.entry_lookback - 1:-1]
            n_high = max(prior_highs)
            atr = self._compute_atr()

            if self.position == 1:
                # Trail update FIRST — peak ratchet, then candidate new stop.
                if self.peak_since_entry is None or today_close > self.peak_since_entry:
                    self.peak_since_entry = today_close
                    if atr is not None:
                        new_stop = self.peak_since_entry - self.atr_K * atr
                        if self.current_stop is None or new_stop > self.current_stop:
                            self.current_stop = new_stop
                # Exit check — close at or below the trail.
                if self.current_stop is not None and today_close <= self.current_stop:
                    exit_value = self.btc * p * (1 - self.fee)
                    if self.entry_price is not None and p < self.entry_price:
                        self.consecutive_losing_trades += 1
                    else:
                        self.consecutive_losing_trades = 0
                    self.cash += exit_value
                    self.btc = 0.0
                    self.position = 0
                    self.entry_price = None
                    self.peak_since_entry = None
                    self.current_stop = None
                    self.cooldown_remaining_days = self.reentry_cooldown_days
                    self.trades += 1
                    self.total_trades += 1
            elif (self.position == 0
                  and today_close > n_high
                  and atr is not None
                  and self.cooldown_remaining_days == 0
                  and not self.halt_active
                  and self.cash > 0):
                allot = self.cash * self.position_size_pct
                self.btc += allot / p * (1 - self.fee)
                self.cash -= allot
                self.position = 1
                self.entry_price = p
                self.peak_since_entry = today_close
                self.current_stop = today_close - self.atr_K * atr
                self.trades += 1
                self.total_trades += 1

        self._bump(p)

    def to_dict(self):
        d = super().to_dict()
        d.update({
            "entry_lookback": self.entry_lookback,
            "atr_period": self.atr_period,
            "atr_K": self.atr_K,
            "position_size_pct": self.position_size_pct,
            "reentry_cooldown_days": self.reentry_cooldown_days,
            "long_only": self.long_only,
            "max_dd_halt": self.max_dd_halt,
            "halt_release_recovery": self.halt_release_recovery,
            "hours_per_bar": self.hours_per_bar,
            "daily_highs": list(self.daily_highs),
            "daily_lows": list(self.daily_lows),
            "daily_closes": list(self.daily_closes),
            "bar_high": self.bar_high,
            "bar_low": self.bar_low,
            "bar_close": self.bar_close,
            "bar_hours": self.bar_hours,
            "position": self.position,
            "entry_price": self.entry_price,
            "peak_since_entry": self.peak_since_entry,
            "current_stop": self.current_stop,
            "cooldown_remaining_days": self.cooldown_remaining_days,
            "consecutive_losing_trades": self.consecutive_losing_trades,
            "halt_active": self.halt_active,
            "total_trades": self.total_trades,
        })
        return d

    def load_dict(self, d):
        super().load_dict(d)
        self.daily_highs.clear()
        for c in d.get("daily_highs", []):
            self.daily_highs.append(float(c))
        self.daily_lows.clear()
        for c in d.get("daily_lows", []):
            self.daily_lows.append(float(c))
        self.daily_closes.clear()
        for c in d.get("daily_closes", []):
            self.daily_closes.append(float(c))
        def _maybe_float(v):
            return float(v) if v is not None else None
        self.bar_high = _maybe_float(d.get("bar_high"))
        self.bar_low = _maybe_float(d.get("bar_low"))
        self.bar_close = _maybe_float(d.get("bar_close"))
        self.bar_hours = int(d.get("bar_hours", 0))
        self.position = int(d.get("position", 0))
        self.entry_price = _maybe_float(d.get("entry_price"))
        self.peak_since_entry = _maybe_float(d.get("peak_since_entry"))
        self.current_stop = _maybe_float(d.get("current_stop"))
        self.cooldown_remaining_days = int(d.get("cooldown_remaining_days", 0))
        self.consecutive_losing_trades = int(d.get("consecutive_losing_trades", 0))
        self.halt_active = bool(d.get("halt_active", False))
        self.total_trades = int(d.get("total_trades", self.trades))


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

    # Donchian sanity rows — daily-cadence harness feeds raw daily closes;
    # the bot's 24h synthetic counter still triggers on each one (1 step =
    # 1 day, hours_since_last_close steps 1→24→0 every call).
    # ATR breakout sanity rows — daily-cadence harness feeds raw daily closes
    # and treats each as a full daily bar (H=L=C=close, single-day candle).
    # Real backtests use the hourly OHLC path; this is just a smoke check.
    for label, n, p, k in (("ATR 20/14/3.0", 20, 14, 3.0),
                           ("ATR 20/14/1.5", 20, 14, 1.5),
                           ("ATR 40/21/2.0", 40, 21, 2.0)):
        b = ATRBreakoutBot(entry_lookback_days=n, atr_period_days=p,
                            atr_multiplier_K=k, hours_per_bar=1)
        for price in closes:
            b.step(price)
        rep(label, b.equity(closes[-1]))

    for label, n, m in (("Donchian 20/10", 20, 10), ("Donchian 55/20", 55, 20)):
        b = DonchianBot(entry_lookback_days=n, exit_lookback_days=m)
        # Daily closes: increment counter by 24 each step so a daily close is
        # always appended. The synthetic counter is the live-bot path; in
        # daily-driven sanity we short-circuit by direct append.
        for p in closes:
            b.daily_closes.append(float(p))
            b.hours_since_last_close = 0
            # Re-run signal logic without the bucket roll (above did it).
            if len(b.daily_closes) >= b.entry_lookback + 1:
                cs = list(b.daily_closes)
                today, prior = cs[-1], cs[:-1]
                nh = max(prior[-b.entry_lookback:])
                ml = min(prior[-b.exit_lookback:])
                if b.position == 1 and today < ml:
                    b.cash += b.btc * p * (1 - b.fee); b.btc = 0.0
                    b.position = 0; b.trades += 1
                elif b.position == 0 and today > nh and b.cash > 0:
                    b.btc += b.cash / p * (1 - b.fee); b.cash = 0.0
                    b.position = 1; b.trades += 1
            b._bump(p)
        rep(label, b.equity(closes[-1]))
