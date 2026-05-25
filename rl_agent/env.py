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


def generate_heston_jump_data(
    n_days: int = 1095,
    starting_price: float = 30_000.0,
    # Heston parameters
    v0: float = 0.50,           # initial variance (ann vol^2)
    theta: float = 0.55,        # long-run variance
    kappa: float = 3.0,         # mean-reversion speed
    xi: float = 0.40,           # vol-of-vol
    rho: float = -0.70,         # correlation: price-vol
    mu: float = 0.10,           # drift
    # Jump parameters (Merton)
    jump_intensity: float = 0.0,  # expected jumps per year (0 = no jumps)
    jump_mean: float = -0.05,     # mean log jump size
    jump_vol: float = 0.10,       # jump size std dev
    seed: int = 42,
) -> np.ndarray:
    """
    Heston stochastic volatility + Merton jump-diffusion price series.
    Returns array of shape (n_days,) with daily closing prices.

    Stage 1 curriculum: call with low xi, no jumps (gentle markets).
    Stage 2+: widen xi, add jumps, vary kappa/theta.
    """
    rng = np.random.default_rng(seed)
    dt = 1.0 / 365.0

    prices = np.zeros(n_days)
    prices[0] = starting_price
    v = v0  # current variance

    for i in range(1, n_days):
        # Correlated Brownian motions
        z1 = rng.standard_normal()
        z2 = rng.standard_normal()
        w_s = z1                              # price Brownian
        w_v = rho * z1 + math.sqrt(1.0 - rho ** 2) * z2  # vol Brownian

        # Variance process (Heston) — floor at 0.01 to prevent negative vol
        v = max(v + kappa * (theta - v) * dt + xi * math.sqrt(max(v, 0.01) * dt) * w_v, 0.01)

        # Price process (GBM with stochastic vol)
        vol = math.sqrt(v)
        log_ret = (mu - 0.5 * v) * dt + vol * math.sqrt(dt) * w_s

        # Jump component (Merton)
        if jump_intensity > 0:
            n_jumps = rng.poisson(jump_intensity * dt)
            if n_jumps > 0:
                jump = sum(rng.normal(jump_mean, jump_vol) for _ in range(n_jumps))
                log_ret += jump

        prices[i] = prices[i - 1] * math.exp(log_ret)

    return prices.astype(np.float64)


# Stage 1 curriculum parameter ranges (narrow, gentle markets)
CURRICULUM_STAGE_1 = {
    "v0": (0.30, 0.60),          # moderate starting vol
    "theta": (0.35, 0.65),       # moderate long-run vol
    "kappa": (2.0, 5.0),         # decent mean reversion
    "xi": (0.20, 0.40),          # low vol-of-vol (gentle)
    "rho": (-0.80, -0.50),       # typical negative correlation
    "mu": (0.00, 0.20),          # slight upward to sideways drift
    "jump_intensity": (0.0, 0.0),  # NO jumps in stage 1
    "jump_mean": (0.0, 0.0),
    "jump_vol": (0.0, 0.0),
    "starting_price": (20_000.0, 120_000.0),
}


def generate_curriculum_episode(
    stage: int = 1,
    n_days: int = 1095,
    seed: int = 42,
) -> np.ndarray:
    """
    Generate a price series with domain-randomised Heston parameters
    drawn from the curriculum stage's parameter ranges.
    """
    rng = np.random.default_rng(seed)
    params = CURRICULUM_STAGE_1  # only stage 1 for now

    def _sample(key):
        lo, hi = params[key]
        if lo == hi:
            return lo
        return rng.uniform(lo, hi)

    return generate_heston_jump_data(
        n_days=n_days,
        starting_price=_sample("starting_price"),
        v0=_sample("v0"),
        theta=_sample("theta"),
        kappa=_sample("kappa"),
        xi=_sample("xi"),
        rho=_sample("rho"),
        mu=_sample("mu"),
        jump_intensity=_sample("jump_intensity"),
        jump_mean=_sample("jump_mean"),
        jump_vol=_sample("jump_vol"),
        seed=seed + 1,  # different seed for the actual generation
    )


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


def load_or_generate_data(data_path: Optional[str] = None) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """
    Returns (prices, iv_rank, raw_iv) arrays. raw_iv is annualised IV in %
    (e.g. 50.0 = 50% annual vol). None if not available.
    """
    if data_path is not None:
        try:
            import pandas as pd
            df = pd.read_csv(data_path, parse_dates=True)
            # Price column
            price_col = None
            for col in ["close", "price", "btc_price", "Close", "Price"]:
                if col in df.columns:
                    price_col = col
                    break
            if price_col is None:
                raise ValueError(f"No recognised price column in {data_path}")
            prices = df[price_col].values.astype(np.float64)

            # IV rank
            iv_rank_col = None
            for col in ["iv_rank", "ivr", "IV_rank"]:
                if col in df.columns:
                    iv_rank_col = col
                    break
            if iv_rank_col is not None:
                iv_rank = df[iv_rank_col].values.astype(np.float64)
            else:
                iv_rank = compute_iv_rank(prices)

            # Raw IV (annualised %)
            raw_iv = None
            for col in ["iv", "deribit_iv", "implied_vol", "IV"]:
                if col in df.columns:
                    raw_iv = df[col].values.astype(np.float64)
                    break

            return prices, iv_rank, raw_iv
        except Exception as e:
            print(f"[BTCOptionsEnv] Could not load {data_path}: {e} — using synthetic data")

    prices = generate_synthetic_btc_data()
    iv_rank = compute_iv_rank(prices)
    return prices, iv_rank, None


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
        n_contracts: int = 1,
        contract_size: float = 0.1,
    ):
        self.pos_type = pos_type
        self.strike = strike
        self.premium_received = premium_received
        self.dte_at_open = dte_at_open
        self.day_opened = day_opened
        self.iv_at_open = iv_at_open
        self.n_contracts = n_contracts
        self.contract_size = contract_size
        self.unrealised_pnl: float = 0.0
        self.dte_remaining: int = dte_at_open

    def update(self, current_day: int, S: float, r: float, sigma: float) -> float:
        """Recompute unrealised P&L and DTE. Returns current total mark value."""
        self.dte_remaining = max(0, self.dte_at_open - (current_day - self.day_opened))
        T = self.dte_remaining / 365.0
        if self.pos_type == 1:  # put
            unit_price = bs_put_price(S, self.strike, T, r, sigma)
        else:  # call
            unit_price = bs_call_price(S, self.strike, T, r, sigma)
        # Total mark value in USD (unit_price $/BTC × BTC/contract × contracts)
        mark = unit_price * self.n_contracts * self.contract_size
        # We sold the option, so unrealised P&L = premium received - current mark
        self.unrealised_pnl = self.premium_received - mark
        return mark

    def delta(self, S: float, T_years: float, r: float, sigma: float) -> float:
        if self.pos_type == 1:
            return abs(bs_put_delta(S, self.strike, T_years, r, sigma))
        else:
            return abs(bs_call_delta(S, self.strike, T_years, r, sigma))

    def intrinsic_loss(self, S: float) -> float:
        """Total intrinsic loss in USD at expiry (scaled by contracts × contract_size)."""
        if self.pos_type == 1:
            return max(self.strike - S, 0.0) * self.n_contracts * self.contract_size
        else:
            return max(S - self.strike, 0.0) * self.n_contracts * self.contract_size


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
    N_OBS = 16

    RISK_FREE = 0.04
    OPTION_DTE = 7          # weekly options
    CONTRACT_SIZE = 0.1     # BTC per contract
    # Deribit realistic costs:
    #   Taker fee: 0.03% of underlying notional
    #   Bid-ask spread: ~2% of premium (conservative avg for OTM weeklies)
    TAKER_FEE_BPS = 0.0003       # 0.03% of underlying
    SPREAD_FRACTION = 0.02       # 2% of premium eaten by spread
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
        curriculum_stage: int = 0,     # 0=use provided data, 1+=curriculum stages
        reward_mode: str = "default",  # "default" or "sharpe"
        action_mode: str = "discrete",  # "discrete" (PPO/DQN) or "continuous" (SAC)
    ):
        super().__init__()

        self.observation_space = spaces.Box(
            low=-np.ones(self.N_OBS, dtype=np.float32),
            high=np.ones(self.N_OBS, dtype=np.float32),
            dtype=np.float32,
        )

        self.action_mode = action_mode
        if action_mode == "continuous":
            self.action_space = spaces.Box(
                low=-1.0, high=1.0, shape=(1,), dtype=np.float32
            )
        else:
            self.action_space = spaces.Discrete(self.N_ACTIONS)

        self.curriculum_stage = curriculum_stage
        self.reward_mode = reward_mode

        # Load data
        all_raw_iv = None
        if curriculum_stage > 0:
            # Curriculum mode: generate Heston data (re-randomised each reset)
            self._curriculum_seed = seed if seed is not None else 42
            all_prices = generate_curriculum_episode(
                stage=curriculum_stage, n_days=1095, seed=self._curriculum_seed
            )
            all_iv = compute_iv_rank(all_prices)
        elif prices is not None and iv_rank is not None:
            all_prices, all_iv = prices, iv_rank
        else:
            all_prices, all_iv, all_raw_iv = load_or_generate_data(data_path)

        # Split
        n = len(all_prices)
        split_idx = int(n * 0.70)
        if split == "train":
            self.prices = all_prices[:split_idx]
            self.iv_rank = all_iv[:split_idx]
            self._raw_iv = all_raw_iv[:split_idx] if all_raw_iv is not None else None
        else:
            self.prices = all_prices[split_idx:]
            self.iv_rank = all_iv[split_idx:]
            self._raw_iv = all_raw_iv[split_idx:] if all_raw_iv is not None else None

        self._recompute_derived()

        self.starting_equity = starting_equity
        self.max_equity_per_leg = max_equity_per_leg

        self._rng = np.random.default_rng(seed)
        self._day = 0
        self._equity = starting_equity
        self._peak_equity = starting_equity
        self._position: Optional[Position] = None
        self._contracts = 0          # vol-adjusted contracts for current position
        self._days_since_trade = 0
        self._realised_pnl_total = 0.0
        self._prev_mtm_equity = starting_equity  # for step-by-step reward computation

        # Differential Sharpe ratio EMA state (Moody & Saffell)
        self._dsr_A = 0.0   # EMA of returns
        self._dsr_B = 0.0   # EMA of squared returns
        self._dsr_eta = 0.002  # EMA decay rate (tuned for weekly options)

    def _trade_cost(self, S: float, premium: float, n_contracts: int) -> float:
        """
        Realistic Deribit transaction cost.
        Taker fee: 0.03% of underlying notional per contract.
        Spread cost: ~2% of premium lost to bid-ask.
        """
        notional = S * self.CONTRACT_SIZE * n_contracts
        fee = notional * self.TAKER_FEE_BPS
        spread = abs(premium) * self.SPREAD_FRACTION
        return fee + spread

    def _recompute_derived(self):
        """Recompute log returns, realised vols, and IV surface proxies."""
        self.n_days = len(self.prices)
        log_rets = np.zeros(self.n_days)
        log_rets[1:] = np.log(self.prices[1:] / self.prices[:-1])
        self.log_rets = log_rets

        self.rv5 = np.zeros(self.n_days)
        self.rv10 = np.zeros(self.n_days)
        self.rv30 = np.zeros(self.n_days)
        self.skew20 = np.zeros(self.n_days)

        for i in range(5, self.n_days):
            self.rv5[i] = np.std(log_rets[i - 5 : i]) * math.sqrt(252)
        for i in range(10, self.n_days):
            self.rv10[i] = np.std(log_rets[i - 10 : i]) * math.sqrt(252)
        for i in range(30, self.n_days):
            self.rv30[i] = np.std(log_rets[i - 30 : i]) * math.sqrt(252)
            # Return skewness (20-day) — negative = crash fear = puts expensive
            if i >= 20:
                window = log_rets[i - 20 : i]
                m = np.mean(window)
                s = np.std(window)
                if s > 1e-10:
                    self.skew20[i] = np.mean(((window - m) / s) ** 3)

        # Fill early values
        self.rv5[:5] = self.rv5[5] if self.n_days > 5 else 0.5
        self.rv10[:10] = self.rv10[10] if self.n_days > 10 else 0.5
        self.rv30[:30] = self.rv30[30] if self.n_days > 30 else 0.5

        # Implied vol for option pricing: use real IV if available, else RV
        if self._raw_iv is not None and len(self._raw_iv) == self.n_days:
            # Convert from annualised % to fraction (e.g. 50% → 0.50)
            self.implied_vol = np.clip(self._raw_iv / 100.0, 0.10, 3.0)
        else:
            # Synthetic data: use RV with a premium (options trade richer than RV)
            self.implied_vol = np.clip(self.rv10 * 1.2, 0.10, 3.0)

    # ------------------------------------------------------------------
    # Gym interface
    # ------------------------------------------------------------------

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)

        # Curriculum mode: generate fresh randomised data each episode
        if self.curriculum_stage > 0:
            ep_seed = int(self._rng.integers(0, 2**31))
            all_prices = generate_curriculum_episode(
                stage=self.curriculum_stage, n_days=1095, seed=ep_seed
            )
            all_iv = compute_iv_rank(all_prices)
            n = len(all_prices)
            split_idx = int(n * 0.70)
            self.prices = all_prices[:split_idx]
            self.iv_rank = all_iv[:split_idx]
            self._raw_iv = None  # synthetic — no real IV
            self._recompute_derived()

        # Start at a random point in first 80% of split data to vary episodes
        max_start = max(0, int(self.n_days * 0.80) - 60)
        self._day = int(self._rng.integers(20, max(21, max_start)))
        self._equity = self.starting_equity
        self._peak_equity = self.starting_equity
        self._position = None
        self._contracts = 0
        self._days_since_trade = 0
        self._realised_pnl_total = 0.0
        self._prev_mtm_equity = self.starting_equity
        # Reset differential Sharpe ratio state
        self._dsr_A = 0.0
        self._dsr_B = 0.0
        return self._obs(), {}

    def step(self, action):
        # Map continuous action to discrete if needed
        if self.action_mode == "continuous":
            raw = float(action[0]) if hasattr(action, '__len__') else float(action)
            if raw < -0.6:
                action = self.ACTION_HOLD
            elif raw < -0.2:
                action = self.ACTION_SELL_PUT_020
            elif raw < 0.2:
                action = self.ACTION_SELL_PUT_025
            elif raw < 0.6:
                action = self.ACTION_SELL_CALL_020
            else:
                action = self.ACTION_CLOSE
        else:
            action = int(action)

        day = self._day
        S = self.prices[day]
        ivr = self.iv_rank[day]
        sigma = self.implied_vol[day]  # actual IV for option pricing

        reward = 0.0
        terminated = False
        truncated = False
        info = {}

        # ------ Update open position ------
        if self._position is not None:
            self._position.update(day, S, self.RISK_FREE, sigma)
            # Check expiry
            if self._position.dte_remaining <= 0:
                loss = self._position.intrinsic_loss(S)  # already scaled by contracts
                settle_cost = self._trade_cost(S, self._position.premium_received, self._position.n_contracts)
                realised = self._position.premium_received - loss - settle_cost
                self._equity += realised
                self._realised_pnl_total += realised
                self._position = None
                self._contracts = 0
                self._days_since_trade = 0

        # ------ Execute action ------
        if action == self.ACTION_HOLD:
            pass  # nothing to do

        elif action == self.ACTION_SELL_PUT_020 or action == self.ACTION_SELL_PUT_025:
            if self._position is None:
                target_delta = 0.20 if action == self.ACTION_SELL_PUT_020 else 0.25
                T = self.OPTION_DTE / 365.0
                K = find_put_strike_for_delta(S, target_delta, T, self.RISK_FREE, sigma)
                unit_premium = bs_put_price(S, K, T, self.RISK_FREE, sigma)

                # Vol-adjusted position sizing: risk 2% of equity on a 10% adverse move
                target_risk = 0.02 * self._equity
                cost_per_contract_10pct = max(K * self.CONTRACT_SIZE * 0.10, 1.0)
                base_contracts = max(1, int(target_risk / cost_per_contract_10pct))
                # IV boost: rich premium environment allows up to 1.5× sizing
                if ivr > 0.70:
                    base_contracts = int(base_contracts * 1.5)
                n_contracts = min(base_contracts, 5)  # hard cap at 5 contracts
                self._contracts = n_contracts

                total_premium = unit_premium * n_contracts * self.CONTRACT_SIZE
                total_premium = max(total_premium, 1.0)  # floor
                cost = self._trade_cost(S, total_premium, n_contracts)
                self._position = Position(
                    pos_type=1,
                    strike=K,
                    premium_received=total_premium - cost,
                    dte_at_open=self.OPTION_DTE,
                    day_opened=day,
                    iv_at_open=sigma,
                    n_contracts=n_contracts,
                    contract_size=self.CONTRACT_SIZE,
                )
                self._days_since_trade = 0
                info["trade"] = f"SELL_PUT K={K:.0f} delta={target_delta} contracts={n_contracts}"

        elif action == self.ACTION_SELL_CALL_020:
            if self._position is None:
                T = self.OPTION_DTE / 365.0
                K = find_call_strike_for_delta(S, 0.20, T, self.RISK_FREE, sigma)
                unit_premium = bs_call_price(S, K, T, self.RISK_FREE, sigma)

                # Vol-adjusted position sizing: risk 2% of equity on a 10% adverse move
                target_risk = 0.02 * self._equity
                cost_per_contract_10pct = max(K * self.CONTRACT_SIZE * 0.10, 1.0)
                base_contracts = max(1, int(target_risk / cost_per_contract_10pct))
                if ivr > 0.70:
                    base_contracts = int(base_contracts * 1.5)
                n_contracts = min(base_contracts, 5)
                self._contracts = n_contracts

                total_premium = unit_premium * n_contracts * self.CONTRACT_SIZE
                total_premium = max(total_premium, 1.0)
                cost = self._trade_cost(S, total_premium, n_contracts)
                self._position = Position(
                    pos_type=2,
                    strike=K,
                    premium_received=total_premium - cost,
                    dte_at_open=self.OPTION_DTE,
                    day_opened=day,
                    iv_at_open=sigma,
                    n_contracts=n_contracts,
                    contract_size=self.CONTRACT_SIZE,
                )
                self._days_since_trade = 0
                info["trade"] = f"SELL_CALL K={K:.0f} delta=0.20 contracts={n_contracts}"

        elif action == self.ACTION_CLOSE:
            if self._position is not None:
                T = max(self._position.dte_remaining / 365.0, 1e-6)
                if self._position.pos_type == 1:
                    unit_price = bs_put_price(S, self._position.strike, T, self.RISK_FREE, sigma)
                else:
                    unit_price = bs_call_price(S, self._position.strike, T, self.RISK_FREE, sigma)
                # Total cost to buy back: same scaling as when we sold
                cost_to_close = unit_price * self._position.n_contracts * self.CONTRACT_SIZE
                close_cost = self._trade_cost(S, cost_to_close, self._position.n_contracts)
                realised = (
                    self._position.premium_received
                    - cost_to_close
                    - close_cost
                )
                self._equity += realised
                self._realised_pnl_total += realised
                self._position = None
                self._contracts = 0
                self._days_since_trade = 0
                info["trade"] = "CLOSE"

        # ------ Compute reward ------
        traded_this_step = "trade" in info

        # Current mark-to-market equity
        curr_mtm = self._equity
        if self._position is not None:
            curr_mtm += self._position.unrealised_pnl

        # Step P&L = theta decay + delta move (naturally captures both)
        premium_earned_this_step = curr_mtm - self._prev_mtm_equity
        self._prev_mtm_equity = curr_mtm

        if curr_mtm > self._peak_equity:
            self._peak_equity = curr_mtm
        drawdown = (self._peak_equity - curr_mtm) / max(self._peak_equity, 1.0)

        no_position = self._position is None
        self._days_since_trade += 1

        if self.reward_mode == "sharpe":
            reward = self._differential_sharpe_reward(
                premium_earned_this_step, curr_mtm, drawdown, traded_this_step
            )
        else:
            reward = self._default_reward(
                premium_earned_this_step, curr_mtm, drawdown, no_position
            )

        # Advance day
        self._day += 1
        if self._day >= self.n_days - 1:
            terminated = True
            # Final settlement
            if self._position is not None:
                S_final = self.prices[-1]
                loss = self._position.intrinsic_loss(S_final)  # already scaled
                settle_cost = self._trade_cost(S_final, self._position.premium_received, self._position.n_contracts)
                realised = self._position.premium_received - loss - settle_cost
                self._equity += realised
                self._position = None
                self._contracts = 0

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
    # Reward functions
    # ------------------------------------------------------------------

    def _default_reward(self, pnl_step, curr_mtm, drawdown, no_position):
        """Original capital-efficiency reward (v1 behaviour)."""
        if self._position is not None:
            capital_at_risk = max(
                self._position.strike * self._contracts * self.CONTRACT_SIZE * 0.20,
                1.0,
            )
            annualisation_factor = 252.0 / self.OPTION_DTE
            efficiency = (pnl_step / capital_at_risk) * annualisation_factor
            reward_base = float(np.tanh(efficiency / 5.0))
            capital_usage_frac = capital_at_risk / max(curr_mtm, 1.0)
            capital_overuse_penalty = 0.0001 * max(0.0, capital_usage_frac - 0.30) ** 2
        else:
            reward_base = 0.0
            capital_overuse_penalty = 0.0
        dd_penalty = 0.0005 * max(0.0, drawdown - self.MAX_DRAWDOWN_PENALTY_THRESHOLD) ** 2
        idle_penalty = 0.00005 if no_position else 0.0
        return float(reward_base - dd_penalty - idle_penalty - capital_overuse_penalty)

    def _differential_sharpe_reward(self, pnl_step, curr_mtm, drawdown, traded=False):
        """
        Differential Sharpe Ratio (Moody & Saffell, NeurIPS 1998).

        Components:
          1. Differential Sharpe ratio (primary driver)
          2. Rolling drawdown penalty
          3. Theta capture bonus when positioned (aligns with wheel edge)
          4. Trade cost penalty (explicit signal that each trade costs money)
        """
        # Step return as fraction of equity
        R = pnl_step / max(curr_mtm, 1.0)

        # Update EMA state
        eta = self._dsr_eta
        delta_A = R - self._dsr_A
        delta_B = R ** 2 - self._dsr_B
        self._dsr_A += eta * delta_A
        self._dsr_B += eta * delta_B

        # Differential Sharpe ratio
        denom = self._dsr_B - self._dsr_A ** 2
        if denom > 1e-12:
            dsr = (self._dsr_B * delta_A - 0.5 * self._dsr_A * delta_B) / (denom ** 1.5)
        else:
            dsr = delta_A  # fallback: use raw return signal early on

        # Squash to prevent extreme values
        dsr = float(np.tanh(dsr))

        # Theta capture bonus: small reward for being positioned (earning theta)
        theta_bonus = 0.0
        if self._position is not None and pnl_step > 0:
            capital_at_risk = max(
                self._position.strike * self._contracts * self.CONTRACT_SIZE * 0.20,
                1.0,
            )
            theta_bonus = 0.01 * float(np.tanh(pnl_step / capital_at_risk))

        # Rolling drawdown penalty
        dd_penalty = 0.5 * drawdown ** 2

        # Trade cost penalty: explicit signal that trading costs real money
        # Scaled to be meaningful relative to DSR (~0.03 penalty per trade)
        trade_penalty = 0.03 if traded else 0.0

        return float(dsr + theta_bonus - dd_penalty - trade_penalty)

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def _obs(self) -> np.ndarray:
        day = min(self._day, self.n_days - 1)
        S = self.prices[day]
        ivr = self.iv_rank[day]
        iv = self.implied_vol[day]   # actual IV (fraction)
        rv10 = self.rv10[day]        # realised vol (fraction)

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
            pos_delta = self._position.delta(S, T, self.RISK_FREE, iv)
            pos_dte = self._position.dte_remaining / 30.0
            pos_pnl = self._position.unrealised_pnl / max(self.starting_equity, 1.0)

        # momentum
        mom5 = 0.0
        mom20 = 0.0
        if day >= 5:
            mom5 = np.clip(np.log(S / self.prices[day - 5]), -0.5, 0.5)
        if day >= 20:
            mom20 = np.clip(np.log(S / self.prices[day - 20]), -1.0, 1.0)

        # realised vol (normalised)
        rv = np.clip(rv10 / 3.0, 0.0, 1.0)

        # days to next monthly expiry (approximate: ~monthly = 30 days)
        days_to_monthly = 30 - (day % 30)
        dte_monthly_norm = days_to_monthly / 30.0

        # --- IV surface features ---
        # VRP: implied vol minus realised vol (both as fractions)
        # High = IV rich vs RV → good time to sell premium (the wheel's edge)
        vrp = float(np.clip((iv - rv10) / 1.0, -1.0, 1.0))      # 12

        # Return skewness (20-day) — negative = crash fear = puts expensive
        skew = float(np.clip(self.skew20[day] / 3.0, -1.0, 1.0)) # 13

        # Term structure proxy: 5-day RV / 30-day RV
        # >1 = short-term stress (inverted), <1 = calm (normal contango)
        rv30 = max(self.rv30[day], 0.01)
        rv_ratio = float(np.clip(self.rv5[day] / rv30 - 1.0, -1.0, 1.0))  # 14

        # 30-day realised vol (longer-term vol context)
        rv30_norm = float(np.clip(self.rv30[day] / 3.0, 0.0, 1.0))  # 15

        obs = np.array(
            [
                btc_norm,                                          # 0
                float(np.clip(ivr, 0.0, 1.0)),                    # 1
                float(np.clip(iv / 3.0, 0.0, 1.0)),               # 2 implied vol
                pos_type / 2.0,                                    # 3
                float(np.clip(pos_delta, 0.0, 1.0)),               # 4
                float(np.clip(pos_dte, 0.0, 1.0)),                 # 5
                float(np.clip(pos_pnl, -1.0, 1.0)),               # 6
                float(np.clip(self._days_since_trade / 30.0, 0.0, 1.0)),  # 7
                float(mom5),                                       # 8
                float(mom20),                                      # 9
                float(rv),                                         # 10
                float(np.clip(dte_monthly_norm, 0.0, 1.0)),       # 11
                vrp,                                               # 12 VRP
                skew,                                              # 13 skew
                rv_ratio,                                          # 14 term structure
                rv30_norm,                                         # 15 long-term vol
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
