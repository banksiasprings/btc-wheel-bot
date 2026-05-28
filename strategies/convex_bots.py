"""
convex_bots.py — options-convexity paper-bot engines (the "big payoff" family).

Three structures that all profit from large moves, but with different shapes —
the pro cousins of the simplified LongVolBot:

  TailHedgeBot   — long FAR out-of-the-money puts = crash insurance. Bleeds a
                   small premium almost every hour; pays a big CONVEX payoff only
                   in a sharp DOWN move (a real crash). Designed to lose a little
                   most of the time and spike hard in disasters. Asymmetric (down).

  GammaScalpBot  — long a straddle and ACTIVELY delta-hedges: every time price
                   swings ~1 expected-move, it re-hedges and banks the gamma
                   profit. Net P&L ≈ realised − implied variance (like long-vol),
                   but earned through discrete TRADES — so unlike LongVolBot it
                   shows a trade count. Bleeds theta when the market sits still.

  BackspreadBot  — a put ratio back-spread (sell 1 near, buy 2 far). Cheap to
                   carry (near-zero / small credit in calm); has a "pain valley"
                   where a MODERATE move loses; a big CONVEX payoff on a LARGE
                   move. A cheaper lottery ticket than the straddle.

All are simplified per-step models (NOT a full options+hedging simulation), built
in the same spirit as income_bots.LongVolBot: cadence-correct (everything scaled
by the per-step implied variance), pure, and persistable so the farm can step them
hourly and resume. Uniform interface: step(price, implied_vol_annual,
periods_per_year), equity_now(), to_dict()/load_dict().
"""

from __future__ import annotations

import math


def _sigma_step(implied_vol_annual: float, periods_per_year: float) -> float:
    """One-step expected move (std-dev of log-return) implied by the vol index."""
    return (implied_vol_annual / 100.0) / math.sqrt(periods_per_year)


class TailHedgeBot:
    """Long far-OTM puts: small steady premium bleed, big convex payoff in a crash.

    Two decoupled knobs: a fixed annual premium you pay continuously (THETA_ANNUAL),
    and the payoff scale (PAYOFF_K) on DOWN moves beyond a FAR strike (~3 expected-
    moves out). Calibrated on real BTC daily data so it bleeds gently (~−1%/yr) in
    normal markets, is roughly flat through ordinary selloffs, and spikes hard only
    in a genuine flash-crash (e.g. +70% on a −30% day). Asymmetric: ignores up-moves.
    """

    STRIKE_MULT = 3.2      # puts ~3.2 expected-moves OTM → only a true tail move pays
    PAYOFF_K = 11.0        # payoff scale once past the strike
    THETA_ANNUAL = 0.10    # premium bled per YEAR (the cost of carrying the insurance)

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
        sig = _sigma_step(implied_vol_annual, periods_per_year)
        # convex payoff only on DOWN moves beyond the (far) strike distance
        excess = max(0.0, (-r) - self.STRIKE_MULT * sig) if r < 0 else 0.0
        payoff = self.PAYOFF_K * excess * excess
        premium_step = self.THETA_ANNUAL / periods_per_year
        pnl = self.notional * self.leverage * (payoff - premium_step)
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


class GammaScalpBot:
    """Long straddle + active delta-hedging — banks gamma profit on each swing.

    Net P&L tracks realised − implied variance (like long-vol), but it's earned in
    discrete re-hedge TRADES, so this bot actually shows a trade count.
    """

    K = 2.0
    SCALP_BAND = 1.0       # re-hedge (and bank a scalp) every ~1 expected-move

    def __init__(self, capital: float = 10_000.0, leverage: float = 1.0):
        self.capital = capital
        self.equity = capital
        self.notional = capital
        self.leverage = leverage
        self.prev_price = None
        self.ref_price = None      # last delta-hedge reference
        self.peak = capital
        self.steps = 0
        self.trades = 0            # number of scalp re-hedges (this bot DOES trade)
        self.liquidated = False

    def step(self, price: float, implied_vol_annual: float, periods_per_year: float = 365 * 24):
        if self.liquidated:
            return
        if self.prev_price is None or price <= 0:
            self.prev_price = price
            self.ref_price = price
            return
        self.prev_price = price
        implied_var_step = (implied_vol_annual / 100.0) ** 2 / periods_per_year
        sig = _sigma_step(implied_vol_annual, periods_per_year)
        # theta: pay for being long the straddle every step
        self.equity -= self.notional * self.K * self.leverage * implied_var_step
        # scalp: when price has swung past the band, bank the gamma profit + re-hedge
        move = math.log(price / self.ref_price) if self.ref_price else 0.0
        if abs(move) >= self.SCALP_BAND * sig and sig > 0:
            self.equity += self.notional * self.K * self.leverage * move * move
            self.ref_price = price
            self.trades += 1
        if self.equity <= 0:
            self.equity = 0.0
            self.liquidated = True
        self.peak = max(self.peak, self.equity)
        self.steps += 1

    def equity_now(self):
        return self.equity

    def to_dict(self):
        return {"equity": self.equity, "peak": self.peak, "steps": self.steps,
                "trades": self.trades, "prev_price": self.prev_price,
                "ref_price": self.ref_price, "liquidated": self.liquidated}

    def load_dict(self, d):
        self.equity = d["equity"]
        self.peak = d.get("peak", self.equity)
        self.steps = d.get("steps", 0)
        self.trades = d.get("trades", 0)
        self.prev_price = d.get("prev_price")
        self.ref_price = d.get("ref_price", d.get("prev_price"))
        self.liquidated = d.get("liquidated", False)


class BackspreadBot:
    """Put ratio back-spread: cheap carry, small loss on moderate moves, big payoff on large ones."""

    K = 2.0
    T1 = 1.0       # below ~1 expected-move: calm, collect a small credit
    T2 = 2.3       # above ~2.3 expected-moves: the long tail pays off convexly
    W_MID = 1.2    # penalty weight in the "pain valley" (short the near strike)
    W_BIG = 2.5    # payoff weight in the tail (long 2× the far strikes)
    CREDIT = 0.05  # small positive carry in calm (the spread is financed)

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
        implied_var_step = (implied_vol_annual / 100.0) ** 2 / periods_per_year
        sig = _sigma_step(implied_vol_annual, periods_per_year)
        m = abs(r) / sig if sig > 0 else 0.0      # move size in expected-moves
        mid = max(0.0, min(m, self.T2) - self.T1)  # how far into the pain valley
        big = max(0.0, m - self.T2)                 # how far into the long tail
        shape = self.CREDIT - self.W_MID * mid * mid + self.W_BIG * big * big
        pnl = self.notional * self.K * self.leverage * implied_var_step * shape
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


# ── sanity test on real daily data ────────────────────────────────────────────

if __name__ == "__main__":
    import csv
    from pathlib import Path

    rows = list(csv.DictReader(open(Path(__file__).resolve().parent.parent
                                    / "rl_agent" / "data" / "btc_daily.csv")))
    closes = [float(r["close"]) for r in rows]
    ivs = [float(r["iv"]) for r in rows]
    dates = [r.get("date", r.get("timestamp", "")) for r in rows]
    yrs = len(rows) / 365.0
    PPY = 365

    def run(bot):
        for p, iv in zip(closes, ivs):
            bot.step(p, iv, periods_per_year=PPY)
        return bot

    def rep(name, bot):
        eq = bot.equity
        apr = (eq / bot.capital) ** (1 / yrs) - 1 if eq > 0 else -1
        tr = f"  {bot.trades} trades" if hasattr(bot, "trades") else ""
        liq = "  💀LIQUIDATED" if bot.liquidated else ""
        print(f"{name:<20} ${eq:>9,.0f}  ({(eq/bot.capital-1)*100:+5.0f}% over {yrs:.1f}y "
              f"= {apr*100:+5.0f}%/yr){tr}{liq}")

    print(f"=== convex bots over {yrs:.1f}y of real BTC daily data ===")
    rep("Tail hedge", run(TailHedgeBot()))
    rep("Gamma scalp", run(GammaScalpBot()))
    rep("Backspread", run(BackspreadBot()))

    # Profile check: how does each behave in a crash window vs a calm window?
    def window(lbl, lo, hi):
        seg = [(p, iv) for d, p, iv in zip(dates, closes, ivs) if lo <= d < hi]
        if len(seg) < 5:
            print(f"  ({lbl}: no data)")
            return
        out = []
        for cls, nm in ((TailHedgeBot, "tail"), (GammaScalpBot, "gamma"), (BackspreadBot, "back")):
            b = cls()
            for p, iv in seg:
                b.step(p, iv, periods_per_year=PPY)
            out.append(f"{nm} {(b.equity/b.capital-1)*100:+.0f}%")
        chg = (seg[-1][0] / seg[0][0] - 1) * 100
        print(f"  {lbl:<16} BTC {chg:+5.0f}%  →  " + " · ".join(out))

    print("--- regime profiles (does tail/back spike in crashes, gamma track moves?) ---")
    window("Jan-Feb26 crash", "2026-01-20", "2026-02-15")
    window("Aug-24 selloff", "2024-07-25", "2024-08-10")
    window("2023-H2 calm", "2023-08-01", "2023-11-01")
    window("2024 bull run", "2024-02-01", "2024-04-01")
    # a synthetic single-day −30% flash crash proves the tail bot's convex payoff fires
    for cls, nm in ((TailHedgeBot, "tail"), (BackspreadBot, "back")):
        b = cls()
        for p, iv in ((100.0, 60.0), (70.0, 60.0)):
            b.step(p, iv, periods_per_year=PPY)
        print(f"  flash −30% day → {nm} {(b.equity / b.capital - 1) * 100:+.0f}%")
