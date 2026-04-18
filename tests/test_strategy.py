"""Unit tests for strategy.py — strike selection and IV rank logic."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from strategy import WheelStrategy
from deribit_client import Instrument, Ticker
from datetime import datetime, timezone


def make_ticker(delta: float, mark_iv: float = 80.0, bid: float = 0.01) -> Ticker:
    return Ticker(
        instrument_name="BTC-TEST-P",
        mark_price=0.02,
        bid=bid,
        ask=0.022,
        mark_iv=mark_iv,
        delta=delta,
        gamma=0.001,
        theta=-5.0,
        vega=10.0,
        underlying_price=60000.0,
        timestamp=datetime.now(tz=timezone.utc),
    )


def make_instrument(name: str, strike: float, dte: int, opt_type: str) -> Instrument:
    import time
    return Instrument(
        instrument_name=name,
        strike=strike,
        expiry_ts=int((time.time() + dte * 86400) * 1000),
        option_type=opt_type,
        dte=dte,
    )


def test_iv_rank_normal():
    strategy = WheelStrategy(MagicMock())
    # Build IV history with known range 50–100
    iv_history = [(i * 86400 * 1000, 50.0 + i * (50.0 / 400)) for i in range(400)]
    rank = strategy.calculate_iv_rank(iv_history)
    assert 0.0 <= rank <= 1.0


def test_iv_rank_insufficient_data():
    strategy = WheelStrategy(MagicMock())
    rank = strategy.calculate_iv_rank([])
    assert rank == 0.0


def test_iv_rank_flat():
    strategy = WheelStrategy(MagicMock())
    iv_history = [(i * 86400 * 1000, 75.0) for i in range(100)]
    rank = strategy.calculate_iv_rank(iv_history)
    assert rank == 0.5


def test_decide_cycle_alternates():
    strategy = WheelStrategy(MagicMock())
    assert strategy.decide_cycle("put") == "call"
    assert strategy.decide_cycle("call") == "put"


def test_select_strike_puts_only():
    """Only put-type instruments should be returned when cycle=put."""
    strategy = WheelStrategy(MagicMock())
    instruments = [
        make_instrument("BTC-60000-P", 60000, 14, "put"),
        make_instrument("BTC-65000-C", 65000, 14, "call"),  # should be excluded
    ]
    tickers = {
        "BTC-60000-P": make_ticker(delta=-0.22),
        "BTC-65000-C": make_ticker(delta=0.22),
    }
    result = strategy.select_strike(instruments, tickers, "put", 63000.0)
    assert result is not None
    assert result.instrument.option_type == "put"


def test_select_strike_delta_filter():
    """Strikes outside delta range should be excluded."""
    strategy = WheelStrategy(MagicMock())
    instruments = [
        make_instrument("BTC-55000-P", 55000, 14, "put"),  # delta too low
        make_instrument("BTC-62000-P", 62000, 14, "put"),  # delta in range
    ]
    tickers = {
        "BTC-55000-P": make_ticker(delta=-0.05),  # below min 0.15
        "BTC-62000-P": make_ticker(delta=-0.22),  # in range
    }
    result = strategy.select_strike(instruments, tickers, "put", 63000.0)
    assert result is not None
    assert result.instrument.instrument_name == "BTC-62000-P"


def test_select_strike_no_liquidity():
    """Strikes with zero bid should be excluded."""
    strategy = WheelStrategy(MagicMock())
    instruments = [make_instrument("BTC-60000-P", 60000, 14, "put")]
    tickers = {"BTC-60000-P": make_ticker(delta=-0.22, bid=0.0)}
    result = strategy.select_strike(instruments, tickers, "put", 63000.0)
    assert result is None


def test_select_strike_dte_filter():
    """Strikes outside DTE range should be excluded."""
    strategy = WheelStrategy(MagicMock())
    instruments = [
        make_instrument("BTC-60000-P-NEAR", 60000, 2, "put"),   # too soon (< min_dte=5)
        make_instrument("BTC-60000-P-FAR",  60000, 50, "put"),  # too far (> max_dte=35)
    ]
    tickers = {
        "BTC-60000-P-NEAR": make_ticker(delta=-0.22),
        "BTC-60000-P-FAR":  make_ticker(delta=-0.22),
    }
    result = strategy.select_strike(instruments, tickers, "put", 63000.0)
    assert result is None
