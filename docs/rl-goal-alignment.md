# RL Agent — Goal Alignment
**Last updated:** 2026-05-28

---

## Steven's Goal

**Maximum annualised ROI with a survival instinct.** Be as aggressive as possible — maximise return on capital — but never get wiped out by a black swan. Risk-seeking with a hard floor.

In one line: **Kelly Criterion mentality — bet as big as you can while staying alive.**

Key principles:
1. **Maximise ROI** — absolute return is the primary objective, not risk-adjusted
2. **Upside volatility is good** — a +15% month is the goal, don't dampen it
3. **Survival is non-negotiable** — drawdown beyond a threshold triggers emergency mode
4. **Asymmetric risk** — gentle on profit swings, brutal on approaching wipeout
5. **Annualised** — measured over full year cycles

The $5/day target from CONTEXT.md ($1,825/year on $100k = 1.8% floor) is the minimum proof-of-concept. The real goal is much higher — maximum sustainable ROI.

---

## Current Reward Function Alignment

### Differential Sharpe Ratio (what we use now)

**What it optimises:** Risk-adjusted return at each step. Equivalent to maximising Sharpe ratio online.

**Alignment gap:** Sharpe ratio = return / volatility. A model can score high Sharpe by:
- Earning 0.5% with 0.1% volatility (Sharpe ~5.0 but useless ROI)
- Earning 30% with 10% volatility (Sharpe ~3.0 and excellent ROI)

The DSR doesn't distinguish these. It will happily converge to the low-return, low-risk policy because it's "easier" to optimise.

**Components that help:**
- Theta capture bonus — rewards being positioned (earning premium)
- Trade cost penalty — prevents overtrading

**Components that are missing:**
- No reward for absolute return magnitude
- No penalty for being flat too long in high-IV environments
- No explicit annualised return target

---

## Proposed Reward V2: Max ROI + Survival

For the next training iteration (after V3 evaluation), align the reward with Steven's actual goal:

```
reward = roi_signal                   # PRIMARY: raw return, more is better
       + theta_bonus                  # wheel-specific: reward premium harvesting
       - trade_cost_penalty           # cost awareness
       - survival_penalty             # HARD FLOOR: catastrophic near wipeout

where:
  # Primary: step P&L as fraction of equity — uncapped upside
  roi_signal = pnl_step / equity
  # No squashing, no tanh — let big wins be big rewards

  # Theta: reward for being positioned and earning decay
  theta_bonus = 0.01 * max(0, pnl_step / capital_at_risk) if positioned

  # Trade cost: same as current
  trade_cost_penalty = 0.03 if traded

  # Survival: asymmetric, non-linear, brutal near danger zone
  if drawdown > 0.30:          # 30% drawdown = emergency
      survival_penalty = 10.0  # catastrophic — learn to never be here
  elif drawdown > 0.20:        # 20% = danger zone
      survival_penalty = 2.0 * (drawdown - 0.20) / 0.10
  elif drawdown > 0.10:        # 10% = warning
      survival_penalty = 0.1 * (drawdown - 0.10) / 0.10
  else:
      survival_penalty = 0.0   # under 10% DD = no penalty at all
```

**Key design decisions:**

1. **No Sharpe/Sortino.** These penalise volatility — Steven wants maximum return, not minimum variance. Upside swings are the goal.

2. **Uncapped ROI signal.** The DSR squashes returns through tanh, killing the gradient for big wins. Raw return fraction preserves "more profit = more reward" linearly.

3. **Stepped survival penalty.** Not a smooth quadratic — a hard escalating floor:
   - 0-10% drawdown: full aggression, zero penalty
   - 10-20%: gentle warning signal
   - 20-30%: strong pain, agent learns to cut risk
   - 30%+: catastrophic penalty — equivalent to "you're fired"

4. **No idle penalty.** Being flat when conditions are bad is survival intelligence, not laziness.

5. **No upside dampening.** A +8% episode reward should be 8x larger than a +1% episode, not squashed to similar values through tanh.

**What this produces:** An agent that swings hard when conditions are right (high IV, favourable skew) and cuts exposure aggressively when drawdown approaches 20%. Maximum aggression inside the survival envelope.

---

## Multi-Agent Decomposition Plan (Future)

When the single agent is consistently profitable, decompose into specialised agents:

| Agent | Job | Reward Aligned To |
|-------|-----|-------------------|
| **Timing Agent** | When to sell premium | VRP capture efficiency |
| **Execution Agent** | Strike/delta/DTE selection | Premium per unit risk |
| **Hedge Agent** | Position management | Drawdown minimisation |
| **Meta Agent** | Regime detection, agent weighting | Overall portfolio Sortino |

Each sub-agent optimises a simpler, more aligned objective. The meta agent's reward IS Steven's goal: stable high annualised ROI measured by portfolio-level Sortino ratio.

**Prerequisites before decomposition:**
1. Single agent consistently profitable (V3 or V4)
2. Phase 2 fine-tuning on real Deribit data complete
3. Understanding of which decisions the single agent gets wrong most often

---

## Implementation Priority

| Step | What | When |
|------|------|------|
| 1 | Evaluate V3 (current run) | Today |
| 2 | If V3 trades/ep < 100 and profitable → fine-tune on real data | This week |
| 3 | Switch reward to Sortino-ROI hybrid | Next training cycle |
| 4 | Multi-seed ensemble (3 seeds) | After reward change validated |
| 5 | Multi-agent decomposition | When single agent Sharpe > 1.0 |
| 6 | Tardis.dev data for full IV surface | When ready for production quality |
