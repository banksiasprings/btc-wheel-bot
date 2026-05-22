"""
BTCOptionsEnv — Gymnasium-compatible RL environment for BTC options wheel strategy.

State space (12 features, all normalised):
  0  btc_price_norm       BTC price / 100_000
  1  iv_rank              IV rank 0–1
  2  iv_current_norm      current annualised IV / 3.0  (cap at 300% ann vol)
  3  position_type        0=none, 1=put, 2=call
  4  position_delta       |delta| of open position (0 if none)
  5  position_dte         DTE remaining / 30
  6  position_pnl_norm    unrealised P&L / starting_equity
  7  days_since_trade     days since last trade / 30
  8  momentum_5d          5-day log return (clipped ±0.5)
  9  momentum_20d         20-day log return (clipped ±1.0)
  10 realised_vol_10d     10-day realised vol / 3.0
  11 days_to_expiry_norm  days to next monthly expiry / 30

Actions (Discrete 5):
  0  hold
  1  sell_put_020delta
  2  sell_put_025delta
  3  sell_call_020delta
  4  close_position
"""

import math
import numpy as np
import gymnasium as gym
from gymnasium import spaces
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Black-Scholes helpers
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European put price via Black-Scholes. Returns 0 if T <= 0."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European call price via Black-Scholes. Returns 0 if T <= 0."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put delta (negative). Returns 0 or -1 at expiry."""
    if T <= 0 or sigma <= 0:
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) - 1.0


def bs_call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Call delta (positive)."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1)


def find_put_strike_for_delta(
    S: float, target_delta: float, T: float, r: float, sigma: float
) -> float:
    """Binary search for put strike at target |delta|. target_delta in (0,1)."""
    target_delta = abs(target_delta)
    lo, hi = S * 0.3, S * 1.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        d = abs(bs_put_delta(S, mid, T, r, sigma))
        if d < target_delta:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def find_call_strike_for_delta(
    S: float, target_delta: float, T: float, r: float, sigma: float
) -> float:
    """Binary search for call strike at target delta. target_delta in (0,1)."""
    target_delta = abs(target_delta)
    lo, hi = S * 1.0, S * 2.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        d = bs_call_delta(S, mid, T, r, sigma)
        if d > target_delta:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Data generation / loading
# ---------------------------------------------------------------------------

def generate_synthetic_btc_data(
    n_days: int = 1095,  # 3 years
    starting_price: float = 30_000.0,
    annual_vol: float = 0.80,
    annual_drift: float = 0.15,
    seed: int = 42,
) -> np.ndarray:
    """
    Geometric Brownian Motion synthetic BTC price series.
    Returns array of shape (n_days,) with daily closing prices.
    """
    rng = np.random.default_rng(seed)
    dt = 1.0 / 365.0
    mu = annual_drift - 0.5 * annual_vol ** 2
    log_returns = rng.normal(mu * dt, annual_vol * math.sqrt(dt), size=n_days)
    prices = starting_price * np.exp(np.cumsum(log_returns))
    return prices.astype(np.float64)


def compute_iv_rank(prices: np.ndarray, window: int = 252) -> np.ndarray:
    """
    Compute a simple IV rank proxy using rolling realised vol percentile.
    Returns array same length as prices, values in [0, 1].
    """
    n = len(prices)
    log_rets = np.zeros(n)
    log_rets[1:] = np.log(prices[1:] / prices[:-1])

    # Rolling 10-day realised vol (annualised)
    rv = np.zeros(n)
    for i in range(10, n):
        rv[i] = np.std(log_rets[i - 10 : i]) * math.sqrt(252)

    # IV rank = percentile rank within rolling window
    iv_rank = np.zeros(n)
    for i in range(window, n):
        window_rv = rv[i - window : i]
        current = rv[i]
        mn, mx = window_rv.min(), window_rv.max()
        if mx > mn:
            iv_rank[i] = (current - mn) / (mx - mn)
        else:
            iv_rank[i] = 0.5
    # Fill early values with 0.5
    iv_rank[:window] = 0.5
    return np.clip(iv_rank, 0.0, 1.0)


def load_or_generate_data(data_path: Optional[str] = None) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns (prices, iv_rank) arrays. Tries to load from CSV first,
    falls back to synthetic GBM data.
    """
    if data_path is not None:
        try:
            import pandas as pd
            df = pd.read_csv(data_path, parse_dates=True)
            # Try common column names
            price_col = None
            for col in ["close", "price", "btc_price", "Close", "Price"]:
                if col in df.columns:
                    price_col = col
                    break
            if price_col is None:
                raise ValueError(f"No recognised price column in {data_path}")
            prices = df[price_col].values.astype(np.float64)

            iv_col = None
            for col in ["iv_rank", "ivr", "IV_rank"]:
                if col in df.columns:
                    iv_col = col
                    break
            if iv_col is not None:
                iv_rank = df[iv_col].values.astype(np.float64)
            else:
                iv_rank = compute_iv_rank(prices)
            return prices, iv_rank
        except Exception as e:
            print(f"[BTCOptionsEnv] Could not load {data_path}: {e} — using synthetic data")

    prices = generate_synthetic_btc_data()
    iv_rank = compute_iv_rank(prices)
    return prices, iv_rank


# ---------------------------------------------------------------------------
# Position tracking
# ---------------------------------------------------------------------------

class Position:
    def __init__(
        self,
        pos_type: int,      # 1=put, 2=call
        strike: float,
        premium_received: float,
        dte_at_open: int,
        day_opened: int,
        iv_at_open: float,
    ):
        self.pos_type = pos_type
        self.strike = strike
        self.premium_received = premium_received
        self.dte_at_open = dte_at_open
        self.day_opened = day_opened
        self.iv_at_open = iv_at_open
        self.unrealised_pnl: float = 0.0
        self.dte_remaining: int = dte_at_open

    def update(self, current_day: int, S: float, r: float, sigma: float) -> float:
        """Recompute unrealised P&L and DTE. Returns current mark price."""
        self.dte_remaining = max(0, self.dte_at_open - (current_day - self.day_opened))
        T = self.dte_remaining / 365.0
        if self.pos_type == 1:  # put
            mark = bs_put_price(S, self.strike, T, r, sigma)
        else:  # call
            mark = bs_call_price(S, self.strike, T, r, sigma)
        # We sold the option, so unrealised P&L = premium received - current mark
        self.unrealised_pnl = self.premium_received - mark
        return mark

    def delta(self, S: float, T_years: float, r: float, sigma: float) -> float:
        if self.pos_type == 1:
            return abs(bs_put_delta(S, self.strike, T_years, r, sigma))
        else:
            return abs(bs_call_delta(S, self.strike, T_years, r, sigma))

    def intrinsic_loss(self, S: float) -> float:
        """Intrinsic value at expiry (cost if assigned)."""
        if self.pos_type == 1:
            return max(self.strike - S, 0.0)
        else:
            return max(S - self.strike, 0.0)


# ---------------------------------------------------------------------------
# Main environment
# ---------------------------------------------------------------------------

class BTCOptionsEnv(gym.Env):
    """
    Gymnasium environment for BTC options wheel strategy.

    The agent decides each day whether to hold, open a new position, or close.
    Rewards are risk-adjusted daily P&L.
    """

    metadata = {"render_modes": ["human"]}

    # Action constants
    ACTION_HOLD = 0
    ACTION_SELL_PUT_020 = 1
    ACTION_SELL_PUT_025 = 2
    ACTION_SELL_CALL_020 = 3
    ACTION_CLOSE = 4

    N_ACTIONS = 5
    N_OBS = 12

    RISK_FREE = 0.04
    OPTION_DTE = 7          # weekly options
    CONTRACT_SIZE = 0.1     # BTC per contract
    TRANSACTION_COST = 0.5  # USD per contract
    MAX_DRAWDOWN_PENALTY_THRESHOLD = 0.05

    def __init__(
        self,
        prices: Optional[np.ndarray] = None,
        iv_rank: Optional[np.ndarray] = None,
        data_path: Optional[str] = None,
        starting_equity: float = 100_000.0,
        split: str = "train",   # "train" (first 70%) or "test" (last 30%)
        max_equity_per_leg: float = 0.10,
        seed: Optional[int] = None,
    ):
        super().__init__()

        self.observation_space = spaces.Box(
            low=-np.ones(self.N_OBS, dtype=np.float32),
            high=np.ones(self.N_OBS, dtype=np.float32),
            dtype=np.float32,
        )
        self.action_space = spaces.Discrete(self.N_ACTIONS)

        # Load data
        if prices is not None and iv_rank is not None:
            all_prices, all_iv = prices, iv_rank
        else:
            all_prices, all_iv = load_or_generate_data(data_path)

        # Split
        n = len(all_prices)
        split_idx = int(n * 0.70)
        if split == "train":
            self.prices = all_prices[:split_idx]
            self.iv_rank = all_iv[:split_idx]
        else:
            self.prices = all_prices[split_idx:]
            self.iv_rank = all_iv[split_idx:]

        self.n_days = len(self.prices)
        # Compute rolling realised vol (10-day, annualised)
        log_rets = np.zeros(self.n_days)
        log_rets[1:] = np.log(self.prices[1:] / self.prices[:-1])
        self.log_rets = log_rets

        self.rv10 = np.zeros(self.n_days)
        for i in range(10, self.n_days):
            self.rv10[i] = np.std(log_rets[i - 10 : i]) * math.sqrt(252)
        self.rv10[:10] = self.rv10[10] if self.n_days > 10 else 0.5

        self.starting_equity = starting_equity
        self.max_equity_per_leg = max_equity_per_leg

        self._rng = np.random.default_rng(seed)
        self._day = 0
        self._equity = starting_equity
        self._peak_equity = starting_equity
        self._position: Optional[Position] = None
        self._days_since_trade = 0
        self._realised_pnl_total = 0.0

    # ------------------------------------------------------------------
    # Gym interface
    # ------------------------------------------------------------------

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        # Start at a random point in first 80% of split data to vary episodes
        max_start = max(0, int(self.n_days * 0.80) - 60)
        self._day = int(self._rng.integers(20, max(21, max_start)))
        self._equity = self.starting_equity
        self._peak_equity = self.starting_equity
        self._position = None
        self._days_since_trade = 0
        self._realised_pnl_total = 0.0
        return self._obs(), {}

    def step(self, action: int):
        day = self._day
        S = self.prices[day]
        ivr = self.iv_rank[day]
        sigma = max(self.rv10[day], 0.10)  # floor at 10% ann vol

        reward = 0.0
        terminated = False
        truncated = False
        info = {}

        # ------ Update open position ------
        if self._position is not None:
            self._position.update(day, S, self.RISK_FREE, sigma)
            # Check expiry
            if self._position.dte_remaining <= 0:
                loss = self._position.intrinsic_loss(S)
                realised = self._position.premium_received - loss - self.TRANSACTION_COST
                self._equity += realised
                self._realised_pnl_total += realised
                self._position = None
                self._days_since_trade = 0

        # ------ Execute action ------
        if action == self.ACTION_HOLD:
            pass  # nothing to do

        elif action == self.ACTION_SELL_PUT_020 or action == self.ACTION_SELL_PUT_025:
            if self._position is None:
                target_delta = 0.20 if action == self.ACTION_SELL_PUT_020 else 0.25
                T = self.OPTION_DTE / 365.0
                K = find_put_strike_for_delta(S, target_delta, T, self.RISK_FREE, sigma)
                premium = bs_put_price(S, K, T, self.RISK_FREE, sigma)
                # Size: allocate up to max_equity_per_leg worth of collateral
                collateral_per_contract = K * self.CONTRACT_SIZE
                max_contracts = max(
                    1,
                    int((self._equity * self.max_equity_per_leg) / collateral_per_contract),
                )
                n_contracts = max_contracts
                total_premium = premium * n_contracts * self.CONTRACT_SIZE * 100  # crude scaling
                total_premium = min(total_premium, self._equity * 0.03)  # cap at 3% equity
                total_premium = max(total_premium, 10.0)  # floor
                cost = self.TRANSACTION_COST * n_contracts
                self._position = Position(
                    pos_type=1,
                    strike=K,
                    premium_received=total_premium - cost,
                    dte_at_open=self.OPTION_DTE,
                    day_opened=day,
                    iv_at_open=sigma,
                )
                self._days_since_trade = 0
                info["trade"] = f"SELL_PUT K={K:.0f} delta={target_delta}"

        elif action == self.ACTION_SELL_CALL_020:
            if self._position is None:
                T = self.OPTION_DTE / 365.0
                K = find_call_strike_for_delta(S, 0.20, T, self.RISK_FREE, sigma)
                premium = bs_call_price(S, K, T, self.RISK_FREE, sigma)
                total_premium = premium * self.CONTRACT_SIZE * 100
                total_premium = min(total_premium, self._equity * 0.03)
                total_premium = max(total_premium, 10.0)
                cost = self.TRANSACTION_COST
                self._position = Position(
                    pos_type=2,
                    strike=K,
                    premium_received=total_premium - cost,
                    dte_at_open=self.OPTION_DTE,
                    day_opened=day,
                    iv_at_open=sigma,
                )
                self._days_since_trade = 0
                info["trade"] = f"SELL_CALL K={K:.0f} delta=0.20"

        elif action == self.ACTION_CLOSE:
            if self._position is not None:
                T = max(self._position.dte_remaining / 365.0, 1e-6)
                if self._position.pos_type == 1:
                    cost_to_close = bs_put_price(S, self._position.strike, T, self.RISK_FREE, sigma)
                else:
                    cost_to_close = bs_call_price(S, self._position.strike, T, self.RISK_FREE, sigma)
                realised = (
                    self._position.premium_received
                    - cost_to_close * self.CONTRACT_SIZE * 100
                    - self.TRANSACTION_COST
                )
                self._equity += realised
                self._realised_pnl_total += realised
                self._position = None
                self._days_since_trade = 0
                info["trade"] = "CLOSE"

        # ------ Compute daily P&L for reward ------
        # Mark-to-market equity
        mtm_equity = self._equity
        if self._position is not None:
            mtm_equity += self._position.unrealised_pnl

        daily_pnl_frac = (mtm_equity - self.starting_equity) / self.starting_equity
        if mtm_equity > self._peak_equity:
            self._peak_equity = mtm_equity
        drawdown = (self._peak_equity - mtm_equity) / max(self._peak_equity, 1.0)

        no_position = self._position is None
        self._days_since_trade += 1

        # Reward function
        # Base: fractional daily P&L
        base = daily_pnl_frac / max(self.n_days, 1)  # normalised per-step
        # Drawdown penalty (squared beyond 5%)
        dd_penalty = 0.001 * max(0.0, drawdown - self.MAX_DRAWDOWN_PENALTY_THRESHOLD) ** 2
        # Idle penalty (small, encourages finding trades)
        idle_penalty = 0.0001 if no_position else 0.0
        reward = float(base - dd_penalty - idle_penalty)

        # Advance day
        self._day += 1
        if self._day >= self.n_days - 1:
            terminated = True
            # Final settlement
            if self._position is not None:
                S_final = self.prices[-1]
                loss = self._position.intrinsic_loss(S_final)
                realised = self._position.premium_received - loss - self.TRANSACTION_COST
                self._equity += realised
                self._position = None

        info.update(
            {
                "equity": self._equity,
                "drawdown": drawdown,
                "btc_price": S,
                "iv_rank": ivr,
                "day": day,
            }
        )

        return self._obs(), reward, terminated, truncated, info

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def _obs(self) -> np.ndarray:
        day = min(self._day, self.n_days - 1)
        S = self.prices[day]
        ivr = self.iv_rank[day]
        sigma = max(self.rv10[day], 0.10)

        # price normalised (divide by 100k, clip)
        btc_norm = np.clip(S / 100_000.0, 0.0, 2.0)

        # position features
        pos_type = 0.0
        pos_delta = 0.0
        pos_dte = 0.0
        pos_pnl = 0.0
        if self._position is not None:
            pos_type = float(self._position.pos_type)  # 1 or 2
            T = max(self._position.dte_remaining / 365.0, 1e-6)
            pos_delta = self._position.delta(S, T, self.RISK_FREE, sigma)
            pos_dte = self._position.dte_remaining / 30.0
            pos_pnl = self._position.unrealised_pnl / max(self.starting_equity, 1.0)

        # momentum
        mom5 = 0.0
        mom20 = 0.0
        if day >= 5:
            mom5 = np.clip(np.log(S / self.prices[day - 5]), -0.5, 0.5)
        if day >= 20:
            mom20 = np.clip(np.log(S / self.prices[day - 20]), -1.0, 1.0)

        # realised vol
        rv = np.clip(self.rv10[day] / 3.0, 0.0, 1.0)

        # days to next monthly expiry (approximate: ~monthly = 30 days)
        days_to_monthly = 30 - (day % 30)
        dte_monthly_norm = days_to_monthly / 30.0

        obs = np.array(
            [
                btc_norm,                                          # 0
                float(np.clip(ivr, 0.0, 1.0)),                    # 1
                float(np.clip(sigma / 3.0, 0.0, 1.0)),            # 2
                pos_type / 2.0,                                    # 3
                float(np.clip(pos_delta, 0.0, 1.0)),               # 4
                float(np.clip(pos_dte, 0.0, 1.0)),                 # 5
                float(np.clip(pos_pnl, -1.0, 1.0)),               # 6
                float(np.clip(self._days_since_trade / 30.0, 0.0, 1.0)),  # 7
                float(mom5),                                       # 8
                float(mom20),                                      # 9
                float(rv),                                         # 10
                float(np.clip(dte_monthly_norm, 0.0, 1.0)),       # 11
            ],
            dtype=np.float32,
        )
        return obs

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self):
        day = min(self._day, self.n_days - 1)
        S = self.prices[day]
        pos_str = "none"
        if self._position is not None:
            ptype = "PUT" if self._position.pos_type == 1 else "CALL"
            pos_str = (
                f"{ptype} K={self._position.strike:.0f} "
                f"DTE={self._position.dte_remaining} "
                f"PnL={self._position.unrealised_pnl:+.0f}"
            )
        print(
            f"Day {day:4d} | BTC={S:>10,.0f} | IV_rank={self.iv_rank[day]:.2f} | "
            f"Equity={self._equity:>12,.2f} | Position: {pos_str}"
        )
