# BTC Options RL Agent v2 — Architecture Document

**Project:** Next-Generation Reinforcement Learning Trading Bot for BTC Options on Deribit  
**Author:** Architecture design session, May 2026  
**Status:** Living document — north star for v2 development  
**Objective function:** Minimum capital, maximum ROI. Survive black swans. Discover strategy via pure RL.

---

## Table of Contents

1. [Vision & Design Philosophy](#1-vision--design-philosophy)
2. [Data Sources & Collection](#2-data-sources--collection)
3. [Feature Engineering / State Space](#3-feature-engineering--state-space)
4. [Action Space](#4-action-space)
5. [Reward Function Design](#5-reward-function-design)
6. [Training Environment](#6-training-environment)
7. [Model Architecture](#7-model-architecture)
8. [Hardware & Training Timeline](#8-hardware--training-timeline)
9. [Evaluation & Testing](#9-evaluation--testing)
10. [Data Pipeline Implementation Plan](#10-data-pipeline-implementation-plan)
11. [Phased Implementation Roadmap](#11-phased-implementation-roadmap)
12. [Open Questions & Genuine Uncertainties](#12-open-questions--genuine-uncertainties)
13. [Sources & References](#13-sources--references)

---

## 1. Vision & Design Philosophy

### The Core Bet

The thesis is that options markets — especially crypto options — are informationally inefficient enough that a pure RL agent, given the right observation space and reward function, can discover profitable strategies that human traders haven't systematized. This is the AlphaStar/AlphaZero bet: don't pre-encode strategy, encode goals and constraints.

### What "Pure RL" Actually Means Here

AlphaZero was given the rules of chess and told to win. It discovered castling-based safety, sacrificial gambits, and endgame tablebase approaches — not because any of that was programmed in, but because those strategies *minimize opponent winning probability over time*. We want the same for BTC options: give the agent the goal (maximize risk-adjusted return, survive), and let it discover whether that means selling premium, buying vol spikes, delta-hedging, running iron condors, or something nobody's named yet.

The constraint is we're not starting from zero. The existing `rl_agent/` codebase (PPO, `env.py`, `train.py`) provides a foundation. v2 is a ground-up redesign of the state space, action space, and reward function — keeping the gym-compatible pattern but making it production-worthy.

### Non-Negotiable Design Principles

1. **Survival first, profit second.** A strategy that returns 40% annually but blows up every 5 years has negative expected value in crypto. The reward function must make liquidation catastrophic.
2. **No strategy encoded in the action space.** The agent gets primitives (buy, sell, size, strike, expiry). It discovers strangles, wheels, and ratio spreads as *emergent behavior*.
3. **Out-of-sample test data is sacred.** 2024–2025 data is locked away. No peeking. Ever.
4. **Realistic costs.** If it only works without commissions and slippage, it doesn't work.

---

## 2. Data Sources & Collection

### 2.1 Primary: Deribit API

**What's available directly from Deribit:**

| Endpoint | Data | Notes |
|---|---|---|
| `public/get_instruments` | All active instruments (options, perps, futures) | Current snapshot only |
| `public/get_order_book` | Full order book per instrument | Real-time / recent only |
| `public/get_tradingview_chart_data` | OHLCV candles per instrument | Historical, but limited by instrument lifetime |
| `public/get_historical_volatility` | Daily historical realized vol (BTC, ETH) | Reasonably long history |
| `public/get_funding_rate_history` | Perpetual funding rate history | Per-currency |
| `public/get_settlement_history_by_currency` | Settlement prices by expiry | Critical for training reward |
| `private/get_user_trades_by_currency` | Your own historical trades | Personal account only |

**Rate limits:** Credit-based system. Public (unauthenticated) endpoints: rate-limited per IP, no exact credit cost publicly documented. Practical limit for bulk downloads: ~5–10 requests/second sustained without throttling. Use exponential backoff with jitter.

**Critical finding:** Deribit's *own* API does not provide a single endpoint to dump all historical options chain snapshots. Options instruments expire and disappear. You need to enumerate expired instruments and query them individually — this is achievable but slow.

**Realistic data depth from Deribit directly:** Options chain data is available back to ~2019 for expired instruments, but requires iterating through every expired instrument ID and fetching candles individually. This is a multi-day download job.

### 2.2 Best Historical Data Source: Tardis.dev

**Recommendation: Use Tardis.dev for bulk historical data.** This is the least-reinventing-wheels decision in the whole project.

- Full Deribit tick data available from **2019-03-30** onward
- Includes: all options (every strike, every expiry), perpetuals, futures, order book snapshots, trades, settlements
- Format: gzipped CSV, easily parseable
- Pricing: $0 for some datasets, paid tiers for full depth. BTC options history is in the $50–200 range as a one-time download for research use. Check current pricing at tardis.dev.
- Python client: `tardis-client` library, straightforward API

**What Tardis provides that Deribit's API doesn't:**
- Point-in-time order book snapshots (not just trades)
- Normalized format across all instruments simultaneously
- No need to reconstruct the options chain from per-instrument queries

### 2.3 Free Supplement: CryptoDataDownload

CryptoDataDownload offers Deribit BTC options OHLCV organized by expiration date (each ZIP = full options chain for one maturity). This is genuinely free and good for prototyping before committing to Tardis.

### 2.4 On-Chain Data

| Metric | Source | Cost | Notes |
|---|---|---|---|
| MVRV Z-Score | Glassnode | Free (weekly res) / $39/mo (daily) | Key macro regime indicator |
| SOPR | Glassnode | Free (weekly) / $39/mo (daily) | Short-term holder behavior |
| Exchange netflows (BTC) | CryptoQuant | Free tier available | Selling pressure indicator |
| Whale wallet activity | Glassnode / Nansen | Paid | Lower priority |
| Realized price | Glassnode | Free | Cost basis of all BTC |
| Coin Metrics (MVRV, SOPR, NVT) | Coin Metrics | Free API tier | Good Glassnode alternative |
| Exchange inflows/outflows | CryptoQuant | Free tier | Daily granularity free |

**Recommendation for v2:** Start with Coin Metrics free API (good historical depth, daily granularity, Python client exists). Add Glassnode $39/mo plan when going live — the daily resolution matters for training signal.

**MVRV and SOPR as features:** These are genuinely useful as *regime detection* signals, not prediction signals. When MVRV > 3.5 historically correlates with cycle tops; when < 1.0 with cycle bottoms. The agent should use these to modulate risk, not predict price.

### 2.5 Macro Calendar Data

- **Fed meeting dates:** Available from Federal Reserve website (federalreserve.gov/monetarypolicy/fomccalendar.htm). Static calendar, update quarterly.
- **CPI release dates:** Bureau of Labor Statistics publishes annual schedule.
- **Options expiry dates:** Deribit uses last Friday of each month (monthly) and quarterly (March, June, September, December). Deterministic — compute programmatically.
- **Fear & Greed Index:** alternative.me API, free, daily data back to 2018.

### 2.6 BTC Spot Price (High Quality)

Use Bitstamp or Coinbase OHLCV via CCXT. These have the longest history (Bitstamp goes to 2011) and are the most reliable reference. Deribit's own index price is a blend of 6–8 exchanges — available via `public/get_index_price_names` and `public/get_index`.

### 2.7 Data We Can't Realistically Get (or Don't Need)

- **Order flow / tape data pre-2019:** Doesn't exist for Deribit options. Don't try.
- **Real-time Level 2 depth for training:** Overkill for v2. Train on candles + settlements.
- **Sentiment from social media:** Noisy, expensive, not worth the complexity for v2.
- **Options flow from TradingView alerts:** Not programmatic at scale.

### 2.8 Expected Storage Requirements

| Dataset | Estimated Size |
|---|---|
| Deribit BTC options OHLCV (2019–2024) | ~15–40 GB compressed |
| Perp funding rates + OI | ~200 MB |
| BTC spot OHLCV (hourly, 2017–2024) | ~50 MB |
| IV surface snapshots (daily, computed) | ~500 MB |
| On-chain metrics (daily, 2017–2024) | ~20 MB |
| Fear & Greed (daily) | ~2 MB |

Total: ~20–50 GB raw. After feature engineering → ~5 GB parquet. MacBook SSD can handle this.

---

## 3. Feature Engineering / State Space

The observation vector is what the agent sees at each timestep. Bigger is not always better — irrelevant features add noise and slow training. Every feature here earns its place.

### 3.1 Full Observation Vector

**Total estimated dimension: ~120–150 features.** This is large but not intractable for a modern MLP/LSTM. Normalize all features to approximately [-1, 1] or [0, 1] range.

#### Portfolio State (15 features)

| Feature | Description | Range |
|---|---|---|
| `portfolio_delta` | Total portfolio delta (in BTC equiv) | [-5, 5] → normalize |
| `portfolio_gamma` | Total gamma | [-∞, ∞] → clip + normalize |
| `portfolio_theta` | Daily theta (P&L decay per day) | usually negative |
| `portfolio_vega` | Total vega (sensitivity to IV change) | [-∞, ∞] |
| `unrealized_pnl_pct` | Unrealized P&L as % of starting capital | [-1, 1] |
| `margin_used_pct` | Margin used / total margin available | [0, 1] |
| `margin_buffer_pct` | (Available - Initial Margin) / Available | [0, 1] — KEY for survival |
| `num_open_positions` | Count of open option legs | [0, 20] → normalize |
| `capital_at_risk_pct` | Max loss if all positions go to zero / capital | [0, 1] |
| `avg_dte` | Weighted avg days-to-expiry across positions | [0, 90] |
| `has_long_put` | Binary: do we have protective long put? | {0, 1} |
| `has_long_call` | Binary: do we have long call hedge? | {0, 1} |
| `position_1_delta` | Delta of largest position | — |
| `position_1_theta_per_day` | Theta of largest position | — |
| `position_1_dte` | DTE of largest position | — |

#### Market State (20 features)

| Feature | Description |
|---|---|
| `btc_price_log` | log(spot price) — use log for stationarity |
| `ret_1h`, `ret_4h`, `ret_24h`, `ret_7d` | Log returns over multiple windows |
| `rv_7d`, `rv_30d`, `rv_90d` | Realized volatility (annualized) at 3 timeframes |
| `rv_7d_rv_30d_ratio` | Short/long RV ratio — regime signal |
| `price_52w_pct` | Where BTC sits in its 52-week range |
| `drawdown_from_ath_pct` | % below all-time high in training window |
| `recent_drawdown_pct` | Max drawdown over last 30 days |
| `perp_basis` | (Perp price - Spot) / Spot — funding pressure |
| `funding_rate_8h` | Latest 8h funding rate |
| `funding_rate_7d_avg` | 7-day average funding rate |
| `perp_open_interest_btc` | Perp OI in BTC (normalized to history) |
| `spot_volume_24h_norm` | 24h volume normalized to 30-day avg |

#### Volatility Surface (30 features)

This is the richest signal for an options market maker/trader.

**ATM IV at key tenors:**
- `iv_atm_7d`, `iv_atm_14d`, `iv_atm_30d`, `iv_atm_60d`, `iv_atm_90d`

**IV by moneyness at 30d tenor:** (normalized strike = K/S)
- `iv_80pct_30d`, `iv_90pct_30d`, `iv_100pct_30d`, `iv_110pct_30d`, `iv_120pct_30d`

**IV rank & percentile:**
- `ivr_30d` — IV rank: (ATM IV - 52w low) / (52w high - 52w low). Range [0,1]. This is the single most important options timing signal.
- `iv_pct_30d` — IV percentile (fraction of days where IV was lower)

**Skew metrics (per tenor, focus on 30d):**
- `rr_25d_30d` — 25-delta risk reversal (call IV - put IV). Negative = put skew (fear)
- `bf_25d_30d` — 25-delta butterfly (wings vs ATM). High = fat tails priced in
- `put_skew_slope` — How steep the left wing is (OTM puts vs ATM)

**Term structure:**
- `ts_slope_7_30` — (IV_30d - IV_7d) / IV_7d. Positive = contango, negative = backwardation
- `ts_slope_30_90` — longer term slope
- `ts_backwardation_flag` — binary: is front-month IV > back-month IV?

**IV change momentum:**
- `iv_change_24h` — ATM IV change over last 24h
- `iv_change_7d` — ATM IV change over last 7 days

#### Options Market Microstructure (10 features)

| Feature | Description |
|---|---|
| `put_call_oi_ratio` | Put OI / Call OI (per expiry-weighted avg) |
| `put_call_volume_ratio` | Put volume / Call volume (last 24h) |
| `max_pain_strike_pct` | Max pain as % deviation from spot |
| `total_options_oi_usd` | Total notional OI in USD (normalized) |
| `gamma_exposure_net` | Market maker net gamma exposure (GEX) |
| `oi_concentration_pct` | % of OI in top 3 strikes (convexity risk) |

#### On-Chain Regime (8 features)

| Feature | Description |
|---|---|
| `mvrv_zscore` | MVRV Z-score (normalize to [-3, 3] range) |
| `sopr_7d_ma` | 7-day moving average of SOPR |
| `exchange_netflow_7d` | Net BTC flow to exchanges (7d avg, normalized) |
| `realized_price_pct` | Spot / Realized price ratio |
| `fear_greed` | Fear & Greed index [0, 100] → normalize to [0, 1] |

#### Temporal Features (10 features)

| Feature | Description |
|---|---|
| `day_of_week` | One-hot (7 dims) — weekend vol behavior differs |
| `days_to_monthly_expiry` | [0, 30] — normalized |
| `days_to_quarterly_expiry` | [0, 90] — normalized |
| `hour_of_day_sin`, `hour_of_day_cos` | Cyclical encoding of UTC hour |
| `days_to_fed_meeting` | [0, 45] — macro risk timing |
| `days_to_cpi` | [0, 30] — macro risk timing |

#### Regime Classification (8 features — computed, not raw)

These are computed from the above features using simple rule-based classifiers. The RL agent could learn these itself, but providing them speeds up training:

| Feature | Description |
|---|---|
| `vol_regime` | One-hot: calm / normal / stressed / crisis |
| `trend_regime` | One-hot: bear / neutral / bull |
| `iv_regime` | One-hot: low-vol / mid-vol / high-vol |

These can be computed with simple thresholds (e.g., vol_regime=crisis when RV_7d > 100% annualized or daily move > 10%).

### 3.2 Observation Vector Summary

```
Total features: ~121
- Portfolio state:     15
- Market state:        20
- Vol surface:         30
- Options microstr.:   10
- On-chain:             8
- Temporal:            10
- Regime (computed):    8
- Padding/reserve:     20
```

All features should be normalized. Use `RunningMeanStd` normalization (as used in Stable-Baselines3) computed on training data and frozen for evaluation.

---

## 4. Action Space

**Recommendation: Hybrid discrete-continuous action space using a two-level structure.** Don't collapse this to pure discrete (too many combinations) or pure continuous (too hard to learn meaningfully for discrete choices like "which instrument to trade").

### 4.1 Two-Level Action Structure

**Level 1 — Action Type (discrete, 15 choices):**

```
0:  DO_NOTHING
1:  SELL_PUT
2:  SELL_CALL
3:  SELL_STRANGLE          # sell put + sell call simultaneously
4:  SELL_STRADDLE          # sell ATM put + ATM call
5:  BUY_PUT                # protective/speculative
6:  BUY_CALL               # directional/hedge
7:  BUY_SPOT_BTC
8:  SELL_SPOT_BTC
9:  LONG_PERP              # delta hedge via perpetual
10: SHORT_PERP             # delta hedge via perpetual
11: CLOSE_PERP_POSITION    # flatten delta hedge
12: CLOSE_POSITION         # close specific options position (+ selector)
13: ROLL_POSITION          # close + reopen at new strike/expiry
14: ADJUST_HEDGE_RATIO     # resize existing perp hedge
```

**Level 2 — Action Parameters (continuous, per action type):**

For options actions (1–6, 13):
- `strike_delta_target` ∈ [0.05, 0.50] — target delta of strike to select (0.05 = deep OTM, 0.5 = ATM)
- `expiry_target_dte` ∈ [7, 90] — target days-to-expiry for new position
- `size_pct_capital` ∈ [0.01, 0.20] — fraction of capital to risk on this position

For spot actions (7, 8):
- `size_pct_capital` ∈ [0.01, 0.50]

For perp actions (9, 10, 14):
- `delta_target` ∈ [-2.0, 2.0] — target net portfolio delta after action

For close/roll (12, 13):
- `position_index` ∈ [0, N_max_positions] — which position to act on (discretized)
- Roll also gets strike_delta_target and expiry_target_dte for the new leg

### 4.2 Why This Structure

Pure discrete with all parameter combinations would require 15 × 10 × 10 × 10 = 15,000+ actions. Unlearnable. Pure continuous fails because "sell a put" vs "do nothing" is a categorical choice that continuous spaces handle poorly.

The two-level approach maps cleanly to PPO with separate policy heads (one softmax for action type, separate beta-distribution heads for each continuous parameter). This is the same structure used in multi-task RL systems.

### 4.3 Algorithm Recommendation

**Primary: PPO with hybrid action head.** Reasons:
- Stable-Baselines3 already supports this pattern
- The existing codebase is PPO-based — lower migration cost
- On-policy with vectorized environments scales well on CPU (important for MacBook)
- No replay buffer management complexity

**Secondary / Long-term: SAC with hybrid action head.** Reasons:
- Off-policy: 5–10x more sample efficient (less simulation time needed)
- Maximum entropy principle naturally encourages strategy diversity (good for discovery)
- Better suited for environments with slow-changing dynamics (options theta decay)

**When to switch to SAC:** After the PPO version demonstrates convergence in Phase 5, run a parallel SAC experiment. If SAC reaches the same Sharpe ratio in 20% of the wall-clock time, switch. If not, stay with PPO.

**Do NOT use Decision Transformer for v2.** Recent research (LLM-LoRA-DT, 2024) shows offline RL can work for trading, but it requires a pre-existing dataset of expert trajectories. We don't have expert trajectories for this specific task — we're discovering the strategy, not imitating one. Decision Transformer is interesting for v3 (fine-tune on successful PPO rollouts), not v2.

### 4.4 Position Limits (Hard Constraints)

Enforce these as part of the environment, not just reward shaping:
- Max open options positions: 10 legs
- Max position size per leg: 20% of capital
- Max portfolio delta: ±3.0 BTC equivalent
- Max margin utilization: 80% (hard stop — if breach, only CLOSE actions valid)
- Max capital at risk (max loss if everything goes to zero): 50% of capital

---

## 5. Reward Function Design

**This is the strategy.** Getting this wrong makes everything else irrelevant.

### 5.1 Core Reward Formula

```python
r_t = w1 * r_return + w2 * r_survival + w3 * r_efficiency + w4 * r_hedging

# Suggested weights (tune these — they are hyperparameters):
w1 = 1.0   # risk-adjusted return (primary)
w2 = 5.0   # survival penalty (large — make liquidation catastrophic)
w3 = 0.2   # capital efficiency bonus
w4 = 0.1   # hedging quality
```

### 5.2 Component: Risk-Adjusted Return (r_return)

**NOT raw P&L.** Raw P&L rewards selling naked options with max leverage — exactly what we don't want.

```python
# Compute over a rolling N-step window (e.g., 20 steps = 20 days)
pnl = portfolio_value_t - portfolio_value_t_minus_1
capital_at_risk = current_max_loss_scenario  # not just margin used

# Sharpe-inspired reward
r_return = pnl / (capital_at_risk + epsilon)

# Additionally add Sortino component to penalize downside variance:
# If this step's return is negative, multiply penalty by 1.5
if pnl < 0:
    r_return *= 1.5
```

**Why capital_at_risk denominator, not just equity?** A position that risks $1000 to make $10 should be punished. This forces the agent to discover capital-efficient strategies.

### 5.3 Component: Survival Penalty (r_survival)

```python
if margin_call_triggered or liquidated:
    r_survival = -100.0  # terminal, episode ends
elif margin_buffer_pct < 0.10:  # within 10% of liquidation
    r_survival = -10.0 * (0.10 - margin_buffer_pct) / 0.10
elif max_drawdown_episode > 0.30:  # 30% drawdown from episode peak
    r_survival = -5.0 * (max_drawdown_episode - 0.30) / 0.30
else:
    r_survival = 0.0
```

The survival penalty is asymmetric: no reward for *not* getting liquidated (that's baseline expected behavior), only punishment for approaching or hitting it. This avoids the agent learning to be so risk-averse it never trades.

### 5.4 Component: Capital Efficiency Bonus (r_efficiency)

```python
# Small bonus for generating theta/premium relative to margin used
# Encourages the agent to learn the wheel/premium-selling concept naturally
daily_theta_collected = portfolio_theta  # theta is negative for option sellers, so positive P&L
theta_on_margin = daily_theta_collected / (margin_used + epsilon)
r_efficiency = 0.1 * tanh(theta_on_margin * 100)  # normalize and clip
```

This is a *shaping reward* — it accelerates learning by rewarding an intermediate behavior (theta collection) that correlates with profitable options strategies. Remove it in Phase 6 once the agent has converged, to test whether it was necessary.

### 5.5 Component: Hedging Quality (r_hedging)

```python
# Small reward for having low absolute portfolio delta when vol is elevated
# Encourages delta-neutral behavior during stressed periods
vol_stress = max(0, iv_atm_30d - 0.60)  # penalize unhedged delta when IV > 60%
delta_penalty = abs(portfolio_delta) * vol_stress
r_hedging = -delta_penalty * 0.1
```

### 5.6 Black Swan Behavior

**The problem:** Standard RL environments will almost never show a 30% crash unless you engineer this. Training on 2019–2023 data, major crashes are rare. The agent needs to have seen enough of them to learn to hedge.

**Solution — Scenario oversampling (mandatory):**

During environment resets, 15% of episodes start within 30 days *before* a known black swan event:
- 2020-03-12 (COVID -50% in 2 days)
- 2022-06-10 to 2022-11-10 (LUNA collapse → FTX collapse)
- 2018-11-14 to 2018-12-15 (Bitcoin Cash fork → -50%)

This isn't cheating — it's curriculum learning. The agent learns that these conditions exist and need hedging. By the time it encounters them in test data, it has a policy for them.

**Additionally:** Inject synthetic shocks. With 5% probability on any episode, randomly insert a -20% to -40% 1-day price shock at a random point. This teaches the agent to always maintain some protective hedge even when things look calm.

### 5.7 Transaction Costs

Use realistic Deribit costs:
- Options taker fee: 0.03% of underlying value per leg (each open/close)
- Options maker fee: 0.02% of underlying value (use maker when possible)
- Perpetual futures fee: 0.05% taker / -0.025% maker (maker rebate)
- Slippage model: 0.1% × position_size_btc / market_depth_factor (simplified)

Subtract all costs from reward immediately at the time of the action. This teaches the agent that churning is expensive.

### 5.8 Time Discounting

Standard RL discount factor γ = 0.99 is appropriate for daily-step environments (100-day "horizon"). For options theta, this means the agent values tomorrow's theta decay almost as much as today's — correct behavior for a premium seller.

Do NOT use γ = 0.999 (too long-horizon for a monthly options strategy) or γ = 0.95 (too myopic — won't learn to hold positions to theta decay).

### 5.9 Dense vs Sparse Rewards

Use **dense rewards** (step-by-step). Options trading with sparse rewards (only reward at episode end) will not converge in reasonable training time. The shaping rewards (efficiency, hedging quality) are critical for early training signal.

However, be aware: dense shaped rewards can cause the agent to optimize the shape rather than the outcome. **After Phase 4, run a version with only r_return + r_survival (no efficiency/hedging shaping) and compare Sharpe ratios on validation set.** Keep whichever version performs better out-of-sample.

---

## 6. Training Environment

### 6.1 Environment Structure

```python
class BTCOptionsEnv(gym.Env):
    """
    Episode: N days of options trading, step = 1 day (or 4h for higher freq)
    
    State: Full observation vector (~125 features)
    Action: (action_type: int, params: np.array[3])
    Reward: Composite as defined in Section 5
    Done: liquidation, or episode length exceeded
    """
    
    # Key parameters
    EPISODE_LENGTH = 90  # days (3 months covers 2-3 options expiries)
    STARTING_CAPITAL = 1.0  # normalize to 1.0 BTC or $X
    MAX_POSITIONS = 10       # option legs simultaneously
    STEP_SIZE = "1D"         # daily steps for v2 (upgrade to 4H in v3)
```

**Why 90-day episodes?** Covers at least 2 monthly expiry cycles. Long enough for the agent to learn multi-leg strategies and theta decay. Short enough to see many episodes per training run.

**Why daily steps?** Options theta decay is primarily a daily phenomenon. 4H steps add complexity (12,000 steps/year vs 365) but don't add much signal for options strategy. Start daily, upgrade later.

### 6.2 Data Handling & Episode Sampling

```python
def reset(self):
    # Sample episode start from train window [2019-04 to 2022-12]
    # 15% probability: start near a black swan event (oversampling)
    # 85% probability: uniform random from train window
    
    start_date = self._sample_start_date()
    self.data_window = self.data.loc[start_date : start_date + 90_days]
    self.portfolio = Portfolio(capital=STARTING_CAPITAL)
```

**Data split (strict):**
- **Train:** 2019-04-01 to 2022-12-31 (~3.75 years)
- **Validation:** 2023-01-01 to 2023-12-31 (1 year — used for hyperparameter tuning)
- **Test:** 2024-01-01 to 2025-12-31 (2 years — touched ONCE at the end)

This gives ~1,370 possible non-overlapping episodes in train, and 365 in validation. With vectorized envs and overlapping episodes, effective data is much larger.

### 6.3 Curriculum Learning

**Phase 3 (simplified env):**
- Action space: SELL_PUT, DO_NOTHING, CLOSE_POSITION only
- No perps, no calls, no complex structures
- Goal: learn that selling puts collects premium and needs margin

**Phase 4 (full action space):**
- Unlock all 15 action types
- Initialize from Phase 3 weights (don't start from scratch)
- This is transfer learning from simple to complex

**Phase 5 (multi-objective):**
- Enable black swan oversampling
- Enable synthetic shock injection
- Full reward function with all 4 components

### 6.4 Vectorized Environments

Use `SubprocVecEnv` from Stable-Baselines3 for parallel environments. On MacBook Pro M3 Max:
- **Recommended:** 8 parallel environments (matches performance cores)
- Each env runs its own episode independently
- PPO collects rollouts from all 8 simultaneously, updates policy in one batch

This multiplies effective training throughput by ~7x (not 8x due to overhead).

### 6.5 Handling the IV Surface

At each training step, the agent needs an IV surface snapshot. Two approaches:

**Option A (build):** Pre-compute the full IV surface for every date in the training window during data pipeline setup. Store as a parquet file (date → IV matrix). Load into memory at training start (~5 GB). Fast at training time.

**Option B (interpolate):** At each step, take the available option quotes and fit a SVI (Stochastic Volatility Inspired) model to the surface. More accurate but ~100ms per step — too slow for training.

**Use Option A** for training. Use Option B for live trading.

---

## 7. Model Architecture

### 7.1 Primary Recommendation: PPO with Dual-Head LSTM Network

```
Input layer (125 features)
    ↓
LayerNorm
    ↓
Linear(125 → 256) + ReLU   [feature extraction]
    ↓
Linear(256 → 256) + ReLU
    ↓
LSTM(256 → 128, num_layers=2)   [temporal memory]
    ↓ (hidden state)
┌──────────────────────┬──────────────────────────────┐
│  Actor head          │  Critic head                 │
│  Linear(128 → 64)    │  Linear(128 → 64)            │
│       ↓              │       ↓                      │
│  Linear(64 → 15)     │  Linear(64 → 1)              │
│  Softmax             │  (value estimate V(s))       │
│  (action type)       │                              │
│       +              │                              │
│  Linear(128 → 64)    │                              │
│  Linear(64 → 3)      │                              │
│  Beta distribution   │                              │
│  params (α, β)       │                              │
│  (continuous params) │                              │
└──────────────────────┴──────────────────────────────┘
```

**Why LSTM?** Options pricing is highly path-dependent. Yesterday's IV spike, this week's funding rate trend, and last month's drawdown all affect what the right action is today. An LSTM with 2-step horizon captures this. Without memory, the agent must re-derive regime from current snapshot alone, which is harder and slower to learn.

**Why not Transformer?** Transformers require fixed-length context windows and are more memory-hungry at inference. For a daily-step system with ~90-day episodes, LSTM is sufficient and trains faster on Apple Silicon. Revisit for v3.

**LSTM sequence length:** Use the last 30 days of observations as the LSTM sequence (rolling window). This gives the agent one month of memory without excessive sequence length.

### 7.2 Hyperparameters (Starting Point)

```python
# PPO hyperparameters
learning_rate = 3e-4
n_steps = 2048          # steps per env before update (with 8 envs: 16K steps/update)
batch_size = 256
n_epochs = 10           # PPO gradient epochs per update
gamma = 0.99
gae_lambda = 0.95
clip_range = 0.2
ent_coef = 0.01         # entropy bonus — encourages exploration
vf_coef = 0.5
max_grad_norm = 0.5

# Network
lstm_hidden_size = 128
lstm_num_layers = 2
mlp_hidden_sizes = [256, 256]
lstm_sequence_length = 30  # days of memory
```

### 7.3 Alternative Architecture: SAC with Hybrid Action Head

For the eventual SAC implementation (Phase 5 parallel experiment):

```
Actor: same network → outputs action_type logits + continuous param distributions
Critic 1 & 2: separate networks (Twin Q-networks, reduces overestimation)
Target networks: soft update τ = 0.005
Replay buffer: 100K transitions
Batch size: 256
Temperature α: auto-tuned (target entropy = -log(1/15) for discrete part)
```

SAC's automatic entropy tuning is particularly valuable here — it will naturally explore more action types early in training and exploit once it finds profitable ones.

### 7.4 What to Borrow from AlphaStar

AlphaStar's key innovations applicable here:

1. **Population-based training (PBT):** Run 4–8 agents simultaneously with different hyperparameters. Each has slightly different risk tolerance, entropy coefficient, and reward weights. The surviving strategies are the ones that work across different market regimes.

2. **Supervised pretraining:** AlphaStar pre-trained on human replays before RL. Analogously: pre-train the network to predict "what a reasonable options seller would do" by supervised learning on a simple rules-based baseline (sell 25-delta puts when IVR > 50, close at 50% profit). This gives RL a warm start — it won't waste the first million steps learning not to trade naked straddles on margin.

3. **Auxiliary tasks:** AlphaStar predicted future game states as auxiliary tasks. Add auxiliary prediction heads: predict IV in 7 days, predict portfolio P&L in 7 days. These auxiliary losses improve the quality of internal representations.

**Do NOT replicate:** Self-play (not applicable), built-in strategy conditioned on opponent (not needed), league training at full scale (too computationally expensive for v2).

---

## 8. Hardware & Training Timeline

### 8.1 Current Hardware: MacBook Pro (M3/M4 Max assumed)

**CPU training (Stable-Baselines3, default):**
- PPO with 8 vectorized envs: ~2,000–5,000 environment steps/second
- The bottleneck is Python gym step simulation, not neural network computation
- Network forward pass is fast (~1ms); env step is slower (~5ms for complex option state)

**With MPS backend (PyTorch on M3/M4 GPU):**
- Neural network inference: 3–5x faster than CPU for medium networks (256-dim MLP + LSTM)
- However: SB3's PPO training loop is not fully MPS-optimized. Expect ~1.5–2x speedup vs CPU in practice due to CPU↔GPU data transfer overhead
- Effective: ~3,000–8,000 steps/second with MPS

**Reality check:** This is our observed v1 training performance on the existing machine. The bottleneck is the environment simulation (pure Python), not the model.

**Environment simulation optimization:** Rewrite the inner step loop using NumPy batch operations (avoid per-step Python loops). Target: 10,000 steps/second with 8 envs.

**Training timeline (MacBook Pro):**

| Target steps | Time @ 5k steps/sec |
|---|---|
| 1M (smoke test) | ~3 minutes |
| 10M (early behavior) | ~30 minutes |
| 100M (convergence for simple env) | ~5.5 hours |
| 500M (full env convergence) | ~28 hours |
| 2B (production quality) | ~5 days |

**Recommendation:** Run 100M-step experiments overnight as unit experiments. Reserve 2B-step runs for weekends.

### 8.2 Future Hardware: Apple Mac Studio M4 Ultra

M4 Ultra = 2× M4 Max dies in one package:
- 32-core CPU vs 14-core (MacBook)
- 80-core GPU vs 40-core (MacBook)
- Up to 192 GB unified memory vs 128 GB

**Expected speedup for PPO training:** ~3–4x on GPU-bound operations, ~2x on CPU-bound (env simulation). Net: expect 2–3x overall speedup.

At 3x speedup: 2B steps in ~40 hours (vs 5 days on MacBook).

**More important benefit:** 192 GB RAM allows 32–64 vectorized environments vs 8 on MacBook. At 32 envs, throughput scales to ~20,000 steps/second. 2B steps in ~28 hours.

**MLX (Apple's ML framework):** Apple's native ML framework may offer 20–40% speedup over PyTorch MPS for this workload. Investigate as an optimization in Phase 5, but don't build v2 on MLX — PyTorch ecosystem compatibility is worth more than 30% speed.

### 8.3 Checkpoint Strategy

```
checkpoints/
├── run_{timestamp}/
│   ├── model_{steps}.zip       # SB3 checkpoint every 1M steps
│   ├── config.yaml             # exact hyperparameters for this run
│   ├── normalizer.pkl          # RunningMeanStd state — critical
│   ├── eval_metrics.csv        # Sharpe, drawdown per checkpoint
│   └── tensorboard/            # training curves
```

**Critical:** Save the normalizer alongside the model. A model evaluated with different normalization statistics will behave arbitrarily differently.

**Comparison between runs:** Use the validation Sharpe ratio at 100M steps as the primary comparison metric. Don't compare by training reward (overfit to reward shaping). Compare on validation environment with same episodes for all runs.

---

## 9. Evaluation & Testing

### 9.1 Metrics Hierarchy

**Primary (use for hyperparameter decisions):**
- **Sharpe Ratio** (annualized, on validation episodes): target > 1.5
- **Max Drawdown** (validation): must stay < 25%
- **Calmar Ratio** (annualized return / max drawdown): target > 1.0

**Secondary:**
- **Sortino Ratio** (downside Sharpe): target > 2.0
- **Win rate** (% of episodes ending above starting capital): target > 65%
- **Avg P&L per trade** (net of costs)
- **Avg episode length before drawdown trigger**: higher is better

**Survival metrics (non-negotiable gates):**
- **Black swan survival rate**: fraction of black-swan-period episodes without liquidation: must be > 95%
- **Crisis Sharpe**: Sharpe ratio computed only on episodes that include a black swan event: must be > -0.5 (i.e., survive without catastrophic loss)

### 9.2 Baselines for Comparison

Every result must be compared against:

| Baseline | Description |
|---|---|
| **Buy & Hold BTC** | Purchase BTC at episode start, hold to end. Should be easy to beat on risk-adjusted basis. |
| **Wheel Bot v1** | Current production bot (sell CSP, take assignment, sell CC). Our actual competition. |
| **Simple delta-neutral** | Sell 25-delta strangles when IVR > 50, close at 50% profit, no hedging. Represents a systematic human strategy. |
| **Random agent** | Uniformly random actions (sanity check — must beat this). |

### 9.3 Black Swan Simulation

Replay these specific periods in test environment:
- **COVID crash (2020-03-01 to 2020-04-15):** -65% peak-to-trough, then rapid recovery
- **2018 bear market (2018-11-14 to 2019-01-01):** Slow grind down -50%
- **LUNA collapse (2022-05-04 to 2022-05-16):** Bitcoin falls 35% in 12 days
- **FTX collapse (2022-11-06 to 2022-11-21):** Bitcoin falls 25% in 2 weeks
- **2021 China mining ban (2021-05-19):** -30% in 24 hours

For each: report whether agent survived (no liquidation), P&L outcome, and whether it hedged proactively or reactively.

### 9.4 Out-of-Sample Test

Test data (2024–2025) is touched exactly once — at project completion. The test methodology:
1. Run 1000 episodes on test data with random start dates
2. Report all 8 metrics above
3. Compare to all 4 baselines
4. If results are materially different from validation, investigate (likely overfitting)

**No hyperparameter tuning based on test results.** If test results are bad, go back to validation and debug. The test set is sacred.

### 9.5 Measuring "Lucky vs Good"

Statistical validation:
- Bootstrap 95% confidence intervals on Sharpe ratio (resample episodes with replacement)
- If CI includes 0.0, the agent is not significantly better than chance
- Run identical evaluation on 3 independently trained agents with different random seeds
- The strategy is "good" only if all 3 agents show positive Sharpe on validation

---

## 10. Data Pipeline Implementation Plan

### 10.1 Recommended Download Strategy

**Step 1: Free data first (test the pipeline)**
- CryptoDataDownload: download all available Deribit BTC options ZIPs (free, ~2GB)
- Fear & Greed: `alternative.me/api/fear-and-greed-index/history` (JSON, instant)
- Coin Metrics free API: MVRV, SOPR, exchange flows (Python: `coinmetrics` library)
- BTC spot OHLCV via CCXT from Bitstamp (hourly, 2017–present)

**Step 2: Deribit API direct (medium effort)**
- Use `deribit_data_collector` library (github.com/schepal/deribit_data_collector)
- Enumerate expired BTC options instruments via `public/get_instruments` with `expired=true`
- For each instrument: fetch OHLCV candles, settlement price, open interest history
- Rate limit: ~5 req/sec → ~10K instruments → ~30 min to enumerate, ~24 hours to fetch all candles
- Store in SQLite first, then export to parquet

**Step 3: Tardis.dev (if budget allows, ~$50–200)**
- Single API call per data type
- Get normalized options snapshots, trades, settlements
- 10–100x faster than DIY Deribit scraping
- **Recommended for saving development time**

### 10.2 Rate Limiting Strategy

```python
import asyncio
from aiohttp import ClientSession

async def fetch_with_backoff(session, url, params, max_retries=5):
    for attempt in range(max_retries):
        async with session.get(url, params=params) as resp:
            if resp.status == 429:  # rate limited
                wait = (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(wait)
                continue
            return await resp.json()
    raise Exception(f"Failed after {max_retries} retries")
```

### 10.3 Storage Format

```
data/
├── raw/
│   ├── deribit_options/       # parquet, partitioned by date
│   ├── deribit_perp/          # funding rates, OI
│   ├── btc_spot_ohlcv.parquet
│   ├── on_chain/              # MVRV, SOPR, etc.
│   └── fear_greed.parquet
├── processed/
│   ├── iv_surface/            # pre-computed daily IV surfaces
│   ├── features/              # pre-computed observation vectors
│   └── episodes/              # pre-segmented training episodes
```

Use **Parquet with snappy compression**. ~5x smaller than CSV, 10x faster to read. DuckDB can query it without loading all into memory — useful for EDA.

### 10.4 IV Surface Computation

For each day, construct the IV surface:
1. Take all active options quotes (bid/ask midpoint)
2. Compute IV using Black-Scholes implied vol (use `py_vollib` or `mibian` library)
3. Fit to a smile model per tenor (linear interpolation across strikes, or SVI parametrization)
4. Extract the key features (ATM IV per tenor, 25-delta RR/BF)
5. Store as a single parquet row per day

This preprocessing step takes ~1–2 hours on full dataset. Run once, cache forever.

---

## 11. Phased Implementation Roadmap

See companion document: `rl-v2-tasks.md`

High-level phases:
- **Phase 1 (Data):** 2–3 weeks. Download, clean, and pre-process all data.
- **Phase 2 (Environment):** 2–3 weeks. Build gym environment with full state + action space.
- **Phase 3 (Baseline):** 1 week. Get simplified agent training and learning *something*.
- **Phase 4 (Full system):** 3–4 weeks. Full feature set, full action space, full reward.
- **Phase 5 (Scale):** 2–3 weeks. Long runs, hyperparameter search, PBT experiments.
- **Phase 6 (Harden):** 2 weeks. Evaluation, comparison, black swan testing, live connection.

---

## 12. Open Questions & Genuine Uncertainties

These are flagged because they have meaningful impact on architecture choices and we don't have confident answers yet:

1. **Data availability before 2019:** We cannot get Deribit options data before March 2019. This means the 2018 bear market is not in the training options data — only BTC spot is. The agent will see the 2018 crash in spot features but not in options-specific experience. We compensate with synthetic shock injection (Section 5.6) but this is an imperfect substitute.

2. **IV surface interpolation accuracy:** For dates/strikes with sparse quotes, the fitted IV surface may be unreliable. Need to validate IV surface quality against known reference points (e.g., published DVOL index from Deribit).

3. **Daily step granularity:** Options markets move on sub-daily timescales. A 24h gap between decisions may mean the agent "wakes up" after a major move with no ability to have hedged. This is a fundamental limitation of daily steps. For v2, accept this. For v3, evaluate 4H steps.

4. **Reward hacking risk:** Even with multi-component reward, the agent may find a strategy that scores well on training reward but fails out-of-sample (e.g., learns to time its trades to avoid the "measured" drawdown window). Validation on held-out data is the primary defense, but some reward hacking may not be detectable until live trading.

5. **Options liquidity modeling:** Deribit has wide bid-ask spreads on far OTM options and near-expiry. Our slippage model (Section 5.7) is simplified. Real slippage may be 2–3x worse than modeled for large positions. This will make live performance worse than backtest.

6. **Regime shift problem:** The agent trained on 2019–2022 data saw three regime types: bull run (2020–21), crash (2022), and recovery. It may have no generalized policy for regimes not seen in training (e.g., a prolonged sideways market at low vol, which 2023 somewhat resembled). This is mitigated but not solved by the broad training window.

---

## 13. Sources & References

**Deribit data availability:**
- [Deribit API Documentation](https://docs.deribit.com/)
- [Tardis.dev — Deribit Historical Data](https://docs.tardis.dev/historical-data-details/deribit) — confirms 2019-03-30 start date
- [Deribit Data Collector (GitHub)](https://github.com/schepal/deribit_data_collector) — Python library for bulk download
- [Deribit Market Data Best Practices](https://support.deribit.com/hc/en-us/articles/29592500256669-Market-Data-Collection-Best-Practices)

**RL algorithms for trading:**
- [Risk-Aware PPO for Options Trading, 2025](https://journals.sagepub.com/doi/10.1177/15741702251398696)
- [Deep RL Strategies in Finance (arxiv 2024)](https://arxiv.org/html/2407.09557v1)
- [PPO vs SAC vs TD3 Algorithm Comparison](https://kindatechnical.com/reinforcement-learning/ppo-vs-sac-vs-td3-choosing-the-right-algorithm.html)

**Reward function design:**
- [Risk-Aware RL Reward for Financial Trading (arxiv 2025)](https://arxiv.org/html/2506.04358v1)
- [Sharpe Ratio Based Reward Scheme in DRL](https://www.researchgate.net/publication/371204505_A_Sharpe_Ratio_Based_Reward_Scheme_in_Deep_Reinforcement_Learning_for_Financial_Trading)
- [Multi-reward Portfolio Optimization, Springer 2025](https://link.springer.com/article/10.1007/s44196-025-00875-8)

**AlphaStar architecture:**
- [Grandmaster level in StarCraft II (DeepMind, Nature)](https://storage.googleapis.com/deepmind-media/research/alphastar/AlphaStar_unformatted.pdf)
- [AlphaStar blog post — Google DeepMind](https://deepmind.google/blog/alphastar-mastering-the-real-time-strategy-game-starcraft-ii/)

**Decision Transformer for trading:**
- [LLM+LoRA as Decision Transformer for Offline RL Trading (2024)](https://arxiv.org/html/2411.17900v1)

**On-chain data:**
- [Glassnode Documentation](https://docs.glassnode.com/) — MVRV, SOPR, institutional tier pricing
- [Coin Metrics](https://coinmetrics.io) — free API alternative for MVRV, SOPR
- [CryptoQuant](https://cryptoquant.com) — exchange flows, free tier

**Open-source RL environments:**
- [BTGym — BackTrader + OpenAI Gym](https://github.com/Kismuz/btgym)
- [FinRL Contests (2025)](https://ietresearch.onlinelibrary.wiley.com/doi/10.1049/aie2.12004)

**Apple Silicon ML performance:**
- [PyTorch MPS on Apple Silicon — Apple Developer](https://developer.apple.com/metal/pytorch/)
- [PyTorch vs MLX on M3 Max benchmark](https://medium.com/@istvan.benedek/pytorch-speed-analysis-on-macbook-pro-m3-max-6a0972e57a3a)

---

*Document version 1.0 — May 2026. Next review: after Phase 2 environment completion.*
