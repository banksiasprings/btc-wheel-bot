"""
rl_agent/rl_strategy.py — PPO model wrapped as a drop-in strategy.

Wraps the trained best_model.zip so bot.py can swap it in place of
WheelStrategy without changing the rest of the trading loop.

Interface mirrors WheelStrategy:
    generate_signal(iv_history, instruments, tickers, underlying_price, last_cycle, **kwargs)
        → OpenSignal | None

Extra methods for bot.py integration:
    update_position_state(positions, equity_usd)
        Call once per tick before generate_signal().
    wants_close() → bool
        True when the model's last action was ACTION_CLOSE (4).

Observation vector (12 features — must match training in env.py):
  0  btc_price_norm       BTC price / 100_000           (clipped 0–2)
  1  iv_rank              IV rank 0–1
  2  iv_current_norm      realised vol / 3.0             (clipped 0–1)
  3  position_type_norm   0=none, 0.5=put, 1.0=call
  4  position_delta       |delta| of open position
  5  position_dte_norm    DTE remaining / 30
  6  position_pnl_norm    unrealised P&L / starting_equity
  7  days_since_trade     days since last trade / 30
  8  momentum_5d          5-day log return               (clipped ±0.5)
  9  momentum_20d         20-day log return              (clipped ±1.0)
  10 realised_vol_10d     10-day ann. realised vol / 3.0
  11 days_to_monthly_norm days to next 30-day mark / 30
"""

from __future__ import annotations

import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

# Lazy imports for heavy deps — keeps startup fast when not used
_PPO = None

# ── Action constants (must match env.py) ──────────────────────────────────────
ACTION_HOLD          = 0
ACTION_SELL_PUT_020  = 1
ACTION_SELL_PUT_025  = 2
ACTION_SELL_CALL_020 = 3
ACTION_CLOSE         = 4

STARTING_EQUITY_DEFAULT = 100_000.0
PRICE_HISTORY_MAXLEN    = 30   # need 20 days of history for momentum


def _load_ppo():
    """Lazy-load stable-baselines3 PPO to avoid import cost on non-RL bots."""
    global _PPO
    if _PPO is None:
        from stable_baselines3 import PPO
        _PPO = PPO
    return _PPO


# ── Minimal strike-finder (no hard delta-range filter) ────────────────────────

def _find_best_strike(
    instruments,
    tickers: dict,
    option_type: str,
    target_delta: float,
    min_dte: int = 4,
    max_dte: int = 14,
):
    """
    Find the instrument with |delta - target_delta| minimised.

    Simpler than WheelStrategy.select_strike(): no hard delta-range gate,
    just find the best available option near the target delta.
    Falls back to a wider DTE window if nothing qualifies in the tight range.
    """
    from strategy import StrikeCandidate

    best: Optional[StrikeCandidate] = None
    best_dist = float("inf")

    for inst in instruments:
        if inst.option_type != option_type:
            continue
        if not (min_dte <= inst.dte <= max_dte):
            continue
        ticker = tickers.get(inst.instrument_name)
        if ticker is None or ticker.mark_price <= 0 or ticker.bid <= 0:
            continue
        delta_abs = abs(ticker.delta)
        dist = abs(delta_abs - target_delta)
        if dist < best_dist:
            best_dist = dist
            best = StrikeCandidate(instrument=inst, ticker=ticker, score=1.0 - dist)

    if best is None:
        # Widen DTE window as fallback
        for inst in instruments:
            if inst.option_type != option_type:
                continue
            if not (1 <= inst.dte <= 30):
                continue
            ticker = tickers.get(inst.instrument_name)
            if ticker is None or ticker.mark_price <= 0:
                continue
            delta_abs = abs(ticker.delta)
            dist = abs(delta_abs - target_delta)
            if dist < best_dist:
                best_dist = dist
                best = StrikeCandidate(instrument=inst, ticker=ticker, score=1.0 - dist)

    return best


# ── Main class ────────────────────────────────────────────────────────────────

class RLStrategy:
    """
    PPO-backed strategy that is a drop-in replacement for WheelStrategy.

    The bot calls:
        strategy.update_position_state(positions, equity_usd)  ← new
        signal = strategy.generate_signal(iv_history, instruments, tickers,
                                          underlying_price, last_cycle)
        if strategy.wants_close(): ...  ← new

    The internal observation vector mirrors BTCOptionsEnv._obs() exactly so
    the trained model sees the same feature distribution it was trained on.
    """

    # Expose same cycle-state flags as WheelStrategy so _tick() doesn't break
    _put_cycle_complete: bool = False

    def __init__(
        self,
        model_path: str,
        starting_equity: float = STARTING_EQUITY_DEFAULT,
    ) -> None:
        PPO = _load_ppo()
        self._model = PPO.load(model_path)
        logger.info(f"[RLStrategy] PPO model loaded from {model_path}")

        self._starting_equity  = starting_equity
        self._price_history: deque[float] = deque(maxlen=PRICE_HISTORY_MAXLEN)
        self._days_since_trade: int = 0
        self._tick_count: int = 0  # proxy for "day index" in obs

        # Position state — updated by update_position_state() each tick
        self._pos_type: int    = 0   # 0=none, 1=put, 2=call
        self._pos_delta: float = 0.0
        self._pos_dte: int     = 0
        self._pos_pnl: float   = 0.0
        self._equity_usd: float = starting_equity

        # Action state
        self._last_action: int  = ACTION_HOLD
        self._wants_close_flag: bool = False

    # ── Position state injection ──────────────────────────────────────────────

    def update_position_state(self, positions: list, equity_usd: float) -> None:
        """
        Inject current bot position context before each call to generate_signal().

        positions: list of Position objects from bot.py (may be empty).
        equity_usd: current equity in USD.
        """
        self._equity_usd = equity_usd

        if not positions:
            self._pos_type  = 0
            self._pos_delta = 0.0
            self._pos_dte   = 0
            self._pos_pnl   = 0.0
            return

        pos = positions[0]

        # option_type is "put" or "call"
        self._pos_type = 1 if pos.option_type == "put" else 2

        self._pos_delta = float(getattr(pos, "current_delta", 0.0))

        # DTE from expiry_ts if available
        if getattr(pos, "expiry_ts", 0):
            self._pos_dte = max(0, int((pos.expiry_ts / 1000 - time.time()) / 86_400))
        else:
            self._pos_dte = 0

        # Unrealised P&L: (entry_price - current_price) * contracts * spot
        # We don't have spot here, but pnl_norm is divided by starting_equity anyway.
        # Use a rough USD estimate: pnl_btc * 100_000 as normalisation proxy.
        entry  = float(getattr(pos, "entry_price",   0.0))
        current = float(getattr(pos, "current_price", entry))
        contracts = float(getattr(pos, "contracts",  1.0))
        pnl_btc = (entry - current) * contracts
        # Use equity_usd as a proxy for rough USD scaling (avoids needing spot)
        rough_spot = equity_usd / max(1.0, contracts * entry * 10) if entry > 0 else 50_000.0
        self._pos_pnl = pnl_btc * rough_spot

    # ── Observation builder ───────────────────────────────────────────────────

    def _build_obs(
        self,
        underlying_price: float,
        iv_rank: float,
        realised_vol: float,
    ) -> np.ndarray:
        """Build the 12-feature normalised observation vector."""
        S = underlying_price

        # Feature 0: price normalised
        btc_norm = float(np.clip(S / 100_000.0, 0.0, 2.0))

        # Feature 1-2: IV
        ivr_norm = float(np.clip(iv_rank, 0.0, 1.0))
        rv_norm  = float(np.clip(realised_vol / 3.0, 0.0, 1.0))

        # Features 3-6: position
        pos_type_norm  = self._pos_type / 2.0
        pos_delta_norm = float(np.clip(self._pos_delta, 0.0, 1.0))
        pos_dte_norm   = float(np.clip(self._pos_dte / 30.0, 0.0, 1.0))
        pos_pnl_norm   = float(np.clip(
            self._pos_pnl / max(self._starting_equity, 1.0), -1.0, 1.0
        ))

        # Feature 7: days since trade
        days_norm = float(np.clip(self._days_since_trade / 30.0, 0.0, 1.0))

        # Features 8-9: momentum (requires at least 5 / 20 prices in buffer)
        prices = list(self._price_history)
        n = len(prices)

        mom5 = 0.0
        if n >= 5 and prices[-5] > 0:
            mom5 = float(np.clip(math.log(S / prices[-5]), -0.5, 0.5))

        mom20 = 0.0
        if n >= 20 and prices[-20] > 0:
            mom20 = float(np.clip(math.log(S / prices[-20]), -1.0, 1.0))

        # Feature 10: realised vol (same as rv_norm above, just alias)
        rv10 = rv_norm

        # Feature 11: days to next 30-day cycle mark
        day_idx = max(self._tick_count, 1)
        days_to_monthly = 30 - (day_idx % 30)
        dte_monthly_norm = float(np.clip(days_to_monthly / 30.0, 0.0, 1.0))

        obs = np.array(
            [
                btc_norm,        # 0
                ivr_norm,        # 1
                rv_norm,         # 2
                pos_type_norm,   # 3
                pos_delta_norm,  # 4
                pos_dte_norm,    # 5
                pos_pnl_norm,    # 6
                days_norm,       # 7
                mom5,            # 8
                mom20,           # 9
                rv10,            # 10
                dte_monthly_norm,# 11
            ],
            dtype=np.float32,
        )
        return obs

    # ── IV helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_iv_rank(iv_history: list) -> float:
        if len(iv_history) < 2:
            return 0.5
        recent = iv_history[-365:]
        values = [v for _, v in recent]
        current_iv = values[-1]
        lo, hi = min(values), max(values)
        if hi == lo:
            return 0.5
        return float(np.clip((current_iv - lo) / (hi - lo), 0.0, 1.0))

    @staticmethod
    def _compute_realised_vol(iv_history: list) -> float:
        """Return the latest IV value from Deribit as a proxy for realised vol."""
        if not iv_history:
            return 0.80
        # iv_history is list of (timestamp_ms, iv_value) — iv already annualised %
        # Convert from percent to fraction (e.g. 80 → 0.80)
        return float(iv_history[-1][1]) / 100.0 if iv_history[-1][1] > 1 else float(iv_history[-1][1])

    # ── Public strategy interface ─────────────────────────────────────────────

    def wants_close(self) -> bool:
        """True if the most recent model action was ACTION_CLOSE."""
        flag = self._wants_close_flag
        self._wants_close_flag = False  # consume once
        return flag

    def generate_signal(
        self,
        iv_history: list,
        instruments: list,
        tickers: dict,
        underlying_price: float,
        last_cycle=None,
        **kwargs,
    ):
        """
        Run the PPO model and return an OpenSignal (or None).

        Compatible with WheelStrategy.generate_signal() signature.
        Additional position context should be injected via
        update_position_state() before this is called.
        """
        from strategy import OpenSignal

        # Update internal price buffer and tick counter
        self._price_history.append(underlying_price)
        self._tick_count += 1

        # Build IV features
        iv_rank      = self._compute_iv_rank(iv_history)
        realised_vol = self._compute_realised_vol(iv_history)

        # Build observation vector
        obs = self._build_obs(underlying_price, iv_rank, realised_vol)

        # Run model — bypass numpy bridge (torch 2.2.x + numpy 2.x incompatibility).
        # torch.FloatTensor(list) constructs from Python scalars, not numpy arrays,
        # so it never calls the broken torch.from_numpy() path.
        try:
            import torch as _th
            _obs_tensor = _th.FloatTensor(obs.tolist()).unsqueeze(0)
            self._model.policy.set_training_mode(False)
            with _th.no_grad():
                _actions, _, _ = self._model.policy.forward(
                    _obs_tensor, deterministic=True
                )
            action = int(_actions.squeeze().item())
        except Exception as _e:
            logger.warning(f"[RLStrategy] policy.forward() failed ({_e}), fallback predict")
            action_array, _ = self._model.predict(obs, deterministic=True)
            action = int(action_array)
        self._last_action = action

        logger.info(
            f"[RLStrategy] obs=[price_norm={obs[0]:.3f} ivr={obs[1]:.2f} "
            f"pos_type={obs[3]:.1f} pos_dte={obs[5]:.2f}] → action={action}"
        )

        # ── Action dispatch ───────────────────────────────────────────────────

        if action == ACTION_HOLD:
            self._days_since_trade += 1
            return None

        if action == ACTION_CLOSE:
            if self._pos_type != 0:
                logger.info("[RLStrategy] ACTION_CLOSE → signalling position close")
                self._wants_close_flag = True
            else:
                logger.debug("[RLStrategy] ACTION_CLOSE with no open position — treating as HOLD")
            self._days_since_trade += 1
            return None

        # Actions 1–3: open a new position
        if self._pos_type != 0:
            # Already have a position — model shouldn't open another
            logger.debug(f"[RLStrategy] action={action} but position open — holding")
            self._days_since_trade += 1
            return None

        if action in (ACTION_SELL_PUT_020, ACTION_SELL_PUT_025):
            target_delta = 0.20 if action == ACTION_SELL_PUT_020 else 0.25
            option_type  = "put"
        elif action == ACTION_SELL_CALL_020:
            target_delta = 0.20
            option_type  = "call"
        else:
            logger.warning(f"[RLStrategy] Unknown action {action} — treating as HOLD")
            self._days_since_trade += 1
            return None

        # Find best matching instrument
        candidate = _find_best_strike(
            instruments=instruments,
            tickers=tickers,
            option_type=option_type,
            target_delta=target_delta,
            min_dte=4,
            max_dte=14,
        )

        if candidate is None:
            logger.warning(
                f"[RLStrategy] No {option_type} strike near delta={target_delta:.2f} "
                f"— treating as HOLD"
            )
            self._days_since_trade += 1
            return None

        self._days_since_trade = 0
        logger.info(
            f"[RLStrategy] action={action} → SELL_{option_type.upper()} "
            f"{candidate.instrument.instrument_name} "
            f"delta={candidate.ticker.delta:.3f} mark={candidate.ticker.mark_price:.4f} BTC"
        )

        return OpenSignal(
            instrument_name=candidate.instrument.instrument_name,
            strike=candidate.instrument.strike,
            option_type=option_type,
            expiry_ts=candidate.instrument.expiry_ts,
            dte=candidate.instrument.dte,
            delta=candidate.ticker.delta,
            mark_iv=candidate.ticker.mark_iv,
            mark_price=candidate.ticker.mark_price,
            underlying_price=underlying_price,
            cycle=option_type,
            iv_rank=iv_rank,
        )

    # ── WheelStrategy compat stubs ────────────────────────────────────────────

    def calculate_iv_rank(self, iv_history: list) -> float:
        return self._compute_iv_rank(iv_history)
