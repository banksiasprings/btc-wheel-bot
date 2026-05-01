"""
tests/test_capital_roi_fitness.py — pin the rewritten capital_roi scorer.

The original capital_roi fitness only weighted margin ROI + Sharpe + drawdown
+ win rate. The 2026-05-01 rewrite adds explicit reward for low minimum
viable capital and low margin utilisation, plus premium-on-margin yield —
the dimensions the user said matter for the "small capital × many bots"
thesis. These tests pin that the new scorer actually rewards what it claims.
"""
from __future__ import annotations

import pytest

from optimizer import _fitness_for_goal


def _result(
    *,
    return_pct: float = 10.0,
    sharpe: float = 1.5,
    win_rate: float = 70.0,
    drawdown: float = 5.0,
    num_trades: int = 12,
    margin_roi: float = 1.0,
    premium_on_margin: float = 0.15,
    min_viable_capital: float = 50_000,
    avg_margin_util: float = 0.30,
) -> dict:
    """Build a synthetic optimizer result dict."""
    return {
        "total_return_pct": return_pct,
        "sharpe_ratio": sharpe,
        "win_rate_pct": win_rate,
        "max_drawdown_pct": drawdown,
        "num_cycles": num_trades,
        "annualised_margin_roi": margin_roi,
        "premium_on_margin": premium_on_margin,
        "min_viable_capital": min_viable_capital,
        "avg_margin_utilization": avg_margin_util,
    }


# ── Capital floor ──────────────────────────────────────────────────────────────


def test_low_capital_beats_high_capital_when_else_equal():
    """A genome that needs $20k to trade should outscore one that needs $200k."""
    small = _fitness_for_goal(_result(min_viable_capital=20_000), "capital_roi")
    large = _fitness_for_goal(_result(min_viable_capital=200_000), "capital_roi")
    assert small > large


def test_capital_score_saturates_below_20k():
    """Below $20k all genomes should score the same on the capital dimension."""
    a = _fitness_for_goal(_result(min_viable_capital=10_000), "capital_roi")
    b = _fitness_for_goal(_result(min_viable_capital=20_000), "capital_roi")
    assert a == b


def test_capital_score_floor_at_200k():
    """At $200k+ capital floor, the metric contributes zero."""
    at_200k = _fitness_for_goal(_result(min_viable_capital=200_000), "capital_roi")
    at_500k = _fitness_for_goal(_result(min_viable_capital=500_000), "capital_roi")
    assert at_200k == at_500k


# ── Margin utilisation ────────────────────────────────────────────────────────


def test_low_margin_util_beats_high_margin_util():
    """30% margin use should outscore 70% margin use, all else equal."""
    safe   = _fitness_for_goal(_result(avg_margin_util=0.30), "capital_roi")
    risky  = _fitness_for_goal(_result(avg_margin_util=0.70), "capital_roi")
    assert safe > risky


def test_margin_util_above_70_pct_floors():
    """Stacking margin past 70% doesn't make the score worse — already zeroed."""
    a = _fitness_for_goal(_result(avg_margin_util=0.70), "capital_roi")
    b = _fitness_for_goal(_result(avg_margin_util=0.95), "capital_roi")
    assert a == b


# ── Premium yield on margin ───────────────────────────────────────────────────


def test_higher_premium_on_margin_scores_higher():
    """5% premium yield should score lower than 25% premium yield."""
    low  = _fitness_for_goal(_result(premium_on_margin=0.05), "capital_roi")
    high = _fitness_for_goal(_result(premium_on_margin=0.25), "capital_roi")
    assert high > low


def test_premium_score_caps_at_30_pct():
    """30%+ premium yield is exceptional but should saturate."""
    at_30 = _fitness_for_goal(_result(premium_on_margin=0.30), "capital_roi")
    at_50 = _fitness_for_goal(_result(premium_on_margin=0.50), "capital_roi")
    assert at_30 == at_50


# ── Activity penalty ──────────────────────────────────────────────────────────


def test_idle_strategy_penalised():
    """Fewer than 6 trades scales the whole score down proportionally."""
    full   = _fitness_for_goal(_result(num_trades=6),  "capital_roi")
    sparse = _fitness_for_goal(_result(num_trades=3),  "capital_roi")
    none   = _fitness_for_goal(_result(num_trades=0),  "capital_roi")
    assert full > sparse > none
    assert none == 0.0


# ── Score range ──────────────────────────────────────────────────────────────


def test_score_clipped_to_unit_interval():
    """Even a maximally great genome stays in [0, 1]."""
    great = _fitness_for_goal(_result(
        return_pct=100, sharpe=10, win_rate=100, drawdown=0,
        margin_roi=10.0, premium_on_margin=1.0,
        min_viable_capital=1, avg_margin_util=0.0,
    ), "capital_roi")
    assert 0.0 <= great <= 1.0


def test_score_zero_for_terrible_genome():
    """A maximally bad genome scores 0 (or near-0)."""
    bad = _fitness_for_goal(_result(
        return_pct=-50, sharpe=-5, win_rate=10, drawdown=50,
        margin_roi=-2.0, premium_on_margin=0.0,
        min_viable_capital=500_000, avg_margin_util=0.95,
        num_trades=0,
    ), "capital_roi")
    assert bad == 0.0


# ── Backwards-compatibility with old result dicts ─────────────────────────────


def test_old_result_without_capital_fields_still_scores():
    """Old optimizer runs lacked the new fields; scorer must default them."""
    old_style = {
        "total_return_pct": 10.0,
        "sharpe_ratio": 1.5,
        "win_rate_pct": 70.0,
        "max_drawdown_pct": 5.0,
        "num_cycles": 12,
        "annualised_margin_roi": 1.0,
        # no premium_on_margin / min_viable_capital / avg_margin_utilization
    }
    score = _fitness_for_goal(old_style, "capital_roi")
    # Should still return a valid score (capital_score will be 0 → some penalty,
    # but the function shouldn't KeyError).
    assert 0.0 <= score <= 1.0
