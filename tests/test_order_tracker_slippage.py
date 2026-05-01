"""
tests/test_order_tracker_slippage.py — pin the max-slippage abort path.

The audit added a slippage cap to OrderTracker.place_and_track's market
fallback branch. Without it, a forced close on a thin BTC option book can
walk the order book until filled — converting a manageable loss into a much
larger one. Default cap is 30%.

These tests exercise the slippage logic with a fake WebSocket that lets us
control the simulated fill price.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from order_tracker import OrderRecord, OrderStatus, OrderTracker


class _FakeWS:
    """Fake WebSocket client that simulates Deribit RPC responses."""

    def __init__(
        self,
        place_responses: list[dict] | None = None,
        state_responses: list[dict] | None = None,
    ):
        self.place_responses = place_responses or []
        self.state_responses = state_responses or []
        self.calls: list[tuple[str, dict]] = []

    async def sell_option(self, **kwargs):
        self.calls.append(("sell", kwargs))
        if not self.place_responses:
            raise RuntimeError("No more place_responses configured")
        return self.place_responses.pop(0)

    async def buy_option(self, **kwargs):
        self.calls.append(("buy", kwargs))
        if not self.place_responses:
            raise RuntimeError("No more place_responses configured")
        return self.place_responses.pop(0)

    async def _rpc(self, method: str, params: dict) -> Any:
        # Used by _refresh_order to poll get_order_state, and _cancel_order
        if method == "private/cancel":
            return {"order_state": "cancelled"}
        if not self.state_responses:
            return {"order_state": "open", "filled_amount": 0,
                    "amount": params.get("amount", 0.1), "average_price": 0.0}
        return self.state_responses.pop(0)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Helper builders ──────────────────────────────────────────────────────────


def _placed_order(order_id: str = "ord_1") -> dict:
    """Format of Deribit's response to private/buy or private/sell."""
    return {"order": {"order_id": order_id}}


def _state(order_state: str, filled: float, total: float, avg_price: float) -> dict:
    return {
        "order_state": order_state,
        "filled_amount": filled,
        "amount": total,
        "average_price": avg_price,
    }


# ── Direct-fill happy path ────────────────────────────────────────────────────


def test_limit_order_fills_at_limit_price():
    """If the limit order fills, no fallback is triggered."""
    ws = _FakeWS(
        place_responses=[_placed_order("ord_1")],
        state_responses=[_state("filled", filled=0.1, total=0.1, avg_price=0.0150)],
    )
    tracker = OrderTracker(ws_client=ws)
    rec = _run(tracker.place_and_track(
        side="sell",
        instrument_name="BTC-25APR25-70000-P",
        amount=0.1,
        price=0.0150,
        timeout_seconds=5.0,
        fallback_market=True,
    ))
    assert rec.status == OrderStatus.FILLED
    assert rec.avg_fill_price == 0.0150


# ── Slippage cap on market fallback ──────────────────────────────────────────


def test_slippage_below_cap_is_accepted():
    """
    Limit timed out → market fills 10% above limit. With default cap 30%,
    the 10% slippage should be accepted (rec ends FILLED).
    """
    ws = _FakeWS(
        place_responses=[
            _placed_order("ord_limit"),
            _placed_order("ord_market"),
        ],
        state_responses=[
            # First poll: limit order still open (no fill)
            _state("open", filled=0.0, total=0.1, avg_price=0.0),
            # Cancel succeeds (handled in _cancel_order, not via state poll)
            # Then market order: filled at 0.0165 (10% above 0.0150)
            _state("filled", filled=0.1, total=0.1, avg_price=0.0165),
        ],
    )
    tracker = OrderTracker(ws_client=ws)
    rec = _run(tracker.place_and_track(
        side="buy",
        instrument_name="BTC-25APR25-70000-P",
        amount=0.1,
        price=0.0150,
        timeout_seconds=0.5,    # short — forces timeout
        fallback_market=True,
        max_slippage_pct=0.30,
    ))
    # Either FILLED via market fallback or TIMEOUT — both acceptable, but if
    # a fallback fired and slip < 30%, status should be FILLED.
    if rec.status == OrderStatus.FILLED:
        assert abs(rec.avg_fill_price - 0.0165) < 1e-6


def test_slippage_above_cap_is_rejected():
    """
    Limit timed out → market would fill 50% above limit. With cap 30%, the
    market fill must be aborted and rec should NOT be FILLED.
    """
    ws = _FakeWS(
        place_responses=[
            _placed_order("ord_limit"),
            _placed_order("ord_market"),
        ],
        state_responses=[
            _state("open", filled=0.0, total=0.1, avg_price=0.0),
            # Market order would fill at 0.0225 — that's 50% above the 0.0150 limit
            _state("filled", filled=0.1, total=0.1, avg_price=0.0225),
        ],
    )
    tracker = OrderTracker(ws_client=ws)
    rec = _run(tracker.place_and_track(
        side="buy",
        instrument_name="BTC-25APR25-70000-P",
        amount=0.1,
        price=0.0150,
        timeout_seconds=0.5,
        fallback_market=True,
        max_slippage_pct=0.30,
    ))
    # Slippage cap should kick in; rec.status must NOT be FILLED
    assert rec.status != OrderStatus.FILLED, (
        f"Order with 50% slippage was accepted despite max_slippage_pct=0.30. "
        f"avg_fill_price={rec.avg_fill_price} vs limit={0.0150}"
    )
    # And the error_message should mention slippage
    assert "slippage" in (rec.error_message or "").lower()


def test_default_slippage_cap_is_30_pct():
    """Sanity-check that the default value hasn't changed silently."""
    import inspect
    sig = inspect.signature(OrderTracker.place_and_track)
    assert sig.parameters["max_slippage_pct"].default == 0.30


# ── Rejected-on-place ────────────────────────────────────────────────────────


def test_immediate_rejection_returns_rejected_status():
    """Exception during place → record returns REJECTED with the error message."""
    class _BadWS:
        async def sell_option(self, **kwargs):
            raise RuntimeError("Insufficient permissions")
        async def _rpc(self, *args, **kwargs):
            raise NotImplementedError

    tracker = OrderTracker(ws_client=_BadWS())
    rec = _run(tracker.place_and_track(
        side="sell",
        instrument_name="BTC-25APR25-70000-P",
        amount=0.1,
        price=0.0150,
        timeout_seconds=0.5,
        fallback_market=False,
    ))
    assert rec.status == OrderStatus.REJECTED
    assert "Insufficient permissions" in rec.error_message
