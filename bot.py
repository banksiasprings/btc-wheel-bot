"""
bot.py — Async main trading loop (paper and live modes).

Architecture:
  - WheelBot.run() is the top-level async entry point
  - 60-second poll loop fetches market state and decides actions
  - 08:00 UTC daily: expiry check → auto-settle → open next leg
  - All orders are confirmed via WebSocket before loop proceeds
  - KILL_SWITCH file halts everything immediately

Phase 2 additions:
  - DeribitPrivateREST: position reconciliation on startup (live mode)
  - WebSocket settlement subscriptions: on_settlement_event callback
    sets strategy._put_cycle_complete from real Deribit settlement data
  - 7-day BTC price history tracked for AI overseer BTC change % metric
  - DTE properly tracked on Position objects (expiry_ts field)
  - buy_option via WebSocket for close orders in live mode
  - Wheel alternation fixed: call leg now fires after OTM put expiry too
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from ai_overseer import AIOverSeer
from config import Config, cfg
from deribit_client import DeribitClient, DeribitPublicREST
from hedge_manager import HedgeManager
import notifier
from order_tracker import OrderTracker, OrderStatus
from risk_manager import Position, RiskManager
from strategy import OpenSignal, WheelStrategy

# ── Farm isolation: data directory resolved from env var ──────────────────────
_DATA_DIR = os.environ.get("WHEEL_BOT_DATA_DIR", str(Path(__file__).parent / "data"))
os.makedirs(_DATA_DIR, exist_ok=True)


def _data_path(filename: str) -> Path:
    """Return the absolute path for a file inside the bot's data directory."""
    return Path(_DATA_DIR) / filename


# ── BTC price ring-buffer (7 days × 24h × 1 sample/min ≈ 10 080 entries max) ──
# We just need the oldest and newest values, so we keep a lightweight deque.
_BTC_PRICE_HISTORY_MAX = 10_080  # 7 days at 1-per-minute


class WheelBot:
    """
    Async wheel-strategy bot.

    Modes:
        paper=True  — fetches live market data, logs simulated orders
        paper=False — fetches live market data, places real orders (LIVE_ONLY)

    Paper mode fully runs both the put and call legs of the wheel using
    simulated expiry detection via _check_expired_positions().

    Live mode additionally:
        • Syncs positions from Deribit on startup via DeribitPrivateREST
        • Subscribes to "user.changes.any.BTC.raw" WebSocket channel for
          real settlement event callbacks
        • Uses actual equity from get_account_summary() each tick
        • Closes positions via WebSocket buy_option()
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
        self._trades_log: list[dict] = []
        self._iv_history_cache: list = []
        self._last_overseer_check: datetime | None = None
        self._last_iv_rank: float = 0.5  # cached IV rank for position entry metadata

        # Phase 2: 7-day BTC price ring-buffer for overseer BTC-change-% metric
        self._btc_price_history: deque[tuple[datetime, float]] = deque(
            maxlen=_BTC_PRICE_HISTORY_MAX
        )

        # Regime filter: one daily close price per calendar day for MA calculation.
        # We keep regime_ma_days + 10 extra days to handle weekends/gaps gracefully.
        _regime_days = max(self._cfg.sizing.regime_ma_days + 10, 60)
        self._regime_daily_prices: deque[tuple[date, float]] = deque(
            maxlen=_regime_days
        )
        self._last_regime_sample_date: date | None = None

        # Stage 3: OrderTracker (initialised in live mode after WS connect)
        self._tracker: OrderTracker | None = None

        # Mobile API state tracking
        self._started_at: datetime = datetime.now(timezone.utc)
        self._force_close_position: bool = False
        self._state_path = _data_path("bot_state.json")

        # DTE alert tracking — set of thresholds already notified for current instrument
        # Reset whenever the instrument changes (new trade opened).
        self._dte_notified_instrument: str = ""
        self._dte_notified_thresholds: set[int] = set()

        # Position risk level tracking — send Telegram when level transitions
        # between "normal", "caution", and "danger". Keyed per instrument so a
        # new trade starts fresh.
        self._risk_notified_instrument: str = ""
        self._last_risk_level: str = "normal"

        # Delta-hedging manager — state_path per-bot so farm bots don't share state
        self._hedge = HedgeManager(
            paper=paper,
            rebalance_threshold=self._cfg.hedge.rebalance_threshold,
            state_path=_data_path("hedge_state.json"),
        ) if self._cfg.hedge.enabled else None

        # AI Overseer
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
        mode_str = "paper" if self._paper else ("testnet" if self._cfg.deribit.testnet else "live")
        logger.info(f"WheelBot starting ({'PAPER' if self._paper else 'LIVE'} mode)")
        try:
            notifier.notify_bot_started(mode_str)
        except Exception:
            pass

        if not self._paper:
            # LIVE_ONLY: connect WebSocket (auth + subscribe)
            await self._client.connect_live()
            # Stage 3: initialise order tracker with fill callback
            self._tracker = OrderTracker(
                ws_client=self._client.ws,
                on_fill=lambda rec: logger.info(
                    f"Fill confirmed: {rec.instrument_name} × {rec.filled_amount} "
                    f"@ {rec.avg_fill_price:.6f} BTC | slippage={rec.slippage_btc:+.6f}"
                ),
            )
            await self._setup_live_subscriptions()
            # Reconcile open positions from Deribit BEFORE stale-hedge check so
            # we don't reset a valid hedge that corresponds to a live open position.
            await self._sync_positions_from_exchange()

        # If no open positions on startup, any persisted hedge state is orphaned
        # (e.g. bot was killed mid-session without closing the hedge).
        # Reset it so the equity calculation starts clean.
        # NOTE: must run AFTER _sync_positions_from_exchange() in live mode so that
        # reconciled positions are visible before we decide the hedge is stale.
        if self._hedge is not None and not self._positions:
            if self._hedge.position_btc != 0.0:
                logger.warning(
                    f"Stale hedge position detected on startup "
                    f"({self._hedge.position_btc:+.3f} BTC with no open options). "
                    "Resetting hedge state."
                )
                self._hedge.reset()

        try:
            while True:
                await self._tick()
                await asyncio.sleep(self._cfg.execution.poll_interval)
        except asyncio.CancelledError:
            logger.info("Bot loop cancelled")
        finally:
            self._write_stopped_state()
            await self._client.disconnect()
            logger.info("WheelBot shut down")

    # ── Phase 2: live startup helpers ──────────────────────────────────────────

    async def _setup_live_subscriptions(self) -> None:
        """Subscribe to WebSocket channels for real-time settlement events (LIVE_ONLY)."""
        if self._client.ws is None:
            return
        channels = [
            f"user.changes.any.{self._cfg.deribit.currency}.raw",
            f"user.portfolio.{self._cfg.deribit.currency.lower()}",
        ]
        await self._client.ws.subscribe(channels, self._on_settlement_event)
        logger.info(f"Live subscriptions active: {channels}")

    async def _sync_positions_from_exchange(self) -> None:
        """
        Reconcile internal position state with open positions on Deribit (LIVE_ONLY).
        Called once on startup so the bot doesn't start with a clean slate
        when it's restarted mid-trade.
        """
        if not self._client.has_private_access():
            logger.warning(
                "No private REST access — skipping position sync. "
                "Set DERIBIT_API_KEY and DERIBIT_API_SECRET to enable."
            )
            return

        try:
            # Fetch equity FIRST so we can use it for entry_equity on reconciled positions.
            account = self._client.private.get_account_summary(
                currency=self._cfg.deribit.currency
            )
            logger.info(
                f"Account equity: {account.equity:.6f} BTC "
                f"| Available: {account.available_funds:.6f} BTC"
            )
            # Rough USD estimate — will be overwritten precisely on first tick.
            # Use a conservative BTC price floor so we don't under-estimate equity.
            rough_btc_price = 50_000.0
            reconcile_equity_usd = max(
                account.equity * rough_btc_price,
                self._cfg.backtest.starting_equity,
            )

            exchange_positions = self._client.private.get_positions(
                currency=self._cfg.deribit.currency
            )
            for ep in exchange_positions:
                # Only import short positions (we are option sellers)
                if ep.direction != "sell" or ep.size >= 0:
                    continue
                contracts = abs(ep.size)
                # ep.delta is the *total position* delta (option_delta × contracts).
                # Divide back to get the per-contract option delta for risk checks.
                per_contract_delta = abs(ep.delta) / contracts if contracts > 0 else 0.0
                pos = Position(
                    instrument_name=ep.instrument_name,
                    strike=float(ep.instrument_name.split("-")[2])
                    if len(ep.instrument_name.split("-")) >= 3 else 0.0,
                    option_type=ep.option_type,
                    entry_price=ep.average_price,
                    underlying_at_entry=0.0,   # unknown at reconcile time
                    contracts=contracts,
                    current_delta=per_contract_delta,
                    current_price=ep.mark_price,
                    entry_equity=reconcile_equity_usd,
                    expiry_ts=ep.expiry_ts,
                    entry_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),  # best approx at reconcile time
                )
                self._positions.append(pos)
                logger.info(
                    f"Reconciled position from Deribit: {ep.instrument_name} "
                    f"× {contracts} contracts | delta={per_contract_delta:.3f}"
                )

            if not exchange_positions:
                logger.info("No open positions found on Deribit — starting flat")

        except Exception as exc:
            logger.error(f"Position sync failed: {exc} — starting with empty state")

    def _on_settlement_event(self, data: dict) -> None:
        """
        WebSocket callback for user.changes.any.BTC.raw (LIVE_ONLY).

        Deribit sends settlement notifications as trades of type "settlement".
        When we detect our option was settled:
          - Update the strategy cycle completion flag
          - Remove the position from internal tracking

        This is the Phase 2 mechanism that enables the wheel's call leg
        based on real Deribit settlement events rather than simulated expiry.
        """
        trades = data.get("trades", [])
        for trade in trades:
            settlement_type = trade.get("settlement_type", "")
            if settlement_type not in ("settlement", "delivery"):
                continue  # not a settlement — it's a fill or other event

            instrument = trade.get("instrument_name", "")
            # Find our position for this instrument
            matching = [p for p in self._positions if p.instrument_name == instrument]
            if not matching:
                continue

            pos = matching[0]
            settlement_price = float(trade.get("price", 0.0))
            profit_loss = float(trade.get("profit_loss", 0.0))

            # ITM if there was a non-zero settlement payout against us
            expired_itm = profit_loss < 0

            if pos.option_type == "put":
                # After ANY put settlement (ITM or OTM), the put leg is complete
                # and the call leg may now fire.
                self._strategy._put_cycle_complete = True
                # Improvement #6: track ITM status for recovery call targeting
                self._strategy._last_put_was_itm  = expired_itm
                self._strategy._last_put_strike   = pos.strike
                logger.info(
                    f"WebSocket settlement: {instrument} expired "
                    f"{'ITM — recovery call mode' if expired_itm else 'OTM'} | "
                    f"P&L: {profit_loss:+.6f} BTC → put cycle complete, call leg now enabled"
                )
            elif pos.option_type == "call":
                # After a call settlement, reset so next cycle starts with a put;
                # clear recovery state
                self._strategy._put_cycle_complete = False
                self._strategy._last_put_was_itm   = False
                self._strategy._last_put_strike     = 0.0
                logger.info(
                    f"WebSocket settlement: {instrument} (call) expired "
                    f"{'ITM' if expired_itm else 'OTM'} | P&L: {profit_loss:+.6f} BTC "
                    f"→ call cycle complete, reverting to put leg"
                )

            # Record P&L and remove from internal tracking
            # (position will be confirmed gone on next REST sync)
            pnl_btc = profit_loss
            pnl_usd = pnl_btc * (self._btc_price_history[-1][1]
                                  if self._btc_price_history else pos.underlying_at_entry)
            trade_record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "instrument": instrument,
                "option_type": pos.option_type,
                "entry_price": pos.entry_price,
                "exit_price": settlement_price,
                "contracts": pos.contracts,
                "pnl_btc": round(pnl_btc, 6),
                "pnl_usd": round(pnl_usd, 2),
                "reason": f"ws_settlement_{'itm' if expired_itm else 'otm'}",
            }
            self._trades_log.append(trade_record)
            self._positions = [p for p in self._positions if p.instrument_name != instrument]

    # ── Overseer helpers ───────────────────────────────────────────────────────

    def _should_run_overseer(self, now: datetime) -> bool:
        if self._overseer is None or not self._overseer.is_enabled():
            return False
        if self._last_overseer_check is None:
            return True
        interval = timedelta(minutes=self._cfg.overseer.check_interval_minutes)
        return now - self._last_overseer_check >= interval

    def _btc_change_7d(self) -> float:
        """Return BTC 7-day price change % using the ring-buffer history."""
        if len(self._btc_price_history) < 2:
            return 0.0
        oldest_ts, oldest_price = self._btc_price_history[0]
        newest_ts, newest_price = self._btc_price_history[-1]
        # Only use as "7d" if we have at least 1 day of history
        if (newest_ts - oldest_ts).total_seconds() < 3600:
            return 0.0
        return (newest_price - oldest_price) / oldest_price * 100.0

    def _is_above_regime_ma(self, current_price: float) -> bool:
        """
        Return True when it is safe to open new put positions under the regime filter.

        Safety rule: BTC must be trading above its N-day simple moving average
        (where N = cfg.sizing.regime_ma_days, default 50).  During a downtrend
        the probability of put assignment rises sharply; skipping new entries
        in that environment preserves capital.

        Returns True (allow trading) when:
          - regime filter is disabled, OR
          - we haven't accumulated enough daily history yet (fail-open during warmup), OR
          - current BTC price >= N-day SMA of daily closing prices
        """
        if not self._cfg.sizing.use_regime_filter:
            return True  # filter disabled — always allow

        n = self._cfg.sizing.regime_ma_days
        if len(self._regime_daily_prices) < n:
            logger.debug(
                f"Regime filter: warming up "
                f"({len(self._regime_daily_prices)}/{n} days) — allowing trade"
            )
            return True  # not enough history yet; fail-open

        prices = [p for _, p in self._regime_daily_prices]
        sma = sum(prices[-n:]) / n
        above = current_price >= sma
        logger.debug(
            f"Regime filter: BTC={current_price:,.0f}  {n}d-SMA={sma:,.0f}  "
            f"{'ABOVE ✅' if above else 'BELOW ⛔'}"
        )
        return above

    def _run_overseer_check(self, now: datetime, btc_price: float, iv_rank: float) -> None:
        if self._overseer is None:
            return

        open_pos: dict | None = None
        if self._positions:
            p = self._positions[0]
            # Phase 2: compute DTE from expiry_ts if available
            if p.expiry_ts:
                dte = max(0, int(
                    (p.expiry_ts / 1000 - time.time()) / 86_400
                ))
            else:
                dte = 0
            open_pos = {
                "option_type": p.option_type,
                "strike": p.strike,
                "delta": p.current_delta,
                "unrealised_pnl": (p.entry_price - p.current_price) * p.contracts * btc_price,
                "dte": dte,
            }

        brief = self._overseer.build_brief(
            equity_curve=self._equity_history or [self._equity_usd],
            trades=self._trades_log,
            current_btc_price=btc_price,
            btc_change_7d_pct=self._btc_change_7d(),   # Phase 2: real 7d BTC Δ
            current_iv=float(self._iv_history_cache[-1][1]) if self._iv_history_cache else 80.0,
            iv_rank=iv_rank,
            open_position=open_pos,
        )

        safe = self._overseer.check(brief)
        self._last_overseer_check = now
        if not safe:
            logger.critical("AI Overseer issued HALT — kill switch activated.")

    # ── State helpers ──────────────────────────────────────────────────────────

    def _write_stopped_state(self) -> None:
        """Write running=False to bot_state.json so the API and mobile app
        immediately reflect that the bot has halted, without waiting for the
        next heartbeat timeout to expire."""
        try:
            import json as _json
            self._state_path.parent.mkdir(exist_ok=True)
            existing: dict = {}
            if self._state_path.exists():
                try:
                    existing = _json.loads(self._state_path.read_text())
                except Exception:
                    pass
            existing.update({
                "running": False,
                "paused": True,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            })
            self._state_path.write_text(_json.dumps(existing))
        except Exception:
            pass  # never let a state write crash the bot

    # ── Main tick ──────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        """Single poll iteration."""
        now = datetime.now(timezone.utc)

        # Process any pending mobile API commands
        await self._process_commands()

        if not self._risk.check_kill_switch():
            self._write_stopped_state()
            return

        # Fetch market state
        try:
            iv_history  = self._client.rest.get_historical_volatility(
                currency=self._cfg.deribit.currency
            )
            instruments = self._client.rest.get_instruments(
                currency=self._cfg.deribit.currency
            )
            # Pre-filter by DTE and option_type before fetching tickers —
            # Deribit returns 900+ instruments alphabetically; the first N
            # are almost always deep-ITM and never qualify by delta.
            dte_min = self._cfg.strategy.min_dte
            dte_max = self._cfg.strategy.max_dte
            candidate_insts = [
                inst for inst in instruments
                if dte_min <= inst.dte <= dte_max
                and inst.option_type in ("put", "call")
            ]
            logger.debug(
                f"Pre-filtered to {len(candidate_insts)} instruments "
                f"(DTE {dte_min}–{dte_max}) from {len(instruments)} total"
            )
            tickers = {}
            for inst in candidate_insts[:self._cfg.strategy.liquidity_top_n]:
                ticker = self._client.rest.get_ticker(inst.instrument_name)
                if ticker:
                    tickers[inst.instrument_name] = ticker
            # Always fetch tickers for open positions so their deltas/marks
            # stay current even if they've drifted outside the DTE window.
            for pos in self._positions:
                if pos.instrument_name not in tickers:
                    ticker = self._client.rest.get_ticker(pos.instrument_name)
                    if ticker:
                        tickers[pos.instrument_name] = ticker
                        logger.debug(
                            f"Fetched ticker for open position "
                            f"{pos.instrument_name} (outside DTE window)"
                        )
        except Exception as exc:
            logger.error(f"Market data fetch failed: {exc}")
            return

        if not tickers:
            logger.warning("No tickers fetched — skipping tick")
            return

        underlying_price = next(iter(tickers.values())).underlying_price

        # Phase 2: update 7d BTC price ring-buffer
        self._btc_price_history.append((now, underlying_price))

        # Regime filter: record one daily price sample (UTC calendar day).
        today_utc = now.date()
        if today_utc != self._last_regime_sample_date:
            self._regime_daily_prices.append((today_utc, underlying_price))
            self._last_regime_sample_date = today_utc

        # Phase 2 (live mode): refresh equity from Deribit account summary each tick
        if not self._paper and self._client.has_private_access():
            try:
                account = self._client.private.get_account_summary(
                    currency=self._cfg.deribit.currency
                )
                # Equity in BTC → convert to USD
                self._equity_usd = account.equity * underlying_price
            except Exception as exc:
                logger.warning(f"Account summary fetch failed: {exc} — using cached equity")

        # IV rank
        if iv_history:
            self._iv_history_cache = iv_history
        recent_ivs = [row[1] for row in iv_history[-365:]] if iv_history else []
        if len(recent_ivs) >= 2:
            lo, hi = min(recent_ivs), max(recent_ivs)
            iv_rank = (recent_ivs[-1] - lo) / (hi - lo) if hi > lo else 0.5
        else:
            iv_rank = 0.5
        self._last_iv_rank = iv_rank

        # Settle any expired positions (paper mode — live mode uses WebSocket callback)
        if self._paper:
            await self._check_expired_positions(now, underlying_price)

        # Update open position mark prices and deltas
        for pos in self._positions:
            ticker = tickers.get(pos.instrument_name)
            if ticker:
                pos.current_price = ticker.mark_price
                if ticker.greeks:
                    pos.current_delta = abs(ticker.greeks.get("delta", pos.current_delta))

        # Recalculate equity (paper mode): starting equity + realised + unrealised P&L
        # Includes hedge P&L so the equity curve reflects the true delta-neutral position.
        if self._paper:
            realised_pnl = sum(t.get("pnl_usd", 0.0) for t in self._trades_log)
            unrealised_pnl = sum(
                (pos.entry_price - pos.current_price) * pos.contracts * underlying_price
                for pos in self._positions
            )
            hedge_realised   = self._hedge.realised_pnl_usd if self._hedge else 0.0
            hedge_unrealised = (
                self._hedge.unrealised_pnl_usd(underlying_price) if self._hedge else 0.0
            )
            self._equity_usd = (
                self._cfg.backtest.starting_equity
                + realised_pnl + unrealised_pnl
                + hedge_realised + hedge_unrealised
            )
        self._equity_history.append(self._equity_usd)

        if not self._risk.check_drawdown(self._equity_history):
            logger.warning("Drawdown limit breached — no new positions this tick")
            return

        if self._should_run_overseer(now):
            self._run_overseer_check(now, underlying_price, iv_rank)
            if not self._risk.check_kill_switch():
                self._write_stopped_state()
                return

        # Mobile API: force-close command received
        if self._force_close_position and self._positions:
            self._force_close_position = False
            pos = self._positions[0]
            logger.info(f"Force-closing {pos.instrument_name} (mobile command)")
            # Close hedge first to capture its realised P&L for the experience record
            hedge_pnl = 0.0
            if self._hedge is not None:
                hedge_pnl = await self._hedge.close_all(underlying_price, self._client.ws)
            closed = await self._close_position(
                pos, "mobile_force_close", underlying_price, hedge_pnl_usd=hedge_pnl
            )
            if closed:
                self._positions.remove(pos)

        # ── Roll check ────────────────────────────────────────────────────────
        # If rolling is enabled, inspect each open position. When a delta or
        # loss breach is detected AND enough time remains before expiry, buy
        # back the position now and let the "open new leg" section below
        # immediately find a replacement at a better strike.
        # The wheel guard (_put_cycle_complete=False) ensures the replacement
        # is another put, not a call.
        if self._cfg.risk.roll_enabled:
            for pos in list(self._positions):   # iterate a copy; we may mutate
                dte_remaining = max(
                    0,
                    int((pos.expiry_ts / 1000 - now.timestamp()) / 86_400)
                ) if pos.expiry_ts else 0
                if dte_remaining < self._cfg.risk.roll_min_dte:
                    # Too close to expiry — let it settle naturally
                    continue
                should_roll, reason = self._risk.should_roll(pos)
                if should_roll:
                    logger.warning(
                        f"Rolling {pos.instrument_name} [{reason}]: "
                        f"delta={pos.current_delta:.3f}  "
                        f"DTE remaining={dte_remaining}"
                    )
                    # Close the hedge first so its P&L is captured in the trade log
                    hedge_pnl = 0.0
                    if self._hedge is not None:
                        hedge_pnl = await self._hedge.close_all(
                            underlying_price, self._client.ws
                        )
                    closed = await self._close_position(
                        pos, f"roll_{reason}", underlying_price, hedge_pnl_usd=hedge_pnl
                    )
                    if closed:
                        self._positions.remove(pos)
                        # _put_cycle_complete stays False after a roll, so the
                        # replacement leg will also be a put (wheel guard enforces this).

        # Delta-hedge rebalance (replaces rolling)
        if self._hedge is not None:
            for pos in self._positions:
                adjustment = await self._hedge.rebalance(
                    pos.option_type,
                    pos.current_delta,
                    pos.contracts,
                    underlying_price,
                    ws_client=self._client.ws,
                )
                if adjustment != 0.0:
                    net_delta = self._hedge.net_delta_btc(
                        pos.option_type, pos.current_delta, pos.contracts
                    )
                    logger.info(
                        f"Hedge rebalanced {adjustment:+.3f} BTC | "
                        f"net delta: {net_delta:+.3f} BTC"
                    )

        # Open new leg if flat (or if ladder has unfilled slots)
        ladder_enabled = getattr(self._cfg.sizing, "ladder_enabled", False)
        ladder_legs    = getattr(self._cfg.sizing, "ladder_legs", 2)

        # Count open put positions (ladder only applies to puts; calls are single)
        open_puts  = [p for p in self._positions if p.option_type == "put"]
        open_calls = [p for p in self._positions if p.option_type == "call"]
        needs_new_leg = (
            (ladder_enabled and len(open_puts) < ladder_legs and not open_calls)
            or (not ladder_enabled and not self._positions)
        )

        if needs_new_leg:
            if not self._is_above_regime_ma(underlying_price):
                logger.info(
                    f"Regime filter ACTIVE — BTC ${underlying_price:,.0f} is below its "
                    f"{self._cfg.sizing.regime_ma_days}-day MA; skipping new entry"
                )
            elif ladder_enabled and self._strategy._put_cycle_complete is False:
                # ── LADDER MODE ──────────────────────────────────────────────
                # Check IV rank once — use same condition as generate_signal()
                iv_rank = self._strategy.calculate_iv_rank(iv_history)
                if iv_rank < self._cfg.strategy.iv_rank_threshold:
                    logger.info(
                        f"Ladder: IV rank {iv_rank:.2%} below threshold — skipping"
                    )
                else:
                    # How many ladder legs still need to be opened?
                    slots_needed = ladder_legs - len(open_puts)
                    candidates = self._strategy.select_ladder_strikes(
                        instruments=instruments,
                        tickers=tickers,
                        underlying_price=underlying_price,
                        n_legs=slots_needed,
                        iv_rank=iv_rank,
                    )
                    # Each ladder leg receives an equal fraction of the total
                    # equity allocation so the combined exposure equals what
                    # a single full-sized leg would have used.
                    per_leg_fraction = (
                        self._cfg.sizing.max_equity_per_leg / ladder_legs
                    )
                    for cand in candidates:
                        ladder_signal = OpenSignal(
                            instrument_name=cand.instrument.instrument_name,
                            strike=cand.instrument.strike,
                            option_type="put",
                            expiry_ts=cand.instrument.expiry_ts,
                            dte=cand.instrument.dte,
                            delta=cand.ticker.delta,
                            mark_iv=cand.ticker.mark_iv,
                            mark_price=cand.ticker.mark_price,
                            underlying_price=underlying_price,
                            cycle="put",
                            iv_rank=iv_rank,
                        )
                        await self._open_position(
                            ladder_signal, underlying_price,
                            equity_fraction=per_leg_fraction,
                        )
            else:
                # ── STANDARD MODE (single leg) ───────────────────────────────
                signal = self._strategy.generate_signal(
                    iv_history=iv_history,
                    instruments=instruments,
                    tickers=tickers,
                    underlying_price=underlying_price,
                    last_cycle=None,
                )
                if signal:
                    await self._open_position(signal, underlying_price)

        self._print_status(now, underlying_price)

    # ── Position management ────────────────────────────────────────────────────

    async def _open_position(
        self, signal, underlying_price: float, equity_fraction: float | None = None
    ) -> None:
        """
        Open a new option position (paper or live).

        equity_fraction: override the max_equity_per_leg fraction for this position.
        Used by the ladder to divide total equity equally across legs so the
        combined exposure matches a single full-sized leg.
        """
        contracts = self._risk.calculate_contracts(
            equity_usd=self._equity_usd,
            strike_usd=signal.strike,
            equity_fraction=equity_fraction,
        )
        if contracts <= 0:
            logger.warning("Zero contracts sized — skipping open")
            return

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
                f"| delta={signal.delta:.3f} | IV={signal.mark_iv:.1f}% "
                f"| cycle={signal.cycle} | DTE={signal.dte}"
            )
        else:
            if self._client.ws is None or self._tracker is None:
                logger.error("WebSocket not connected — cannot place order")
                return
            # Stage 3: use OrderTracker for confirmed fills + slippage tracking
            rec = await self._tracker.place_and_track(
                side="sell",
                instrument_name=signal.instrument_name,
                amount=contracts,
                price=signal.mark_price,
                label="wheel_bot",
                timeout_seconds=self._cfg.execution.order_timeout_seconds
                if hasattr(self._cfg.execution, "order_timeout_seconds") else 45.0,
                fallback_market=True,
            )
            if rec.status != OrderStatus.FILLED:
                logger.error(
                    f"Open order did not fill: {rec.status.value} — "
                    f"skipping position entry"
                )
                return
            # Use the actual fill price for position tracking
            signal = signal._replace(mark_price=rec.avg_fill_price) \
                if hasattr(signal, "_replace") else signal

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
            expiry_ts=signal.expiry_ts,       # Phase 2: store expiry for DTE tracking
            iv_rank_at_entry=self._last_iv_rank,  # for trades.csv enrichment
            dte_at_entry=signal.dte,              # for trades.csv enrichment
            entry_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),  # actual open date
        )
        # Check if this is the first trade this bot has ever made
        import csv as _csv_check
        _is_first_trade = False
        _trades_csv = _data_path("trades.csv")
        if not _trades_csv.exists():
            _is_first_trade = True
        else:
            try:
                with open(_trades_csv, newline="") as _f:
                    _rows = list(_csv_check.DictReader(_f))
                    _is_first_trade = len(_rows) == 0
            except Exception:
                pass

        self._positions.append(pos)
        try:
            notifier.notify_trade_opened(
                pos.instrument_name, pos.strike, pos.entry_price, pos.dte_at_entry
            )
        except Exception:
            pass

        if _is_first_trade:
            try:
                _bot_name = os.path.basename(_DATA_DIR.rstrip("/")) if _DATA_DIR != str(Path(__file__).parent / "data") else "main"
                notifier._send(
                    f"🎉 <b>FIRST TRADE FIRED!</b>\n"
                    f"Bot: <code>{_bot_name}</code>\n"
                    f"Instrument: {pos.instrument_name}\n"
                    f"Strike: ${pos.strike:,.0f}\n"
                    f"Delta: {pos.current_delta:.3f}\n"
                    f"IV Rank: {self._last_iv_rank:.1%}\n"
                    f"Premium: {pos.entry_price:.4f} BTC\n"
                    f"DTE: {pos.dte_at_entry} days"
                )
            except Exception:
                pass

    async def _close_position(
        self,
        pos: Position,
        reason: str,
        underlying_price: float = 0.0,
        hedge_pnl_usd: float = 0.0,
    ) -> bool:
        """
        Close / roll a position (paper or live).

        hedge_pnl_usd: realised P&L from closing the delta-hedge position (if any).
            Passed in by callers that call hedge.close_all() immediately after this.
            Used to record the *total* trade P&L (option + hedge) in experience.jsonl
            so the adaptive calibration gets an accurate signal.

        Returns True if the close was confirmed (or paper mode).
        Returns False if the live order failed — caller should NOT remove the
        position from self._positions and should retry next tick.

        Phantom-trade fix: in live mode, P&L is only recorded to CSV after a
        confirmed fill.  A failed order is logged but does not pollute the
        trade history.
        """
        import csv as _csv
        import os as _os

        slippage_btc = 0.0
        fill_time_sec = 0.0

        if self._paper:
            logger.info(
                f"[PAPER CLOSE] BUY BACK {pos.instrument_name} "
                f"x{pos.contracts} @ {pos.current_price:.4f} BTC | {reason}"
            )
        else:
            # LIVE_ONLY: buy to close via OrderTracker (confirmed fill)
            if self._client.ws is None or self._tracker is None:
                logger.error("WebSocket not connected — cannot close position")
                return False  # caller keeps position in list; retry next tick

            rec = await self._tracker.place_and_track(
                side="buy",
                instrument_name=pos.instrument_name,
                amount=pos.contracts,
                price=pos.current_price,
                label="wheel_bot_close",
                timeout_seconds=self._cfg.execution.order_timeout_seconds
                if hasattr(self._cfg.execution, "order_timeout_seconds") else 45.0,
                fallback_market=True,
            )
            if rec.status == OrderStatus.FILLED:
                pos.current_price = rec.avg_fill_price
                slippage_btc = getattr(rec, "slippage_btc", 0.0)
                fill_time_sec = (
                    rec.filled_at - rec.created_at
                    if rec.filled_at is not None
                    else rec.elapsed_seconds
                )
                logger.info(
                    f"Close confirmed: {pos.instrument_name} "
                    f"@ {rec.avg_fill_price:.6f} BTC | slippage={slippage_btc:+.6f}"
                )
            else:
                logger.error(
                    f"Close order did not fill: {rec.status.value} — "
                    f"position NOT removed; will retry next tick"
                )
                return False  # ← phantom-trade fix: do NOT write CSV

        # ── Record P&L (only reached on confirmed close or paper mode) ──────────
        eff_price = underlying_price if underlying_price > 0 else pos.underlying_at_entry
        pnl_btc = (pos.entry_price - pos.current_price) * pos.contracts
        pnl_usd = pnl_btc * eff_price
        equity_before = self._equity_usd
        equity_after  = equity_before + pnl_usd

        # DTE remaining at close time
        if pos.expiry_ts:
            dte_at_close = max(0, int((pos.expiry_ts / 1000 - time.time()) / 86_400))
        else:
            dte_at_close = 0

        mode_str = "paper" if self._paper else (
            "testnet" if self._cfg.deribit.testnet else "live"
        )

        trade_record = {
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "instrument":      pos.instrument_name,
            "option_type":     pos.option_type,
            "strike":          pos.strike,
            "entry_price":     pos.entry_price,
            "exit_price":      pos.current_price,
            "contracts":       pos.contracts,
            "pnl_btc":         round(pnl_btc, 6),
            "pnl_usd":         round(pnl_usd, 2),
            "equity_before":   round(equity_before, 2),
            "equity_after":    round(equity_after, 2),
            "btc_price":       round(eff_price, 2),
            "iv_rank_at_entry": round(pos.iv_rank_at_entry, 4),
            "dte_at_entry":    pos.dte_at_entry,
            "dte_at_close":    dte_at_close,
            "slippage_btc":    round(slippage_btc, 6),
            "fill_time_sec":   round(fill_time_sec, 2),
            "reason":          reason,
            "mode":            mode_str,
        }
        self._trades_log.append(trade_record)

        # ── Write to CSV ─────────────────────────────────────────────────────────
        csv_path = _data_path("trades.csv")
        _os.makedirs(str(csv_path.parent), exist_ok=True)
        file_exists = csv_path.exists()
        fieldnames = [
            "timestamp", "instrument", "option_type", "strike",
            "entry_price", "exit_price", "contracts",
            "pnl_btc", "pnl_usd", "equity_before", "equity_after",
            "btc_price", "iv_rank_at_entry", "dte_at_entry", "dte_at_close",
            "slippage_btc", "fill_time_sec", "reason", "mode",
        ]
        with open(csv_path, "a", newline="") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade_record)

        logger.info(
            f"Trade recorded: {pos.instrument_name} P&L ${pnl_usd:+,.2f} | "
            f"equity ${equity_before:,.0f} → ${equity_after:,.0f}"
        )

        # ── Update equity_curve.json for mobile API ───────────────────────────
        self._update_equity_curve(datetime.now(timezone.utc), equity_after)

        try:
            notifier.notify_trade_closed(pos.instrument_name, pnl_usd, reason)
        except Exception:
            pass

        # ── Write experience.jsonl (adaptive learning — MUST NEVER block close) ─
        try:
            import json as _json
            _exp_path = _data_path("experience.jsonl")
            _exp_record = {
                "timestamp": time.time(),
                "mode": mode_str,
                "params": {
                    "iv_rank_threshold":        self._cfg.strategy.iv_rank_threshold,
                    "target_delta_min":         self._cfg.strategy.target_delta_min,
                    "target_delta_max":         self._cfg.strategy.target_delta_max,
                    "approx_otm_offset":        self._cfg.backtest.approx_otm_offset,
                    "max_dte":                  self._cfg.strategy.max_dte,
                    "min_dte":                  self._cfg.strategy.min_dte,
                    "max_equity_per_leg":       self._cfg.sizing.max_equity_per_leg,
                    "premium_fraction_of_spot": self._cfg.backtest.premium_fraction_of_spot,
                    "iv_rank_window_days":      getattr(
                        self._cfg.strategy, "iv_rank_window_days", 365
                    ),
                },
                "conditions_at_open": {
                    "iv_rank":      round(pos.iv_rank_at_entry, 4),
                    "btc_price":    round(pos.underlying_at_entry, 2),
                    "option_type":  pos.option_type,
                    "strike":       pos.strike,
                    "dte_at_entry": pos.dte_at_entry,
                },
                "outcome": {
                    # total_pnl_usd = option P&L + delta-hedge P&L so the calibration
                    # learns from the true combined outcome, not just the option leg.
                    "pnl_usd":        round(pnl_usd, 2),
                    "hedge_pnl_usd":  round(hedge_pnl_usd, 2),
                    "total_pnl_usd":  round(pnl_usd + hedge_pnl_usd, 2),
                    "pnl_pct":   round((pnl_usd + hedge_pnl_usd) / equity_before, 4) if equity_before > 0 else 0.0,
                    "hold_days": max(0, pos.dte_at_entry - dte_at_close),
                    "reason":    reason,
                    "win":       (pnl_usd + hedge_pnl_usd) > 0,
                },
            }
            with open(_exp_path, "a") as _expf:
                _expf.write(_json.dumps(_exp_record) + "\n")
        except Exception:
            pass  # experience log MUST NEVER interrupt the trade close

        return True

    async def _check_expired_positions(
        self, now: datetime, underlying_price: float
    ) -> None:
        """
        Settle positions whose expiry date has passed (paper mode only).

        Phase 2 fix: After ANY put expiry (OTM or ITM), set _put_cycle_complete=True
        so the call leg can fire on the next cycle. After a call expiry, reset to False.
        Previously, OTM put expiry was leaving the flag False, blocking the call leg forever.
        """
        for pos in list(self._positions):
            expiry = self._parse_expiry(pos.instrument_name)
            if not (expiry and now >= expiry):
                continue

            if pos.option_type == "put":
                expired_itm = underlying_price < pos.strike
            else:
                expired_itm = underlying_price > pos.strike

            if expired_itm:
                if pos.option_type == "put":
                    settlement_price = (pos.strike - underlying_price) / underlying_price
                else:
                    settlement_price = (underlying_price - pos.strike) / underlying_price
                pos.current_price = max(settlement_price, 0.0)
                logger.warning(
                    f"Position {pos.instrument_name} expired ITM at "
                    f"{underlying_price:.0f} (strike {pos.strike:.0f}) — settled at loss"
                )
            else:
                pos.current_price = 0.0   # expired worthless — full premium kept
                logger.info(
                    f"Position {pos.instrument_name} expired OTM — full premium kept"
                )

            # Phase 2 fix: update cycle completion flag regardless of ITM/OTM
            if pos.option_type == "put":
                # Both OTM and ITM put expiry unlock the call leg
                self._strategy._put_cycle_complete = True
                # Improvement #6: track ITM status for recovery call targeting.
                # If the put expired ITM (BTC < strike), the next call leg will
                # prefer strikes ≥ put strike to capture a full BTC recovery.
                self._strategy._last_put_was_itm  = expired_itm
                self._strategy._last_put_strike   = pos.strike
                logger.info(
                    f"Put cycle complete ({'ITM — recovery call mode' if expired_itm else 'OTM'}) — "
                    f"call leg now enabled for next cycle"
                )
            elif pos.option_type == "call":
                # After a call completes, go back to selling puts; clear recovery state
                self._strategy._put_cycle_complete = False
                self._strategy._last_put_was_itm   = False
                self._strategy._last_put_strike     = 0.0
                logger.info(
                    f"Call cycle complete ({'ITM' if expired_itm else 'OTM'}) — "
                    f"returning to put-selling mode"
                )

            # Close hedge first to capture its realised P&L for the experience record
            hedge_pnl = 0.0
            if self._hedge is not None:
                hedge_pnl = await self._hedge.close_all(underlying_price, self._client.ws)
            closed = await self._close_position(
                pos, "expiry_settlement", underlying_price, hedge_pnl_usd=hedge_pnl
            )
            if closed:
                self._positions.remove(pos)

    def _parse_expiry(self, instrument_name: str) -> datetime | None:
        """Parse expiry datetime from Deribit instrument like BTC-25APR25-90000-P."""
        try:
            parts = instrument_name.split("-")
            expiry_str = parts[1]   # e.g. "25APR25"
            from datetime import datetime as _dt
            expiry = _dt.strptime(expiry_str, "%d%b%y").replace(
                hour=8, minute=0, second=0, tzinfo=timezone.utc
            )
            return expiry
        except Exception:
            return None

    # ── Mobile API helpers ─────────────────────────────────────────────────────

    def _write_current_position(self, spot: float) -> None:
        """Write data/current_position.json for the mobile API."""
        try:
            data_dir = Path(_DATA_DIR)
            data_dir.mkdir(exist_ok=True)
            if not self._positions:
                payload: dict = {"open": False}
            else:
                p = self._positions[0]
                dte = 0
                if p.expiry_ts:
                    dte = max(0, int((p.expiry_ts / 1000 - time.time()) / 86_400))
                expiry_str = ""
                try:
                    parts = p.instrument_name.split("-")
                    from datetime import datetime as _dt
                    expiry_str = _dt.strptime(parts[1], "%d%b%y").strftime("%Y-%m-%d")
                except Exception:
                    pass
                unrealized = (p.entry_price - p.current_price) * p.contracts * spot
                pct = unrealized / (p.strike * p.contracts) * 100 if p.strike > 0 else 0.0
                hedge_data = self._hedge.to_dict(spot) if self._hedge else None
                net_delta  = (
                    self._hedge.net_delta_btc(p.option_type, p.current_delta, p.contracts)
                    if self._hedge else None
                )
                payload = {
                    "open": True,
                    "type": f"short_{p.option_type}",
                    "strike": p.strike,
                    "contracts": p.contracts,
                    "expiry": expiry_str,
                    "entry_date": p.entry_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "premium_collected": round(p.entry_price * p.contracts * spot, 2),
                    "current_spot": round(spot, 2),
                    "unrealized_pnl_usd": round(unrealized, 2),
                    "unrealized_pnl_pct": round(pct, 2),
                    "days_to_expiry": dte,
                    "current_delta": round(p.current_delta, 4),
                    "net_delta": round(net_delta, 4) if net_delta is not None else None,
                    "hedge": hedge_data,
                }
            (data_dir / "current_position.json").write_text(json.dumps(payload))
        except Exception:
            pass

    def _update_equity_curve(self, now: datetime, equity: float) -> None:
        """Append a data point to data/equity_curve.json on each position close."""
        try:
            curve_path = _data_path("equity_curve.json")
            if curve_path.exists():
                existing = json.loads(curve_path.read_text())
            else:
                existing = {
                    "dates": [],
                    "equity": [],
                    "starting_equity": self._cfg.backtest.starting_equity,
                }
            existing["dates"].append(now.strftime("%Y-%m-%d"))
            existing["equity"].append(round(equity, 2))
            curve_path.write_text(json.dumps(existing))
        except Exception:
            pass

    async def _process_commands(self) -> None:
        """Poll data/bot_commands.json for pending commands and execute them."""
        cmd_path = _data_path("bot_commands.json")
        if not cmd_path.exists():
            return
        try:
            data = json.loads(cmd_path.read_text())
            cmd_path.unlink()
        except Exception:
            return

        command = data.get("command", "")
        if command == "stop":
            kill_path = Path(__file__).parent / "KILL_SWITCH"
            kill_path.write_text("STOP")
            logger.info("Mobile command: stop — KILL_SWITCH created")
        elif command == "start":
            kill_path = Path(__file__).parent / "KILL_SWITCH"
            if kill_path.exists():
                kill_path.unlink()
            logger.info("Mobile command: start — KILL_SWITCH cleared")
        elif command == "close_position":
            if self._positions:
                self._force_close_position = True
                logger.info(
                    f"Mobile command: close_position — will close "
                    f"{self._positions[0].instrument_name} this tick"
                )
        elif command == "set_mode":
            logger.info(
                f"Mobile command: set_mode → {data.get('mode')} "
                f"(takes effect on restart)"
            )

    def _print_status(self, now: datetime, spot: float) -> None:
        """Log a brief status line each tick, write tick_log.csv and heartbeat."""
        import csv as _csv

        # ── Compute position snapshot ─────────────────────────────────────────
        pos_data: dict | None = None
        if self._positions:
            p = self._positions[0]
            if p.expiry_ts:
                dte = max(0, int((p.expiry_ts / 1000 - time.time()) / 86_400))
            else:
                dte = 0
            unrealized_pnl_usd = (p.entry_price - p.current_price) * p.contracts * spot
            pos_str = f"{p.instrument_name} delta={p.current_delta:.3f} | DTE={dte}d"
            pos_data = {
                "name":              p.instrument_name,
                "option_type":       p.option_type,
                "strike":            p.strike,
                "delta":             round(p.current_delta, 4),
                "dte":               dte,
                "dte_at_entry":      p.dte_at_entry,
                "entry_price":       p.entry_price,
                "current_price":     p.current_price,
                "contracts":         p.contracts,
                "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
            }
        else:
            pos_str = "FLAT"
            unrealized_pnl_usd = 0.0
            dte = 0

        cycle_state = "✓call-ok" if self._strategy._put_cycle_complete else "→put-mode"
        logger.info(
            f"[{now.strftime('%H:%M:%S')} UTC] "
            f"BTC=${spot:,.0f} | equity=${self._equity_usd:,.0f} | "
            f"{pos_str} | wheel={cycle_state}"
        )

        # ── Write tick_log.csv (one row per tick for charting / analysis) ─────
        try:
            tick_log_path = _data_path("tick_log.csv")
            tick_log_path.parent.mkdir(exist_ok=True)
            file_exists = tick_log_path.exists()
            tick_row = {
                "timestamp":          now.isoformat(),
                "btc_price":          round(spot, 2),
                "equity_usd":         round(self._equity_usd, 2),
                "position_name":      pos_data["name"] if pos_data else "",
                "delta":              pos_data["delta"] if pos_data else 0.0,
                "dte":                pos_data["dte"]   if pos_data else 0,
                "iv_rank":            round(self._last_iv_rank, 4),
                "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
            }
            with open(tick_log_path, "a", newline="") as f:
                writer = _csv.DictWriter(f, fieldnames=list(tick_row.keys()))
                if not file_exists:
                    writer.writeheader()
                writer.writerow(tick_row)
        except Exception:
            pass  # tick log is non-critical

        # ── Write heartbeat for dashboard cross-process detection ─────────────
        try:
            mode_str = "paper" if self._paper else (
                "testnet" if self._cfg.deribit.testnet else "live"
            )
            hedge_data = self._hedge.to_dict(spot) if self._hedge else None
            net_delta  = (
                self._hedge.net_delta_btc(
                    self._positions[0].option_type,
                    self._positions[0].current_delta,
                    self._positions[0].contracts,
                )
                if self._hedge and self._positions else 0.0
            )
            heartbeat = {
                "pid":        os.getpid(),
                "timestamp":  time.time(),
                "mode":       mode_str,
                "equity_usd": self._equity_usd,
                "btc_price":  spot,
                "iv_rank":    round(self._last_iv_rank, 4),
                "wheel":      cycle_state,
                "position":   pos_data,
                "hedge":      hedge_data,
                "net_delta":  round(net_delta, 4),
            }
            hb_path = Path(__file__).parent / "bot_heartbeat.json"
            hb_path.write_text(json.dumps(heartbeat))
        except Exception:
            pass  # never let heartbeat write block the tick

        # ── Write bot_state.json for mobile API ───────────────────────────────
        try:
            state = {
                "running": True,
                "mode": mode_str,
                "started_at": self._started_at.isoformat(),
                "last_heartbeat": now.isoformat(),
            }
            state_path = _data_path("bot_state.json")
            state_path.parent.mkdir(exist_ok=True)
            state_path.write_text(json.dumps(state))
        except Exception:
            pass  # non-critical

        # ── DTE proximity alerts ─────────────────────────────────────────���────
        try:
            if pos_data and self._positions:
                p = self._positions[0]
                _instr = p.instrument_name
                # Reset tracker if position changed (new instrument)
                if _instr != self._dte_notified_instrument:
                    self._dte_notified_instrument = _instr
                    self._dte_notified_thresholds = set()
                _dte_now = pos_data.get("dte", 0)
                _spot = spot
                _strike = p.strike
                _be = round(_strike - p.entry_price * p.contracts, 0) if p.contracts > 0 else 0.0
                _upnl = pos_data.get("unrealized_pnl_usd", 0)
                _bot_name_str = os.path.basename(_DATA_DIR.rstrip("/")) if _DATA_DIR != str(Path(__file__).parent / "data") else "main"
                for _threshold in (3, 1):
                    if _dte_now <= _threshold and _threshold not in self._dte_notified_thresholds:
                        self._dte_notified_thresholds.add(_threshold)
                        try:
                            notifier.notify_expiry_approaching(
                                _bot_name_str, _dte_now, _instr,
                                _strike, _spot, _be, _upnl
                            )
                        except Exception:
                            pass
        except Exception:
            pass  # never let notification logic block the tick

        # ── Position risk-level alerts ────────────────────────────────────────
        # Fires a Telegram notification whenever the position transitions between
        # "normal" → "caution" → "danger" (or back down). Only one alert per
        # level transition so the user isn't spammed every tick.
        try:
            if pos_data and self._positions:
                p = self._positions[0]
                _instr      = p.instrument_name
                _abs_delta  = abs(p.current_delta)
                _max_delta  = cfg.risk.max_adverse_delta
                _upnl       = pos_data.get("unrealized_pnl_usd", 0)
                _max_loss   = p.entry_equity * cfg.risk.max_loss_per_leg if p.entry_equity > 0 else float("inf")
                _be         = round(p.strike - p.entry_price * p.contracts, 0) if p.contracts > 0 else 0.0
                _bot_name_str = os.path.basename(_DATA_DIR.rstrip("/")) if _DATA_DIR != str(Path(__file__).parent / "data") else "main"

                # Compute current risk level
                if _abs_delta >= _max_delta or (_max_loss < float("inf") and _upnl < -_max_loss):
                    _risk_level = "danger"
                elif _abs_delta >= 0.80 * _max_delta or (_max_loss < float("inf") and _upnl < -0.50 * _max_loss):
                    _risk_level = "caution"
                else:
                    _risk_level = "normal"

                # Reset tracking on new instrument
                if _instr != self._risk_notified_instrument:
                    self._risk_notified_instrument = _instr
                    self._last_risk_level = "normal"

                # Alert only when the level changes
                if _risk_level != self._last_risk_level:
                    self._last_risk_level = _risk_level
                    if _risk_level in ("caution", "danger"):
                        try:
                            notifier.notify_position_risk(
                                _bot_name_str,
                                _risk_level,
                                {
                                    "strike":             p.strike,
                                    "current_spot":       spot,
                                    "unrealized_pnl_usd": _upnl,
                                    "current_delta":      _abs_delta,
                                    "dte":                pos_data.get("dte", 0),
                                    "type":               p.option_type,
                                    "breakeven":          _be,
                                },
                            )
                        except Exception:
                            pass
        except Exception:
            pass  # never let notification logic block the tick

        # ── Write current_position.json for mobile API ────────────────────────
        self._write_current_position(spot)
