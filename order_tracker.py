"""
order_tracker.py — Order confirmation and fill tracking for live mode.

Monitors pending orders until they fill, handle partial fills, cancel
timed-out orders, and optionally retry at market price.

Used by WheelBot in live mode to ensure every order is confirmed before
updating internal position state.

Usage (inside bot.py live mode):
    tracker = OrderTracker(ws_client)
    order_id = await tracker.place_and_track(
        side="sell",
        instrument_name="BTC-25APR25-80000-P",
        amount=0.1,
        price=0.005,       # limit price in BTC
        timeout_seconds=30,
        fallback_market=True,
    )
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from loguru import logger


# ── Order state machine ────────────────────────────────────────────────────────


class OrderStatus(Enum):
    PENDING    = "pending"      # submitted, awaiting confirmation
    OPEN       = "open"         # on order book, not yet filled
    FILLED     = "filled"       # fully filled ✓
    PARTIAL    = "partial"      # partially filled (amount_remaining > 0)
    CANCELLED  = "cancelled"    # cancelled (by bot, user, or exchange)
    REJECTED   = "rejected"     # exchange rejected (insufficient margin etc.)
    TIMEOUT    = "timeout"      # bot timed out waiting for fill


@dataclass
class OrderRecord:
    order_id: str
    instrument_name: str
    side: str                      # "buy" | "sell"
    requested_amount: float        # contracts requested
    requested_price: float | None  # None = market order
    label: str = ""

    # Mutable state
    status: OrderStatus = OrderStatus.PENDING
    filled_amount: float = 0.0
    avg_fill_price: float = 0.0
    remaining_amount: float = 0.0
    slippage_btc: float = 0.0      # avg_fill_price - requested_price (sell: negative = better)
    created_at: float = field(default_factory=time.time)
    filled_at: float | None = None
    error_message: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.TIMEOUT,
        )

    @property
    def fill_pct(self) -> float:
        if self.requested_amount <= 0:
            return 0.0
        return self.filled_amount / self.requested_amount * 100.0

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.created_at


# ── Tracker ────────────────────────────────────────────────────────────────────


class OrderTracker:
    """
    Tracks live Deribit orders from placement to fill.

    Design:
        - place_and_track() places the order and polls until terminal state
        - Poll interval starts at 1s, backs off to 5s for long-running orders
        - On timeout: cancel the limit order, optionally retry at market
        - Slippage is computed and logged for every fill
        - on_fill callback (optional) is invoked when fully filled
    """

    def __init__(
        self,
        ws_client,    # DeribitWebSocket instance
        on_fill: Callable[[OrderRecord], None] | None = None,
    ) -> None:
        self._ws       = ws_client
        self._on_fill  = on_fill
        self._orders: dict[str, OrderRecord] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    async def place_and_track(
        self,
        side: str,
        instrument_name: str,
        amount: float,
        price: float | None = None,
        label: str = "wheel_bot",
        timeout_seconds: float = 45.0,
        fallback_market: bool = True,
        poll_interval: float = 2.0,
    ) -> OrderRecord:
        """
        Place a limit (or market) order and wait until it fills or times out.

        Args:
            side:             "sell" (open short) or "buy" (close short)
            instrument_name:  e.g. "BTC-25APR25-80000-P"
            amount:           number of contracts (min 0.1 on Deribit)
            price:            limit price in BTC (None = market order)
            label:            order label for Deribit UI
            timeout_seconds:  seconds to wait before cancelling + retrying
            fallback_market:  if True, retry as market order after timeout
            poll_interval:    base polling interval in seconds

        Returns:
            OrderRecord with final status (FILLED, CANCELLED, TIMEOUT, REJECTED)
        """
        order_type = "limit" if price is not None else "market"
        logger.info(
            f"Placing {side.upper()} {order_type} order: "
            f"{instrument_name} × {amount} @ "
            f"{price:.6f} BTC" if price else f"{instrument_name} × {amount} [market]"
        )

        # Place the order
        try:
            if side == "sell":
                result = await self._ws.sell_option(
                    instrument_name=instrument_name,
                    amount=amount,
                    order_type=order_type,
                    price=price,
                    label=label,
                )
            else:
                result = await self._ws.buy_option(
                    instrument_name=instrument_name,
                    amount=amount,
                    order_type=order_type,
                    price=price,
                    label=label,
                )
        except Exception as exc:
            logger.error(f"Order placement failed: {exc}")
            rec = OrderRecord(
                order_id="",
                instrument_name=instrument_name,
                side=side,
                requested_amount=amount,
                requested_price=price,
                label=label,
                status=OrderStatus.REJECTED,
                error_message=str(exc),
            )
            return rec

        # Parse order ID from result
        order = result.get("order", result) if isinstance(result, dict) else {}
        order_id = order.get("order_id", "")
        if not order_id:
            logger.error(f"No order_id in response: {result}")
            rec = OrderRecord(
                order_id="",
                instrument_name=instrument_name,
                side=side,
                requested_amount=amount,
                requested_price=price,
                label=label,
                status=OrderStatus.REJECTED,
                error_message=f"No order_id in response: {result}",
            )
            return rec

        # Create tracking record
        rec = OrderRecord(
            order_id=order_id,
            instrument_name=instrument_name,
            side=side,
            requested_amount=amount,
            requested_price=price,
            label=label,
            status=OrderStatus.OPEN,
            remaining_amount=amount,
        )
        self._orders[order_id] = rec
        logger.info(f"Order placed: {order_id} | polling for fills…")

        # Poll until terminal
        deadline = time.time() + timeout_seconds
        interval = poll_interval

        while not rec.is_terminal:
            await asyncio.sleep(interval)
            await self._refresh_order(rec)

            if rec.is_terminal:
                break

            if time.time() >= deadline:
                # Timeout — cancel the order
                logger.warning(
                    f"Order {order_id} timed out after {timeout_seconds}s "
                    f"({rec.fill_pct:.0f}% filled)"
                )
                await self._cancel_order(rec)

                if fallback_market and rec.remaining_amount > 0:
                    logger.info(
                        f"Retrying {rec.remaining_amount} remaining contracts at market…"
                    )
                    market_rec = await self.place_and_track(
                        side=side,
                        instrument_name=instrument_name,
                        amount=rec.remaining_amount,
                        price=None,          # market
                        label=f"{label}_mkt",
                        timeout_seconds=15.0,
                        fallback_market=False,
                    )
                    # Merge fills into original record
                    if market_rec.status == OrderStatus.FILLED:
                        rec.filled_amount += market_rec.filled_amount
                        rec.status = OrderStatus.FILLED
                        rec.remaining_amount = 0.0
                        # Blended average fill price
                        if rec.filled_amount > 0:
                            orig_contrib  = (rec.filled_amount - market_rec.filled_amount) * rec.avg_fill_price
                            mkt_contrib   = market_rec.filled_amount * market_rec.avg_fill_price
                            rec.avg_fill_price = (orig_contrib + mkt_contrib) / rec.filled_amount
                break

            # Gentle backoff (cap at 5s)
            interval = min(interval * 1.2, 5.0)

        self._log_outcome(rec)
        if rec.status == OrderStatus.FILLED and self._on_fill:
            self._on_fill(rec)

        return rec

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _refresh_order(self, rec: OrderRecord) -> None:
        """Poll Deribit for the latest order state."""
        try:
            result = await self._ws._rpc("private/get_order_state", {
                "order_id": rec.order_id,
            })
            self._apply_order_state(rec, result)
        except Exception as exc:
            logger.debug(f"Order state poll failed for {rec.order_id}: {exc}")

    @staticmethod
    def _apply_order_state(rec: OrderRecord, state: dict) -> None:
        """Map Deribit order_state response fields onto the OrderRecord."""
        deribit_status = state.get("order_state", "")
        filled   = float(state.get("filled_amount", 0.0))
        total    = float(state.get("amount", rec.requested_amount))
        avg_price = float(state.get("average_price", 0.0))

        rec.filled_amount    = filled
        rec.remaining_amount = max(0.0, total - filled)
        rec.avg_fill_price   = avg_price

        if avg_price and rec.requested_price:
            rec.slippage_btc = avg_price - rec.requested_price

        if deribit_status == "filled":
            rec.status    = OrderStatus.FILLED
            rec.filled_at = time.time()
        elif deribit_status == "cancelled":
            rec.status = OrderStatus.CANCELLED
        elif deribit_status == "rejected":
            rec.status = OrderStatus.REJECTED
            rec.error_message = state.get("reject_reason", "unknown")
        elif filled > 0:
            rec.status = OrderStatus.PARTIAL
        else:
            rec.status = OrderStatus.OPEN

    async def _cancel_order(self, rec: OrderRecord) -> None:
        """Cancel a pending order on Deribit."""
        try:
            await self._ws._rpc("private/cancel", {"order_id": rec.order_id})
            rec.status = OrderStatus.TIMEOUT
            logger.info(f"Order {rec.order_id} cancelled (timeout)")
        except Exception as exc:
            logger.warning(f"Cancel failed for {rec.order_id}: {exc}")
            rec.status = OrderStatus.TIMEOUT  # assume cancelled anyway

    def _log_outcome(self, rec: OrderRecord) -> None:
        """Emit a structured log line summarising the order outcome."""
        fill_str = (
            f"avg_fill={rec.avg_fill_price:.6f} BTC "
            f"slippage={rec.slippage_btc:+.6f} BTC"
            if rec.avg_fill_price else "no fill"
        )
        logger.info(
            f"Order {rec.order_id} → {rec.status.value.upper()} | "
            f"{rec.fill_pct:.0f}% filled ({rec.filled_amount}/{rec.requested_amount}) | "
            f"{fill_str} | elapsed={rec.elapsed_seconds:.1f}s"
        )

    # ── Convenience accessors ──────────────────────────────────────────────────

    def get(self, order_id: str) -> OrderRecord | None:
        return self._orders.get(order_id)

    @property
    def all_orders(self) -> list[OrderRecord]:
        return list(self._orders.values())

    @property
    def open_orders(self) -> list[OrderRecord]:
        return [o for o in self._orders.values() if not o.is_terminal]

    def slippage_summary(self) -> dict:
        """Return aggregate slippage stats across all tracked fills."""
        filled = [o for o in self._orders.values() if o.status == OrderStatus.FILLED]
        if not filled:
            return {"count": 0, "avg_slippage_btc": 0.0, "total_slippage_btc": 0.0}
        slippages = [o.slippage_btc for o in filled if o.slippage_btc != 0.0]
        avg = sum(slippages) / len(slippages) if slippages else 0.0
        return {
            "count": len(filled),
            "avg_slippage_btc": round(avg, 8),
            "total_slippage_btc": round(sum(slippages), 8),
        }
