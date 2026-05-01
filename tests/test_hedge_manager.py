"""
tests/test_hedge_manager.py — pin HedgeManager rebalance + paper-trade math.

The HedgeManager is the engine behind the user's "nullify directional risk
by hedging" thesis. Without test coverage, a future refactor could silently
break delta calculations or P&L accounting and the bot would drift away
from delta-neutral while paper mode looked fine.

Covers:
- required_hedge_btc sign convention (short put → short perp; short call → long perp)
- net_delta_btc reflects current state
- _paper_trade weighted-average entry, realised P&L on closing trades,
  flip-direction handling
- rebalance threshold gating (skip < threshold, fire ≥ threshold)
- close_all returns realised P&L and zeroes state
- State persistence (load → modify → save → load)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from hedge_manager import HedgeManager, HedgeState, PERP_MIN_LOT


@pytest.fixture
def hedge(tmp_path: Path) -> HedgeManager:
    """Fresh paper HedgeManager with isolated state path."""
    state_path = tmp_path / "hedge_state.json"
    return HedgeManager(paper=True, rebalance_threshold=0.05, state_path=state_path)


def _run(coro):
    """Synchronously run a coroutine in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Sign convention ───────────────────────────────────────────────────────────


def test_short_put_requires_short_perp(hedge):
    """Short put has positive portfolio delta → hedge with negative perp."""
    required = hedge.required_hedge_btc("put", delta_abs=0.30, contracts=1.0)
    assert required == -0.30


def test_short_call_requires_long_perp(hedge):
    """Short call has negative portfolio delta → hedge with positive perp."""
    required = hedge.required_hedge_btc("call", delta_abs=0.30, contracts=1.0)
    assert required == +0.30


def test_required_scales_with_contracts(hedge):
    """Required hedge scales linearly with contracts."""
    a = hedge.required_hedge_btc("put", delta_abs=0.30, contracts=0.1)
    b = hedge.required_hedge_btc("put", delta_abs=0.30, contracts=1.0)
    c = hedge.required_hedge_btc("put", delta_abs=0.30, contracts=10.0)
    assert b == 10 * a
    assert c == 10 * b


# ── net_delta_btc ─────────────────────────────────────────────────────────────


def test_net_delta_zero_when_perfectly_hedged(hedge):
    """If perp position offsets option delta, net should be ~zero."""
    hedge._state.perp_position_btc = -0.30
    net = hedge.net_delta_btc("put", delta_abs=0.30, contracts=1.0)
    assert net == pytest.approx(0.0, abs=1e-9)


def test_net_delta_positive_when_underhedged(hedge):
    """No perp at all → net delta = full option delta."""
    net = hedge.net_delta_btc("put", delta_abs=0.30, contracts=1.0)
    assert net == 0.30


# ── _paper_trade math ─────────────────────────────────────────────────────────


def test_paper_open_short_position(hedge):
    """First trade: -0.5 BTC at $70k → state goes negative."""
    realised = _run(hedge._paper_trade(adjustment_btc=-0.5, spot_price=70_000.0))
    assert hedge._state.perp_position_btc == -0.5
    assert hedge._state.avg_entry_price == 70_000.0
    assert realised == 0.0   # no realised on opening


def test_paper_close_at_profit(hedge):
    """Open -0.5 BTC at $70k, close +0.5 BTC at $60k → profit (short into drop)."""
    _run(hedge._paper_trade(adjustment_btc=-0.5, spot_price=70_000.0))
    realised = _run(hedge._paper_trade(adjustment_btc=+0.5, spot_price=60_000.0))
    # Short 0.5 BTC entered $70k, closed $60k → 0.5 × (70k - 60k) = +$5,000
    assert realised == pytest.approx(5_000.0)
    assert hedge._state.perp_position_btc == 0.0
    assert hedge._state.avg_entry_price == 0.0


def test_paper_close_at_loss(hedge):
    """Open +0.5 BTC at $70k, close -0.5 BTC at $60k → loss (long into drop)."""
    _run(hedge._paper_trade(adjustment_btc=+0.5, spot_price=70_000.0))
    realised = _run(hedge._paper_trade(adjustment_btc=-0.5, spot_price=60_000.0))
    # Long 0.5 BTC entered $70k, closed $60k → 0.5 × (60k - 70k) = -$5,000
    assert realised == pytest.approx(-5_000.0)


def test_paper_partial_close(hedge):
    """Closing only part of a position realises proportional P&L."""
    _run(hedge._paper_trade(adjustment_btc=-1.0, spot_price=70_000.0))
    realised = _run(hedge._paper_trade(adjustment_btc=+0.4, spot_price=65_000.0))
    # Close 0.4 of the short: 0.4 × (70k - 65k) = +$2,000
    assert realised == pytest.approx(2_000.0)
    assert hedge._state.perp_position_btc == pytest.approx(-0.6)
    # Entry price unchanged on a partial reduction
    assert hedge._state.avg_entry_price == 70_000.0


def test_paper_add_to_position_uses_weighted_average(hedge):
    """Adding to a position should weight-average the entry price."""
    _run(hedge._paper_trade(adjustment_btc=-1.0, spot_price=70_000.0))
    _run(hedge._paper_trade(adjustment_btc=-1.0, spot_price=80_000.0))
    # 1 BTC at 70k + 1 BTC at 80k → 2 BTC at 75k average
    assert hedge._state.avg_entry_price == pytest.approx(75_000.0)
    assert hedge._state.perp_position_btc == pytest.approx(-2.0)


def test_paper_flip_direction_resets_entry(hedge):
    """Flipping from short to long resets entry price to current spot."""
    _run(hedge._paper_trade(adjustment_btc=-0.5, spot_price=70_000.0))
    _run(hedge._paper_trade(adjustment_btc=+1.5, spot_price=80_000.0))
    # Closed 0.5 short at $80k (loss of $5k), then opened 1.0 long at $80k
    assert hedge._state.perp_position_btc == pytest.approx(+1.0)
    assert hedge._state.avg_entry_price == pytest.approx(80_000.0)


# ── rebalance threshold gating ────────────────────────────────────────────────


def test_rebalance_skips_below_threshold(hedge):
    """Adjustment < threshold → no trade fires."""
    hedge._state.perp_position_btc = -0.30
    # Required is -0.32 (delta 0.32 × contracts 1.0); diff is -0.02 BTC
    adj = _run(hedge.rebalance("put", delta_abs=0.32, contracts=1.0, spot_price=70_000.0))
    assert adj == 0.0
    assert hedge._state.perp_position_btc == -0.30   # unchanged


def test_rebalance_fires_above_threshold(hedge):
    """Adjustment ≥ threshold → trade executes and state updates."""
    hedge._state.perp_position_btc = -0.30
    # Required is -0.50 (delta 0.50); diff is -0.20 BTC > threshold
    adj = _run(hedge.rebalance("put", delta_abs=0.50, contracts=1.0, spot_price=70_000.0))
    assert adj == pytest.approx(-0.20)
    assert hedge._state.perp_position_btc == pytest.approx(-0.50)


# ── close_all ─────────────────────────────────────────────────────────────────


def test_close_all_zeroes_position(hedge):
    """close_all returns realised P&L and zeroes state."""
    _run(hedge._paper_trade(adjustment_btc=-1.0, spot_price=70_000.0))
    realised = _run(hedge.close_all(spot_price=65_000.0))
    # Short 1 BTC entered 70k closed at 65k → +$5k
    assert realised == pytest.approx(5_000.0)
    assert hedge._state.perp_position_btc == 0.0


def test_close_all_when_already_flat_returns_zero(hedge):
    """No-op when there's nothing to close."""
    realised = _run(hedge.close_all(spot_price=70_000.0))
    assert realised == 0.0


# ── State persistence ────────────────────────────────────────────────────────


def test_state_round_trip(tmp_path):
    """State persists across HedgeManager instances via the JSON file."""
    state_path = tmp_path / "hedge_state.json"
    hm1 = HedgeManager(paper=True, state_path=state_path)
    _run(hm1._paper_trade(adjustment_btc=-0.5, spot_price=70_000.0))
    # Fresh instance reads the state from disk
    hm2 = HedgeManager(paper=True, state_path=state_path)
    assert hm2._state.perp_position_btc == -0.5
    assert hm2._state.avg_entry_price == 70_000.0


def test_reset_zeroes_all_state(tmp_path):
    """reset() should clear everything."""
    state_path = tmp_path / "hedge_state.json"
    hm = HedgeManager(paper=True, state_path=state_path)
    _run(hm._paper_trade(adjustment_btc=-1.0, spot_price=70_000.0))
    hm.reset()
    assert hm._state.perp_position_btc == 0.0
    assert hm._state.avg_entry_price == 0.0
    assert hm._state.realised_pnl_usd == 0.0
    assert hm._state.rebalance_count == 0


# ── unrealised P&L ────────────────────────────────────────────────────────────


def test_unrealised_pnl_short_at_lower_spot_is_profit(hedge):
    """Short BTC and price drops → unrealised profit."""
    _run(hedge._paper_trade(adjustment_btc=-1.0, spot_price=70_000.0))
    pnl = hedge.unrealised_pnl_usd(current_spot=60_000.0)
    # -1 BTC × ($60k - $70k) = +$10,000
    assert pnl == pytest.approx(10_000.0)


def test_unrealised_pnl_zero_when_flat(hedge):
    """No position → no mark-to-market P&L."""
    pnl = hedge.unrealised_pnl_usd(current_spot=70_000.0)
    assert pnl == 0.0


def test_unrealised_pnl_zero_at_zero_spot(hedge):
    """Defensive: spot=0 returns 0 (rather than blowing up the math)."""
    _run(hedge._paper_trade(adjustment_btc=-1.0, spot_price=70_000.0))
    pnl = hedge.unrealised_pnl_usd(current_spot=0.0)
    assert pnl == 0.0
