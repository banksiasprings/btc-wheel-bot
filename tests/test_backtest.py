"""
tests/test_backtest.py -- Smoke tests for the backtester.

These tests run the backtester against mocked Deribit responses so they
work offline without hitting the real API.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backtester import (
    Backtester,
    BacktestResults,
    bs_put_price,
    bs_call_price,
    bs_put_delta,
    bs_call_delta,
    strike_for_put_delta,
)
from config import load_config


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_price_response(n_days: int = 400, start_price: float = 50_000):
    """Generate synthetic BTC-PERPETUAL OHLCV response."""
    now_ms = int(time.time() * 1_000)
    prices = [start_price + i * 100 for i in range(n_days)]
    ticks  = [now_ms - (n_days - i) * 86_400_000 for i in range(n_days)]
    return {
        "status": "ok",
        "ticks":  ticks,
        "open":   prices,
        "high":   [p * 1.01 for p in prices],
        "low":    [p * 0.99 for p in prices],
        "close":  prices,
        "volume": [100.0] * n_days,
    }


def _make_iv_response(n_days: int = 600, base_iv: float = 70.0):
    """Generate synthetic IV history response (Deribit format: [[ts_ms, iv], ...])."""
    now_ms = int(time.time() * 1_000)
    return [
        [now_ms - (n_days - i) * 86_400_000, base_iv + 10 * math.sin(i / 30)]
        for i in range(n_days)
    ]


@pytest.fixture
def bt_with_mock():
    """Backtester instance with mocked Deribit REST responses."""
    cfg = load_config()
    cfg.backtest.lookback_months = 3   # short run for tests
    cfg.backtest.starting_equity = 10_000.0

    bt = Backtester(config=cfg)

    price_resp = _make_price_response(n_days=500)
    iv_resp    = _make_iv_response(n_days=700, base_iv=70.0)

    bt._rest._get = MagicMock(side_effect=lambda method, params=None: (
        price_resp if "chart" in method else iv_resp
    ))
    return bt


# ── Smoke test ────────────────────────────────────────────────────────────────

def test_backtest_runs_and_returns_results(bt_with_mock):
    """Backtester must complete without raising and return a BacktestResults."""
    results = bt_with_mock.run()
    assert isinstance(results, BacktestResults)


def test_backtest_has_valid_metrics(bt_with_mock):
    """Key metrics must be finite numbers in sane ranges."""
    results = bt_with_mock.run()

    assert math.isfinite(results.total_return_pct)
    assert math.isfinite(results.sharpe_ratio)
    assert results.max_drawdown_pct <= 0.0 or results.max_drawdown_pct == 0.0, (
        "max_drawdown_pct stored as negative fraction * 100"
    )
    assert 0.0 <= results.win_rate_pct <= 100.0


def test_backtest_equity_curve_non_empty(bt_with_mock):
    """Equity curve must have data points matching the simulation dates."""
    results = bt_with_mock.run()
    assert len(results.equity_curve) > 0
    assert len(results.dates) > 0
    assert len(results.equity_curve) == len(results.dates) + 1 \
        or abs(len(results.equity_curve) - len(results.dates)) <= 2  # off-by-one OK


def test_backtest_no_negative_equity(bt_with_mock):
    """Equity should never go below zero in the simulation."""
    results = bt_with_mock.run()
    assert all(e >= 0 for e in results.equity_curve), "Equity went negative"


def test_backtest_trades_have_required_fields(bt_with_mock):
    """Each trade record must have option_type, strike, pnl_usd."""
    results = bt_with_mock.run()
    for trade in results.trades:
        assert trade.option_type in ("put", "call")
        assert trade.strike > 0
        assert isinstance(trade.pnl_usd, float)


def test_backtest_win_loss_consistency(bt_with_mock):
    """winning + losing trades must equal total trades."""
    results = bt_with_mock.run()
    wins   = sum(1 for t in results.trades if t.pnl_usd >= 0)
    losses = sum(1 for t in results.trades if t.pnl_usd < 0)
    assert wins + losses == results.num_cycles


def test_save_plot_runs(bt_with_mock, tmp_path, monkeypatch):
    """save_plot must create a PNG without raising."""
    results = bt_with_mock.run()
    img = str(tmp_path / "test_chart.png")
    monkeypatch.setattr(bt_with_mock._cfg.backtest, "results_image", img)
    bt_with_mock.save_plot(results)
    import os
    assert os.path.exists(img), "Chart PNG was not created"
