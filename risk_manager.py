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
    expiry_ts: int = 0         # Deribit expiry timestamp (ms) — used for DTE tracking
    iv_rank_at_entry: float = 0.0  # IV rank at time of entry (0–1)
    dte_at_entry: int = 0          # Days to expiry when position was opened


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

    def calculate_contracts(
        self,
        equity_usd: float,
        strike_usd: float,
        equity_fraction: float | None = None,
    ) -> float:
        """
        Calculate the number of contracts to sell given account equity and strike.

        Collateral required per contract = strike × contract_size_btc
        But since we're selling puts, the collateral is in USD notional.
        We target max_equity_per_leg fraction of equity.

        equity_fraction: optional override for the max_equity_per_leg fraction.
        Used by the strike ladder to split exposure evenly across legs.

        Returns number of contracts (floored to nearest 0.1 for Deribit).
        """
        if strike_usd <= 0 or equity_usd <= 0:
            return 0.0

        # Maximum notional exposure
        fraction    = equity_fraction if equity_fraction is not None else cfg.sizing.max_equity_per_leg
        max_notional = equity_usd * fraction

        # On Deribit: 1 contract = 1 BTC notional, minimum lot = contract_size_btc (0.1)
        # Collateral per FULL contract = strike_usd (USD value of 1 BTC at strike)
        collateral_per_contract = strike_usd

        raw_contracts = max_notional / collateral_per_contract

        # Floor to nearest minimum lot (0.1 contracts on Deribit)
        min_lot = cfg.sizing.contract_size_btc  # 0.1
        import math
        contracts = max(min_lot, math.floor(raw_contracts / min_lot) * min_lot)

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
        # Canonical formula: collateral per contract = strike (1 contract = 1 BTC notional).
        # contract_size_btc (0.1) is the minimum lot size, NOT a collateral multiplier.
        collateral_per_contract = strike_usd
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

    def check_free_margin(
        self,
        equity_usd: float,
        open_positions: list[Position],
        proposed_strike_usd: float,
        proposed_contracts: float,
    ) -> bool:
        """
        Verify opening a new leg leaves enough FREE (unreserved) capital.

        For cash-secured puts on Deribit the full strike notional is locked
        as collateral.  This check ensures we never over-commit liquidity —
        even if individual position sizing looks fine, stacking multiple
        legs (or a large strike) can leave the account with no cushion.

        Calculation
        -----------
            reserved_now  = Σ(strike × contracts × contract_size) for existing legs
            reserved_new  = proposed_strike × proposed_contracts × contract_size
            free_after    = equity - reserved_now - reserved_new
            required_free = equity × min_free_equity_fraction

        Returns False (block trade) if free_after < required_free.
        """
        threshold = cfg.sizing.min_free_equity_fraction
        if threshold <= 0.0:
            return True   # check disabled in config

        reserved_now = sum(
            pos.strike * pos.contracts * cfg.sizing.contract_size_btc
            for pos in open_positions
        )
        reserved_new  = proposed_strike_usd * proposed_contracts * cfg.sizing.contract_size_btc
        free_after    = equity_usd - reserved_now - reserved_new
        required_free = equity_usd * threshold

        if free_after < required_free:
            logger.warning(
                f"Free-margin check FAILED: ${free_after:,.0f} free after proposed leg "
                f"(need ≥ ${required_free:,.0f} = {threshold:.0%} of equity). "
                f"Reserved now: ${reserved_now:,.0f}  |  Proposed leg: ${reserved_new:,.0f}"
            )
            return False

        logger.debug(
            f"Free-margin OK: ${free_after:,.0f} free after leg "
            f"(min ${required_free:,.0f})"
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
        proposed_contracts: float | None = None,
    ) -> bool:
        """
        Run all pre-trade checks in sequence.
        Returns True only if every check passes.

        proposed_contracts is optional — if provided, the free-margin check
        uses the exact contract count.  If omitted, it is estimated from
        the position-sizing formula (same logic as calculate_contracts).
        """
        # Estimate contracts for free-margin check if not provided
        if proposed_contracts is None:
            proposed_contracts = self.calculate_contracts(equity_usd, strike_usd)

        checks = [
            self.check_kill_switch(),
            self.check_max_legs(open_positions),
            self.check_position_size(equity_usd, strike_usd),
            self.check_collateral(open_positions, equity_usd, btc_price),
            self.check_free_margin(equity_usd, open_positions, strike_usd, proposed_contracts),
        ]
        result = all(checks)
        if result:
            logger.info("Pre-trade checks PASSED")
        else:
            logger.warning("Pre-trade checks FAILED — position not opened")
        return result
