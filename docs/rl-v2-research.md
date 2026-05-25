# RL Agent v2 — Research & State of the Art
**Date:** 2026-05-23
**Status:** Research complete, ready to inform implementation planning

---

## 1. Current Agent (v1) Summary

| Aspect | Current State |
|--------|--------------|
| Algorithm | PPO (stable-baselines3) |
| State space | 12 features (price, IV rank, position state, momentum, realised vol) |
| Action space | 5 discrete (hold, sell put 0.20d, sell put 0.25d, sell call 0.20d, close) |
| Training data | Real Deribit BTC daily data (1,096 days, May 2023 - May 2026) + GBM synthetic fallback |
| Reward | daily_pnl_fraction - drawdown_penalty - idle_penalty |
| Training | 2M steps on CPU (~2.5-3 hours), single environment |
| Evaluation | Sharpe > 0.3, max drawdown < 20% |
| Integration | Drop-in `RLStrategy` class for bot.py |
| Status | Trained, passes quality gates, ready for paper trading |

---

## 2. Algorithm Research: PPO vs SAC vs TD3

### SAC (Soft Actor-Critic) — Recommended Upgrade

SAC consistently outperforms PPO in trading contexts. In ensemble benchmarks, SAC achieved a composite backtest score of 1.302 vs PPO's 0.914. Key advantages:

- **Off-policy**: Reuses past transitions from a replay buffer rather than discarding after each update. 3-5x more sample efficient.
- **Entropy-regularized exploration**: Prevents premature convergence to a single strategy — critical in non-stationary markets.
- **Discrete variant available**: SAC-Discrete handles our 5-action space natively.

TD3 scored 1.042 in the same benchmark — between PPO and SAC. Excels at continuous action spaces but advantage reduced for discrete actions.

### Ensemble Approaches

Hi-DARTS (2025) uses a meta-agent to dynamically activate specialised sub-agents based on detected market regime. Achieved 25.17% return with 0.75 Sharpe on AAPL (Jan 2024 - May 2025).

Practical option: train PPO + SAC in parallel, use whichever performs better per-regime.

---

## 3. State Space: Missing Features

### Tier 1 — High Impact (add these)

| Feature | Why | Signal |
|---------|-----|--------|
| ATM IV level | Raw vol regime, not percentile-ranked | Direct options pricing input |
| 25-delta put skew | Market fear gauge, crash protection demand | Skew predicts assignment risk |
| Term structure slope | Front vs back month IV | Contango/backwardation in vol |
| Variance Risk Premium (VRP) | IV minus RV — **this is literally what the wheel harvests** | Core edge signal |
| Funding rate / basis | Crypto-specific leveraged positioning signal | Directional sentiment |

Research from HEC Montreal (2025) shows integrating the evolving IV surface "amplifies responsiveness to changes in market expectations."

### Tier 2 — Medium Impact

| Feature | Why |
|---------|-----|
| Regime indicator | Simple vol regime classifier (low/med/high) from rolling RV + DVOL |
| Order book imbalance | Bid-ask spread and depth at tradeable strikes |

### Current Features (Keep As-Is)
Price, momentum (5d/20d), realised vol, position state, IV rank — reasonable baseline.

Going from 12 to ~17 features with the Tier 1 additions gives high signal density.

---

## 4. Reward Function: Replace with Differential Sharpe Ratio

### Current Problems
- `daily_pnl - drawdown - idle` is too simple and produces brittle policies
- Idle penalty pushes agent to trade when sitting out is correct (low IV)
- Drawdown penalty (0.0005) is too light relative to base reward
- No alignment with the actual edge being harvested (theta decay)

### Recommended Replacement

**Differential Sharpe Ratio** (Moody & Saffell, NeurIPS 1998): Computes a step-level, online approximation of the Sharpe ratio. Trains risk-adjusted behaviour at every timestep. Agents trained on this produce "more consistent returns" than raw profit maximisers.

Composite reward structure:
```
reward = differential_sharpe_ratio
       + theta_capture_bonus         (premium collected / time)
       - assignment_risk_penalty     (scaled by how far ITM at assignment)
       - rolling_drawdown_penalty    (proportional to drawdown over rolling window)
```

Key change: **Drop the idle penalty**. When IV is low, being flat is correct. Replace with opportunity cost: penalise only when IV is high and you're not positioned.

Research supports this — composite reward with annualised return + downside risk + differential return achieved max drawdown of only 5.03% vs 45.57% for baseline approaches.

---

## 5. Training Data: Synthetic Upgrade

### Problem with GBM
GBM produces normally distributed returns without:
- Fat tails (BTC regularly has 10-20% daily moves)
- Volatility clustering
- Regime changes

Training on GBM and deploying on real markets is a known failure mode — the agent has never seen the dynamics that actually matter.

### Replacement: Heston + Jump-Diffusion

| Model | What It Adds |
|-------|-------------|
| Heston | Stochastic volatility, vol clustering, mean reversion in vol |
| Merton jumps | Fat tails, sudden crashes/rallies |
| Domain randomisation | Randomise model params across episodes (vol-of-vol, jump intensity, mean reversion speed) |

### Curriculum Learning Protocol
1. **Phase 1**: Heston with narrow parameter ranges (gentle markets)
2. **Phase 2**: Widen ranges, introduce jumps
3. **Phase 3**: Add regime switches (alternating low-vol and high-vol periods)
4. **Phase 4**: Fine-tune on real Deribit data with small learning rate

Key insight from 2025 research: "overreliance on high-fidelity simulation leads to brittle policies" — broad randomisation beats a perfect simulator.

---

## 6. Multi-Timeframe & Hierarchical RL

### OPHR (NeurIPS 2025) — Most Relevant Paper
Multi-agent RL for volatility trading, tested on Deribit BTC/ETH options 2021-2024:
- **Option Position Agent**: Volatility timing (when to be long/short vol)
- **Hedger Routing Agent**: Risk management strategy selection
- Significantly outperformed baselines. Code released on GitHub.

### Practical Hierarchy for Wheel Strategy
- **High-level agent** (daily): Regime detection, target delta/DTE parameters
- **Low-level agent** (per-expiry cycle): Strike selection, roll timing, close decisions

**Pragmatic shortcut**: Add a simple regime signal (DVOL > 80 = high vol, < 40 = low vol) that conditions the policy. Captures 80% of the benefit at 10% of the complexity.

---

## 7. Common Pitfalls & Failure Modes

1. **Overfitting to historical regimes** — "RL methods suffer from considerable overfitting, with trained models prone to memorizing history instead of learning generalizable policies."
2. **Non-stationarity** — Financial markets violate i.i.d. assumptions. Distribution shifts make optimal policies harmful.
3. **Backtest overfitting** — "If you tune adaptation logic on past market regimes, you risk overfitting to the sequence of regimes that happened historically."
4. **Sim-to-real gap with GBM** — Agent has never seen fat tails, vol clustering, or liquidity gaps.
5. **Sparse reward signals** — Daily PnL is noisy; credit assignment is difficult.

### Mitigations
- Train on multiple synthetic regimes with domain randomisation
- Maintain portfolio of policies trained on different regimes, select based on detected current regime
- Implement continuous adaptation (online fine-tuning with small learning rate)
- Validate on held-out data from a *different regime* than training
- Walk-forward validation, not random train/test splits

---

## 8. Sim-to-Real Transfer Tips

1. Use many imperfect simulators with randomised parameters, not one perfect one
2. Pretrain on synthetic, fine-tune on real with reduced learning rate
3. Match observation statistics — normalise features the same way for synthetic and real
4. Start at 10-25% of target position size in live while collecting performance data
5. Monitor feature distribution drift — alert when live diverges from training
6. Iterative cycles: simulate → deploy small → collect real data → retrain with real data mixed in → repeat

---

## 9. Prioritised Recommendations

| Priority | Change | Impact | Complexity |
|----------|--------|--------|------------|
| 1 | Replace GBM with Heston + jumps | Very High | Low |
| 2 | Replace reward with differential Sharpe ratio | High | Low (~20 LOC) |
| 3 | Add IV surface features (ATM IV, skew, term structure, VRP) | High | Low (4 new features) |
| 4 | Switch PPO to SAC | High | Medium |
| 5 | Curriculum learning (easy to hard markets) | Medium-High | Medium |
| 6 | Add regime conditioning in state | Medium | Low |
| 7 | Remove idle penalty, add theta capture bonus | Medium | Low |
| 8 | Fine-tune on real Deribit data after synthetic pretraining | Medium | Medium |
| 9 | Expand action space (more deltas, DTEs) | Medium | Medium |
| 10 | Hierarchical architecture (OPHR-style) | High | High |

**If you do only three things:** #1, #2, #3. These address the most fundamental weaknesses (unrealistic training data, misaligned reward, missing key features) with minimal architectural disruption.

---

## 10. Key References

| Paper/Resource | Relevance |
|---------------|-----------|
| OPHR (NeurIPS 2025) — Multi-agent RL for volatility trading on Deribit | Most directly relevant — architecture, state space, tested on BTC/ETH |
| Hi-DARTS (2025) — Hierarchical dynamically adapting RL trading | Meta-agent regime switching |
| Moody & Saffell (NeurIPS 1998) — Differential Sharpe ratio | Reward function design |
| HEC Montreal (2025) — Deep hedging with IV surface | State space design for options |
| Risk-Aware RL Reward (2025) — Composite risk-adjusted rewards | Reward function components |
| Diffusion-Augmented RL (2025) — Domain randomisation for trading | Sim-to-real transfer |
| SAC-Discrete — Soft Actor-Critic for discrete actions | Algorithm upgrade path |

---

## 11. Relationship to Existing v2 Docs

This research validates and extends the existing `rl-v2-architecture.md` and `rl-v2-tasks.md`. Key alignments:
- Architecture doc's emphasis on IV surface features confirmed by research
- Task list Phase 1 (data collection) is prerequisite for many recommendations here
- Heston + jumps should be added to the training environment tasks
- Differential Sharpe ratio should replace the reward function specified in the architecture doc
