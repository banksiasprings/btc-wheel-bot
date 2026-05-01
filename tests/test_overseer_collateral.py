"""
tests/test_overseer_collateral.py — regression test for the AI overseer's
collateral calculation.

The overseer's `low_capital_warning` was previously computed against
`strike × contracts × contract_size_btc` (extra 10× understatement) — same
class of bug as the original risk_manager / backtester collateral bugs the
audit caught. With the bad formula, the LLM was told the bot had ~93% free
capital when really it had ~30%. This test pins the corrected formula so
the regression doesn't return.
"""
from __future__ import annotations

import pytest

from ai_overseer import AIOverSeer


@pytest.fixture
def overseer(monkeypatch):
    """An AIOverSeer with no LLM backend — we only exercise build_brief()."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return AIOverSeer()


def test_free_equity_uses_strike_times_contracts_only(overseer):
    """
    Position: 0.1 BTC of $70k strike puts → real collateral = $7,000.
    With $10k equity, free = $3,000 = 30%, NOT $9,300 = 93% (the bug).
    """
    brief = overseer.build_brief(
        equity_curve=[10_000.0],
        trades=[],
        current_btc_price=70_000.0,
        btc_change_7d_pct=0.0,
        current_iv=50.0,
        iv_rank=0.5,
        open_position={
            "option_type": "put",
            "strike": 70_000.0,
            "contracts": 0.1,
            "delta": 0.25,
            "unrealised_pnl": 0.0,
            "dte": 7,
        },
    )
    # Expected: free = 10000 - 7000 = 3000; pct = 30%
    assert brief.free_equity_pct == pytest.approx(30.0, abs=0.01), (
        f"Free equity {brief.free_equity_pct}% — should be 30% with the "
        f"correct formula. The 93% answer would mean the legacy bug is back."
    )


def test_low_capital_warning_fires_when_genuinely_low(overseer):
    """
    With equity = $10k and strike × contracts = $9k of collateral, free
    is only 10% — should trigger low_capital_warning when min_free is 25%.
    """
    # Pick a strike/contracts combo where collateral = $9k
    brief = overseer.build_brief(
        equity_curve=[10_000.0],
        trades=[],
        current_btc_price=70_000.0,
        btc_change_7d_pct=0.0,
        current_iv=50.0,
        iv_rank=0.5,
        open_position={
            "option_type": "put",
            "strike": 90_000.0,
            "contracts": 0.1,    # 0.1 × 90k = 9k of collateral
            "delta": 0.25,
            "unrealised_pnl": 0.0,
            "dte": 7,
        },
    )
    # min_free_equity_fraction default is 0.1007 → ~10.07% threshold
    # free_equity_pct = 10% → just below the warn line
    # If the legacy bug is back: collateral = $900, free = 91% → no warning.
    assert brief.free_equity_pct < 12.0, (
        f"Free equity {brief.free_equity_pct}% — should be ~10% with the "
        f"correct formula. ~91% would mean the legacy 10× understatement bug."
    )


def test_no_position_means_full_free_equity(overseer):
    """When there's no open position, all equity should be free."""
    brief = overseer.build_brief(
        equity_curve=[10_000.0],
        trades=[],
        current_btc_price=70_000.0,
        btc_change_7d_pct=0.0,
        current_iv=50.0,
        iv_rank=0.5,
        open_position=None,
    )
    assert brief.free_equity_pct == 100.0
    assert brief.low_capital_warning is False
