"""
bot.py — Main async trading loop for paper and live modes.

# LIVE_ONLY: This module is scaffolded but NOT activated in Phase 1.
# It will be wired up after backtesting is validated.

Modes:
  paper — connects to Deribit testnet, executes real orders but no real money
  live  — connects to mainnet, real orders, real money
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from config import cfg
from deribit_client import DeribitClient
from risk_manager import Position, RiskManager
from strategy import WheelStrategy


class WheelBot:
    """
    Async wheel strategy bot.

    Loop:
      1. Check kill switch
      2. Fetch account equity + open positions
      3. Check drawdown
      4. If no open position: run strategy, size, place order
      5. If position open: check roll/close conditions
      6. Sleep poll_interval seconds
      7. Repeat
    """

    def __init__(self) -> None:
        self._client = DeribitClient()
        self._risk = RiskManager()
        self._open_positions: list[Position] = []
        self._equity_curve: list[float] = []
        self._running: bool = False

    async def start(self) -> None:
        """Connect to Deribit and start the main loop."""
        logger.info(
            f"Starting WheelBot | mode={'testnet' if cfg.deribit.testnet else 'MAINNET'} | "
            f"poll={cfg.execution.poll_interval}s"
        )
        await self._client.connect_live()
        self._running = True

        # Register graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop)

        try:
            await self._main_loop()
        finally:
            await self._client.disconnect()
            logger.info("Bot stopped")

    def _stop(self) -> None:
        logger.warning("Shutdown signal received — stopping after current iteration")
        self._running = False

    async def _main_loop(self) -> None:
        """Core execution loop."""
        strategy: WheelStrategy | None = None
        last_cycle = cfg.strategy.initial_cycle

        while self._running:
            # ── Kill switch ────────────────────────────────────────────────────
            if not self._risk.check_kill_switch():
                logger.critical("Kill switch active — sleeping 60s then checking again")
                await asyncio.sleep(60)
                continue

            try:
                # ── Fetch market state ─────────────────────────────────────────
                # TODO (Phase 2): Fetch real equity, positions, IV history
                # equity = await self._client.ws.get_account_equity()
                # iv_history = self._client.rest.get_historical_volatility()
                # instruments = self._client.rest.get_instruments()
                logger.info("Main loop iteration — paper/live data fetch not yet implemented")

                # ── Drawdown check ─────────────────────────────────────────────
                if not self._risk.check_drawdown(self._equity_curve):
                    logger.warning("Drawdown limit — skipping this cycle")
                    await asyncio.sleep(cfg.execution.poll_interval)
                    continue

                # ── In-trade management ────────────────────────────────────────
                for pos in list(self._open_positions):
                    should, reason = self._risk.should_roll(pos)
                    if should:
                        logger.warning(f"Rolling {pos.instrument_name}: {reason}")
                        # TODO: execute roll order via self._client.ws.sell_option(...)

                # ── New position ───────────────────────────────────────────────
                if not self._open_positions:
                    logger.info("No open position — checking for entry signal")
                    # TODO: run strategy.generate_signal() and place order

            except Exception as exc:
                logger.error(f"Loop error: {exc}", exc_info=True)

            await asyncio.sleep(cfg.execution.poll_interval)
