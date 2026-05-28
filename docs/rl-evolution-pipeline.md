# RL Agent — Evolution Pipeline
**Last updated:** 2026-05-28

---

## The Goal

**Maximum annualised ROI with a survival instinct.**

Be as aggressive as possible — maximise return on capital — but never get wiped out by a black swan. Kelly Criterion mentality: bet as big as you can while staying alive. Upside volatility is the product, not a problem.

---

## Evolution Phases

### Phase 1: Foundation (COMPLETE)
*Learn basic options trading mechanics on synthetic data.*

| Run | What Changed | Result |
|-----|-------------|--------|
| PPO 5M | Heston data + DSR reward | -6.05%, 0/10 profitable |
| PPO 50M | More training | -3.70%, 2/10 profitable |
| SAC 5M | Algorithm switch + 16 features + realistic costs | -4.48%, 0/10 profitable |
| SAC 20M | Trade penalty + IV pricing fix | **+1.00%, 4/10 profitable** |

**Lessons learned:**
- PPO hits diminishing returns fast — SAC's replay buffer is worth the slower FPS
- Transaction costs matter enormously — fake costs = fake results
- Option pricing must use IV not RV — critical bug that inflated backtests
- Trade cost penalty works but doesn't fix root cause of overtrading

---

### Phase 2: Fix Overtrading (IN PROGRESS)
*Discrete actions + action masking + low entropy.*

| Run | What Changed | Status |
|-----|-------------|--------|
| V3 10M | MaskablePPO, action masking, ent_coef=0.005, VecNormalize | Training now |

**Expected outcome:** Trades/ep drops from 298 to <100, profitability maintained or improved.

---

### Phase 3: Reward Alignment (NEXT)
*Switch reward to match Steven's actual goal: max ROI with survival floor.*

**Current reward (DSR):** Optimises Sharpe ratio — penalises ALL volatility including upside, doesn't care about return magnitude. Wrong for the goal.

**New reward: Max ROI + Survival**

```
reward = roi_signal + theta_bonus - trade_cost - survival_penalty

roi_signal     = pnl_step / equity              # uncapped, linear, more = better
theta_bonus    = 0.01 * (pnl / capital_at_risk)  # when positioned + earning
trade_cost     = 0.03 per trade                  # cost awareness

survival_penalty:
  0-10% drawdown  → 0.0          (full aggression)
  10-20% drawdown → 0.1 ramping  (warning)
  20-30% drawdown → 2.0 ramping  (strong pain)
  30%+ drawdown   → 10.0         (catastrophic — never be here)
```

**Key differences from DSR:**
- No tanh squashing — big wins get big rewards
- No upside volatility penalty
- No Sharpe/Sortino — those dampen aggression
- Stepped survival floor — not smooth, not gentle

**Plan:**
1. Implement new reward as `reward_mode="max_roi"` in env.py
2. Train V4 with MaskablePPO + new reward (10M steps, ~8 hours)
3. Compare V4 vs V3 on same Heston episodes
4. If V4 shows higher returns without breaching 20% DD → validated

---

### Phase 4: Real Data Fine-Tuning
*Move from synthetic to real Deribit data.*

**Prerequisites:** V3 or V4 profitable on synthetic with trades/ep < 100.

**Plan:**
1. Pretrained model from Phase 2/3 (best performer)
2. Fine-tune on real Deribit data (1,096 days) with low learning rate (1e-5)
3. 1-2M steps (~3-6 hours)
4. Validate on 30% holdout (different time period than training)

**Data limitations:**
- Only 17 days of real Deribit IV — rest is RV proxy
- No IV surface (skew, term structure from actual options chain)
- Fix: Tardis.dev Academic plan (~$300) for full historical options data

---

### Phase 5: Robustness
*Ensemble + stress testing before any real capital.*

1. **Multi-seed ensemble:** Train 3x best model with different seeds, majority vote
   - Expected: halves variance, +0.2 Sharpe, -4% max drawdown
2. **Black swan testing:** Inject -20% to -40% single-day shocks into 5% of episodes
   - Retrain with shock injection, verify survival instinct triggers
3. **Walk-forward validation:** Monthly retrain on rolling 2-year window
   - Test on forward 3-month out-of-sample period
   - Must pass across multiple regimes (bull, bear, sideways)

---

### Phase 6: Multi-Agent Decomposition
*Split monolithic agent into specialised sub-agents (OPHR-style).*

**Prerequisites:** Single agent Sharpe > 1.0 and consistently profitable.

| Agent | Job | Reward |
|-------|-----|--------|
| Timing Agent | When to sell premium (regime awareness) | VRP capture efficiency |
| Execution Agent | Strike/delta/DTE selection | Premium per unit risk |
| Hedge Agent | Position management, early close, rolling | Drawdown minimisation |
| Meta Agent | Regime detection, agent weighting | Portfolio-level ROI + survival |

**Why decompose:**
- Each agent solves a simpler problem
- Rewards can be perfectly aligned per sub-task
- Meta agent's reward IS Steven's goal: max annualised ROI + survive everything

---

### Phase 7: Production
*Paper trading → live with real capital.*

1. Paper trade on Deribit testnet for 4+ weeks
2. Readiness gate: Sharpe > 0.5, max DD < 15%, 4+ weeks of data
3. Go live with minimum capital ($1,000)
4. Target: $5/day sustained → proof the loop works
5. Scale capital only after 3 months of live positive returns

---

## Decision Log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-05-23 | Replace GBM with Heston | GBM has no realistic vol dynamics |
| 2026-05-23 | Switch to DSR reward | Step-level risk adjustment |
| 2026-05-24 | PPO → SAC | 3-5x sample efficiency |
| 2026-05-24 | Add IV surface features (16 obs) | VRP is the wheel's actual edge |
| 2026-05-25 | Realistic Deribit costs | Flat $0.50 was fantasy |
| 2026-05-25 | Add trade cost penalty | Reduce overtrading |
| 2026-05-25 | Fix IV pricing bug | Options priced with RV not IV — fake edge |
| 2026-05-26 | MaskablePPO + action masking | Fix overtrading root cause |
| 2026-05-28 | Redefine goal: max ROI + survival | DSR/Sharpe wrong for Steven's intent |
| 2026-05-28 | Plan: stepped survival penalty | Asymmetric — brutal near danger, zero in safe zone |

---

## Hardware Reality

| Machine | PPO FPS | SAC FPS | 10M PPO | 20M SAC |
|---------|---------|---------|---------|---------|
| Current iMac (i5-6500, 4 core) | ~355-500 | ~80 | ~8 hrs | ~69 hrs |
| Mac Studio M4 Ultra (est.) | ~2,000 | ~300 | ~1.5 hrs | ~18 hrs |

Faster hardware would enable rapid iteration. Current setup works but limits experiment throughput to ~1 run/day for PPO, ~1 run/3 days for SAC.
