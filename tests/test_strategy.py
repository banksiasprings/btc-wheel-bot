"""
tests/test_strategy.py -- Unit tests for strategy logic.
"""
from __future__ import annotations

import pytest
from backtester import (
    bs_put_delta,
    bs_call_delta,
    bs_put_price,
    bs_call_price,
    strike_for_put_delta,
    strike_for_call_delta,
)


# ── Black-Scholes sanity checks ────────────────────────────────────────────────

def test_put_call_parity():
    """C - P == S - K*exp(-rT) (put-call parity)."""
    S, K, T, r, sigma = 50_000, 48_000, 7 / 365, 0.04, 0.80
    c = bs_call_price(S, K, T, r, sigma)
    p = bs_put_price(S, K, T, r, sigma)
    import math
    parity = abs((c - p) - (S - K * math.exp(-r * T)))
    assert parity < 1.0, f"Put-call parity violated: {parity}"


def test_put_delta_range():
    """Put delta must be in (-1, 0) for positive T."""
    delta = bs_put_delta(50_000, 45_000, 7 / 365, 0.04, 0.80)
    assert -1.0 < delta < 0.0


def test_call_delta_range():
    """Call delta must be in (0, 1) for positive T."""
    delta = bs_call_delta(50_000, 55_000, 7 / 365, 0.04, 0.80)
    assert 0.0 < delta < 1.0


def test_strike_for_put_delta_roundtrip():
    """
    Finding the strike for delta=-0.25 and then computing the delta of
    that strike should return approximately -0.25.
    """
    S, T, r, sigma = 60_000, 7 / 365, 0.04, 0.80
    target = -0.25
    K = strike_for_put_delta(S, target, T, r, sigma)
    computed = bs_put_delta(S, K, T, r, sigma)
    assert abs(computed - target) < 0.005, (
        f"Delta roundtrip failed: target={target} computed={computed}"
    )


def test_strike_for_call_delta_roundtrip():
    """Same roundtrip test for call delta=+0.25."""
    S, T, r, sigma = 60_000, 7 / 365, 0.04, 0.80
    target = 0.25
    K = strike_for_call_delta(S, target, T, r, sigma)
    computed = bs_call_delta(S, K, T, r, sigma)
    assert abs(computed - target) < 0.005


def test_atm_put_delta_near_half():
    """ATM put delta should be approximately -0.5."""
    S = K = 50_000
    delta = bs_put_delta(S, K, 30 / 365, 0.04, 0.80)
    assert abs(delta + 0.5) < 0.08  # ATM delta shifts from -0.5 with nonzero r


def test_deep_itm_put_price():
    """Very deep ITM put should be worth approximately K - S."""
    S, K = 50_000, 90_000
    price = bs_put_price(S, K, 1 / 365, 0.04, 0.80)
    intrinsic = K - S
    assert abs(price - intrinsic) < 500


def test_put_price_positive():
    """Option prices must be non-negative."""
    assert bs_put_price(50_000, 45_000, 7 / 365, 0.04, 0.80) >= 0
    assert bs_call_price(50_000, 55_000, 7 / 365, 0.04, 0.80) >= 0


def test_otm_put_below_spot():
    """OTM put strike (delta ~ -0.25) should be below current spot."""
    S, T, r, sigma = 50_000, 7 / 365, 0.04, 0.80
    K = strike_for_put_delta(S, -0.25, T, r, sigma)
    assert K < S, f"OTM put strike {K:.0f} should be < spot {S}"


def test_otm_call_above_spot():
    """OTM call strike (delta ~ +0.25) should be above current spot."""
    S, T, r, sigma = 50_000, 7 / 365, 0.04, 0.80
    K = strike_for_call_delta(S, 0.25, T, r, sigma)
    assert K > S, f"OTM call strike {K:.0f} should be > spot {S}"
