"""
strategy.py — Core wheel strategy logic.

Responsibilities:
  - Calculate IV rank from historical volatility series
  - Select the best strike given a target delta range and cycle (put/call)
  - Decide whether to open a new position based on IV rank threshold
  - Determine the next cycle direction (put → call → put ...)

ML_HOOK stubs are marked with # ML_HOOK for future scikit-learn integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import numpy as np
from loguru import logger

from config import cfg
from deribit_client import DeribitPublicREST, Instrument, Ticker

Cycle = Literal["put", "call"]


# ── Data ───────────────────────────────────────────────────────────────────────


@dataclass
class StrikeCandidate:
    instrument: Instrument
    ticker: Ticker
    score: float          # higher = preferred


@dataclass
class OpenSignal:
    """Returned when strategy decides to open a position."""
    instrument_name: str
    strike: float
    option_type: str
    expiry_ts: int
    dte: int
    delta: float
    mark_iv: float
    mark_price: float
    underlying_price: float
    cycle: Cycle
    iv_rank: float


# ── Strategy class ─────────────────────────────────────────────────────────────


class WheelStrategy:
    """
    Implements the BTC options wheel premium-collection strategy.

    Flow:
        1. calculate_iv_rank()  → is now a good time to sell?
        2. decide_cycle()       → sell put or call?
        3. select_strike()      → which strike to target?
        4. generate_signal()    → combine all into an OpenSignal (or None)
    """

    def __init__(self, rest_client: DeribitPublicREST) -> None:
        self._rest = rest_client
        self._current_cycle: Cycle = cfg.strategy.initial_cycle
        # Phase 2: track whether the current put leg has completed (OTM or ITM expiry).
        # Set to True after ANY put expiry so the call leg can fire.
        # Reset to False after any call expiry so the next cycle starts with a put.
        # Defaults to False so the bot always starts by selling puts.
        _put_cycle_complete: bool = False
        self._put_cycle_complete: bool = _put_cycle_complete

    # ── IV rank ───────────────────────────────────────────────────────────────

    def calculate_iv_rank(self, iv_history: list[tuple[int, float]]) -> float:
        """
        Calculate IV rank (0–1) over a 52-week rolling window.

        IV rank = (current_iv - 52w_low) / (52w_high - 52w_low)

        Args:
            iv_history: List of (timestamp_ms, iv_value) from Deribit
                        historical_volatility endpoint.

        Returns:
            IV rank as a float in [0, 1], or 0.0 if insufficient data.
        """
        if len(iv_history) < 2:
            logger.warning("Insufficient IV history for rank calculation")
            return 0.0

        # Take last 365 data points (daily data = ~1 year)
        recent = iv_history[-365:]
        values = [v for _, v in recent]
        current_iv = values[-1]
        low_52w = min(values)
        high_52w = max(values)

        if high_52w == low_52w:
            return 0.5  # Flat IV — no signal either way

        rank = (current_iv - low_52w) / (high_52w - low_52w)
        logger.debug(f"IV rank: {rank:.2%} (current={current_iv:.1f}, "
                     f"low={low_52w:.1f}, high={high_52w:.1f})")
        return round(float(np.clip(rank, 0.0, 1.0)), 4)

        # ML_HOOK: Replace or augment IV rank with a trained classifier:
        # from ml_model import IVRankPredictor
        # predictor = IVRankPredictor.load("models/iv_rank_model.pkl")
        # return predictor.predict(iv_history, spot_price, term_structure)

    # ── Cycle decision ────────────────────────────────────────────────────────

    def decide_cycle(self, last_cycle: Cycle | None = None) -> Cycle:
        """
        Determine whether to sell a put or call for the next leg.

        Default: strictly alternate put → call → put → ...
        Override: pass last_cycle explicitly to force alternation.

        # ML_HOOK: could use skew signal or short-term trend predictor
        # to choose put vs call based on market regime.
        """
        if last_cycle is None:
            last_cycle = self._current_cycle

        next_cycle: Cycle = "call" if last_cycle == "put" else "put"
        self._current_cycle = next_cycle
        logger.debug(f"Cycle decision: {last_cycle} → {next_cycle}")
        return next_cycle

    # ── Strike selection ──────────────────────────────────────────────────────

    def select_strike(
        self,
        instruments: list[Instrument],
        tickers: dict[str, Ticker],
        cycle: Cycle,
        underlying_price: float,
        iv_rank: float = 0.5,
        delta_target_override: float | None = None,
    ) -> StrikeCandidate | None:
        """
        Select the best-scoring option strike within the target delta range.

        Scoring:
          - Primary: delta closest to dynamic target midpoint
          - Secondary: highest mark_iv (more premium)
          - Filter: DTE within [min_dte, max_dte]
          - Filter: option_type == cycle
          - Filter: has a live ticker with valid delta/price

        Dynamic delta (when iv_dynamic_delta=True):
          The target midpoint shifts linearly with IV rank.
          Low IV rank → conservative (toward target_delta_min, sell far OTM).
          High IV rank → aggressive (toward target_delta_max, sell closer ATM
          to capture more premium when volatility is expensive).

        Args:
            instruments:      Full list of active instruments.
            tickers:          Dict of instrument_name → Ticker.
            cycle:            "put" or "call".
            underlying_price: Current BTC spot price.
            iv_rank:          Current IV rank [0–1] for dynamic delta scaling.

        Returns:
            StrikeCandidate or None if no qualifying strikes found.
        """
        d_min = cfg.strategy.target_delta_min
        d_max = cfg.strategy.target_delta_max

        if delta_target_override is not None:
            # Explicit delta target — used by strike laddering to place each
            # ladder leg at a precise position within the delta range.
            target_delta_mid = float(np.clip(delta_target_override, d_min, d_max))
        elif cfg.strategy.iv_dynamic_delta:
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
            target_delta_mid = (d_min + d_max) / 2.0

        candidates: list[StrikeCandidate] = []

        for inst in instruments:
            # Filter: option type must match cycle
            if inst.option_type != cycle:
                continue

            # Filter: DTE must be in range
            if not (cfg.strategy.min_dte <= inst.dte <= cfg.strategy.max_dte):
                continue

            # Filter: must have a live ticker
            ticker = tickers.get(inst.instrument_name)
            if ticker is None or ticker.mark_price <= 0:
                continue

            # Filter: delta must be in target range (use absolute value)
            delta_abs = abs(ticker.delta)
            if not (cfg.strategy.target_delta_min <= delta_abs <= cfg.strategy.target_delta_max):
                continue

            # Filter: must have a positive bid (liquidity check)
            if ticker.bid <= 0:
                continue

            # Score: penalise distance from target delta, reward IV
            delta_score = 1.0 - abs(delta_abs - target_delta_mid) / target_delta_mid
            iv_score = min(ticker.mark_iv / 100.0, 1.0)   # normalise
            score = 0.7 * delta_score + 0.3 * iv_score

            candidates.append(StrikeCandidate(
                instrument=inst,
                ticker=ticker,
                score=score,
            ))

        if not candidates:
            logger.warning(
                f"No qualifying {cycle} strikes found "
                f"(delta {cfg.strategy.target_delta_min}–{cfg.strategy.target_delta_max}, "
                f"DTE {cfg.strategy.min_dte}–{cfg.strategy.max_dte})"
            )
            return None

        # Sort by score descending; take top liquidity_top_n first
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]

        logger.info(
            f"Selected strike: {best.instrument.instrument_name} | "
            f"delta={best.ticker.delta:.3f} | IV={best.ticker.mark_iv:.1f}% | "
            f"mark={best.ticker.mark_price:.4f} BTC | score={best.score:.3f}"
        )

        # ML_HOOK: Replace score calculation with ML model output:
        # from ml_model import StrikeSelector
        # selector = StrikeSelector.load("models/strike_selector.pkl")
        # candidates = selector.rank(candidates, market_features)
        # best = candidates[0]

        return best

    # ── Strike ladder ─────────────────────────────────────────────────────────

    def select_ladder_strikes(
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

        Returns a list of up to n_legs candidates (may be shorter if fewer
        qualifying strikes exist).
        """
        d_min = cfg.strategy.target_delta_min
        d_max = cfg.strategy.target_delta_max

        # Evenly space n_legs targets within [d_min, d_max]
        targets = [
            d_min + (d_max - d_min) * (k + 1) / (n_legs + 1)
            for k in range(n_legs)
        ]

        results: list[StrikeCandidate] = []
        used_strikes: set[float] = set()

        for target in targets:
            candidate = self.select_strike(
                instruments=instruments,
                tickers=tickers,
                cycle="put",
                underlying_price=underlying_price,
                iv_rank=iv_rank,
                delta_target_override=target,
            )
            if candidate is None:
                continue
            # Avoid duplicate strikes across ladder legs
            if candidate.instrument.strike in used_strikes:
                logger.debug(
                    f"Ladder: skipping duplicate strike {candidate.instrument.strike:,.0f}"
                )
                continue
            used_strikes.add(candidate.instrument.strike)
            results.append(candidate)
            logger.info(
                f"Ladder leg {len(results)}/{n_legs}: "
                f"{candidate.instrument.instrument_name} "
                f"delta_tgt={target:.3f} actual={candidate.ticker.delta:.3f} "
                f"strike={candidate.instrument.strike:,.0f}"
            )

        return results

    # ── Signal generation ─────────────────────────────────────────────────────

    def generate_signal(
        self,
        iv_history: list[tuple[int, float]],
        instruments: list[Instrument],
        tickers: dict[str, Ticker],
        underlying_price: float,
        last_cycle: Cycle | None = None,
    ) -> OpenSignal | None:
        """
        Full strategy pass: IV rank check → cycle → strike → signal.

        Returns OpenSignal if conditions are met, None otherwise.
        """
        # Step 1: IV rank filter
        iv_rank = self.calculate_iv_rank(iv_history)
        if iv_rank < cfg.strategy.iv_rank_threshold:
            logger.info(
                f"IV rank {iv_rank:.2%} below threshold "
                f"{cfg.strategy.iv_rank_threshold:.2%} — skipping"
            )
            return None

        # Step 2: Cycle decision
        cycle = self.decide_cycle(last_cycle)

        # Wheel guard: only sell a call after the put leg has fully completed (OTM or ITM).
        # Cash-settled BTC options on Deribit don't deliver BTC, but we still need the
        # put leg to expire before opening the call leg to avoid running two short legs
        # simultaneously (which doubles exposure).
        if cycle == "call" and not self._put_cycle_complete:
            logger.info(
                "Wheel guard: put leg not yet complete — staying in put-selling mode"
            )
            cycle = "put"
            self._current_cycle = "put"

        # Step 3: Strike selection (pass iv_rank for dynamic delta targeting)
        candidate = self.select_strike(
            instruments, tickers, cycle, underlying_price, iv_rank=iv_rank
        )
        if candidate is None:
            return None

        return OpenSignal(
            instrument_name=candidate.instrument.instrument_name,
            strike=candidate.instrument.strike,
            option_type=cycle,
            expiry_ts=candidate.instrument.expiry_ts,
            dte=candidate.instrument.dte,
            delta=candidate.ticker.delta,
            mark_iv=candidate.ticker.mark_iv,
            mark_price=candidate.ticker.mark_price,
            underlying_price=underlying_price,
            cycle=cycle,
            iv_rank=iv_rank,
        )
