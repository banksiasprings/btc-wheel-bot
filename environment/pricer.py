"""
Black-Scholes pricer + Greeks for the RL environment.

All quantities are in *USD* unless noted: option prices, strikes, spot. Greek
conventions:
  delta    dC/dS (calls 0..1, puts -1..0)
  gamma    d²C/dS²  (>0 for both calls and puts)
  theta    dC/dt  — per *day* (T is in years internally; we convert)
  vega     dC/dsigma — per *1.0* change in vol (i.e. 100 vol-points). We expose
                       it per 1 vol-point (i.e. divide by 100) so a typical
                       front-month BTC vega prints in the same order as price.

Sign convention for portfolio aggregation: positive `qty` = long, negative =
short. A short put has negative qty so its delta contribution flips sign.

IV input: pass annualised vol as a fraction (e.g. 0.65 for 65% IV), NOT
percent. The data loader gives DVOL in percent — divide by 100 before passing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Literal

import numpy as np


SECONDS_PER_YEAR = 365.25 * 24 * 3600.0


def _norm_cdf(x):
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0))) if isinstance(x, np.ndarray) else \
           0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x):
    return np.exp(-0.5 * np.asarray(x) ** 2) / math.sqrt(2.0 * math.pi)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return float("nan"), float("nan")
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return d1, d2


def bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: Literal["call", "put"],
) -> float:
    """European Black-Scholes option price in USD.

    T is years to expiry. At T<=0 returns intrinsic value.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if option_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: Literal["call", "put"],
) -> dict:
    """Greeks for one option contract (qty=1, contract_multiplier=1).

    Returns a dict with delta, gamma, theta_per_day, vega_per_volpoint, price.
    Caller is responsible for multiplying by qty and contract size.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # Intrinsic only — Greeks degenerate
        if option_type == "call":
            delta = 1.0 if S > K else 0.0
            price = max(S - K, 0.0)
        else:
            delta = -1.0 if S < K else 0.0
            price = max(K - S, 0.0)
        return {
            "price": price,
            "delta": delta,
            "gamma": 0.0,
            "theta_per_day": 0.0,
            "vega_per_volpoint": 0.0,
        }
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    pdf_d1 = _norm_pdf(d1)
    sqrtT = math.sqrt(T)
    disc = math.exp(-r * T)
    gamma = pdf_d1 / (S * sigma * sqrtT)
    vega = S * pdf_d1 * sqrtT  # per 1.0 change in sigma
    if option_type == "call":
        delta = _norm_cdf(d1)
        price = S * delta - K * disc * _norm_cdf(d2)
        theta = -(S * pdf_d1 * sigma) / (2.0 * sqrtT) - r * K * disc * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        price = K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        theta = -(S * pdf_d1 * sigma) / (2.0 * sqrtT) + r * K * disc * _norm_cdf(-d2)
    return {
        "price": float(price),
        "delta": float(delta),
        "gamma": float(gamma),
        "theta_per_day": float(theta / 365.25),
        "vega_per_volpoint": float(vega / 100.0),
    }


def implied_vol_from_dvol(dvol_pct: float) -> float:
    """Convert Deribit DVOL (annualised IV in %) to the fraction the BSM
    formulas expect. Clip to a sane range to avoid Greeks blowing up."""
    if not math.isfinite(dvol_pct) or dvol_pct <= 0:
        return 0.60  # sensible fallback
    return float(np.clip(dvol_pct / 100.0, 0.10, 3.00))


def years_to_expiry(now_ts_seconds: float, expiry_ts_seconds: float) -> float:
    return max(0.0, (expiry_ts_seconds - now_ts_seconds) / SECONDS_PER_YEAR)


# ---------------------------------------------------------------------------
# Portfolio aggregation
# ---------------------------------------------------------------------------


@dataclass
class OptionLeg:
    """One open option position.

    qty is in *contract units* — positive long, negative short. Deribit BTC
    options are settled in BTC but quoted as fractions of BTC; for the RL env
    we treat one contract as exposure to 1 BTC of underlying. The risk manager
    can rescale this later (the existing wheel bot uses 0.1 BTC min lots).
    """

    option_type: Literal["call", "put"]
    strike: float
    expiry_ts: float           # unix seconds
    qty: float                 # signed contracts; +1 long, -1 short
    entry_price: float         # USD per contract at open
    entry_ts: float            # unix seconds
    entry_iv: float            # fraction (e.g. 0.65)
    contract_size_btc: float = 1.0  # BTC per contract
    meta: dict = field(default_factory=dict)

    def mark(self, spot: float, now_ts: float, iv: float, r: float = 0.0) -> dict:
        T = years_to_expiry(now_ts, self.expiry_ts)
        g = bs_greeks(spot, self.strike, T, r, iv, self.option_type)
        notional_scale = self.contract_size_btc
        price = g["price"]
        # Unrealised P&L per contract = current price - entry price for long;
        # for short, it's entry - current. qty's sign handles that:
        pnl = (price - self.entry_price) * self.qty * notional_scale
        return {
            "price": price,
            "delta": g["delta"] * self.qty * notional_scale,
            "gamma": g["gamma"] * self.qty * notional_scale,
            "theta_per_day": g["theta_per_day"] * self.qty * notional_scale,
            "vega_per_volpoint": g["vega_per_volpoint"] * self.qty * notional_scale,
            "dte": T * 365.25,
            "unrealized_pnl": pnl,
            "moneyness": spot / self.strike if self.strike > 0 else 1.0,
        }


def portfolio_greeks(
    legs: Iterable[OptionLeg],
    spot: float,
    now_ts: float,
    iv: float,
    r: float = 0.0,
) -> dict:
    """Aggregate Greeks and P&L across a portfolio of legs.

    Uses a single `iv` for all legs (DVOL-based ATM proxy). A more
    sophisticated env can later pass a per-strike IV from a surface.
    """
    total = {
        "delta": 0.0,
        "gamma": 0.0,
        "theta_per_day": 0.0,
        "vega_per_volpoint": 0.0,
        "unrealized_pnl": 0.0,
        "gross_premium": 0.0,
        "num_legs": 0,
        "num_short": 0,
        "num_long": 0,
        "avg_dte": 0.0,
    }
    dte_sum = 0.0
    for leg in legs:
        m = leg.mark(spot, now_ts, iv, r)
        total["delta"] += m["delta"]
        total["gamma"] += m["gamma"]
        total["theta_per_day"] += m["theta_per_day"]
        total["vega_per_volpoint"] += m["vega_per_volpoint"]
        total["unrealized_pnl"] += m["unrealized_pnl"]
        total["gross_premium"] += abs(leg.entry_price * leg.qty * leg.contract_size_btc)
        total["num_legs"] += 1
        if leg.qty > 0:
            total["num_long"] += 1
        elif leg.qty < 0:
            total["num_short"] += 1
        dte_sum += m["dte"]
    if total["num_legs"] > 0:
        total["avg_dte"] = dte_sum / total["num_legs"]
    return total


if __name__ == "__main__":
    # Quick sanity print
    S, K, T, r, sigma = 70_000.0, 65_000.0, 30 / 365.25, 0.0, 0.65
    print("ATM-ish put:", bs_greeks(S, K, T, r, sigma, "put"))
    print("ATM-ish call:", bs_greeks(S, K, T, r, sigma, "call"))

    leg = OptionLeg(
        option_type="put",
        strike=65_000,
        expiry_ts=1_900_000_000 + 30 * 86400,
        qty=-1.0,  # short 1 put
        entry_price=1_200.0,
        entry_ts=1_900_000_000,
        entry_iv=0.65,
    )
    print(
        "Mark short put:",
        leg.mark(spot=70_000.0, now_ts=1_900_000_000 + 5 * 86400, iv=0.55),
    )
    print(
        "Portfolio:",
        portfolio_greeks([leg], spot=70_000.0, now_ts=1_900_000_000 + 5 * 86400, iv=0.55),
    )
