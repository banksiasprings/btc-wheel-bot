"""
BTCOptionsEnv — Phase 2 Gymnasium-compatible training environment.

Design
------
Step granularity:  1 hour (matches the data loader's index)
Episode length:    720 steps (30 days)
Observation:       Box(~125 features, normalised to roughly [-3, 3])
Action:            MultiDiscrete([num_action_types, n_strikes, n_dtes, n_sizes])
                   The strike/dte/size legs are ignored for non-options actions.
Reward:            risk-adjusted equity delta minus cost/drawdown/delta penalties.

The env carries a `data` (pandas DataFrame from data_loader.load_feature_matrix)
and an internal `Portfolio` of OptionLeg + spot position. At each step the
agent picks an action; if it's an option open, we synthesise a leg with the
target delta + DTE + size at the prevailing DVOL, then advance one hour and
remark everything. Closed legs realise their P&L in cash.

This is deliberately self-contained: it does not depend on rl_agent/env.py and
does not touch the live bot's risk_manager.py. The reward and constraints are
configured by the @dataclass `EnvConfig` for easy tuning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import gymnasium as gym
from gymnasium import spaces
from scipy.stats import norm

from environment.data_loader import load_feature_matrix
from environment.pricer import (
    OptionLeg,
    bs_greeks,
    bs_price,
    implied_vol_from_dvol,
    portfolio_greeks,
    years_to_expiry,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


# Action type ids — keep numeric so MultiDiscrete works directly.
ACT_DO_NOTHING = 0
ACT_SELL_PUT = 1
ACT_SELL_CALL = 2
ACT_BUY_PUT = 3
ACT_BUY_CALL = 4
ACT_BUY_SPOT = 5
ACT_SELL_SPOT = 6
ACT_ROLL_POSITION = 7
ACT_CLOSE_POSITION = 8
NUM_ACTION_TYPES = 9

STRIKE_DELTA_BUCKETS = (0.10, 0.20, 0.30, 0.40, 0.50)   # target |delta|
DTE_BUCKETS = (7, 14, 30, 60)                            # days to expiry
SIZE_BUCKETS = (0.05, 0.10, 0.15, 0.20)                  # fraction of equity

OBS_DIM = 125  # padded if features < dim; clipped if more.


@dataclass
class EnvConfig:
    starting_equity_usd: float = 100_000.0
    episode_length_hours: int = 24 * 30
    max_open_legs: int = 10
    max_margin_util: float = 0.80
    max_loss_per_leg_usd: float = 25_000.0     # also expressed as fraction below
    max_loss_per_leg_frac: float = 0.25         # of starting equity
    max_episode_drawdown: float = 0.50          # episode ends at 50% peak->trough
    risk_free_rate: float = 0.00
    commission_rate: float = 0.0003             # 3 bps, Deribit taker
    # Reward shaping weights — tune to taste.
    w_return: float = 1.0
    w_drawdown: float = 5.0
    w_delta_exposure: float = 0.1
    w_max_loss_breach: float = 5.0
    w_commission: float = 1.0
    pnl_norm_scale: float = 1_000.0             # divide $ delta by this for reward
    # Episode sampling
    random_start: bool = True
    seed: Optional[int] = None


@dataclass
class SpotPosition:
    qty_btc: float = 0.0        # signed: long positive, short negative
    avg_entry_price: float = 0.0


# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------


class BTCOptionsEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(
        self,
        data: Optional[pd.DataFrame] = None,
        config: Optional[EnvConfig] = None,
        data_path_override: Optional[str] = None,
    ):
        super().__init__()
        self.config = config or EnvConfig()
        if data is None:
            data = load_feature_matrix()
        # We need consecutive valid rows. Build a list of valid timestamp ints
        # so episode sampling is deterministic.
        self.data = data.copy()
        self.valid_mask = self.data["valid"].to_numpy()
        self._valid_indices = np.flatnonzero(self.valid_mask)
        if len(self._valid_indices) < self.config.episode_length_hours + 2:
            raise ValueError(
                f"Not enough valid rows ({len(self._valid_indices)}) for an "
                f"episode of {self.config.episode_length_hours} hours."
            )
        self._feature_columns = self._select_feature_columns()
        self._rng = np.random.default_rng(self.config.seed)

        # Spaces
        self.action_space = spaces.MultiDiscrete(
            [NUM_ACTION_TYPES, len(STRIKE_DELTA_BUCKETS), len(DTE_BUCKETS), len(SIZE_BUCKETS)]
        )
        self.observation_space = spaces.Box(
            low=-10.0, high=10.0, shape=(OBS_DIM,), dtype=np.float32
        )

        # Episode state (populated in reset)
        self._reset_episode_state()

    # -------------------- public API --------------------

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        super().reset(seed=seed)
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._reset_episode_state()
        # Sample a start index from valid rows such that the full episode fits.
        max_start_pos = len(self._valid_indices) - self.config.episode_length_hours - 1
        if self.config.random_start:
            start_pos = int(self._rng.integers(0, max_start_pos))
        else:
            start_pos = 0
        self._start_row = int(self._valid_indices[start_pos])
        self._cursor = self._start_row
        self._step_count = 0
        self._equity_history = [self.config.starting_equity_usd]
        self._peak_equity = self.config.starting_equity_usd
        self._cash_usd = self.config.starting_equity_usd
        obs = self._build_obs()
        info = self._build_info()
        return obs, info

    def step(self, action):
        action = np.asarray(action, dtype=np.int64).flatten()
        if action.shape[0] < 4:
            # Pad with zeros — useful for sanity tests that pass discrete-only
            action = np.concatenate([action, np.zeros(4 - action.shape[0], dtype=np.int64)])
        act_type, strike_idx, dte_idx, size_idx = action[:4]

        # 1) execute action (incurs commission) using state @ cursor
        commission_paid = self._execute_action(int(act_type), int(strike_idx), int(dte_idx), int(size_idx))

        # 2) advance time 1h
        self._cursor += 1
        self._step_count += 1

        # 3) compute new equity at new state
        equity_before = self._equity_history[-1]
        equity_now = self._mark_to_market()
        pnl_step = equity_now - equity_before
        self._equity_history.append(equity_now)
        if equity_now > self._peak_equity:
            self._peak_equity = equity_now

        # 4) reward
        cfg = self.config
        ret_term = pnl_step / cfg.pnl_norm_scale
        # Sortino-ish kick: amplify down steps.
        if pnl_step < 0:
            ret_term *= 1.5

        # Drawdown penalty fires once episode dd exceeds 30%.
        dd_now = (self._peak_equity - equity_now) / max(self._peak_equity, 1.0)
        dd_pen = max(0.0, dd_now - 0.30) * cfg.w_drawdown

        # Delta exposure penalty during high vol regimes.
        port = self._portfolio_state()
        delta_pen = abs(port["delta_btc_equiv"]) * cfg.w_delta_exposure

        # Per-leg max-loss breach: if any open leg lost more than the cap.
        max_loss_breach = 0.0
        cap = min(cfg.max_loss_per_leg_usd, cfg.starting_equity_usd * cfg.max_loss_per_leg_frac)
        for leg, mark in zip(self._option_legs, self._latest_leg_marks):
            if mark["unrealized_pnl"] < -cap:
                max_loss_breach += (-mark["unrealized_pnl"] - cap) / max(cap, 1.0)

        reward = (
            cfg.w_return * ret_term
            - dd_pen
            - delta_pen
            - cfg.w_max_loss_breach * max_loss_breach
            - cfg.w_commission * (commission_paid / cfg.pnl_norm_scale)
        )

        # 5) termination conditions
        terminated = False
        truncated = False
        if self._step_count >= cfg.episode_length_hours:
            truncated = True
        if equity_now < cfg.starting_equity_usd * (1.0 - cfg.max_episode_drawdown):
            terminated = True
            reward -= 10.0  # bigger one-time penalty for blowing up
        # margin breach
        margin = self._estimate_total_margin()
        if margin > equity_now * 1.05:  # leeway since this is approximate
            terminated = True
            reward -= 10.0
        # ran off the end of data
        if self._cursor >= len(self.data) - 1:
            truncated = True

        obs = self._build_obs() if not (terminated or truncated) else self._build_obs(safe=True)
        info = self._build_info()
        info["pnl_step"] = pnl_step
        info["commission_paid"] = commission_paid
        info["equity"] = equity_now
        info["drawdown"] = dd_now
        info["margin_util"] = margin / max(equity_now, 1.0)

        return obs, float(reward), bool(terminated), bool(truncated), info

    def render(self):
        return None

    # -------------------- internal --------------------

    def _reset_episode_state(self):
        self._option_legs: list[OptionLeg] = []
        self._latest_leg_marks: list[dict] = []
        self._spot = SpotPosition()
        self._cash_usd = self.config.starting_equity_usd
        self._step_count = 0
        self._start_row = 0
        self._cursor = 0
        self._equity_history = [self.config.starting_equity_usd]
        self._peak_equity = self.config.starting_equity_usd

    def _select_feature_columns(self) -> list[str]:
        """Pick the columns we feed into the obs. The portfolio-state slice is
        appended dynamically in _build_obs()."""
        candidates = [
            "log_price", "ret_1h", "ret_4h", "ret_24h", "ret_7d",
            "rv_7d", "rv_30d", "rv_90d",
            "dvol", "dvol_z", "iv_ratio_short_long",
            "funding_1h", "funding_z",
            "fear_greed_norm",
            "mvrv", "mvrv_z",
            "exchange_netflow_z", "hash_rate_z", "active_addr_z",
            "price_52w_pct", "drawdown_from_ath_pct", "recent_drawdown_30d",
            "hour_sin", "hour_cos", "dow_sin", "dow_cos",
        ]
        return [c for c in candidates if c in self.data.columns]

    def _row(self) -> pd.Series:
        return self.data.iloc[self._cursor]

    def _now_ts(self) -> float:
        return float(self.data.index[self._cursor].timestamp())

    def _spot_price(self) -> float:
        row = self._row()
        px = float(row.get("close", float("nan")))
        if not math.isfinite(px) or px <= 0:
            # Fall back to last known price.
            return float(self.data["close"].iloc[: self._cursor + 1].ffill().iloc[-1])
        return px

    def _current_iv(self) -> float:
        row = self._row()
        return implied_vol_from_dvol(float(row.get("dvol", 60.0)))

    def _mark_to_market(self) -> float:
        spot = self._spot_price()
        iv = self._current_iv()
        now_ts = self._now_ts()
        # Spot leg
        spot_pnl = self._spot.qty_btc * (spot - self._spot.avg_entry_price)
        # Option legs — keep marks in sync so reward fn can re-read them.
        self._latest_leg_marks = [leg.mark(spot, now_ts, iv, self.config.risk_free_rate) for leg in self._option_legs]
        opt_pnl = sum(m["unrealized_pnl"] for m in self._latest_leg_marks)
        equity = self._cash_usd + spot_pnl + opt_pnl
        # Avoid -inf/nan equity if pricer hiccups
        if not math.isfinite(equity):
            equity = self._cash_usd
        return float(equity)

    def _portfolio_state(self) -> dict:
        spot = self._spot_price()
        iv = self._current_iv()
        now_ts = self._now_ts()
        port = portfolio_greeks(self._option_legs, spot, now_ts, iv, self.config.risk_free_rate)
        # Add the spot leg's delta in BTC.
        port["delta_btc_equiv"] = port["delta"] / max(spot, 1.0) + self._spot.qty_btc
        port["unrealized_pnl_total"] = port["unrealized_pnl"] + self._spot.qty_btc * (spot - self._spot.avg_entry_price)
        return port

    def _estimate_total_margin(self) -> float:
        """Rough portfolio-margin: 25% × spot per BTC equivalent of short option
        exposure + intrinsic, plus 10% × spot per BTC of short spot. Long
        options/spot use no margin."""
        spot = self._spot_price()
        margin = 0.0
        for leg in self._option_legs:
            if leg.qty >= 0:
                continue  # longs only cost their premium (already in cash)
            notional = abs(leg.qty) * leg.contract_size_btc * spot
            intrinsic = (
                max(0.0, leg.strike - spot) if leg.option_type == "put" else max(0.0, spot - leg.strike)
            )
            margin += 0.25 * notional + intrinsic * abs(leg.qty) * leg.contract_size_btc
        if self._spot.qty_btc < 0:
            margin += abs(self._spot.qty_btc) * spot * 0.10
        return margin

    # ----------- action execution -----------

    def _execute_action(self, act_type: int, strike_idx: int, dte_idx: int, size_idx: int) -> float:
        """Apply the action to the portfolio, returning USD commission paid."""
        if act_type == ACT_DO_NOTHING:
            return 0.0
        spot = self._spot_price()
        iv = self._current_iv()
        now_ts = self._now_ts()
        equity_est = self._mark_to_market()

        size_frac = SIZE_BUCKETS[size_idx % len(SIZE_BUCKETS)]
        dte_days = DTE_BUCKETS[dte_idx % len(DTE_BUCKETS)]
        target_delta = STRIKE_DELTA_BUCKETS[strike_idx % len(STRIKE_DELTA_BUCKETS)]

        commission = 0.0

        if act_type in (ACT_SELL_PUT, ACT_SELL_CALL, ACT_BUY_PUT, ACT_BUY_CALL):
            if len(self._option_legs) >= self.config.max_open_legs:
                return 0.0  # silently no-op when over cap
            opt_type = "put" if act_type in (ACT_SELL_PUT, ACT_BUY_PUT) else "call"
            is_short = act_type in (ACT_SELL_PUT, ACT_SELL_CALL)

            K = self._strike_for_target_delta(spot, iv, dte_days, target_delta, opt_type)
            T = max(dte_days / 365.25, 1.0 / 365.25)
            price_per_contract = bs_price(spot, K, T, self.config.risk_free_rate, iv, opt_type)
            if price_per_contract <= 0 or not math.isfinite(price_per_contract):
                return 0.0

            # Size: contracts = (equity * size_frac) / (price for longs, spot for shorts as margin proxy)
            if is_short:
                # collateral_per_contract ≈ 0.25 * spot
                contracts = max(0.0, (equity_est * size_frac) / (0.25 * spot))
            else:
                contracts = max(0.0, (equity_est * size_frac) / price_per_contract)
            contracts = float(min(contracts, equity_est / max(spot, 1.0)))  # absolute cap
            if contracts < 1e-6:
                return 0.0

            qty = -contracts if is_short else contracts
            expiry_ts = now_ts + dte_days * 86400.0
            leg = OptionLeg(
                option_type=opt_type,
                strike=K,
                expiry_ts=expiry_ts,
                qty=qty,
                entry_price=price_per_contract,
                entry_ts=now_ts,
                entry_iv=iv,
            )
            # Cash flow: long pays premium, short collects premium.
            cash_flow = -leg.entry_price * qty  # qty positive = pay, negative = receive
            self._cash_usd += cash_flow
            commission = self.config.commission_rate * abs(qty) * leg.contract_size_btc * spot
            self._cash_usd -= commission
            self._option_legs.append(leg)
            return commission

        if act_type == ACT_BUY_SPOT:
            usd_amount = equity_est * size_frac
            qty = usd_amount / spot
            if qty < 1e-6:
                return 0.0
            new_total = self._spot.qty_btc + qty
            if new_total != 0:
                self._spot.avg_entry_price = (
                    self._spot.qty_btc * self._spot.avg_entry_price + qty * spot
                ) / new_total
            self._spot.qty_btc = new_total
            self._cash_usd -= qty * spot
            commission = self.config.commission_rate * qty * spot
            self._cash_usd -= commission
            return commission

        if act_type == ACT_SELL_SPOT:
            usd_amount = equity_est * size_frac
            qty = usd_amount / spot
            if qty < 1e-6:
                return 0.0
            new_total = self._spot.qty_btc - qty
            self._spot.qty_btc = new_total
            self._cash_usd += qty * spot
            commission = self.config.commission_rate * qty * spot
            self._cash_usd -= commission
            return commission

        if act_type == ACT_CLOSE_POSITION:
            if not self._option_legs:
                return 0.0
            # Close the leg with the largest absolute current delta.
            marks = [leg.mark(spot, now_ts, iv, self.config.risk_free_rate) for leg in self._option_legs]
            i = int(np.argmax([abs(m["delta"]) for m in marks]))
            commission = self._close_leg(i, spot, now_ts, iv)
            return commission

        if act_type == ACT_ROLL_POSITION:
            if not self._option_legs:
                return 0.0
            # Close oldest leg, open a fresh one with the chosen params if it
            # was a short option.
            old_leg = self._option_legs[0]
            commission = self._close_leg(0, spot, now_ts, iv)
            # Open a new leg in the same direction.
            sub_act = ACT_SELL_PUT if old_leg.option_type == "put" and old_leg.qty < 0 else \
                      ACT_SELL_CALL if old_leg.option_type == "call" and old_leg.qty < 0 else \
                      ACT_BUY_PUT if old_leg.option_type == "put" else ACT_BUY_CALL
            commission += self._execute_action(sub_act, strike_idx, dte_idx, size_idx)
            return commission

        return 0.0

    def _close_leg(self, idx: int, spot: float, now_ts: float, iv: float) -> float:
        leg = self._option_legs[idx]
        m = leg.mark(spot, now_ts, iv, self.config.risk_free_rate)
        # Realise: pay/receive the current price.
        # If we're long (qty>0), selling to close gives us +price*qty cash.
        # If we're short (qty<0), buying to close costs us +price*|qty| cash.
        cash_flow = m["price"] * leg.qty  # qty positive → receive; negative → pay
        self._cash_usd += cash_flow
        commission = self.config.commission_rate * abs(leg.qty) * leg.contract_size_btc * spot
        self._cash_usd -= commission
        del self._option_legs[idx]
        return commission

    def _strike_for_target_delta(
        self,
        spot: float,
        iv: float,
        dte_days: int,
        target_abs_delta: float,
        opt_type: str,
    ) -> float:
        """Closed-form strike that produces |delta| = target.

        For a call:  delta = N(d1) = target  ⇒  d1 = Φ⁻¹(target)
        For a put:   delta = N(d1) - 1 = -target  ⇒  N(d1) = 1 - target
                     ⇒  d1 = Φ⁻¹(1 - target)
        Then K = S * exp(0.5*σ²*T - r*T - d1*σ*√T).
        """
        T = max(dte_days / 365.25, 1.0 / 365.25)
        sigma = max(iv, 0.05)
        r = self.config.risk_free_rate
        sqrtT = math.sqrt(T)
        if opt_type == "call":
            d1 = norm.ppf(np.clip(target_abs_delta, 0.01, 0.99))
        else:
            d1 = norm.ppf(np.clip(1.0 - target_abs_delta, 0.01, 0.99))
        K = spot * math.exp(0.5 * sigma * sigma * T - r * T - d1 * sigma * sqrtT)
        return float(max(K, 1.0))

    # ----------- observation -----------

    def _build_obs(self, safe: bool = False) -> np.ndarray:
        """Construct the obs vector. `safe=True` is used after terminal state
        to avoid reading past the end of data."""
        cursor = min(self._cursor, len(self.data) - 1)
        row = self.data.iloc[cursor]
        feats: list[float] = []
        for col in self._feature_columns:
            v = row.get(col, 0.0)
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                v = 0.0
            feats.append(float(v))

        # Append portfolio-state features.
        port = self._portfolio_state()
        equity_now = self._equity_history[-1]
        spot = self._spot_price()
        eq0 = max(self.config.starting_equity_usd, 1.0)
        port_feats = [
            equity_now / eq0 - 1.0,                       # equity drawdown vs start
            self._cash_usd / eq0,                          # cash fraction
            port["delta_btc_equiv"],                       # portfolio delta in BTC
            port["gamma"] * spot / eq0,                    # gamma normalised
            port["theta_per_day"] / eq0,                   # theta normalised
            port["vega_per_volpoint"] / eq0,               # vega normalised
            port["unrealized_pnl_total"] / eq0,
            port["num_legs"] / 10.0,
            port["num_short"] / 10.0,
            port["num_long"] / 10.0,
            port["avg_dte"] / 60.0,
            self._spot.qty_btc,
            (self._spot.qty_btc * spot) / eq0,
            self._estimate_total_margin() / eq0,
            (equity_now - self._peak_equity) / eq0,        # current drawdown (≤ 0)
            min(self._step_count / max(self.config.episode_length_hours, 1), 2.0),
            len(self._option_legs) / 10.0,
        ]
        feats.extend(port_feats)

        # Pad / clip to OBS_DIM.
        if len(feats) < OBS_DIM:
            feats.extend([0.0] * (OBS_DIM - len(feats)))
        elif len(feats) > OBS_DIM:
            feats = feats[:OBS_DIM]

        arr = np.asarray(feats, dtype=np.float32)
        arr = np.clip(arr, -10.0, 10.0)
        # last-resort safety: replace any non-finite leftover
        if not np.all(np.isfinite(arr)):
            arr = np.nan_to_num(arr, nan=0.0, posinf=10.0, neginf=-10.0)
        return arr

    def _build_info(self) -> dict:
        return {
            "ts": str(self.data.index[min(self._cursor, len(self.data) - 1)]),
            "step": self._step_count,
            "spot": self._spot_price(),
            "iv": self._current_iv(),
            "num_legs": len(self._option_legs),
            "cash_usd": self._cash_usd,
        }


if __name__ == "__main__":
    env = BTCOptionsEnv()
    obs, info = env.reset(seed=0)
    print(f"obs shape: {obs.shape}, action space: {env.action_space}")
    for _ in range(10):
        a = env.action_space.sample()
        obs, r, term, trunc, info = env.step(a)
        print(f"a={a} r={r:.4f} eq={info['equity']:.1f} legs={info['num_legs']} done={term or trunc}")
        if term or trunc:
            break
