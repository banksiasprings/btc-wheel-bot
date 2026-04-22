interface Props {
  onClose?: () => void
}

interface Block {
  type: 'heading' | 'sub' | 'prose' | 'code' | 'note' | 'file' | 'image'
  text?: string
  lang?: string
  label?: string
  src?: string
  caption?: string
}

const SECTIONS: { title: string; icon: string; blocks: Block[] }[] = [
  // ── 1 ─────────────────────────────────────────────────────────────────────
  {
    title: 'Genome → Config → Bot Parameters',
    icon: '🧬',
    blocks: [
      {
        type: 'prose',
        text: 'After running evolution, the best parameter set for each goal is saved as a YAML file. Loading a preset in Settings writes these values into config.yaml, which the bot reads on startup.',
      },
      {
        type: 'file',
        label: 'data/optimizer/best_genome_balanced.yaml',
        text: `iv_rank_threshold: 0.52
target_delta_min: 0.14
target_delta_max: 0.27
approx_otm_offset: 0.07
max_dte: 28
min_dte: 7
max_equity_per_leg: 0.09
premium_fraction_of_spot: 0.012
iv_rank_window_days: 365
min_free_equity_fraction: 0.20
fitness_goal: balanced`,
      },
      {
        type: 'prose',
        text: 'Each field maps directly to a strategy control in the bot:',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'config.py (loaded at startup)',
        text: `# iv_rank_threshold → minimum IV Rank to open a new trade
# target_delta_min/max → acceptable delta range for strike selection
# max_equity_per_leg  → position sizing cap (fraction of equity)
# min_free_equity_fraction → minimum cash buffer always kept undeployed

class StrategyConfig:
    iv_rank_threshold: float       # e.g. 0.52  (52nd percentile)
    target_delta_min: float        # e.g. 0.14
    target_delta_max: float        # e.g. 0.27
    min_dte: int                   # e.g. 7
    max_dte: int                   # e.g. 28`,
      },
      {
        type: 'note',
        text: 'Changing config.yaml requires a bot restart — the config is read once at __init__ time.',
      },
    ],
  },

  // ── 2 ─────────────────────────────────────────────────────────────────────
  {
    title: 'Startup Sequence',
    icon: '🚀',
    blocks: [
      {
        type: 'prose',
        text: 'When the bot starts in live mode it connects to Deribit via WebSocket, authenticates with API key + secret, then reconciles any open positions before entering the main loop.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py → WheelBot.run()',
        text: `async def run(self) -> None:
    if not self._paper:
        # 1. Open WebSocket and authenticate
        await self._client.connect_live()

        # 2. Attach OrderTracker (fill confirmation + slippage logging)
        self._tracker = OrderTracker(ws_client=self._client.ws, ...)

        # 3. Subscribe to settlement events (real Deribit expiry notifications)
        await self._setup_live_subscriptions()

        # 4. Pull any open positions from Deribit REST into internal state
        #    MUST happen before the stale-hedge check below
        await self._sync_positions_from_exchange()

    # 5. If no open options exist, any persisted hedge state is orphaned — reset it
    if self._hedge is not None and not self._positions:
        if self._hedge.position_btc != 0.0:
            self._hedge.reset()

    # 6. Enter the 60-second tick loop
    while True:
        await self._tick()
        await asyncio.sleep(60)`,
      },
      {
        type: 'prose',
        text: '_sync_positions_from_exchange() calls the Deribit private REST endpoint to find any short option positions. Each one is imported as a Position object so the bot can manage it as if it opened the trade itself.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py → _sync_positions_from_exchange()',
        text: `exchange_positions = self._client.private.get_positions(currency="BTC")

for ep in exchange_positions:
    if ep.direction != "sell" or ep.size >= 0:
        continue  # only import short positions (we are sellers)

    contracts = abs(ep.size)
    per_contract_delta = abs(ep.delta) / contracts

    pos = Position(
        instrument_name = ep.instrument_name,   # e.g. "BTC-25APR25-80000-P"
        strike          = 80000.0,
        option_type     = "put",
        entry_price     = ep.average_price,     # BTC — e.g. 0.0042
        contracts       = contracts,
        current_delta   = per_contract_delta,   # e.g. 0.18
        expiry_ts       = ep.expiry_ts,         # Unix ms
    )
    self._positions.append(pos)`,
      },
    ],
  },

  // ── 3 ─────────────────────────────────────────────────────────────────────
  {
    title: 'The 60-Second Tick Loop',
    icon: '🔁',
    blocks: [
      {
        type: 'prose',
        text: 'Every 60 seconds _tick() runs the full strategy cycle. Steps execute in this exact order:',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py → _tick() — top-level flow',
        text: `async def _tick(self) -> None:
    now = datetime.now(timezone.utc)

    # 1. Process any commands from the mobile app (stop/start/close)
    await self._process_commands()

    # 2. Check kill switch file — halt immediately if present
    if not self._risk.check_kill_switch():
        return

    # 3. Fetch all market data from Deribit REST
    iv_history  = client.rest.get_historical_volatility("BTC")
    instruments = client.rest.get_instruments("BTC")
    tickers     = {inst: client.rest.get_ticker(inst) for inst in candidates}

    # 4. Update BTC price ring-buffer (7-day history for overseer metrics)
    self._btc_price_history.append((now, underlying_price))

    # 5. (Live mode) Refresh equity from account summary — equity in BTC × spot
    account = self._client.private.get_account_summary("BTC")
    self._equity_usd = account.equity * underlying_price

    # 6. Calculate IV Rank
    iv_rank = calculate_iv_rank(iv_history)

    # 7. Settle expired positions (paper mode — live mode uses WS callback)
    if self._paper:
        await self._check_expired_positions(now, underlying_price)

    # 8. Update mark prices and greeks on open positions
    for pos in self._positions:
        pos.current_price = tickers[pos.instrument_name].mark_price
        pos.current_delta = abs(tickers[pos.instrument_name].greeks["delta"])

    # 9. Rebalance delta-hedge on every open position
    for pos in self._positions:
        await self._hedge.rebalance(
            pos.option_type, pos.current_delta, pos.contracts, underlying_price
        )

    # 10. Open a new leg if flat (no open positions)
    if not self._positions:
        signal = self._strategy.generate_signal(...)
        if signal:
            await self._open_position(signal, underlying_price)

    # 11. Write heartbeat, tick log, current_position.json for mobile app
    self._print_status(now, underlying_price)`,
      },
    ],
  },

  // ── 4 ─────────────────────────────────────────────────────────────────────
  {
    title: 'IV Rank Calculation',
    icon: '📊',
    blocks: [
      {
        type: 'prose',
        text: 'IV Rank tells the bot whether options are expensive right now vs. the past year. The bot only opens new trades when IV Rank is above the configured threshold.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py → _tick() / strategy.py → calculate_iv_rank()',
        text: `# iv_history is a list of (timestamp_ms, iv_value) tuples from Deribit
# The endpoint returns daily historical realized volatility for BTC

recent_ivs = [row[1] for row in iv_history[-365:]]   # last 365 days

lo = min(recent_ivs)   # lowest IV seen in a year
hi = max(recent_ivs)   # highest IV seen in a year

# Current IV as a percentile of the past year's range
iv_rank = (recent_ivs[-1] - lo) / (hi - lo)
# 0.0 = cheapest options have been all year
# 1.0 = most expensive options have been all year

# Gate: is now a good time to sell?
if iv_rank < cfg.strategy.iv_rank_threshold:   # e.g. 0.52
    logger.info(f"IV rank {iv_rank:.2%} below threshold — skipping")
    return None   # no signal — bot sits on its hands`,
      },
      {
        type: 'note',
        text: 'Deribit\'s /public/get_historical_volatility endpoint returns DVOL (Deribit Volatility Index) data. The bot uses this as its IV proxy — it correlates with option implied volatility but is a market-wide index, not per-contract IV.',
      },
    ],
  },

  // ── 5 ─────────────────────────────────────────────────────────────────────
  {
    title: 'Strike Selection',
    icon: '🎯',
    blocks: [
      {
        type: 'prose',
        text: 'After passing the IV gate, the bot scans all active BTC option instruments to find the best strike. It pre-filters by DTE window then scores each candidate by how well its delta matches the target range.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'strategy.py → select_strike()',
        text: `target_delta_mid = (cfg.strategy.target_delta_min + cfg.strategy.target_delta_max) / 2
# e.g. (0.14 + 0.27) / 2 = 0.205  ← the "ideal" delta

best = None
for inst in instruments:
    # Filter 1: must be the right option type (put / call)
    if inst.option_type != cycle:
        continue

    # Filter 2: DTE must be within the configured window
    if not (cfg.strategy.min_dte <= inst.dte <= cfg.strategy.max_dte):
        continue

    ticker = tickers.get(inst.instrument_name)
    if not ticker:
        continue

    # Filter 3: delta must be in target range (farther OTM = lower delta)
    delta_abs = abs(ticker.delta)
    if not (cfg.strategy.target_delta_min <= delta_abs <= cfg.strategy.target_delta_max):
        continue

    # Filter 4: minimum premium — doesn't cover carry cost if too small
    min_premium = underlying_price * cfg.backtest.premium_fraction_of_spot
    if ticker.mark_price * underlying_price < min_premium:
        continue

    # Score: 70% weight on delta accuracy, 30% on IV (higher IV = more premium)
    delta_score = 1.0 - abs(delta_abs - target_delta_mid) / target_delta_mid
    iv_score    = ticker.mark_iv / 200.0     # normalise
    score       = 0.7 * delta_score + 0.3 * iv_score

    if not best or score > best.score:
        best = StrikeCandidate(instrument=inst, ticker=ticker, score=score)

return best   # best strike, or None if nothing qualifies`,
      },
      {
        type: 'note',
        text: 'Deribit instrument names encode everything: BTC-25APR25-80000-P = BTC option, expiry 25 April 2025, strike $80,000, Put. The bot parses expiry and strike directly from this string.',
      },
    ],
  },

  // ── 6 ─────────────────────────────────────────────────────────────────────
  {
    title: 'Order Placement — Sell to Open',
    icon: '📤',
    blocks: [
      {
        type: 'prose',
        text: 'Once a strike is selected the bot calculates contract size, runs pre-trade risk checks, then places the order. In live mode orders go through OrderTracker which waits for a confirmed fill before proceeding.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py → _open_position()',
        text: `# 1. Size: how many contracts can we afford at this strike?
contracts = self._risk.calculate_contracts(
    equity_usd = self._equity_usd,        # e.g. $50,000
    strike_usd = signal.strike,           # e.g. $80,000
)
# calculate_contracts:
#   max_commitment = equity × max_equity_per_leg  → e.g. $50k × 0.09 = $4,500
#   contracts = floor(max_commitment / strike)    → floor(4500 / 80000) = 0
#   ↑ each contract commits "strike_usd" in potential assignment risk
#   result is always an integer; minimum 1

# 2. Full pre-trade risk gate
#   - existing open positions check
#   - free equity check (must keep min_free_equity_fraction undeployed)
#   - drawdown check (don't open new trades in drawdown)
if not self._risk.full_pre_trade_check(...):
    return   # risk veto — no trade

# 3. Place order via WebSocket with fill confirmation (live mode)
rec = await self._tracker.place_and_track(
    side             = "sell",
    instrument_name  = "BTC-25APR25-80000-P",
    amount           = contracts,
    price            = signal.mark_price,      # limit price (in BTC)
    label            = "wheel_bot",
    timeout_seconds  = 45.0,
    fallback_market  = True,                   # market order if limit doesn't fill
)

if rec.status != OrderStatus.FILLED:
    return   # order didn't fill — abort, try again next tick

# 4. Record the position internally
pos = Position(
    instrument_name  = "BTC-25APR25-80000-P",
    strike           = 80000.0,
    option_type      = "put",
    entry_price      = rec.avg_fill_price,   # actual fill price in BTC
    contracts        = contracts,
    current_delta    = abs(signal.delta),    # e.g. 0.18
    entry_equity     = self._equity_usd,
    expiry_ts        = signal.expiry_ts,
    iv_rank_at_entry = self._last_iv_rank,
    dte_at_entry     = signal.dte,
)
self._positions.append(pos)`,
      },
      {
        type: 'note',
        text: 'Prices on Deribit options are quoted in BTC, not USD. entry_price = 0.0042 BTC on a $80,000 strike = $336 premium per contract. The bot converts to USD using spot price for P&L tracking.',
      },
    ],
  },

  // ── 7 ─────────────────────────────────────────────────────────────────────
  {
    title: 'Delta-Neutral Hedge — BTC-PERPETUAL',
    icon: '⚖️',
    blocks: [
      {
        type: 'prose',
        text: 'Immediately after opening, and then every tick thereafter, the hedge manager calculates how much BTC-PERPETUAL futures position is needed to cancel the option\'s directional exposure.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'hedge_manager.py → required_hedge_btc()',
        text: `def required_hedge_btc(self, option_type, delta_abs, contracts) -> float:
    """
    Short put  → portfolio delta = +delta × contracts  (we gain if BTC rises)
                 Cancel it with: SHORT BTC-PERP = -delta × contracts

    Short call → portfolio delta = -delta × contracts  (we gain if BTC falls)
                 Cancel it with: LONG  BTC-PERP = +delta × contracts
    """
    size = delta_abs * contracts
    return -size if option_type == "put" else +size

# Example:
# Short 2 contracts of a put with delta 0.18
# required_hedge = -(0.18 × 2) = -0.36 BTC  → SHORT 0.36 BTC-PERP
# Net delta = +0.36 (option) + -0.36 (perp) = 0.0  ✓`,
      },
      {
        type: 'code',
        lang: 'python',
        label: 'hedge_manager.py → rebalance() — called every tick',
        text: `async def rebalance(self, option_type, delta_abs, contracts, spot_price, ws_client):
    required   = self.required_hedge_btc(option_type, delta_abs, contracts)
    adjustment = required - self._state.perp_position_btc  # how much to add/remove

    # Round to nearest 0.1 BTC (Deribit minimum lot size)
    lots       = round(adjustment / 0.1)
    adjustment = lots * 0.1

    # Only rebalance if the drift is >= threshold (e.g. 0.05 BTC)
    # This avoids churning on tiny delta moves
    if abs(adjustment) < self._rebalance_threshold:
        return 0.0   # within tolerance — no action

    # Paper mode: simulate the trade, update internal position + P&L
    # Live mode:  place private/buy or private/sell on BTC-PERPETUAL via WebSocket
    if self._paper:
        await self._paper_trade(adjustment, spot_price)
    else:
        await self._live_trade(adjustment, spot_price, ws_client)

    return adjustment   # BTC adjusted (caller logs this)`,
      },
      {
        type: 'code',
        lang: 'python',
        label: 'hedge_manager.py → _live_trade() — actual Deribit order',
        text: `async def _live_trade(self, adjustment_btc, spot_price, ws_client) -> float:
    direction = "buy" if adjustment_btc > 0 else "sell"
    amount    = abs(adjustment_btc)

    params = {
        "instrument_name": "BTC-PERPETUAL",
        "amount":          amount,     # BTC, minimum 0.1
        "type":            "market",   # always market for speed
        "label":           "wheel_hedge",
    }
    method = "private/buy" if direction == "buy" else "private/sell"
    result = await ws_client._rpc(method, params)

    fill_price = result["order"]["average_price"]
    # Then update internal weighted-average entry price + realised P&L
    return await self._paper_trade(adjustment_btc, fill_price)`,
      },
      {
        type: 'prose',
        text: 'Hedge state is persisted to data/hedge_state.json after every trade, so if the bot restarts mid-trade the existing perp position is known and managed correctly.',
      },
    ],
  },

  // ── 8 ─────────────────────────────────────────────────────────────────────
  {
    title: 'Position Close & Expiry Settlement',
    icon: '📥',
    blocks: [
      {
        type: 'prose',
        text: 'Positions close in one of three ways: natural expiry at 08:00 UTC, mobile force-close command, or manual close. The hedge is always closed first, capturing its realised P&L before the option record is written.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py — expiry settlement flow',
        text: `# PAPER MODE: bot detects expiry by parsing the instrument name date
#   "BTC-25APR25-80000-P" → expiry = 25 Apr 2025 08:00 UTC
#   Runs inside _check_expired_positions() each tick

# LIVE MODE: Deribit sends a WebSocket event on "user.changes.any.BTC.raw"
#   Settlement shows as a trade with settlement_type = "settlement"
#   The _on_settlement_event() callback handles it immediately

# In both cases, the closing sequence is:

# Step 1: Close the hedge first (capture its realised P&L)
hedge_pnl = 0.0
if self._hedge is not None:
    hedge_pnl = await self._hedge.close_all(underlying_price, self._client.ws)
    # close_all() sells/buys back the entire perp position at market

# Step 2: Close the option position + record combined P&L
closed = await self._close_position(
    pos, "expiry_settlement", underlying_price, hedge_pnl_usd=hedge_pnl
)

if closed:
    self._positions.remove(pos)   # remove from internal tracking`,
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py → _close_position() — P&L calculation',
        text: `# Option P&L (in BTC, converted to USD)
pnl_btc = (pos.entry_price - pos.current_price) * pos.contracts
pnl_usd = pnl_btc * underlying_price
# Positive = option decayed / expired OTM — we keep premium
# Negative = option moved against us (ITM) — we owe the difference

# LIVE MODE: place a buy-to-close order and wait for fill confirmation
rec = await self._tracker.place_and_track(
    side="buy", instrument_name=pos.instrument_name,
    amount=pos.contracts, price=pos.current_price,
    fallback_market=True,   # market order if limit doesn't fill in 45s
)
if rec.status != OrderStatus.FILLED:
    return False   # ← PHANTOM-TRADE FIX: do not write CSV if unfilled
                   # Position stays in self._positions; retry next tick

# Write to trades.csv (permanent record)
trade_record = {
    "timestamp":   now.isoformat(),
    "instrument":  "BTC-25APR25-80000-P",
    "entry_price": pos.entry_price,     # BTC — e.g. 0.0042
    "exit_price":  pos.current_price,   # BTC — e.g. 0.0 (expired OTM)
    "pnl_usd":     round(pnl_usd, 2),
    "reason":      "expiry_settlement",
    ...
}

# Write to experience.jsonl (adaptive learning)
experience_record = {
    "params":    { ...current config parameters... },
    "outcome": {
        "pnl_usd":       round(pnl_usd, 2),
        "hedge_pnl_usd": round(hedge_pnl_usd, 2),
        "total_pnl_usd": round(pnl_usd + hedge_pnl_usd, 2),
        "win":           (pnl_usd + hedge_pnl_usd) > 0,
        "hold_days":     dte_at_entry - dte_at_close,
    }
}`,
      },
    ],
  },

  // ── 9 ─────────────────────────────────────────────────────────────────────
  {
    title: 'Data Files — What Writes Where',
    icon: '📁',
    blocks: [
      {
        type: 'prose',
        text: 'Every component writes to a specific file. Nothing shares a file. This is the full map:',
      },
      {
        type: 'code',
        lang: 'text',
        label: 'data/ directory — runtime files',
        text: `data/
├── trades.csv              ← every closed trade (option P&L, fill prices, slippage)
├── experience.jsonl        ← per-trade parameter + outcome log (adaptive learning)
├── tick_log.csv            ← one row per 60s tick (BTC price, equity, IV rank, delta)
├── equity_curve.json       ← equity snapshots on each trade close (charted in Dashboard)
├── bot_state.json          ← running / paused / last heartbeat (mobile status)
├── current_position.json   ← live position data (strike, delta, DTE, hedge state)
├── bot_commands.json       ← written by mobile API, consumed + deleted by bot
└── hedge_state.json        ← persisted perp position (avg entry, realised P&L)

bot_heartbeat.json          ← written every tick (equity, BTC price, position snapshot)
KILL_SWITCH                 ← presence of this file stops the bot immediately

data/optimizer/
├── best_genome_{goal}.yaml     ← evolved parameters per goal (balanced/max_yield/safest/sharpe)
├── evolve_history_{goal}.json  ← version history: timestamp + metrics per evolution run
├── sweep_results.json          ← raw sweep scores per parameter value
├── evolution_leaderboard.csv   ← sorted leaderboard from last evolution run
├── walk_forward_results.json   ← IS/OOS fitness + robustness score
├── monte_carlo_results.json    ← p5/p50/p95 return, probability of profit
└── reconcile_results.json      ← BS model vs actual trade comparison`,
      },
      {
        type: 'note',
        text: 'experience.jsonl grows with every trade and is read by the optimizer to bias future evolution runs toward parameter combinations that have performed well in live/paper trading — not just in historical backtest.',
      },
    ],
  },

  // ── 10 ────────────────────────────────────────────────────────────────────
  {
    title: 'Deribit API — What Gets Called When',
    icon: '🌐',
    blocks: [
      {
        type: 'prose',
        text: 'The bot uses two Deribit connections: a public REST endpoint (no auth) for market data, and an authenticated WebSocket for orders and account data.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'Deribit endpoints used — full list',
        text: `# ── PUBLIC REST (no auth required) ─────────────────────────────────────────
# Called once per tick (every 60 seconds):
GET /public/get_historical_volatility?currency=BTC
# → daily DVOL index history → used to calculate IV Rank

GET /public/get_instruments?currency=BTC&kind=option&expired=false
# → all active BTC option instruments (~900 results)
# → bot pre-filters to DTE window and fetches tickers only for those

GET /public/ticker?instrument_name=BTC-25APR25-80000-P
# → mark_price (BTC), delta, mark_iv, underlying_price, bid/ask
# → called for each candidate instrument in the DTE window

# ── PRIVATE REST (API key + secret required) ─────────────────────────────────
# Called once on startup (live mode):
GET /private/get_positions?currency=BTC&kind=option
# → any existing open option positions → imported into bot state

GET /private/get_account_summary?currency=BTC
# → equity in BTC, available_funds → converted to USD for position sizing

# Called every tick (live mode):
GET /private/get_account_summary?currency=BTC
# → real-time equity update (BTC price × equity BTC = equity USD)

# ── PRIVATE WEBSOCKET (authenticated, persistent connection) ─────────────────
# Called when opening a position:
private/sell { instrument_name, amount, type: "limit", price, label: "wheel_bot" }

# Called when closing a position:
private/buy  { instrument_name, amount, type: "limit", price, label: "wheel_bot_close" }

# Called when rebalancing hedge:
private/sell or private/buy { instrument_name: "BTC-PERPETUAL", amount, type: "market" }

# WebSocket SUBSCRIPTIONS (push, not poll):
user.changes.any.BTC.raw    → settlement event callback (expiry detection)
user.portfolio.btc          → portfolio update (equity changes)`,
      },
      {
        type: 'note',
        text: 'OrderTracker wraps the WebSocket sell/buy calls with a 45-second timeout and automatic fallback to market order. It tracks the order_id, polls for fills, and returns a fill record with avg_fill_price and slippage_btc so every executed price is auditable.',
      },
    ],
  },

  // ── 11 ────────────────────────────────────────────────────────────────────
  {
    title: 'Adaptive Learning — experience.jsonl',
    icon: '🧠',
    blocks: [
      {
        type: 'prose',
        text: 'Every closed trade appends one JSON line to experience.jsonl. The optimizer reads this file during evolution to weight its fitness function toward parameter sets that have actually worked in live/paper trading — not just in backtest.',
      },
      {
        type: 'code',
        lang: 'json',
        label: 'data/experience.jsonl — one line per closed trade',
        text: `{
  "timestamp": 1714000000.0,
  "mode": "paper",
  "params": {
    "iv_rank_threshold": 0.52,
    "target_delta_min": 0.14,
    "target_delta_max": 0.27,
    "max_dte": 28,
    "min_dte": 7,
    "max_equity_per_leg": 0.09,
    "premium_fraction_of_spot": 0.012
  },
  "conditions_at_open": {
    "iv_rank": 0.64,
    "btc_price": 82450.0,
    "option_type": "put",
    "strike": 72000.0,
    "dte_at_entry": 21
  },
  "outcome": {
    "pnl_usd": 312.0,
    "hedge_pnl_usd": -28.0,
    "total_pnl_usd": 284.0,
    "pnl_pct": 0.0057,
    "hold_days": 21,
    "reason": "expiry_settlement",
    "win": true
  }
}`,
      },
      {
        type: 'code',
        lang: 'python',
        label: 'optimizer.py — how experience blends into fitness',
        text: `def blend_fitness(historical_fitness, experience_data, params):
    """
    Blend backtest fitness with actual live/paper trade outcomes.

    historical_fitness:  score from the backtester (0.0 – 1.0+)
    experience_data:     trades from experience.jsonl that used similar params

    Confidence grows as more real trades come in.
    At ~20+ trades, experience_data has equal weight with backtest history.
    """
    if not experience_data or n_trades < 5:
        return historical_fitness   # not enough data — trust backtest

    # Weight: 50% experience at 20 trades, up to 60% at 50+ trades
    exp_weight  = min(0.60, 0.50 * (n_trades / 20))
    hist_weight = 1.0 - exp_weight

    exp_score = average_of(win_rate, pnl_pct_norm)   # normalised 0–1
    return hist_weight * historical_fitness + exp_weight * exp_score`,
      },
    ],
  },

  // ── 12 ────────────────────────────────────────────────────────────────────
  {
    title: 'Strategy Improvements Overview',
    icon: '🔧',
    blocks: [
      {
        type: 'prose',
        text: 'Six validated improvements were added to the live strategy in April 2026. Each is documented below with its implementation details, config flag, and expected impact.',
      },
      {
        type: 'image',
        src: '/charts/chart_improvements_summary.png',
        caption: 'Summary of all 6 strategy improvements and their backtest impact',
      },
    ],
  },

  // ── 13 ────────────────────────────────────────────────────────────────────
  {
    title: 'Weekly Expiries — Double Trade Frequency',
    icon: '📅',
    blocks: [
      {
        type: 'heading',
        text: 'The Problem',
      },
      {
        type: 'prose',
        text: "The live bot's instrument pre-filter silently excluded options with exactly 7 DTE. The backtester already used 7-DTE weeklies, creating a gap between simulated and live behaviour.",
      },
      {
        type: 'heading',
        text: 'The Fix',
      },
      {
        type: 'prose',
        text: 'Lowered min_dte from 8→7 and capped max_dte at 14 (bi-weekly). This captures all Deribit weekly expiries and keeps capital cycling faster.',
      },
      {
        type: 'code',
        lang: 'yaml',
        label: 'config.yaml — DTE window',
        text: `strategy:
  min_dte: 7    # was 8 — now includes 7-DTE weekly expiries
  max_dte: 14   # capped at bi-weekly; avoids slow monthly capital lock-up`,
      },
      {
        type: 'note',
        text: 'Always enabled. No config flag needed.',
      },
      {
        type: 'image',
        src: '/charts/chart_improvement_1_weekly.png',
        caption: 'Weekly expiry fix — trade frequency comparison',
      },
    ],
  },

  // ── 14 ────────────────────────────────────────────────────────────────────
  {
    title: 'Regime Filter — Skip Puts in Downtrends',
    icon: '📉',
    blocks: [
      {
        type: 'heading',
        text: 'The Problem',
      },
      {
        type: 'prose',
        text: 'Selling puts during a sustained BTC downtrend (spot below 50-day SMA) leads to repeated near-ITM or ITM assignments with no premium buffer.',
      },
      {
        type: 'heading',
        text: 'The Implementation',
      },
      {
        type: 'prose',
        text: 'A rolling N-day SMA is computed from daily close prices. If spot < SMA, the bot skips opening new put legs. Existing positions always run to expiry.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py → _is_above_regime_ma()',
        text: `def _is_above_regime_ma(self, current_price: float) -> bool:
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
        return True  # filter disabled — always allow`,
      },
      {
        type: 'note',
        text: 'Opt-in. Set `sizing.use_regime_filter: true` in config.yaml. The 12-month backtest was mostly bearish — enabling this would have blocked 10 of 11 trades (correct capital-preservation behaviour).',
      },
      {
        type: 'image',
        src: '/charts/chart_improvement_2_regime.png',
        caption: 'Regime filter — BTC vs 50-day SMA with blocked/allowed periods',
      },
    ],
  },

  // ── 15 ────────────────────────────────────────────────────────────────────
  {
    title: 'Dynamic Delta — Scale Aggression with IV Rank',
    icon: '🎯',
    blocks: [
      {
        type: 'heading',
        text: 'The Problem',
      },
      {
        type: 'prose',
        text: 'A static delta target ignores the cost of volatility. When IV is high, options are expensively priced — you can sell closer to ATM and collect more premium without extra risk. When IV is low, selling far OTM avoids overexposure.',
      },
      {
        type: 'heading',
        text: 'The Formula',
      },
      {
        type: 'prose',
        text: 'The target delta midpoint is linearly interpolated with IV rank. At IV rank 0 it equals `target_delta_min` (far OTM). At IV rank 1 it equals `target_delta_max` (close to ATM).',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'strategy.py → select_strike() — dynamic delta interpolation',
        text: `elif cfg.strategy.iv_dynamic_delta:
    # Linearly interpolate: IV rank 0 → target midpoint = d_min,
    #                        IV rank 1 → target midpoint = d_max.
    # This biases strike selection toward more aggressive (higher delta)
    # options when IV is richly priced and premiums are most attractive.
    target_delta_mid = d_min + (d_max - d_min) * float(np.clip(iv_rank, 0.0, 1.0))
    logger.debug(
        f"Dynamic delta: IV rank={iv_rank:.2%} → "
        f"target mid={target_delta_mid:.4f} "
        f"(static mid would be {(d_min + d_max)/2:.4f})"
    )
else:
    target_delta_mid = (d_min + d_max) / 2.0`,
      },
      {
        type: 'heading',
        text: 'Backtest Results',
      },
      {
        type: 'prose',
        text: 'Enabling dynamic delta improved 12-month total return from +67.64% to +74.40%, raised Sharpe ratio from 1.16 to 1.22, and increased average premium yield from 1.34% to 1.57% per contract.',
      },
      {
        type: 'note',
        text: 'Always enabled. `strategy.iv_dynamic_delta: true` in config.yaml.',
      },
      {
        type: 'image',
        src: '/charts/chart_improvement_3_dynamic_delta.png',
        caption: 'Dynamic delta — equity curve comparison vs static delta target',
      },
    ],
  },

  // ── 16 ────────────────────────────────────────────────────────────────────
  {
    title: 'Strike Laddering — Spread Risk Across Strikes',
    icon: '🪜',
    blocks: [
      {
        type: 'heading',
        text: 'The Problem',
      },
      {
        type: 'prose',
        text: 'A single large put creates a binary outcome: BTC either stays above one specific strike (full win) or crosses it (full loss). This concentrates risk at one price level.',
      },
      {
        type: 'heading',
        text: 'The Implementation',
      },
      {
        type: 'prose',
        text: 'With laddering enabled, the bot opens N puts at evenly-spaced delta targets across the configured range. For 2 legs: one conservative (far OTM) and one aggressive (closer ATM). Each leg gets 1/N of the normal equity allocation so total exposure is unchanged.',
      },
      {
        type: 'code',
        lang: 'python',
        label: 'strategy.py → select_ladder_strikes()',
        text: `def select_ladder_strikes(
    self,
    instruments: list[Instrument],
    tickers: dict[str, Ticker],
    underlying_price: float,
    n_legs: int,
    iv_rank: float = 0.5,
) -> list[StrikeCandidate]:
    """
    Select N put strike candidates at evenly-spaced delta targets across
    the configured delta range.

    For n_legs=2 with delta range [0.15, 0.39]:
      Leg 1 (conservative): target delta ≈ 0.22  (far OTM, lower risk)
      Leg 2 (aggressive):   target delta ≈ 0.31  (closer ATM, more premium)

    Each leg targets delta at position (k / (n+1)) along the [min, max] range,
    ensuring targets stay inside the configured bounds.  Duplicate strikes
    are excluded so each leg is at a distinct price level.
    """`,
      },
      {
        type: 'note',
        text: 'Opt-in. Set `sizing.ladder_enabled: true` and `sizing.ladder_legs: 2` in config.yaml.',
      },
      {
        type: 'image',
        src: '/charts/chart_improvement_4_ladder.png',
        caption: 'Strike laddering — two puts at different delta targets',
      },
    ],
  },

  // ── 17 ────────────────────────────────────────────────────────────────────
  {
    title: 'Position Rolling — Cut and Re-enter on Breaches',
    icon: '🔄',
    blocks: [
      {
        type: 'heading',
        text: 'The Problem',
      },
      {
        type: 'prose',
        text: 'When a put is breached (delta exceeds 0.40 or loss exceeds 2% of equity), holding it to expiry risks full assignment with no chance to recover premium.',
      },
      {
        type: 'heading',
        text: 'How Rolling Works',
      },
      {
        type: 'prose',
        text: "The bot buys back the breached put and immediately re-opens a new put using the same signal logic. Because BTC has moved lower, the new strike lands below the original — naturally achieving 'roll down and out'. Only rolls when DTE ≥ roll_min_dte (default 3); close-to-expiry positions settle naturally since rolling costs more in slippage.",
      },
      {
        type: 'code',
        lang: 'python',
        label: 'bot.py → _tick() — roll check',
        text: `if self._cfg.risk.roll_enabled:
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
                self._positions.remove(pos)`,
      },
      {
        type: 'note',
        text: 'Opt-in. Set `risk.roll_enabled: true`. Tune `risk.roll_min_dte` (default 3) to control how close to expiry rolling is allowed.',
      },
      {
        type: 'image',
        src: '/charts/chart_improvement_5_roll.png',
        caption: 'Position rolling — breach detection and re-entry logic',
      },
    ],
  },

  // ── 18 ────────────────────────────────────────────────────────────────────
  {
    title: 'Recovery Calls — Capture Full BTC Rebound',
    icon: '🔁',
    blocks: [
      {
        type: 'heading',
        text: 'The Problem',
      },
      {
        type: 'prose',
        text: 'After a put expires ITM (BTC dropped below the put strike), the wheel transitions to selling a covered call. If that call is placed below the put strike, any BTC recovery between the two strikes produces no revenue — the premium opportunity is missed.',
      },
      {
        type: 'heading',
        text: 'The Fix',
      },
      {
        type: 'prose',
        text: "When the last put expired ITM, the strategy flags 'recovery mode'. The next call is constrained to strikes ≥ the put strike. If BTC rallies back above the put strike, the entire recovery is captured.",
      },
      {
        type: 'code',
        lang: 'python',
        label: 'strategy.py → generate_signal() — recovery_min_strike logic',
        text: `# Recovery mode: if the previous put expired ITM ("assignment"), target
# a call strike >= the put strike so BTC recovery is fully captured.
recovery_min_strike: float | None = None
if cycle == "call" and self._last_put_was_itm and self._last_put_strike > 0:
    recovery_min_strike = self._last_put_strike
    logger.info(
        f"Recovery call mode: targeting strikes >= \${recovery_min_strike:,.0f} "
        f"(last put strike — ITM assignment)"
    )

candidate = self.select_strike(
    instruments, tickers, cycle, underlying_price,
    iv_rank=iv_rank,
    recovery_min_strike=recovery_min_strike,
)`,
      },
      {
        type: 'heading',
        text: 'Real Example',
      },
      {
        type: 'prose',
        text: 'In the 12-month backtest, put 5 expired ITM at $98,793 when BTC was $96,226. Without recovery mode the next call was placed at $96,676 — missing the $2,117 recovery range. With recovery mode, the call was placed at $98,793, capturing the full upside.',
      },
      {
        type: 'note',
        text: 'Always active automatically. No config flag needed.',
      },
      {
        type: 'image',
        src: '/charts/chart_improvement_6_recovery.png',
        caption: 'Recovery calls — call placement before and after the fix',
      },
    ],
  },
]

// ── Sub-components ────────────────────────────────────────────────────────────

function CodeBlock({ lang, text, label }: { lang: string; text: string; label?: string }) {
  return (
    <div className="rounded-xl overflow-hidden">
      {label && (
        <div className="flex items-center justify-between bg-slate-800 px-3 py-1.5">
          <span className="text-xs text-slate-400 font-mono truncate">{label}</span>
          <span className="text-xs text-slate-600 ml-2 shrink-0">{lang}</span>
        </div>
      )}
      <pre className="bg-slate-900 px-3 py-3 overflow-x-auto">
        <code className="text-xs font-mono text-slate-200 leading-relaxed whitespace-pre">
          {text.trim()}
        </code>
      </pre>
    </div>
  )
}

function FileBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="rounded-xl overflow-hidden">
      <div className="bg-green-950 border-b border-green-900 px-3 py-1.5 flex items-center gap-2">
        <span className="text-green-500 text-xs">📄</span>
        <span className="text-xs text-green-400 font-mono">{label}</span>
      </div>
      <pre className="bg-slate-900 px-3 py-3 overflow-x-auto">
        <code className="text-xs font-mono text-slate-200 leading-relaxed whitespace-pre">
          {text.trim()}
        </code>
      </pre>
    </div>
  )
}

function NoteBlock({ text }: { text: string }) {
  return (
    <div className="bg-amber-950 border border-amber-800 rounded-xl px-3 py-2.5 flex gap-2">
      <span className="text-amber-500 text-sm shrink-0">⚠</span>
      <p className="text-xs text-amber-200 leading-relaxed">{text}</p>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function CodeGuide({ onClose }: Props) {
  return (
    <div className="p-4 space-y-4 pb-8">
      <div className="flex items-start justify-between pt-2">
        <div>
          <h1 className="text-lg font-bold text-white">How the Bot Executes</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Real code + plain-English annotations — for coders and traders.
          </p>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-slate-400 hover:text-white text-lg leading-none mt-1 ml-4">✕</button>
        )}
      </div>

      {SECTIONS.map((section, si) => (
        <div key={si} className="bg-card border border-border rounded-2xl overflow-hidden">
          {/* Section header */}
          <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
            <span className="text-lg leading-none">{section.icon}</span>
            <div>
              <span className="text-xs text-slate-500 font-medium">{si + 1} of {SECTIONS.length}</span>
              <h2 className="text-sm font-bold text-white leading-snug">{section.title}</h2>
            </div>
          </div>

          {/* Blocks */}
          <div className="px-4 py-3 space-y-3">
            {section.blocks.map((block, bi) => {
              if (block.type === 'prose') {
                return (
                  <p key={bi} className="text-sm text-slate-300 leading-relaxed">
                    {block.text}
                  </p>
                )
              }
              if (block.type === 'heading') {
                return (
                  <p key={bi} className="text-xs font-bold text-slate-400 uppercase tracking-wider pt-1">
                    {block.text}
                  </p>
                )
              }
              if (block.type === 'code') {
                return (
                  <CodeBlock key={bi} lang={block.lang ?? 'python'} text={block.text ?? ''} label={block.label} />
                )
              }
              if (block.type === 'file') {
                return (
                  <FileBlock key={bi} label={block.label ?? ''} text={block.text ?? ''} />
                )
              }
              if (block.type === 'note') {
                return <NoteBlock key={bi} text={block.text ?? ''} />
              }
              if (block.type === 'image') {
                return (
                  <div key={bi} className="my-4">
                    <img
                      src={block.src}
                      alt={block.caption ?? ''}
                      className="w-full rounded-lg border border-gray-700"
                      style={{ maxHeight: '300px', objectFit: 'contain', background: '#111' }}
                    />
                    {block.caption && (
                      <p className="text-xs text-gray-500 mt-1 text-center">{block.caption}</p>
                    )}
                  </div>
                )
              }
              return null
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
