# RL Agent — Training Results & Evolution Log
**Last updated:** 2026-05-28

---

## Training Run History

### Run 1: PPO 5M (Baseline)
**Date:** 2026-05-23 | **Duration:** 1.5 hours | **Algorithm:** PPO

| Setting | Value |
|---------|-------|
| State space | 12 features |
| Actions | 5 discrete |
| Data | Heston stochastic vol (domain-randomised) |
| Reward | Differential Sharpe Ratio (eta=0.01) |
| Transaction costs | $0.50 flat per contract |

| Metric | Result |
|--------|--------|
| Avg return | -6.05% |
| Profitable episodes | 0/10 |
| Trades/episode | 350 |
| Action dist | 25% hold, 33% sell put 020, 8% sell put 025, 14% sell call, 20% close |

**Takeaway:** Model learns diverse actions but loses money. Baseline established.

---

### Run 2: PPO 50M (Extended)
**Date:** 2026-05-23–24 | **Duration:** 15.2 hours | **Algorithm:** PPO

Same settings as Run 1, continued training.

| Metric | Result | vs Run 1 |
|--------|--------|----------|
| Avg return | -3.70% | +2.35% |
| Profitable episodes | 2/10 | +2 |
| Trades/episode | 352 | ~same |
| Max return | +3.02% | first profitable episodes |

**Takeaway:** Improving but log-linear returns — 10x compute for 2.3% improvement. PPO hitting diminishing returns.

---

### Run 3: SAC 5M (Algorithm Switch)
**Date:** 2026-05-24–25 | **Duration:** 16.4 hours | **Algorithm:** SAC

| Setting | Change from Run 1-2 |
|---------|---------------------|
| Algorithm | PPO → SAC (off-policy, replay buffer) |
| State space | 12 → 16 features (added VRP, skew, term structure, 30d RV) |
| Actions | Continuous [-1,1] mapped to 5 discrete |
| Transaction costs | $0.50 flat → Deribit realistic (0.03% taker + 2% spread) |
| DSR eta | 0.01 → 0.002 |

| Metric | Result | vs PPO 5M |
|--------|--------|-----------|
| Avg return | -4.48% | +1.57% (at same step count) |
| Profitable episodes | 0/10 | same |
| Trades/episode | 438 | +88 (overtrading!) |
| Action dist | 6% hold, 47% sell call, 32% close | call-heavy, barely holds |

**Takeaway:** SAC more sample-efficient than PPO but severe overtrading. Continuous-to-discrete mapping + high auto-tuned entropy = agent takes random actions constantly. Need trade cost penalty.

---

### Run 4: SAC 20M (Trade Cost Penalty)
**Date:** 2026-05-25–28 | **Duration:** 73.9 hours | **Algorithm:** SAC

| Setting | Change from Run 3 |
|---------|---------------------|
| Trade cost penalty | Added -0.03 reward per trade |
| Option pricing | Fixed: uses actual IV instead of realised vol (critical bug fix) |
| Data pipeline | Added funding rate fetch from Deribit API |

| Metric | Result | vs SAC 5M |
|--------|--------|-----------|
| **Avg return** | **+1.00%** | **+5.48%** |
| **Profitable episodes** | **4/10** | **+4** |
| Trades/episode | 298 | -140 (penalty working) |
| Max return | +8.07% | strong upside |
| Min return | -3.33% | tighter downside |
| Std return | 3.58% | lower variance |
| Action dist | 36% hold, 22% sell put 020, 11% sell put 025, 16% sell call, 15% close | much more balanced |

**Takeaway:** First profitable model. Trade penalty reduced overtrading from 438→298 but still ~3x too high. Action distribution now balanced. The combination of more training + trade penalty + IV pricing fix drove profitability.

---

## All Models Comparison

| Model | Avg Return | Profitable | Trades/ep | Hold % | Best Episode |
|-------|-----------|-----------|-----------|--------|-------------|
| PPO 5M | -6.05% | 0/10 | 350 | 25% | -1.88% |
| PPO 50M | -3.70% | 2/10 | 352 | 25% | +3.02% |
| SAC 5M | -4.48% | 0/10 | 438 | 6% | -0.20% |
| **SAC 20M** | **+1.00%** | **4/10** | **298** | **36%** | **+8.07%** |
| V3 (pending) | ? | ? | ? | ? | ? |

---

## Prediction vs Actual (SAC 20M)

| Metric | Predicted | Actual | Accuracy |
|--------|-----------|--------|----------|
| Avg return | -1.20% | +1.00% | Directionally right, underestimated |
| Profitable | 3/10 | 4/10 | Close |
| Trades/ep | 90-120 | 298 | Way off — overtrading persists |
| Hold % | 45% | 36% | Directionally right |
| Min return | -6.0% | -3.33% | Conservative (actual better) |
| Max return | +3.5% | +8.07% | Underestimated significantly |

---

## Key Fixes Applied (Chronological)

| Date | Fix | Impact |
|------|-----|--------|
| 2026-05-23 | Heston + jump-diffusion data (replaced GBM) | Realistic vol dynamics |
| 2026-05-23 | Differential Sharpe Ratio reward | Risk-adjusted step-level learning |
| 2026-05-24 | SAC algorithm (replaced PPO) | 3-5x sample efficiency |
| 2026-05-24 | 16 features (VRP, skew, term structure, 30d RV) | IV surface signal |
| 2026-05-24 | DSR eta 0.01 → 0.002 | Tuned for weekly options cycle |
| 2026-05-25 | Deribit realistic transaction costs | 0.03% taker + 2% spread |
| 2026-05-25 | Trade cost penalty (-0.03 per trade) | Reduced overtrading |
| 2026-05-25 | Option pricing uses actual IV (not RV) | Critical bug fix |
| 2026-05-25 | Funding rate data in pipeline | New data signal |

---

## V3 Fixes (Pending Evaluation)

| Fix | Rationale |
|-----|-----------|
| MaskablePPO (discrete actions) | Eliminates wasted exploration on invalid actions |
| Action masking | Can't sell when positioned, can't close when flat |
| ent_coef=0.005 (fixed, not auto) | Root cause fix for overtrading |
| VecNormalize (obs + reward) | Running mean/std > manual clipping |
| Layer norm on networks | More stable gradients |

**Hypothesis:** V3 should reduce trades/ep from 298 to <100 while maintaining or improving profitability.

---

## Discovered Root Causes of Poor Performance

1. **GBM synthetic data** — no fat tails, no vol clustering. Agent never saw realistic dynamics. Fixed with Heston.
2. **Reward too simple** — daily PnL with flat penalties. Fixed with Differential Sharpe Ratio.
3. **Option pricing used RV, not IV** — created artificial edge that wouldn't exist in live trading. Fixed.
4. **Transaction costs unrealistic** — $0.50 flat vs real Deribit ~$8.50. Fixed.
5. **SAC entropy too high** — auto-tuned entropy encourages diverse actions = overtrading. V3 fix pending.
6. **Continuous-to-discrete action mapping** — SAC optimises smooth space but only 5 points matter. V3 fix pending.
7. **No action masking** — agent wastes capacity learning that "sell when positioned" does nothing. V3 fix pending.

---

## Research References

| Paper/Resource | Used For |
|---------------|----------|
| OPHR (NeurIPS 2025) | Multi-agent volatility trading architecture |
| Moody & Saffell (NeurIPS 1998) | Differential Sharpe Ratio reward |
| SimBa (ICLR 2025) | Layer norm + residual connections for RL |
| Meta-SAC | Entropy temperature tuning |
| Revisiting Discrete SAC | Proper discrete action SAC |
| HEC Montreal (2025) | IV surface features for options RL |

Full research details in `rl-v2-research.md`.
