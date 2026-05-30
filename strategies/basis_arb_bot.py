"""
basis_arb_bot.py — long-spot / short-perp basis arbitrage with z-score entry.

The bot watches the relative basis `(perp − spot) / spot` z-scored on a
rolling window. When z spikes above +entry_z (basis abnormally wide), it
opens a long-spot / short-perp pair and rides the convergence back to the
mean. It exits on convergence (z < exit_z), on time-out, or — only on
catastrophic moves — halts when drawdown blows the budget OR basis
dislocates further (|z| ≥ dislocation_guard_z).

Per spec 03-basis-arb-spec.md, this is the FIRST farm bot with two legs
and a real architectural decision: the simulator tracks spot_qty and
perp_qty separately so failure modes the BIS paper warns about (margin
cascades, FTX-style halts) are *expressible* — collapsing to a synthetic
basis would silently rate the strategy higher than reality.

Conservative paper model: cash-funded spot (no borrow); isolated perp
margin (no cross-margin netting); taker fees + slippage on both legs;
hourly close fills. Capital efficiency is a Gate 4 optimisation, not a
Gate 3 cheat.

Step shape (unique in the farm):
    bot.step(spot_price, perp_price, funding_1h)

Persistence: to_dict / load_dict round-trip the full state including the
rolling basis deque, running sums for fast z-stats, position fields, and
the halt reason if any.
"""

from __future__ import annotations

import math
from collections import deque


class BasisArbBot:
    """Long-spot / short-perp basis-convergence specialist. Single-venue paper."""

    # State labels — kept as class constants so the dispatcher can match on them.
    FLAT = "FLAT"
    SHORT_BASIS = "SHORT_BASIS"
    HALTED = "HALTED"

    def __init__(
        self,
        capital: float = 10_000.0,
        *,
        lookback_hours: int = 168,
        entry_z_threshold: float = 2.0,
        exit_z_threshold: float = 0.25,
        max_position_btc: float = 0.10,
        position_sizing_z_cap: float = 4.0,
        min_position_btc: float = 0.002,
        max_hours_in_position: int = 168,
        perp_margin_frac: float = 1.0,
        halt_drawdown_pct: float = 0.10,
        dislocation_guard_z: float = 5.0,
        funding_gate: bool = True,
        fee_spot: float = 0.0006,
        fee_perp: float = 0.0006,
        slip_spot_bps: float = 5.0,
        slip_perp_bps: float = 3.0,
        # Capital-funded auto-cap (spec §10): a $10k account can't fund
        # both legs of the spec-default 0.10 BTC position at $90k BTC
        # (~$18k required). The constructor scales max_position_btc down
        # to (capital × capital_max_position_frac) / spot_price on the
        # FIRST step it sees a spot price. capital_max_position_frac =
        # 0.4 keeps the auto-cap conservative (40 % of capital per leg).
        capital_max_position_frac: float = 0.4,
    ):
        if not (0.0 <= perp_margin_frac <= 1.0):
            raise ValueError(f"perp_margin_frac must be in [0, 1], got {perp_margin_frac}")
        if exit_z_threshold >= entry_z_threshold:
            raise ValueError("exit_z must be < entry_z (otherwise instant re-entry on open)")
        if dislocation_guard_z <= entry_z_threshold:
            raise ValueError("dislocation_guard_z must be > entry_z (otherwise can't open)")

        self.capital = capital
        self.cash = capital                    # unlocked cash
        self.lookback_hours = int(lookback_hours)
        self.entry_z_threshold = float(entry_z_threshold)
        self.exit_z_threshold = float(exit_z_threshold)
        self.spec_max_position_btc = float(max_position_btc)
        self.max_position_btc = float(max_position_btc)   # adjusted after first step
        self.position_sizing_z_cap = float(position_sizing_z_cap)
        self.min_position_btc = float(min_position_btc)
        self.max_hours_in_position = int(max_hours_in_position)
        self.perp_margin_frac = float(perp_margin_frac)
        self.halt_drawdown_pct = float(halt_drawdown_pct)
        self.dislocation_guard_z = float(dislocation_guard_z)
        self.funding_gate = bool(funding_gate)
        self.fee_spot = float(fee_spot)
        self.fee_perp = float(fee_perp)
        self.slip_spot_bps = float(slip_spot_bps)
        self.slip_perp_bps = float(slip_perp_bps)
        self.capital_max_position_frac = float(capital_max_position_frac)

        # Position state.
        self.state: str = self.FLAT
        self.spot_qty: float = 0.0
        self.perp_qty: float = 0.0
        self.spot_entry: float = 0.0
        self.perp_entry: float = 0.0
        self.entry_basis_bps: float = 0.0
        self.entry_z: float = 0.0
        self.hours_in_position: int = 0
        # `perp_margin_reserved` is a *constraint marker* — see equity() for the
        # accounting choice. It is NOT subtracted from cash at open.
        self.perp_margin_reserved: float = 0.0

        # Rolling basis stats (Welford-style running sums for O(1) mean+std).
        self.basis_q: deque[float] = deque(maxlen=self.lookback_hours)
        self.basis_sum: float = 0.0
        self.basis_sum_sq: float = 0.0

        # Bookkeeping.
        self.peak_equity: float = capital
        self.trades: int = 0
        self.total_funding_collected: float = 0.0
        self.total_convergence_pnl: float = 0.0
        self.realized_pnl: float = 0.0       # cumulative realised PnL on closes
        self.halted_reason: str | None = None
        self.last_spot: float | None = None
        self.last_perp: float | None = None
        self.auto_capped: bool = False

    # ── price utilities ────────────────────────────────────────────────────────

    def _basis_bps(self, perp: float, spot: float) -> float:
        if spot <= 0:
            return 0.0
        return 10_000.0 * (perp - spot) / spot

    def _push_basis(self, b_bps: float) -> None:
        if len(self.basis_q) == self.basis_q.maxlen:
            old = self.basis_q[0]
            self.basis_sum -= old
            self.basis_sum_sq -= old * old
        self.basis_q.append(b_bps)
        self.basis_sum += b_bps
        self.basis_sum_sq += b_bps * b_bps

    def _z(self, b_bps: float) -> float | None:
        n = len(self.basis_q)
        if n < self.lookback_hours:
            return None
        mean = self.basis_sum / n
        var = (self.basis_sum_sq / n) - (mean * mean)
        if var <= 0:
            return None
        sd = math.sqrt(var)
        if sd < 1e-6:
            return None
        return (b_bps - mean) / sd

    def _sized_position(self, z: float) -> float:
        """Linear ramp from 25 % of cap at entry_z to 100 % at sizing_z_cap."""
        if z <= self.entry_z_threshold:
            return 0.0
        ramp = (z - self.entry_z_threshold) / max(
            1e-9, self.position_sizing_z_cap - self.entry_z_threshold
        )
        frac = max(0.25, min(1.0, ramp))
        return frac * self.max_position_btc

    def _auto_cap_position(self, spot_price: float) -> None:
        """First-touch auto-scale per spec §10 — both legs together must fit
        within `capital_max_position_frac × capital`."""
        if self.auto_capped or spot_price <= 0:
            return
        # Total collateral per BTC = spot (full purchase) + perp × margin_frac.
        # Approximate perp ≈ spot for this sizing check.
        collateral_per_btc = spot_price * (1.0 + self.perp_margin_frac)
        budget = self.capital * self.capital_max_position_frac * 2.0
        # ^ *2.0 because capital_max_position_frac is per-leg-equivalent in the
        # spec; the combined two-leg budget is 2 × that fraction.
        cap = budget / max(1e-9, collateral_per_btc)
        if cap < self.spec_max_position_btc:
            self.max_position_btc = cap
        self.auto_capped = True

    # ── core step ──────────────────────────────────────────────────────────────

    def step(self, spot_price: float, perp_price: float, funding_1h: float) -> None:
        """One hourly step. Updates state machine, position PnL, equity."""
        if spot_price <= 0 or perp_price <= 0:
            return
        self.last_spot = spot_price
        self.last_perp = perp_price
        self._auto_cap_position(spot_price)

        # 1. Update rolling basis stats with the new observation.
        b = self._basis_bps(perp_price, spot_price)
        self._push_basis(b)
        z = self._z(b)

        # 2. Funding accrual while in position (short perp earns positive
        # funding). Spec §2.3: equity += position_btc × spot × funding_1h.
        if self.state == self.SHORT_BASIS:
            fpnl = self.perp_qty * spot_price * funding_1h
            self.cash += fpnl
            self.total_funding_collected += fpnl
            self.hours_in_position += 1

        # 3. State machine dispatch.
        if self.state == self.HALTED:
            self._update_peak(spot_price, perp_price)
            return

        if self.state == self.SHORT_BASIS:
            self._handle_in_position(spot_price, perp_price, b, z, funding_1h)
        elif self.state == self.FLAT and z is not None:
            self._maybe_open(spot_price, perp_price, b, z, funding_1h)

        self._update_peak(spot_price, perp_price)

    # ── opening / closing ──────────────────────────────────────────────────────

    def _maybe_open(self, spot: float, perp: float, basis_bps: float,
                    z: float, funding_1h: float) -> None:
        if z <= self.entry_z_threshold:
            return
        # Funding-gate: don't open if funding pays the wrong way more than the
        # basis is offering. Approximate "annualised funding" vs basis_bps.
        if self.funding_gate and funding_1h < 0:
            # Annualised funding bps = funding_1h × 24 × 365 × 10_000.
            # If the cost-of-carry over the expected hold (~168h) exceeds the
            # current basis, skip the trade.
            expected_funding_cost_bps = abs(funding_1h) * self.max_hours_in_position * 10_000.0
            if expected_funding_cost_bps > basis_bps:
                return

        position_btc = self._sized_position(z)
        if position_btc < self.min_position_btc:
            return

        # Required collateral (cash floor check per spec §5 cap #7).
        slip_spot = self.slip_spot_bps / 10_000.0
        slip_perp = self.slip_perp_bps / 10_000.0
        spot_cost = position_btc * spot * (1.0 + slip_spot) * (1.0 + self.fee_spot)
        perp_margin = position_btc * perp * self.perp_margin_frac
        # NOTE on accounting: cash IS decremented by spot_cost (the spot leg is
        # a real purchase). cash is NOT decremented by perp_margin — the margin
        # is a "locked" constraint marker (see equity()). The floor check still
        # demands cash ≥ spot_cost + perp_margin so the bot can survive a
        # margin call up to perp_margin_reserved without going negative.
        if self.cash < spot_cost + perp_margin:
            return

        self.cash -= spot_cost
        self.spot_qty = position_btc
        self.perp_qty = position_btc
        self.spot_entry = spot * (1.0 + slip_spot)  # effective entry after slip
        self.perp_entry = perp * (1.0 - slip_perp)  # short perp fills at slight discount
        self.entry_basis_bps = basis_bps
        self.entry_z = z
        self.hours_in_position = 0
        self.perp_margin_reserved = perp_margin
        self.state = self.SHORT_BASIS
        self.trades += 1

    def _handle_in_position(self, spot: float, perp: float, basis_bps: float,
                            z: float | None, funding_1h: float) -> None:
        # Margin call: perp unrealised loss exceeded the locked reservation.
        perp_pnl = -self.perp_qty * (perp - self.perp_entry)
        if perp_pnl < -self.perp_margin_reserved:
            self._close(spot, perp, reason="perp_margin_call", halt=True,
                        forced_perp_loss=self.perp_margin_reserved)
            return

        # Dislocation guard: |z| ≥ guard_z while in position ⇒ basis blowing
        # *further* from the mean. Close + HALT, manual reset required.
        if z is not None and abs(z) >= self.dislocation_guard_z:
            self._close(spot, perp, reason="dislocation_guard", halt=True)
            return

        # Drawdown halt.
        equity = self.equity(spot, perp)
        dd = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        if dd >= self.halt_drawdown_pct:
            self._close(spot, perp, reason="drawdown_halt", halt=True)
            return

        # Time-out.
        if self.hours_in_position >= self.max_hours_in_position:
            self._close(spot, perp, reason="time_out", halt=False)
            return

        # Convergence exit.
        if z is not None and z < self.exit_z_threshold:
            self._close(spot, perp, reason="convergence", halt=False)
            return

    def _close(self, spot: float, perp: float, *, reason: str, halt: bool,
               forced_perp_loss: float | None = None) -> None:
        """Close both legs at current prices. Settle PnL into cash."""
        slip_spot = self.slip_spot_bps / 10_000.0
        slip_perp = self.slip_perp_bps / 10_000.0

        # Spot leg: sell at spot × (1 - slip) net of fee.
        spot_sale = self.spot_qty * spot * (1.0 - slip_spot) * (1.0 - self.fee_spot)
        self.cash += spot_sale

        if forced_perp_loss is not None:
            # Margin call: the locked reservation is wiped out; that's the loss.
            perp_realized = -forced_perp_loss
        else:
            # Normal close: realise perp PnL at the exit price (with slip on
            # the buy-back).
            perp_exit = perp * (1.0 + slip_perp)
            perp_realized = -self.perp_qty * (perp_exit - self.perp_entry)
            perp_realized -= self.perp_qty * perp_exit * self.fee_perp
        self.cash += perp_realized

        # Convergence PnL = sum of both legs at the moment of close, exclusive
        # of fees/slippage and funding accrued during the hold.
        spot_leg_pnl = self.spot_qty * (spot - self.spot_entry)
        perp_leg_pnl = -self.perp_qty * (perp - self.perp_entry)
        self.total_convergence_pnl += spot_leg_pnl + perp_leg_pnl
        self.realized_pnl += spot_sale + perp_realized - self.spot_entry * self.spot_qty

        self.spot_qty = 0.0
        self.perp_qty = 0.0
        self.perp_margin_reserved = 0.0
        self.hours_in_position = 0
        self.trades += 1

        if halt:
            self.state = self.HALTED
            self.halted_reason = reason
        else:
            self.state = self.FLAT

    # ── equity / accounting ────────────────────────────────────────────────────

    def equity(self, spot_price: float, perp_price: float) -> float:
        """Spec §6.3 — cash + spot value + perp unrealised PnL. The locked
        perp_margin is implicitly part of cash (it's a constraint, not a
        deduction). Spot leg is real cash spent on a real BTC asset."""
        if self.state != self.SHORT_BASIS:
            return self.cash + self.spot_qty * spot_price
        perp_unreal = -self.perp_qty * (perp_price - self.perp_entry)
        return self.cash + self.spot_qty * spot_price + perp_unreal

    def equity_now(self) -> float:
        """Cached-price wrapper for the dispatcher's `equity_now()` path."""
        if self.last_spot is None or self.last_perp is None:
            return self.cash
        return self.equity(self.last_spot, self.last_perp)

    def _update_peak(self, spot: float, perp: float) -> None:
        eq = self.equity(spot, perp)
        if eq > self.peak_equity:
            self.peak_equity = eq

    def btc_held(self) -> float:
        """Reported as the spot leg only — the perp short is not BTC inventory."""
        return self.spot_qty

    # ── persistence ────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "capital": self.capital,
            "cash": self.cash,
            "state": self.state,
            "spot_qty": self.spot_qty,
            "perp_qty": self.perp_qty,
            "spot_entry": self.spot_entry,
            "perp_entry": self.perp_entry,
            "entry_basis_bps": self.entry_basis_bps,
            "entry_z": self.entry_z,
            "hours_in_position": self.hours_in_position,
            "perp_margin_reserved": self.perp_margin_reserved,
            "basis_q": list(self.basis_q),
            "basis_sum": self.basis_sum,
            "basis_sum_sq": self.basis_sum_sq,
            "peak_equity": self.peak_equity,
            "trades": self.trades,
            "total_funding_collected": self.total_funding_collected,
            "total_convergence_pnl": self.total_convergence_pnl,
            "realized_pnl": self.realized_pnl,
            "halted_reason": self.halted_reason,
            "last_spot": self.last_spot,
            "last_perp": self.last_perp,
            "max_position_btc": self.max_position_btc,
            "auto_capped": self.auto_capped,
        }

    def load_dict(self, d: dict) -> None:
        self.capital = d.get("capital", self.capital)
        self.cash = d.get("cash", self.cash)
        self.state = d.get("state", self.FLAT)
        self.spot_qty = d.get("spot_qty", 0.0)
        self.perp_qty = d.get("perp_qty", 0.0)
        self.spot_entry = d.get("spot_entry", 0.0)
        self.perp_entry = d.get("perp_entry", 0.0)
        self.entry_basis_bps = d.get("entry_basis_bps", 0.0)
        self.entry_z = d.get("entry_z", 0.0)
        self.hours_in_position = d.get("hours_in_position", 0)
        self.perp_margin_reserved = d.get("perp_margin_reserved", 0.0)
        bq = d.get("basis_q", [])
        self.basis_q = deque(bq, maxlen=self.lookback_hours)
        self.basis_sum = d.get("basis_sum", sum(bq))
        self.basis_sum_sq = d.get("basis_sum_sq", sum(x * x for x in bq))
        self.peak_equity = d.get("peak_equity", self.capital)
        self.trades = d.get("trades", 0)
        self.total_funding_collected = d.get("total_funding_collected", 0.0)
        self.total_convergence_pnl = d.get("total_convergence_pnl", 0.0)
        self.realized_pnl = d.get("realized_pnl", 0.0)
        self.halted_reason = d.get("halted_reason")
        self.last_spot = d.get("last_spot")
        self.last_perp = d.get("last_perp")
        self.max_position_btc = d.get("max_position_btc", self.spec_max_position_btc)
        self.auto_capped = d.get("auto_capped", False)

    # ── warmup ─────────────────────────────────────────────────────────────────

    def warmup(self, spot_prices: list[float], perp_prices: list[float]) -> None:
        """Seed the basis_q deque from historical paired prices so z-stats are
        computable from step 1. Both arrays must be the same length and on the
        same hourly grid (spec §6.5)."""
        if len(spot_prices) != len(perp_prices):
            raise ValueError(
                f"warmup arrays differ: spot={len(spot_prices)} perp={len(perp_prices)}"
            )
        for s, p in zip(spot_prices, perp_prices):
            if s <= 0 or p <= 0:
                continue
            self._push_basis(self._basis_bps(p, s))
