"""
hedge_manager.py — Delta-neutral hedging via BTC-PERPETUAL futures.

Keeps the portfolio's net delta near zero by maintaining an offsetting
perpetual futures position. The bot collects only theta (time decay) and
the IV premium — not directional BTC exposure.

Sign convention
---------------
  Short put  → portfolio delta POSITIVE  (+delta × contracts)
               BTC rises → we profit on the option
               Hedge: SHORT perp (negative perp position)

  Short call → portfolio delta NEGATIVE  (-delta × contracts)
               BTC falls → we profit on the option
               Hedge: LONG perp (positive perp position)

  Net delta = option_portfolio_delta + perp_position_btc  ≈ 0

Rebalancing
-----------
  Each tick: if |required_perp − current_perp| >= rebalance_threshold,
  execute a BTC-PERPETUAL trade to close the gap.

  In paper mode: positions and P&L are fully simulated.
  In live mode:  market orders are placed via the WebSocket client.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loguru import logger


# Minimum lot size on Deribit perpetual (BTC)
PERP_MIN_LOT = 0.1


@dataclass
class HedgeState:
    """Persisted hedge state — written to data/hedge_state.json each tick."""
    perp_position_btc: float = 0.0   # + = long BTC-PERP, - = short BTC-PERP
    avg_entry_price: float  = 0.0    # weighted average entry (USD)
    realised_pnl_usd: float = 0.0   # cumulative realised P&L from hedge trades
    funding_paid_usd: float = 0.0   # cumulative funding rate costs (estimated)
    rebalance_count: int    = 0      # total hedge adjustments made


class HedgeManager:
    """
    Tracks and rebalances a BTC-PERPETUAL position to neutralise option delta.

    Usage (from bot.py tick loop):
        # After updating option mark prices:
        await self._hedge.rebalance(pos.option_type, pos.current_delta,
                                    pos.contracts, spot_price, ws_client)

        # When closing the option position:
        await self._hedge.close_all(spot_price, ws_client)
    """

    def __init__(
        self,
        paper: bool = True,
        rebalance_threshold: float = 0.05,
        state_path: Path | None = None,
    ) -> None:
        self._paper = paper
        self._rebalance_threshold = rebalance_threshold
        self._state_path = state_path or (
            Path(__file__).parent / "data" / "hedge_state.json"
        )
        self._state = self._load_state()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_state(self) -> HedgeState:
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text())
                return HedgeState(**{
                    k: v for k, v in data.items()
                    if k in HedgeState.__dataclass_fields__
                })
        except Exception:
            pass
        return HedgeState()

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(exist_ok=True)
            self._state_path.write_text(json.dumps(asdict(self._state), indent=2))
        except Exception:
            pass

    # ── Core calculations ──────────────────────────────────────────────────────

    def required_hedge_btc(
        self, option_type: str, delta_abs: float, contracts: float
    ) -> float:
        """
        Ideal perp position (BTC) to make net delta zero.

        Short put:  option portfolio delta = +delta_abs × contracts
                    hedge = -delta_abs × contracts  (short perp)

        Short call: option portfolio delta = -delta_abs × contracts
                    hedge = +delta_abs × contracts  (long perp)
        """
        size = delta_abs * contracts
        return -size if option_type == "put" else +size

    def net_delta_btc(
        self, option_type: str, delta_abs: float, contracts: float
    ) -> float:
        """
        Current net portfolio delta (options + perp).
        0.0 = perfectly hedged. Positive = net long BTC. Negative = net short.
        """
        option_portfolio_delta = (
            +delta_abs * contracts if option_type == "put"
            else -delta_abs * contracts
        )
        return option_portfolio_delta + self._state.perp_position_btc

    def unrealised_pnl_usd(self, current_spot: float) -> float:
        """Mark-to-market P&L on the current open perp position."""
        pos = self._state.perp_position_btc
        entry = self._state.avg_entry_price
        if pos == 0.0 or entry == 0.0 or current_spot == 0.0:
            return 0.0
        return pos * (current_spot - entry)

    # ── Rebalancing ────────────────────────────────────────────────────────────

    async def rebalance(
        self,
        option_type: str,
        delta_abs: float,
        contracts: float,
        spot_price: float,
        ws_client: Any = None,
    ) -> float:
        """
        Check if the hedge needs adjustment and execute if so.

        Returns the BTC adjustment made (0.0 if within threshold).
        """
        required = self.required_hedge_btc(option_type, delta_abs, contracts)
        raw_adjustment = required - self._state.perp_position_btc

        # Round to nearest minimum lot
        lots = round(raw_adjustment / PERP_MIN_LOT)
        adjustment = lots * PERP_MIN_LOT

        if abs(adjustment) < self._rebalance_threshold:
            return 0.0

        if self._paper:
            await self._paper_trade(adjustment, spot_price)
        else:
            await self._live_trade(adjustment, spot_price, ws_client)

        # Return the BTC trade size so callers can detect that a trade occurred
        return adjustment

    async def close_all(
        self, spot_price: float, ws_client: Any = None
    ) -> float:
        """
        Close the entire perp hedge position when the option closes.
        Returns realised P&L from closing.
        """
        if abs(self._state.perp_position_btc) < PERP_MIN_LOT / 2:
            self._state.perp_position_btc = 0.0
            self._state.avg_entry_price = 0.0
            self._save_state()
            return 0.0

        closing_trade = -self._state.perp_position_btc
        if self._paper:
            return await self._paper_trade(closing_trade, spot_price)
        else:
            return await self._live_trade(closing_trade, spot_price, ws_client)

    # ── Execution ──────────────────────────────────────────────────────────────

    async def _paper_trade(self, adjustment_btc: float, spot_price: float) -> float:
        """
        Simulate a BTC-PERPETUAL trade in paper mode.

        Handles weighted-average entry price and realises P&L when
        an existing position is partially or fully closed.

        Returns realised P&L (USD) from this trade.
        """
        old_pos   = self._state.perp_position_btc
        old_entry = self._state.avg_entry_price
        realised  = 0.0

        # Realise P&L on any portion that is being closed/reduced
        same_sign_close = (
            old_pos != 0.0
            and old_entry > 0.0
            and (
                (old_pos > 0 and adjustment_btc < 0)
                or (old_pos < 0 and adjustment_btc > 0)
            )
        )
        if same_sign_close:
            closing_btc = min(abs(adjustment_btc), abs(old_pos))
            if old_pos > 0:
                realised = closing_btc * (spot_price - old_entry)
            else:
                realised = closing_btc * (old_entry - spot_price)
            self._state.realised_pnl_usd += realised

        new_pos = round(old_pos + adjustment_btc, 4)

        # Update weighted average entry price
        if abs(new_pos) < 0.001:
            self._state.avg_entry_price = 0.0
        elif old_pos == 0.0 or (old_pos > 0) != (new_pos > 0):
            # Starting fresh or flipped direction
            self._state.avg_entry_price = spot_price
        elif (adjustment_btc > 0 and new_pos > 0) or (adjustment_btc < 0 and new_pos < 0):
            # Adding to position — weighted average
            self._state.avg_entry_price = (
                abs(old_pos) * old_entry + abs(adjustment_btc) * spot_price
            ) / abs(new_pos)
        # else: reducing position — entry price unchanged

        self._state.perp_position_btc = new_pos
        self._state.rebalance_count  += 1

        direction = "BUY" if adjustment_btc > 0 else "SELL"
        logger.info(
            f"[PAPER HEDGE] {direction} {abs(adjustment_btc):.3f} BTC-PERP @ "
            f"${spot_price:,.0f} | hedge position: {new_pos:+.3f} BTC"
            + (f" | realised: ${realised:+.2f}" if realised != 0 else "")
        )
        self._save_state()
        return realised

    async def _live_trade(
        self, adjustment_btc: float, spot_price: float, ws_client: Any
    ) -> float:
        """
        Place a real BTC-PERPETUAL market order via the WebSocket client.
        Returns realised P&L (estimated from fill price).
        """
        if ws_client is None:
            logger.error("HedgeManager: ws_client required for live trading")
            return 0.0

        direction = "buy" if adjustment_btc > 0 else "sell"
        amount = abs(adjustment_btc)

        try:
            params: dict = {
                "instrument_name": "BTC-PERPETUAL",
                "amount": amount,
                "type": "market",
                "label": "wheel_hedge",
            }
            method = "private/buy" if direction == "buy" else "private/sell"
            result = await ws_client._rpc(method, params)

            fill_price = float(
                result.get("order", {}).get("average_price", spot_price)
            )
            logger.info(
                f"[LIVE HEDGE] {direction.upper()} {amount:.3f} BTC-PERP @ "
                f"${fill_price:,.0f}"
            )
            return await self._paper_trade(adjustment_btc, fill_price)

        except Exception as exc:
            logger.error(f"HedgeManager live trade failed: {exc}")
            return 0.0

    # ── Public accessors ───────────────────────────────────────────────────────

    @property
    def position_btc(self) -> float:
        return self._state.perp_position_btc

    @property
    def realised_pnl_usd(self) -> float:
        return self._state.realised_pnl_usd

    @property
    def rebalance_count(self) -> int:
        return self._state.rebalance_count

    def reset(self) -> None:
        """Reset all hedge state (use when starting a fresh paper session)."""
        self._state = HedgeState()
        self._save_state()
        logger.info("HedgeManager: state reset")

    def to_dict(self, spot_price: float = 0.0) -> dict:
        """Serialise hedge state for API / heartbeat output."""
        return {
            "perp_position_btc":  round(self._state.perp_position_btc, 4),
            "avg_entry_price":    round(self._state.avg_entry_price, 2),
            "unrealised_pnl_usd": round(self.unrealised_pnl_usd(spot_price), 2),
            "realised_pnl_usd":   round(self._state.realised_pnl_usd, 2),
            "funding_paid_usd":   round(self._state.funding_paid_usd, 2),
            "rebalance_count":    self._state.rebalance_count,
        }
