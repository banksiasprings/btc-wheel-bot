"""
bot.py -- Async main trading loop (paper and live modes).

Architecture:
  - WheelBot.run() is the top-level async entry point
  - 60-second poll loop fetches market state and decides actions
  - 08:00 UTC daily: expiry check -> auto-settle -> open next leg
  - All orders are confirmed via WebSocket before loop proceeds
  - KILL_SWITCH file halts everything immediately

This module is SCAFFOLDED for Phase 1.
Live order execution is not active; paper mode logs simulated actions.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from ai_overseer import AIOverSeer
from config import Config, cfg
from deribit_client import DeribitClient, DeribitPublicREST
from risk_manager import Position, RiskManager
from strategy import WheelStrategy


class WheelBot:
    """
    Async wheel-strategy bot.

    Modes:
        paper=True  -- fetches live data, logs simulated orders
        paper=False -- fetches live data, places real orders (LIVE_ONLY)
    """

    def __init__(self, config: Config | None = None, paper: bool = True) -> None:
        self._cfg        = config or cfg
        self._paper      = paper
        self._risk       = RiskManager()
        self._client     = DeribitClient()
        self._strategy   = WheelStrategy(self._client.rest)
        self._positions: list[Position] = []
        self._equity_usd: float = self._cfg.backtest.starting_equity
        self._equity_history: list[float] = []
        self._kill_path  = Path(self._cfg.risk.kill_switch_file)
        self._trades_log: list[dict] = []          # lightweight in-memory trade log
        self._iv_history_cache: list = []
        self._last_overseer_check: datetime | None = None

        # AI Overseer (disabled if no LLM key found or config disabled)
        if self._cfg.overseer.enabled:
            self._overseer = AIOverSeer()
            if self._overseer.is_enabled():
                logger.info(
                    f"AI Overseer active — check every "
                    f"{self._cfg.overseer.check_interval_minutes}min"
                )
            else:
                logger.info("AI Overseer: no LLM key found; oversight disabled")
        else:
            self._overseer = None
            logger.info("AI Overseer: disabled in config")

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main async loop."""
        logger.info(
            f"WheelBot starting ({'PAPER' if self._paper else 'LIVE'} mode)"
        )
        if not self._paper:
            # LIVE_ONLY: connect and authenticate WebSocket
            await self._client.connect_live()

        try:
            while True:
                await self._tick()
                await asyncio.sleep(self._cfg.execution.poll_interval)
        except asyncio.CancelledError:
            logger.info("Bot loop cancelled")
        finally:
            await self._client.disconnect()
            logger.info("WheelBot shut down")

    def _should_run_overseer(self, now: datetime) -> bool:
        """Return True if enough time has elapsed since the last LLM oversight check."""
        if self._overseer is None or not self._overseer.is_enabled():
            return False
        if self._last_overseer_check is None:
            return True
        interval = timedelta(minutes=self._cfg.overseer.check_interval_minutes)
        return now - self._last_overseer_check >= interval

    def _run_overseer_check(self, now: datetime, btc_price: float, iv_rank: float) -> None:
        """Build a MarketBrief and ask the LLM whether to CONTINUE or HALT."""
        if self._overseer is None:
            return

        open_pos: dict | None = None
        if self._positions:
            p = self._positions[0]
            open_pos = {
                "option_type": p.option_type,
                "strike": p.strike,
                "delta": p.current_delta,
                "unrealised_pnl": (p.entry_price - p.current_price) * p.contracts * btc_price,
                "dte": 0,          # DTE not tracked at this stage; Phase 2 TODO
            }

        brief = self._overseer.build_brief(
            equity_curve=self._equity_history or [self._equity_usd],
            trades=self._trades_log,
            current_btc_price=btc_price,
            btc_change_7d_pct=0.0,   # TODO: track 7d price in Phase 2
            current_iv=float(self._iv_history_cache[-1][1]) if self._iv_history_cache else 80.0,
            iv_rank=iv_rank,
            open_position=open_pos,
        )

        safe = self._overseer.check(brief)
        self._last_overseer_check = now

        if not safe:
            # Kill switch already written by overseer — kill switch check on
            # next tick will halt the loop cleanly.
            logger.critical("AI Overseer issued HALT — kill switch activated.")

    async def _tick(self) -> None:
        """Single poll iteration."""
        now = datetime.now(timezone.utc)

        # Kill switch check (highest priority — catches both manual and AI-triggered halts)
        if not self._risk.check_kill_switch():
            return

        # Fetch market state
        try:
            iv_history   = self._client.rest.get_historical_volatility(
                currency=self._cfg.deribit.currency
            )
            instruments  = self._client.rest.get_instruments(
                currency=self._cfg.deribit.currency
            )
            # Fetch tickers for qualifying strikes
            tickers = {}
            for inst in instruments[:self._cfg.strategy.liquidity_top_n]:
                ticker = self._client.rest.get_ticker(inst.instrument_name)
                if ticker:
                    tickers[inst.instrument_name] = ticker
        except Exception as exc:
            logger.error(f"Market data fetch failed: {exc}")
            return

        if not tickers:
            logger.warning("No tickers fetched — skipping tick")
            return

        # Underlying price and current IV rank
        underlying_price = next(iter(tickers.values())).underlying_price
        if iv_history:
            self._iv_history_cache = iv_history
        recent_ivs = [row[1] for row in iv_history[-365:]] if iv_history else []
        if len(recent_ivs) >= 2:
            lo, hi = min(recent_ivs), max(recent_ivs)
            iv_rank = (recent_ivs[-1] - lo) / (hi - lo) if hi > lo else 0.5
        else:
            iv_rank = 0.5

        # Settle any expired positions first (BUG 4)
        await self._check_expired_positions(now, underlying_price)

        # Update open position mark prices and deltas from live tickers (BUG 1)
        for pos in self._positions:
            ticker = tickers.get(pos.instrument_name)
            if ticker:
                pos.current_price = ticker.mark_price
                if hasattr(ticker, 'greeks') and ticker.greeks:
                    pos.current_delta = abs(ticker.greeks.get('delta', pos.current_delta))

        # Update equity: starting equity + realised PnL + unrealised PnL (BUG 2)
        realised_pnl = sum(t.get('pnl_usd', 0.0) for t in self._trades_log)
        unrealised_pnl = sum(
            (pos.entry_price - pos.current_price) * pos.contracts * underlying_price
            for pos in self._positions
        )
        self._equity_usd = self._cfg.backtest.starting_equity + realised_pnl + unrealised_pnl
        self._equity_history.append(self._equity_usd)

        # Drawdown guard
        if not self._risk.check_drawdown(self._equity_history):
            logger.warning("Drawdown limit breached — no new positions this tick")
            return

        # AI Overseer (runs on its own cadence, not every tick)
        if self._should_run_overseer(now):
            self._run_overseer_check(now, underlying_price, iv_rank)
            # Overseer may have written a kill switch; re-check before continuing
            if not self._risk.check_kill_switch():
                return

        # In-trade checks
        for pos in list(self._positions):
            should_roll, reason = self._risk.should_roll(pos)
            if should_roll:
                logger.warning(f"Rolling {pos.instrument_name}: {reason}")
                await self._close_position(pos, reason, underlying_price)
                self._positions.remove(pos)

        # Open new leg if flat
        if not self._positions:
            last_cycle = None  # strategy decides based on internal state
            signal = self._strategy.generate_signal(
                iv_history=iv_history,
                instruments=instruments,
                tickers=tickers,
                underlying_price=underlying_price,
                last_cycle=last_cycle,
            )
            if signal:
                await self._open_position(signal, underlying_price)

        # Dashboard tick
        self._print_status(now, underlying_price)

    async def _open_position(self, signal, underlying_price: float) -> None:
        """Open a new option position (paper or live)."""
        contracts = self._risk.calculate_contracts(
            equity_usd=self._equity_usd,
            strike_usd=signal.strike,
        )
        if contracts <= 0:
            logger.warning("Zero contracts sized — skipping open")
            return

        # Full pre-trade check (kill switch, max legs, collateral, free margin)
        if not self._risk.full_pre_trade_check(
            open_positions=self._positions,
            equity_usd=self._equity_usd,
            strike_usd=signal.strike,
            btc_price=underlying_price,
            proposed_contracts=contracts,
        ):
            return

        if self._paper:
            logger.info(
                f"[PAPER OPEN] SELL {signal.instrument_name} "
                f"x{contracts} @ {signal.mark_price:.4f} BTC "
                f"| delta={signal.delta:.3f} | IV={signal.mark_iv:.1f}%"
            )
        else:
            # LIVE_ONLY: place real sell order via WebSocket
            if self._client.ws is None:
                logger.error("WebSocket not connected — cannot place order")
                return
            try:
                result = await self._client.ws.sell_option(
                    instrument_name=signal.instrument_name,
                    amount=contracts,
                    order_type="limit",
                    price=signal.mark_price,
                    label="wheel_bot",
                )
                logger.info(f"Order placed: {result}")
            except Exception as exc:
                logger.error(f"Order failed: {exc}")
                return

        # Track position in memory (live mode would use exchange state)
        pos = Position(
            instrument_name=signal.instrument_name,
            strike=signal.strike,
            option_type=signal.option_type,
            entry_price=signal.mark_price,
            underlying_at_entry=underlying_price,
            contracts=contracts,
            current_delta=abs(signal.delta),
            current_price=signal.mark_price,
            entry_equity=self._equity_usd,
        )
        self._positions.append(pos)

    async def _close_position(self, pos: Position, reason: str, underlying_price: float = 0.0) -> None:
        """Close / roll a position (paper or live)."""
        import csv as _csv
        import os as _os
        if self._paper:
            logger.info(
                f"[PAPER CLOSE] BUY BACK {pos.instrument_name} "
                f"x{pos.contracts} @ {pos.current_price:.4f} BTC | {reason}"
            )
        else:
            # LIVE_ONLY: place buy-to-close order
            logger.warning(f"[LIVE CLOSE] {pos.instrument_name} | {reason}")

        # Record trade P&L (BUG 2 + BUG 6)
        eff_price = underlying_price if underlying_price > 0 else pos.underlying_at_entry
        pnl_btc = (pos.entry_price - pos.current_price) * pos.contracts
        pnl_usd = pnl_btc * eff_price
        trade_record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'instrument': pos.instrument_name,
            'option_type': pos.option_type,
            'entry_price': pos.entry_price,
            'exit_price': pos.current_price,
            'contracts': pos.contracts,
            'pnl_btc': round(pnl_btc, 6),
            'pnl_usd': round(pnl_usd, 2),
            'reason': reason,
        }
        self._trades_log.append(trade_record)

        # Write to CSV so dashboard Paper Trading tab can display it (BUG 6)
        csv_path = 'data/trades.csv'
        _os.makedirs('data', exist_ok=True)
        file_exists = _os.path.exists(csv_path)
        with open(csv_path, 'a', newline='') as f:
            writer = _csv.DictWriter(
                f,
                fieldnames=['timestamp', 'instrument', 'option_type',
                            'entry_price', 'exit_price', 'contracts',
                            'pnl_btc', 'pnl_usd', 'reason'],
            )
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade_record)

    async def _check_expired_positions(self, now: datetime, underlying_price: float) -> None:
        """Settle positions whose expiry date has passed (BUG 4)."""
        for pos in list(self._positions):
            expiry = self._parse_expiry(pos.instrument_name)
            if expiry and now >= expiry:
                if pos.option_type == 'put':
                    expired_itm = underlying_price < pos.strike
                else:
                    expired_itm = underlying_price > pos.strike

                if expired_itm:
                    if pos.option_type == 'put':
                        settlement_price = (pos.strike - underlying_price) / underlying_price
                    else:
                        settlement_price = (underlying_price - pos.strike) / underlying_price
                    pos.current_price = max(settlement_price, 0.0)
                    logger.warning(
                        f'Position {pos.instrument_name} expired ITM at '
                        f'{underlying_price:.0f} (strike {pos.strike:.0f}) — settled at loss'
                    )
                    if pos.option_type == 'put':
                        self._strategy._last_put_was_assigned = True
                else:
                    pos.current_price = 0.0  # expired worthless — full premium kept
                    logger.info(
                        f'Position {pos.instrument_name} expired OTM — full premium kept'
                    )
                    if pos.option_type == 'put':
                        self._strategy._last_put_was_assigned = False

                await self._close_position(pos, 'expiry_settlement', underlying_price)
                self._positions.remove(pos)

    def _parse_expiry(self, instrument_name: str):
        """Parse expiry datetime from Deribit instrument like BTC-25APR25-90000-P."""
        try:
            parts = instrument_name.split('-')
            expiry_str = parts[1]  # e.g. '25APR25'
            from datetime import datetime as _dt
            expiry = _dt.strptime(expiry_str, '%d%b%y').replace(
                hour=8, minute=0, second=0, tzinfo=timezone.utc
            )
            return expiry
        except Exception:
            return None

    def _print_status(self, now: datetime, spot: float) -> None:
        """Log a brief status line each tick."""
        pos_str = (
            f"{self._positions[0].instrument_name} "
            f"delta={self._positions[0].current_delta:.3f}"
            if self._positions else "FLAT"
        )
        logger.info(
            f"[{now.strftime('%H:%M:%S')} UTC] "
            f"BTC=${spot:,.0f} | equity=${self._equity_usd:,.0f} | {pos_str}"
        )
