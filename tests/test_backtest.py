"""Smoke tests for backtester.py — verifies it runs and returns valid metrics."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from backtester import Backtester, BacktestResults


SYNTHETIC_OHLCV = [
    {"timestamp": (1700000000 + i * 86400) * 1000,
     "open": 40000 + i * 10,
     "high": 41000 + i * 10,
     "low":  39000 + i * 10,
     "close": 40500 + i * 10,
     "volume": 100.0}
    for i in range(400)
]

SYNTHETIC_IV = [
    ((1700000000 + i * 86400) * 1000, 60.0 + (i % 40) * 1.0)
    for i in range(400)
]


def test_backtester_smoke():
    """Full backtest run with mocked network calls — should not raise."""
    with patch.object(
        Backtester, "_fetch_btc_ohlcv", return_value=_make_df()
    ), patch.object(
        Backtester, "_fetch_iv_history", return_value=SYNTHETIC_IV
    ):
        bt = Backtester()
        results = bt.run()

        assert isinstance(results, BacktestResults)
        assert isinstance(results.num_cycles, int)
        assert results.starting_equity > 0
        assert results.ending_equity >= 0
        assert -100.0 <= results.total_return_pct <= 10000.0
        assert results.max_drawdown_pct >= 0.0
        assert 0.0 <= results.win_rate_pct <= 100.0


def test_backtester_no_data():
    """Backtester should return empty results when no IV data qualifies."""
    import pandas as pd
    import numpy as np

    df = _make_df()
    # Set IV permanently low so no trades are triggered
    low_iv = [((1700000000 + i * 86400) * 1000, 30.0) for i in range(400)]

    with patch.object(Backtester, "_fetch_btc_ohlcv", return_value=df), \
         patch.object(Backtester, "_fetch_iv_history", return_value=low_iv):
        bt = Backtester()
        results = bt.run()
        assert results.num_cycles == 0


def _make_df():
    """Build a synthetic OHLCV DataFrame for testing."""
    import pandas as pd
    rows = [
        {
            "timestamp": (1700000000 + i * 86400) * 1000,
            "open": 40000 + i * 10,
            "high": 41000 + i * 10,
            "low":  39000 + i * 10,
            "close": 40500 + i * 10,
            "volume": 100.0,
        }
        for i in range(400)
    ]
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df
