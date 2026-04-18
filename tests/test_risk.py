"""Unit tests for risk_manager.py — sizing and collateral checks."""
from __future__ import annotations

import pytest
from pathlib import Path
from risk_manager import RiskManager, Position


@pytest.fixture
def rm():
    return RiskManager()


def make_position(delta: float = -0.22, current_price: float = 0.015,
                  entry_price: float = 0.02) -> Position:
    return Position(
        instrument_name="BTC-60000-P",
        strike=60000.0,
        option_type="put",
        entry_price=entry_price,
        underlying_at_entry=65000.0,
        contracts=0.1,
        current_delta=delta,
        current_price=current_price,
        entry_equity=10000.0,
    )


def test_kill_switch_absent(rm, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert rm.check_kill_switch() is True


def test_kill_switch_present(rm, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "KILL_SWITCH").write_text("stop")
    assert rm.check_kill_switch() is False


def test_max_legs_ok(rm):
    assert rm.check_max_legs([]) is True


def test_max_legs_at_limit(rm):
    positions = [make_position() for _ in range(10)]
    assert rm.check_max_legs(positions) is False


def test_calculate_contracts(rm):
    # equity=$10000, strike=$60000, max_equity_per_leg=5% → $500 max → 500/60000 ≈ 0.1
    contracts = rm.calculate_contracts(equity_usd=10000.0, strike_usd=60000.0)
    assert contracts >= 0.1


def test_check_position_size_ok(rm):
    assert rm.check_position_size(100000.0, 60000.0) is True


def test_check_position_size_too_small(rm):
    # Very small equity → can't afford even 0.1 contract
    assert rm.check_position_size(100.0, 80000.0) is False


def test_collateral_ok(rm):
    pos = make_position()
    assert rm.check_collateral([pos], equity_usd=10000.0, btc_price=65000.0) is True


def test_collateral_exceeded(rm):
    # Create many large positions that exceed 150% buffer
    positions = [make_position() for _ in range(30)]
    # 30 × 60000 × 0.1 = $180,000 collateral vs $10,000 × 1.5 = $15,000 allowed
    assert rm.check_collateral(positions, equity_usd=10000.0, btc_price=65000.0) is False


def test_should_roll_delta_breach(rm):
    pos = make_position(delta=-0.50)  # exceeds max_adverse_delta=0.40
    should, reason = rm.should_roll(pos)
    assert should is True
    assert reason == "delta_breach"


def test_should_roll_ok(rm):
    pos = make_position(delta=-0.22)
    should, reason = rm.should_roll(pos)
    assert should is False
    assert reason == "ok"


def test_drawdown_ok(rm):
    equity_curve = [10000.0, 10200.0, 10100.0, 10300.0]
    assert rm.check_drawdown(equity_curve) is True


def test_drawdown_breached(rm):
    # Drop from 10000 to 8000 = 20% drawdown > 10% limit
    equity_curve = [10000.0, 10100.0, 8000.0]
    assert rm.check_drawdown(equity_curve) is False
