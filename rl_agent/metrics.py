"""
metrics.py — performance metrics computed from a daily mark-to-market equity curve.

Each env step is one day, so periods_per_year=365 for annualisation.
A "curve" is a 1-D array of MTM equity, one point per day (curve[0] = starting equity).
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

PERIODS_PER_YEAR = 365.0


def _daily_returns(curve: np.ndarray) -> np.ndarray:
    curve = np.asarray(curve, dtype=np.float64)
    if len(curve) < 2:
        return np.zeros(0)
    prev = curve[:-1]
    return np.where(prev > 0, np.diff(curve) / prev, 0.0)


def total_return(curve: Sequence[float]) -> float:
    curve = np.asarray(curve, dtype=np.float64)
    if len(curve) < 2 or curve[0] <= 0:
        return 0.0
    return curve[-1] / curve[0] - 1.0


def annualised_return(curve: Sequence[float], periods_per_year: float = PERIODS_PER_YEAR) -> float:
    """CAGR — compounded growth rate scaled to a full year."""
    curve = np.asarray(curve, dtype=np.float64)
    n = len(curve)
    if n < 2 or curve[0] <= 0 or curve[-1] <= 0:
        return 0.0
    years = (n - 1) / periods_per_year
    if years <= 0:
        return 0.0
    return (curve[-1] / curve[0]) ** (1.0 / years) - 1.0


def sharpe(curve: Sequence[float], periods_per_year: float = PERIODS_PER_YEAR, rf: float = 0.0) -> float:
    r = _daily_returns(curve)
    if len(r) < 2:
        return 0.0
    rf_step = rf / periods_per_year
    excess = r - rf_step
    sd = excess.std(ddof=1)
    if sd == 0:
        return 0.0
    return float(excess.mean() / sd * math.sqrt(periods_per_year))


def sortino(curve: Sequence[float], periods_per_year: float = PERIODS_PER_YEAR, rf: float = 0.0) -> float:
    r = _daily_returns(curve)
    if len(r) < 2:
        return 0.0
    rf_step = rf / periods_per_year
    excess = r - rf_step
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf") if excess.mean() > 0 else 0.0
    dd = math.sqrt(float(np.mean(downside ** 2)))
    if dd == 0:
        return 0.0
    return float(excess.mean() / dd * math.sqrt(periods_per_year))


def max_drawdown(curve: Sequence[float]) -> float:
    """Largest peak-to-trough decline as a positive fraction (0.25 = -25%)."""
    curve = np.asarray(curve, dtype=np.float64)
    if len(curve) == 0:
        return 0.0
    peak = np.maximum.accumulate(curve)
    dd = np.where(peak > 0, (peak - curve) / peak, 0.0)
    return float(dd.max())


def calmar(curve: Sequence[float], periods_per_year: float = PERIODS_PER_YEAR) -> float:
    mdd = max_drawdown(curve)
    if mdd == 0:
        return 0.0
    return annualised_return(curve, periods_per_year) / mdd


def win_rate(trade_pnls: Sequence[float]) -> float:
    if not len(trade_pnls):
        return 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls)


def summarise(curve: Sequence[float], trade_pnls: Sequence[float], n_opens: int,
              steps_in_market: int) -> dict:
    curve = np.asarray(curve, dtype=np.float64)
    n_steps = max(len(curve), 1)
    return {
        "final_equity": float(curve[-1]) if len(curve) else 0.0,
        "total_return": total_return(curve),
        "annualised_return": annualised_return(curve),
        "sharpe": sharpe(curve),
        "sortino": sortino(curve),
        "max_drawdown": max_drawdown(curve),
        "calmar": calmar(curve),
        "win_rate": win_rate(trade_pnls),
        "n_trades": int(n_opens),
        "n_closed": len(trade_pnls),
        "time_in_market": steps_in_market / n_steps,
        "days": len(curve),
    }
