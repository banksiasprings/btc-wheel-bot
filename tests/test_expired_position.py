"""
tests/test_expired_position.py — stranded-expired-position handling.

The audit found that bot.log was looping forever on "Invalid params" because
an expired option (BTC-24APR26-72000-P, expired April 24) cannot be bought
back via the order API. _is_instrument_expired + _local_settlement_price
detect this state and let _close_position settle locally instead of
retrying indefinitely.
"""
from __future__ import annotations

import time

import pytest

from bot import WheelBot
from risk_manager import Position


# ── _is_instrument_expired ─────────────────────────────────────────────────────


def test_is_expired_no_timestamp_returns_false():
    """expiry_ts == 0 means we don't know — fail-safe is `not expired`."""
    assert WheelBot._is_instrument_expired(0) is False


def test_is_expired_far_future_returns_false():
    """An option expiring in 30 days is not expired."""
    future_ts = int((time.time() + 30 * 86_400) * 1000)
    assert WheelBot._is_instrument_expired(future_ts) is False


def test_is_expired_within_grace_returns_false():
    """An option that expired 10 minutes ago is still in grace period."""
    just_expired_ts = int((time.time() - 600) * 1000)   # 10 min ago
    assert WheelBot._is_instrument_expired(just_expired_ts, grace_seconds=1800) is False


def test_is_expired_past_grace_returns_true():
    """An option that expired 1 hour ago is past the 30-min grace period."""
    well_expired_ts = int((time.time() - 3600) * 1000)  # 1 hour ago
    assert WheelBot._is_instrument_expired(well_expired_ts, grace_seconds=1800) is True


def test_is_expired_days_old_returns_true():
    """An option that expired a week ago is definitely stranded."""
    old_ts = int((time.time() - 7 * 86_400) * 1000)
    assert WheelBot._is_instrument_expired(old_ts) is True


# ── _local_settlement_price ────────────────────────────────────────────────────


def _put(strike: float, contracts: float = 0.1) -> Position:
    return Position(
        instrument_name=f"BTC-X-{int(strike)}-P",
        strike=strike,
        option_type="put",
        entry_price=0.02,
        underlying_at_entry=strike,
        contracts=contracts,
        current_delta=0.3,
        current_price=0.02,
        entry_equity=10_000.0,
    )


def _call(strike: float, contracts: float = 0.1) -> Position:
    return Position(
        instrument_name=f"BTC-X-{int(strike)}-C",
        strike=strike,
        option_type="call",
        entry_price=0.02,
        underlying_at_entry=strike,
        contracts=contracts,
        current_delta=0.3,
        current_price=0.02,
        entry_equity=10_000.0,
    )


def test_settle_otm_put_returns_zero():
    """Put with strike $70k, BTC at $75k: OTM → worthless → 0."""
    price = WheelBot._local_settlement_price(_put(70_000.0), underlying_price=75_000.0)
    assert price == 0.0


def test_settle_itm_put_returns_intrinsic_in_btc():
    """Put with strike $70k, BTC at $60k: ITM by $10k → 10000/60000 BTC ≈ 0.1667."""
    price = WheelBot._local_settlement_price(_put(70_000.0), underlying_price=60_000.0)
    assert price == pytest.approx(10_000.0 / 60_000.0, abs=1e-6)


def test_settle_otm_call_returns_zero():
    """Call with strike $80k, BTC at $75k: OTM → 0."""
    price = WheelBot._local_settlement_price(_call(80_000.0), underlying_price=75_000.0)
    assert price == 0.0


def test_settle_itm_call_returns_intrinsic_in_btc():
    """Call with strike $80k, BTC at $90k: ITM by $10k → 10000/90000 BTC ≈ 0.1111."""
    price = WheelBot._local_settlement_price(_call(80_000.0), underlying_price=90_000.0)
    assert price == pytest.approx(10_000.0 / 90_000.0, abs=1e-6)


def test_settle_at_strike_returns_zero():
    """Exactly ATM is neither ITM nor losing — treat as worthless."""
    assert WheelBot._local_settlement_price(_put(70_000.0), 70_000.0) == 0.0
    assert WheelBot._local_settlement_price(_call(70_000.0), 70_000.0) == 0.0


def test_settle_zero_underlying_returns_zero():
    """Defensive: if we don't have a spot price, settle at zero (full premium)."""
    assert WheelBot._local_settlement_price(_put(70_000.0), 0.0) == 0.0
    assert WheelBot._local_settlement_price(_call(70_000.0), 0.0) == 0.0
