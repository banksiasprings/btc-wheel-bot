"""
tests/test_hedge_cost_calibration.py — pin the hedge funding/spread constants.

Real Deribit BTC-PERP funding settles every 8 hours at ~0.01-0.03% per
epoch. Pre-2026-05-01 the backtester used 0.0001/day (1 bp/day) which
understated real funding by 3-9×. The fix raised it to 0.0003/day. These
tests pin the calibration so a future refactor doesn't silently revert.
"""
from __future__ import annotations

import pytest

from backtester import HEDGE_FUNDING_DAILY, HEDGE_REBALANCE_BPS


def test_funding_calibrated_to_real_deribit():
    """
    0.03%/day = ~0.01% per 8-hour epoch (3 epochs/day) — conservative middle
    of the typical 0.01-0.03% per-epoch range. Anything below 0.0002/day
    silently understates real costs by > 3×.
    """
    assert HEDGE_FUNDING_DAILY >= 0.00025, (
        f"HEDGE_FUNDING_DAILY={HEDGE_FUNDING_DAILY} understates real Deribit "
        f"funding (~0.01-0.03% per 8h epoch = 0.03-0.09%/day). The pre-fix "
        f"value of 0.0001 was too optimistic; do not revert."
    )
    assert HEDGE_FUNDING_DAILY <= 0.001, (
        f"HEDGE_FUNDING_DAILY={HEDGE_FUNDING_DAILY} overstates funding by > 3×. "
        f"Cap at 0.001/day (0.1%/day) — anything more is a stress-test value."
    )


def test_rebalance_spread_in_realistic_band():
    """
    Real BTC-PERP spread on Deribit is ~$1-5 on $77k spot ≈ 0.001-0.006%.
    The 0.02% calibration is conservative-but-realistic — leave alone unless
    we have new data showing it's wrong.
    """
    assert 0.0001 <= HEDGE_REBALANCE_BPS <= 0.0010, (
        f"HEDGE_REBALANCE_BPS={HEDGE_REBALANCE_BPS} outside realistic band "
        f"[0.0001, 0.0010] for Deribit BTC-PERP."
    )


def test_hedge_funding_used_consistently_in_simulator():
    """
    Both the in-trade rebalance branch and the expiry-settlement branch must
    use HEDGE_FUNDING_DAILY (not a hardcoded 0.0001). Otherwise a future
    refactor of one branch creates a silent inconsistency.
    """
    import backtester
    src = open(backtester.__file__).read()
    # No bare 0.0001 multiplier on `* spot *` (that was the old funding rate)
    bad_pattern = "* spot * 0.0001"
    assert bad_pattern not in src, (
        f"Found legacy hedge-funding constant `{bad_pattern}` in backtester.py. "
        f"Use HEDGE_FUNDING_DAILY for daily perp funding."
    )


def test_hedge_rebalance_spread_used_consistently():
    """Same check for the spread cost — should be the named constant."""
    import backtester
    src = open(backtester.__file__).read()
    # No bare 0.0002 multiplier — should be HEDGE_REBALANCE_BPS
    # (Allowing the constant definition itself, of course.)
    lines = [
        line for line in src.split("\n")
        if "0.0002" in line and "HEDGE_REBALANCE_BPS" not in line
    ]
    # The constant definition line will mention 0.0002 — exclude it
    non_def_lines = [l for l in lines if "= 0.0002" not in l]
    assert non_def_lines == [], (
        f"Found bare 0.0002 multipliers in backtester.py — use "
        f"HEDGE_REBALANCE_BPS instead. Offending lines:\n  "
        + "\n  ".join(non_def_lines)
    )
