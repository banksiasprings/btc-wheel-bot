# BTC Options RL Agent v2 — Implementation Task List

**Total tasks:** 52  
**Effort key:** S = 1–2h, M = 2–4h, L = 4–8h  
**Phases:** 6 phases, roughly sequential. Some tasks within a phase can run in parallel.

---

## Phase 1: Data Collection & Infrastructure
*Goal: Have all historical data on disk, cleaned, ready for feature engineering.*
*Estimated duration: 2–3 weeks*

| # | Task | Description | Effort |
|---|---|---|---|
| 1 | Set up data directory structure | Create `data/raw`, `data/processed`, `data/episodes` with README for each. Set up `.gitignore` so raw data isn't committed. | S |
| 2 | Download BTC spot OHLCV (2017–2024) | Use CCXT + Bitstamp. Hourly candles. Store as parquet. Write a validation script that checks for gaps > 2h. | S |
| 3 | Download Fear & Greed index | Fetch `alternative.me/api/fear-and-greed-index/history?limit=3000`. Store as parquet. Daily since 2018-02-01. | S |
| 4 | Set up Coin Metrics free API client | Install `coinmetrics-api-client`, authenticate with free key, pull MVRV Z-score, SOPR, and BTC realized price (2017–2024). | M |
| 5 | Download Deribit perp funding rates | Use Deribit API `public/get_funding_rate_history` for BTC perpetual. 8-hour granularity back to 2019. Paginate fully. | M |
| 6 | Download Deribit perp OI history | Use `public/get_open_interest` with historical date range. Note: OI history API depth is limited — combine with Tardis if needed. | M |
| 7 | Enumerate all expired BTC options instruments | Call Deribit `public/get_instruments?currency=BTC&kind=option&expired=true`. Paginate. Save master instrument list as CSV. Expect ~50K+ rows. | M |
| 8 | Build async Deribit OHLCV fetcher | Write async Python script using `aiohttp` with exponential backoff. For each expired instrument: fetch daily OHLCV candles from `public/get_tradingview_chart_data`. | L |
| 9 | Run OHLCV bulk download (multi-day job) | Execute the fetcher from task 8. Checkpoint progress to SQLite so it can resume. Full run: ~24 hours. Monitor rate limit logs. | L |
| 10 | Download Deribit settlement history | Call `public/get_settlement_history_by_currency?currency=BTC&type=delivery`. All settlement prices by expiry. Critical for training signal. | S |
| 11 | Evaluate Tardis.dev for gap-filling | Check Tardis pricing for Deribit BTC options. If < $200, buy. Download via `tardis-client` Python library. Compare against DIY download for completeness. | M |
| 12 | Download macro calendar data | Compile Fed meeting dates and CPI release dates (2019–2026) from BLS and Federal Reserve websites. Store as CSV. Write script to compute `days_to_event` for any date. | S |
| 13 | Data validation and gap analysis | Write a report script: for each trading day 2019–2024, what fraction of the options chain has data? Identify gaps, outliers, suspicious IV values. Flag problem dates. | M |
| 14 | Clean and normalize raw data | Winsorize outlier IV values (cap at 500% annualized). Handle exchange outage gaps (forward-fill for features, mark as missing for training exclusions). | M |

---

## Phase 2: Environment & Backtester
*Goal: A gym-compatible environment that the RL agent can train against.*
*Estimated duration: 2–3 weeks*

| # | Task | Description | Effort |
|---|---|---|---|
| 15 | Compute IV surface for each training day | For each day, take midpoint quotes from options chain, compute BSM implied vols, extract ATM IV per tenor (7/14/30/60/90d), 25-delta RR and BF. Store as parquet. | L |
| 16 | Build feature engineering pipeline | Compute all ~125 observation vector features from raw data. Output: a single parquet file per date with the full feature row. Use vectorized pandas/numpy, target < 1 min to recompute full dataset. | L |
| 17 | Build Portfolio class | Python class tracking: open positions (instrument, strike, expiry, qty, entry price), current Greeks (summed across legs), margin used, unrealized P&L. Include methods: `open_position()`, `close_position()`, `compute_greeks()`. | L |
| 18 | Build Options pricing utility | Black-Scholes pricer for marking portfolio at each step. Inputs: spot, strike, expiry, IV. Output: option price + Greeks. Use `py_vollib` or implement BSM directly. Needs to be fast (vectorized for all positions at once). | M |
| 19 | Build env.py v2 (skeleton) | Create `BTCOptionsEnvV2(gym.Env)`. Implement `reset()`, `step()`, `render()`. Start with hardcoded observation/action shapes. No actual trading logic yet — just structure. Run `check_env()` from SB3. | M |
| 20 | Implement observation vector construction | Wire the feature pipeline into `env._get_obs()`. At each step, look up pre-computed features for current date + portfolio state features. Return full normalized numpy array. | M |
| 21 | Implement action execution logic | Map the (action_type, params) output into actual portfolio operations. Action type 0 = do nothing. Action types 1–6 = open options. Types 7–8 = spot. Types 9–11 = perp hedge. Types 12–14 = position management. | L |
| 22 | Implement reward function | Implement all 4 reward components from architecture doc Section 5. Include transaction cost deduction. Unit test each component in isolation with known inputs/outputs. | M |
| 23 | Implement episode termination conditions | Liquidation (margin exhausted), max episode length (90 days), drawdown > 40% from peak. Test each condition fires correctly with edge-case inputs. | S |
| 24 | Implement black swan oversampling | In `env.reset()`, implement 15% probability of starting near a black swan event. Define the 5 black swan periods from architecture doc. Test distribution of start dates. | S |
| 25 | Implement synthetic shock injection | With 5% probability per episode, inject a -20% to -40% 1-day price shock at a random step. The shock must affect all option prices, IV, and portfolio mark-to-market. | M |
| 26 | Build vectorized env wrapper | Wrap env in `SubprocVecEnv` with 8 parallel instances. Validate that all envs get different random seeds, different episode start dates. Benchmark steps/second. | M |
| 27 | End-to-end backtester validation | Run a deterministic rule-based agent (sell 25-delta puts when IVR > 50, close at 50% profit) through the environment. Validate P&L matches expectations from manual calculation on a specific 90-day window. | L |

---

## Phase 3: Baseline Agent (Simplified)
*Goal: Train the first working agent on a simplified version. Verify learning is happening.*
*Estimated duration: 1 week*

| # | Task | Description | Effort |
|---|---|---|---|
| 28 | Define simplified action space | Reduce to 3 actions: DO_NOTHING, SELL_PUT (25-delta, 30 DTE, fixed 5% capital), CLOSE_ALL_PUTS. Hard-code all parameters — no continuous action head yet. | S |
| 29 | Define simplified observation space | Reduce to 20 most informative features: spot, RV_7d, RV_30d, IVR, ATM_IV_30d, portfolio_delta, margin_buffer, unrealized_pnl, dte_avg, put_call_ratio, regime flags. | S |
| 30 | Train simplified PPO for 10M steps | Use SB3 PPO with default MLP policy (no LSTM yet). 8 envs, 1M steps per overnight run. Plot training reward curve. Goal: reward should trend upward. | M |
| 31 | Diagnose if learning fails | If training reward doesn't improve after 5M steps, run diagnostics: action entropy (is it exploring?), value function loss (is critic learning?), episode length (is it dying early?). Common fix: adjust entropy coeff, reward scale. | M |
| 32 | Compare simplified agent to random baseline | Run 200 validation episodes with trained agent and random agent. Compute Sharpe, win rate, max drawdown. Agent must beat random by a statistically significant margin. | M |

---

## Phase 4: Full Feature Set + Full Action Space
*Goal: Full v2 system with LSTM, all features, all actions, full reward.*
*Estimated duration: 3–4 weeks*

| # | Task | Description | Effort |
|---|---|---|---|
| 33 | Implement LSTM policy network | Subclass SB3's `RecurrentActorCriticPolicy` or use `sb3-contrib` `RecurrentPPO`. Configure 2-layer LSTM, sequence length 30. Test that hidden state carries between steps within an episode and resets on `reset()`. | L |
| 34 | Implement hybrid action head | Actor network outputs: (15-way softmax for action_type) + (3 × Beta distribution for continuous params). Wire into SB3's custom policy. Validate action sampling produces valid ranges. | L |
| 35 | Enable full observation vector | Swap in the full 125-feature observation vector. Re-run `check_env()`. Verify normalization is working — check that features are in roughly [-3, 3] range at start of training. | M |
| 36 | Enable full action space | Unlock all 15 action types. Add position selection logic for CLOSE and ROLL actions. Test that each action type can be executed without crashing the environment. | M |
| 37 | Transfer weights from Phase 3 | Initialize LSTM policy from simplified MLP policy where shapes match (input embedding layers). Verify this warm start converges faster than random init. | M |
| 38 | Add auxiliary prediction heads | Add two auxiliary output heads to the network: (1) predict ATM IV in 7 days, (2) predict portfolio P&L in 7 days. Add auxiliary losses (MSE) to training. Weight: 0.1 × each auxiliary loss. | L |
| 39 | Run 100M step training | First full-system training run. Monitor: entropy should decrease slowly, not spike to zero. Value function loss should converge. Watch for NaN (indicates gradient explosion — add grad clipping). | L |
| 40 | Run reward component ablation | Train 3 variants: (a) full reward, (b) no efficiency bonus, (c) no hedging bonus. Compare validation Sharpe after 50M steps. Determine which components help vs hurt. | L |

---

## Phase 5: Scale Training + Hyperparameter Search
*Goal: Find the best hyperparameters, run long training runs, explore SAC as alternative.*
*Estimated duration: 2–3 weeks*

| # | Task | Description | Effort |
|---|---|---|---|
| 41 | Set up Optuna hyperparameter search | Use Optuna to search over: learning_rate [1e-4, 1e-3], n_epochs [5, 20], ent_coef [0.001, 0.1], gamma [0.97, 0.999], gae_lambda [0.90, 0.99]. Run 20 trials × 20M steps each. Pick best by validation Sharpe. | L |
| 42 | Run 500M step production training | With best hyperparameters from task 41. Save checkpoints every 50M steps. Log validation Sharpe at each checkpoint to detect overfitting. This is the main training run. | L |
| 43 | Implement population-based training (PBT) | Run 4 agents simultaneously with different reward weight configurations (different w1/w2/w3/w4). Evaluate all on validation at 100M steps. Keep best 2, discard others. Re-run with best configs for 500M steps. | L |
| 44 | Implement SAC as parallel experiment | Build hybrid-action SAC using `stable-baselines3` SAC with custom policy. Run 100M steps. Compare final validation Sharpe to PPO at same step count. Document findings — this informs v3 architecture. | L |
| 45 | Benchmark MPS vs CPU training speed | Run identical 10M step training on CPU vs MPS. Record steps/sec. Validate that MPS-trained model produces same policy (check action distributions on same state inputs). | S |
| 46 | Profile environment bottlenecks | Use Python profiler (`cProfile`) on a 10K-step run. Identify the top 3 slowest functions. Rewrite with NumPy vectorization or Numba if > 50% time in Python loops. | M |

---

## Phase 6: Evaluation & Hardening
*Goal: Rigorous evaluation, black swan testing, live connection readiness.*
*Estimated duration: 2 weeks*

| # | Task | Description | Effort |
|---|---|---|---|
| 47 | Run full black swan simulation suite | Replay all 5 black swan periods from architecture doc Section 9.3. For each: 100 episodes with different starting positions. Compute survival rate, avg P&L, max intra-episode drawdown. Document findings. | L |
| 48 | Compare to all 4 baselines | On validation data: run 500 episodes each for: RL agent, buy-and-hold, wheel bot v1, simple delta-neutral, random. Compute all 8 metrics. Build comparison table. | M |
| 49 | Out-of-sample test (run once) | Unlock 2024–2025 test data. Run 1000 episodes. Compute all metrics. Compare to validation metrics — if Sharpe drops > 30%, investigate overfitting. Document and freeze results. | M |
| 50 | Bootstrap confidence intervals | Resample the validation episodes 1000 times (bootstrap). Compute 95% CI on Sharpe ratio. If CI includes 0, the agent is not reliably better than chance — must improve before live. | M |
| 51 | Build live inference module | Write `rl_agent/live_inference.py`. Loads model + normalizer. Takes current market state as input (from existing `deribit_client.py`). Outputs action recommendation. Dry-run against testnet for 1 week. | L |
| 52 | Write final evaluation report | Document: training methodology, hyperparameter choices, validation metrics, black swan results, out-of-sample results, comparison to baselines, known limitations, recommended live position sizing. | M |

---

## Summary by Phase

| Phase | Tasks | Estimated Duration |
|---|---|---|
| Phase 1: Data Collection & Infrastructure | 1–14 (14 tasks) | 2–3 weeks |
| Phase 2: Environment & Backtester | 15–27 (13 tasks) | 2–3 weeks |
| Phase 3: Baseline Agent | 28–32 (5 tasks) | 1 week |
| Phase 4: Full Feature Set & Action Space | 33–40 (8 tasks) | 3–4 weeks |
| Phase 5: Scale Training & Hyperparameter Search | 41–46 (6 tasks) | 2–3 weeks |
| Phase 6: Evaluation & Hardening | 47–52 (6 tasks) | 2 weeks |
| **Total** | **52 tasks** | **~12–16 weeks** |

---

## Critical Path (don't start these late)

The following tasks gate everything downstream — start them early even if the implementation is rough:

1. **Task 7** (enumerate expired instruments) — takes days to run, start immediately
2. **Task 8+9** (bulk OHLCV download) — takes 24h+, run asynchronously while doing other work
3. **Task 15** (IV surface computation) — needed for all feature engineering
4. **Task 17** (Portfolio class) — blocks all of Phase 2
5. **Task 22** (reward function) — must be right before Phase 3 training

---

*Companion to: `docs/rl-v2-architecture.md`*  
*Last updated: May 2026*
