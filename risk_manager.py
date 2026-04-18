"""
risk_manager.py — Position sizing and risk controls.

All checks return True = safe to proceed, False = halt/roll.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from config import cfg


@dataclass
class Position:
    instrument_name: str
    strike: float
    option_type: str           # "put" | "call"
    entry_price: float         # premium received (BTC per contract)
    underlying_at_entry: float # spot price when sold
    contracts: float           # number of contracts
    current_delta: float       # current absolute delta
    current_price: float       # current mark price (BTC)
    entry_equity: float        # account equity at time of entry


class RiskManager:
    """
    Enforces all risk rules before and during position lifecycle.

    Pre-trade checks (call before opening):
        check_kill_switch()
        check_max_legs()
        check_position_size()
        check_collateral()

    In-trade checks (call on each poll):
        should_roll()
        check_drawdown()
    """

    def __init__(self) -> None:
        self._kill_switch_path = Path(cfg.risk.kill_switch_file)

    # ── Pre-trade ─────────────────────────────────────────────────────────────

    def check_kill_switch(self) -> bool:
        """Return False (block trading) if KILL_SWITCH file exists."""
        if self._kill_switch_path.exists():
            logger.critical(
                f"KILL SWITCH ACTIVE — file '{cfg.risk.kill_switch_file}' found. "
                "All trading halted. Delete the file to resume."
            )
            return False
        return True

    def check_max_legs(self, open_positions: list[Position]) -> bool:
        """Block new positions if at maximum open legs."""
        if len(open_positions) >= cfg.sizing.max_open_legs:
            logger.info(
                f"Max open legs reached ({cfg.sizing.max_open_legs}). "
                "Skipping new position."
            )
            return False
        return True

    def calculate_contracts(self, equity_usd: float, strike_usd: float) -> float:
        """
        Calculate the number of contracts to sell given account equity and strike.

        Collateral required per contract = strike × contract_size_btc
        But since we're selling puts, the collateral is in USD notional.
        We target max_equity_per_leg fraction of equity.

        Returns number of contracts (floored to nearest 0.1 for Deribit).
        """
        if strike_usd <= 0 or equity_usd <= 0:
            return 0.0

        # Maximum notional exposure
        max_notional = equity_usd * cfg.sizing.max_equity_per_leg

        # Collateral per contract = strike price (USD) for cash-secured put
        collateral_per_contract = strike_usd * cfg.sizing.contract_size_btc

        contracts = max_notional / collateral_per_contract
        # Floor to 1 decimal place (Deribit minimum 0.1 BTC contracts)
        contracts = max(0.1, round(contracts, 1))

        logger.debug(
            f"Sizing: equity=${equity_usd:,.0f}, strike=${strike_usd:,.0f}, "
            f"max_notional=${max_notional:,.0f} → {contracts} contracts"
        )
        return contracts

    def check_position_size(self, equity_usd: float, strike_usd: float) -> bool:
        """Return True if at least 0.1 contracts can be sized from current equity."""
        if strike_usd <= 0 or equity_usd <= 0:
            return False
        max_notional = equity_usd * cfg.sizing.max_equity_per_leg
        collateral_per_contract = strike_usd * cfg.sizing.contract_size_btc
        if collateral_per_contract <= 0:
            return False
        raw_contracts = max_notional / collateral_per_contract
        if raw_contracts < 0.1:  # check before clamping to minimum lot size
            logger.warning(
                f"Position too small: {raw_contracts:.4f} raw contracts at strike "
                f"${strike_usd:,.0f}. Equity ${equity_usd:,.0f} too low."
            )
            return False
        return True

    def check_collateral(
        self, open_positions: list[Position], equity_usd: float, btc_price: float
    ) -> bool:
        """
        Verify total collateral exposure does not exceed the buffer limit.

        Collateral used = sum(strike × contracts × contract_size) for each put.
        Buffer limit = equity × collateral_buffer (default 150%).
        """
        if not open_positions:
            return True

        total_collateral = sum(
            pos.strike * pos.contracts * cfg.sizing.contract_size_btc
            for pos in open_positions
        )
        max_allowed = equity_usd * cfg.sizing.collateral_buffer

        if total_collateral > max_allowed:
            logger.warning(
                f"Collateral check FAILED: ${total_collateral:,.0f} used "
                f"vs ${max_allowed:,.0f} allowed ({cfg.sizing.collateral_buffer:.0%} of equity)"
            )
            return False

        logger.debug(
            f"Collateral OK: ${total_collateral:,.2f} / ${max_allowed:,.2f}"
        )
        return True

    # ── In-trade ──────────────────────────────────────────────────────────────

    def should_roll(self, position: Position) -> tuple[bool, str]:
        """
        Check if an open position should be rolled or closed.

        Returns (should_roll: bool, reason: str).
        Reasons: "delta_breach", "loss_breach", "ok"
        """
        # Delta breach
        if abs(position.current_delta) > cfg.risk.max_adverse_delta:
            logger.warning(
                f"Delta breach on {position.instrument_name}: "
                f"|delta|={abs(position.current_delta):.3f} > "
                f"{cfg.risk.max_adverse_delta}"
            )
            return True, "delta_breach"

        # Loss breach (unrealised)
        premium_received = position.entry_price * position.contracts
        current_cost = position.current_price * position.contracts
        unrealised_pnl_btc = premium_received - current_cost
        unrealised_pnl_usd = unrealised_pnl_btc * position.underlying_at_entry
        loss_pct = -unrealised_pnl_usd / position.entry_equity

        if loss_pct > cfg.risk.max_loss_per_leg:
            logger.warning(
                f"Loss breach on {position.instrument_name}: "
                f"loss={loss_pct:.2%} > {cfg.risk.max_loss_per_leg:.2%}"
            )
            return True, "loss_breach"

        return False, "ok"

    def check_drawdown(self, equity_curve: list[float]) -> bool:
        """
        Check daily drawdown from peak equity.
        Returns False (halt trading) if drawdown exceeds threshold.
        """
        if len(equity_curve) < 2:
            return True

        peak = max(equity_curve)
        current = equity_curve[-1]
        drawdown = (peak - current) / peak

        if drawdown > cfg.risk.max_daily_drawdown:
            logger.error(
                f"Drawdown limit breached: {drawdown:.2%} > "
                f"{cfg.risk.max_daily_drawdown:.2%}. Trading paused."
            )
            return False

        logger.debug(f"Drawdown OK: {drawdown:.2%}")
        return True

    def full_pre_trade_check(
        self,
        open_positions: list[Position],
        equity_usd: float,
        strike_usd: float,
        btc_price: float,
    ) -> bool:
        """
        Run all pre-trade checks in sequence.
        Returns True only if every check passes.
        """
        checks = [
            self.check_kill_switch(),
            self.check_max_legs(open_positions),
            self.check_position_size(equity_usd, strike_usd),
            self.check_collateral(open_positions, equity_usd, btc_price),
        ]
        result = all(checks)
        if result:
            logger.info("Pre-trade checks PASSED")
        else:
            logger.warning("Pre-trade checks FAILED — position not opened")
        return result
