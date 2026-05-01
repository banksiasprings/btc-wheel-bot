"""
tests/test_collateral_consistency.py — backtester and live sizing must agree.

The audit found that backtester._size used `strike × contract_size_btc` as
collateral per contract while RiskManager.calculate_contracts used `strike`
alone — a 10× sizing discrepancy that inflated backtest returns. These tests
pin the agreement so the regression doesn't reappear.
"""
from __future__ import annotations

import pytest

from backtester import Backtester
from config import cfg, load_config
from risk_manager import Position, RiskManager


@pytest.fixture
def rm():
    return RiskManager()


@pytest.fixture
def bt():
    return Backtester(config=cfg)


# ── Backtester sizing matches live sizing ──────────────────────────────────────


@pytest.mark.parametrize(
    "equity,strike",
    [
        (100_000.0,  70_000),     # realistic single-position
        (1_000_000.0, 70_000),    # large account
        (5_000_000.0, 100_000),   # very large
    ],
)
def test_backtester_size_matches_live_sizing_when_tradeable(rm, bt, equity, strike):
    """
    When the live bot would actually trade (check_position_size passes), the
    backtester must size identically. A 10× discrepancy was inflating backtest
    returns by ~10×; this regression test pins the agreement.
    """
    assert rm.check_position_size(equity, strike), (
        "Test fixture: pick equity/strike where the live bot would trade"
    )
    live_contracts = rm.calculate_contracts(equity_usd=equity, strike_usd=strike)
    bt_contracts = bt._size(equity, strike)
    assert bt_contracts == pytest.approx(live_contracts, abs=1e-6), (
        f"Backtester sized {bt_contracts} but live would size {live_contracts}. "
        f"Backtest returns would be ~{bt_contracts/live_contracts if live_contracts else float('inf'):.1f}x "
        f"realised — the same regression the audit caught."
    )


@pytest.mark.parametrize(
    "equity,strike",
    [
        (1_000.0,  50_000),    # below minimum lot
        (100.0,    50_000),    # tiny equity
        (5_000.0, 100_000),    # equity high but strike too high
    ],
)
def test_backtester_skips_when_live_would_block(rm, bt, equity, strike):
    """
    When the live bot's check_position_size would block the trade (raw
    contracts below the 0.1 minimum lot), the backtester must skip rather
    than simulating an impossible trade.
    """
    assert not rm.check_position_size(equity, strike), (
        "Test fixture: pick equity/strike where the live bot would NOT trade"
    )
    bt_contracts = bt._size(equity, strike)
    assert bt_contracts == 0.0, (
        f"Backtester returned {bt_contracts} but live would block this trade — "
        f"backtest is simulating impossible positions."
    )


def test_backtester_zero_strike_returns_zero(bt):
    assert bt._size(equity=10_000, strike=0) == 0.0


# ── check_collateral uses strike × contracts (no contract_size_btc) ───────────


def test_check_collateral_uses_correct_formula(rm):
    """
    Position: 0.1 BTC at $70k strike. Real cash-secured collateral = $7,000.
    With the bug, check_collateral computed $700 (10× too low).
    Verify the corrected formula now returns the right scale by checking
    that an over-margined position is correctly blocked, regardless of
    whether the live config has collateral_buffer at 1.0 (legacy default)
    or 1.5 (post-2026-05-02 paper-mode aggressive value).
    """
    from config import cfg
    buffer = cfg.sizing.collateral_buffer

    pos = Position(
        instrument_name="BTC-1JAN26-70000-P",
        strike=70_000.0,
        option_type="put",
        entry_price=0.02,
        underlying_at_entry=70_000.0,
        contracts=0.1,                # 0.1 BTC of underlying
        current_delta=0.25,
        current_price=0.02,
        entry_equity=10_000.0,
    )
    # Real collateral = $7,000. With $7,000 / buffer + $1 of equity, the
    # check should pass (well within budget); with $7,000 / buffer / 2, it
    # should fail (over-budget regardless of buffer value).
    safe_equity   = (7_000.0 / buffer) * 2.0      # 2× headroom
    tight_equity  = (7_000.0 / buffer) * 0.5      # half budget — must fail
    assert rm.check_collateral([pos], equity_usd=safe_equity, btc_price=70_000.0)
    assert not rm.check_collateral([pos], equity_usd=tight_equity, btc_price=70_000.0)

    # Sanity: with the buggy formula (× contract_size_btc), collateral was
    # $700, so check_collateral([pos], equity=2_000) would have erroneously
    # passed regardless of buffer. Verify it now FAILS at $2k equity since
    # real collateral $7,000 > $2,000 × buffer in any reasonable config.
    if buffer <= 3.0:    # well below absurd levels
        assert not rm.check_collateral([pos], equity_usd=2_000.0, btc_price=70_000.0)


def test_check_free_margin_uses_correct_formula(rm):
    """
    Same scale check for check_free_margin. Without the fix, the function
    would think a fresh leg only consumed 1/10th of the actual collateral.
    """
    # No existing positions; want to open 0.1 BTC at $70k strike.
    # Real reserved_new = $7,000. Equity = $10,000, min_free_fraction=0.1 → required free = $1,000.
    # free_after = 10_000 - 7_000 = 3_000 ≥ 1_000 → pass
    assert rm.check_free_margin(
        equity_usd=10_000.0,
        open_positions=[],
        proposed_strike_usd=70_000.0,
        proposed_contracts=0.1,
    )
    # Equity = $8,000, min_free_fraction=0.25 → required free = $2,000.
    # free_after = 8_000 - 7_000 = 1_000 < 2_000 → fail
    # (With the bug, free_after = 8_000 - 700 = 7_300 > 2_000 → would have passed wrongly.)
    cfg_obj = load_config()
    cfg_obj.sizing.min_free_equity_fraction = 0.25
    # Apply via the singleton since check_free_margin reads cfg directly
    from config import cfg as cfg_singleton
    original = cfg_singleton.sizing.min_free_equity_fraction
    cfg_singleton.sizing.min_free_equity_fraction = 0.25
    try:
        assert not rm.check_free_margin(
            equity_usd=8_000.0,
            open_positions=[],
            proposed_strike_usd=70_000.0,
            proposed_contracts=0.1,
        )
    finally:
        cfg_singleton.sizing.min_free_equity_fraction = original


# ── Round-trip: collateral consumed matches what we asked for ──────────────────


def test_sizing_collateral_round_trip(rm):
    """
    If calculate_contracts says you can afford X contracts at strike S, then
    X × S should be ≤ equity × max_equity_per_leg (with rounding tolerance for
    the 0.1 lot floor).
    """
    equity = 200_000.0
    strike = 60_000.0
    contracts = rm.calculate_contracts(equity_usd=equity, strike_usd=strike)
    real_collateral = contracts * strike
    cap = equity * cfg.sizing.max_equity_per_leg
    # Allow up to one minimum lot worth of overshoot from the floor operation
    one_lot = strike * cfg.sizing.contract_size_btc
    assert real_collateral <= cap + one_lot
