"""
tests/test_risk.py -- Unit tests for risk manager sizing and checks.
"""
from __future__ import annotations

import pytest
from risk_manager import Position, RiskManager
from config import load_config


@pytest.fixture
def rm():
    return RiskManager()


@pytest.fixture
def sample_position():
    return Position(
        instrument_name="BTC-27DEC24-45000-P",
        strike=45_000,
        option_type="put",
        entry_price=0.02,            # 0.02 BTC per contract
        underlying_at_entry=50_000,
        contracts=1.0,
        current_delta=0.20,
        current_price=0.02,
        entry_equity=10_000,
    )


# ── calculate_contracts ────────────────────────────────────────────────────────

def test_contracts_basic(rm):
    """5% of $10k equity at $50k strike = 0.01 contracts -> floor to 0.1."""
    c = rm.calculate_contracts(equity_usd=10_000, strike_usd=50_000)
    # max_equity_per_leg=0.05 -> $500 / $50k = 0.01 -> floored to 0.1
    assert c == 0.1


def test_contracts_large_equity(rm):
    """Larger equity should yield proportionally more contracts."""
    c = rm.calculate_contracts(equity_usd=1_000_000, strike_usd=50_000)
    # With current config: max_equity_per_leg=0.1078
    # max_notional = $1M × 0.1078 = $107,800
    # collateral_per_contract = strike = $50,000 (1 contract = 1 BTC notional)
    # raw = 107,800 / 50,000 = 2.156 → floored to nearest 0.1 lot = 2.1
    assert c == 2.1


def test_contracts_zero_equity(rm):
    assert rm.calculate_contracts(equity_usd=0, strike_usd=50_000) == 0.0


def test_contracts_zero_strike(rm):
    assert rm.calculate_contracts(equity_usd=10_000, strike_usd=0) == 0.0


# ── check_position_size ────────────────────────────────────────────────────────

def test_position_size_passes(rm):
    """$1M equity can always open at least 0.1 contracts."""
    assert rm.check_position_size(equity_usd=1_000_000, strike_usd=50_000) is True


def test_position_size_fails_tiny_equity(rm):
    """Very small equity cannot open a position."""
    # $100 equity -> 5% = $5 / $50k -> 0.0001 contracts < 0.1
    assert rm.check_position_size(equity_usd=100, strike_usd=50_000) is False


# ── check_collateral ──────────────────────────────────────────────────────────

def test_collateral_no_positions(rm):
    """No open positions means collateral is always fine."""
    assert rm.check_collateral([], equity_usd=10_000, btc_price=50_000) is True


def test_collateral_within_buffer(rm, sample_position):
    """One position at $45k strike, $1M equity -> well within 150%."""
    assert rm.check_collateral([sample_position], equity_usd=1_000_000, btc_price=50_000)


def test_collateral_exceeds_buffer(rm):
    """Position larger than 150% of equity should fail."""
    big_pos = Position(
        instrument_name="BTC-X-P",
        strike=200_000,     # massive strike
        option_type="put",
        entry_price=0.01,
        underlying_at_entry=50_000,
        contracts=1.0,
        current_delta=0.1,
        current_price=0.01,
        entry_equity=5_000,
    )
    # collateral = 200k, equity = 5k, buffer = 7.5k -> 200k > 7.5k
    assert not rm.check_collateral([big_pos], equity_usd=5_000, btc_price=50_000)


# ── should_roll ───────────────────────────────────────────────────────────────

def test_no_roll_healthy(rm, sample_position):
    """Healthy position should not trigger a roll."""
    roll, reason = rm.should_roll(sample_position)
    assert not roll
    assert reason == "ok"


def test_roll_delta_breach(rm, sample_position):
    """Delta exceeding max_adverse_delta triggers a roll."""
    sample_position.current_delta = 0.45   # above default 0.40
    roll, reason = rm.should_roll(sample_position)
    assert roll
    assert reason == "delta_breach"


def test_roll_loss_breach(rm, sample_position):
    """Unrealised loss > 2% of entry equity triggers a roll."""
    # Premium received: 0.02 BTC * $50k = $1k
    # Current cost: 0.05 BTC * $50k = $2.5k -> loss = $1.5k -> 15% of $10k
    sample_position.current_price = 0.05
    roll, reason = rm.should_roll(sample_position)
    assert roll
    assert reason == "loss_breach"


# ── check_drawdown ────────────────────────────────────────────────────────────

def test_drawdown_ok(rm):
    """Equity curve with small dip should pass."""
    curve = [10_000, 10_500, 9_800, 10_200]
    # peak=10500, current=10200 -> dd=2.9% < 10%
    assert rm.check_drawdown(curve) is True


def test_drawdown_breached(rm):
    """15% drop from peak should halt trading."""
    curve = [10_000, 10_000, 8_400]  # 16% drop from 10k
    assert rm.check_drawdown(curve) is False


# ── kill switch ───────────────────────────────────────────────────────────────

def test_kill_switch_inactive(rm, tmp_path, monkeypatch):
    """No KILL_SWITCH file -> trading allowed."""
    monkeypatch.setattr(rm, "_kill_switch_path", tmp_path / "KILL_SWITCH")
    assert rm.check_kill_switch() is True


def test_kill_switch_active(rm, tmp_path, monkeypatch):
    """KILL_SWITCH file present -> trading blocked."""
    ks = tmp_path / "KILL_SWITCH"
    ks.touch()
    monkeypatch.setattr(rm, "_kill_switch_path", ks)
    assert rm.check_kill_switch() is False
