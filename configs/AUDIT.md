# Bot Farm Audit -- 2026-05-22

**Before:** 24 configs  **After:** 10 configs  (removed 16, added 3)

---

## Final Lineup

| Bot Name | Strategy | Hypothesis | Status |
|---|---|---|---|
| rl-agent-v1 | RL Agent (PPO) | PPO model trained on 3yr Deribit BTC data. Sharpe 0.33, max_dd 5.35pct on holdout. Core experiment. | kept |
| capital ROI V1 | Wheel (evolved) | Best evolved baseline: iv_rank>0.70, delta 0.19-0.41, DTE 3-37. 242pct backtest return, Sharpe 2.44. Primary RL comparison point. | kept |
| atm_premium_hunter | Wheel (thesis) | Delta 0.30-0.50: sell ATM for max absolute premium. More assignments -- tests whether gross premium beats OTM selectivity. | kept |
| deep_otm_safety | Wheel (thesis) | Delta 0.10-0.20: deep OTM for >90pct win rate. Tests whether high win rate compounds better than raw premium capture. | kept |
| no_hedge_naked | Wheel (thesis) | Fully directional, hedge disabled. Tests whether the delta hedge earns its keep or just damps returns. | kept |
| short_dte_theta | Wheel (thesis) | DTE 1-7 only. Fast capital recycling -- tests whether theta velocity outweighs short-DTE whipsaw risk. | kept |
| long_dte_monthly | Wheel (thesis) | DTE 21-45 only. Smoother decay, fewer assignments -- tests whether monthly cadence suits BTCs ranging regimes. | kept |
| high_iv_only | Wheel (new) | iv_rank>0.70 hard gate, DTE 7-30, delta 0.15-0.35. Hypothesis: IV selectivity improves risk-adjusted returns via vol mean-reversion tailwind. | added |
| close_at_21dte | Wheel (new) | Enter at 40-45 DTE, roll/close at 21 DTE (roll_enabled=true). Hypothesis: 21 DTE rule captures best theta-per-risk portion and avoids gamma blowups. | added |
| rl_agent_stress | RL Agent (new) | Same PPO model, max_daily_drawdown=0.20, max_adverse_delta=0.80, max_loss_per_leg=0.10. Hypothesis: tight guardrails mask or protect the model -- wider limits reveal which. | added |

---

## Removals

| Config | Reason |
|---|---|
| ROI rest 1.yaml | Archived; no hypothesis; params identical to capital ROI V1. |
| Safest V1.yaml | Paper but IDENTICAL params to capital ROI V1 (same iv_rank, delta range, DTE). Near-duplicate. |
| sharpe V1.yaml | Paper but IDENTICAL params to capital ROI V1. Third clone. |
| daily trader V1.yaml | Paper but IDENTICAL params to capital ROI V1. Fourth clone. |
| balanced_20260423_2346.yaml | Archived. No hypothesis beyond evolved balanced. |
| bot_1.yaml | Archived. Migrated placeholder, no hypothesis, no differentiation. |
| bot_2.yaml | Archived. Same as bot_1. |
| capital_roi_20260501_1813.yaml | Paper but Sharpe -4.54, return 2.27pct. No distinct angle. |
| capital_roi_20260503_0733.yaml | Draft. Sharpe -3.38. Poor evolve run, no distinct angle. |
| chaos-tester.yaml | TEST ONLY -- infra/logging test, not a trading hypothesis. Done its job. |
| chaos-hedged.yaml | TEST ONLY companion. Hedge question better answered by no_hedge_naked running long-term. |
| max_stack.yaml | Tests margin utilisation (20 legs), not a trading hypothesis. Engineering test. |
| safest_20260425_1838.yaml | Draft. Near-duplicate of other safest bots with minor delta tweaks. |
| safest_20260425_1839.yaml | Draft. Seeded from chaos-tester, redundant. |
| small_bot_specialist_20260501_1813.yaml | Paper but Sharpe -5.78, return 1.43pct. No distinct hypothesis, terrible metrics. |
| max yield V1.yaml | Corrupted format (top-level fields mixed with structured sections). Same 242pct / Sharpe 2.44 as capital ROI V1 -- likely a broken duplicate. |

---

## What to expect from each bot

**rl-agent-v1** -- Core experiment. Does the PPO policy generalise from backtest to live paper? Watch whether it triggers SELL actions (not just HOLD) and whether Sharpe holds above 0.2 over 30+ days.

**capital ROI V1** -- The human benchmark. If the RL agent cant beat Sharpe 2.44, the model needs retraining or richer features. Cleanest signal for what a well-tuned wheel achieves with IV filtering.

**atm_premium_hunter** -- If it underperforms deep_otm_safety on Sharpe but beats it on absolute return, the wheel is better as an income strategy than a compounder. If both underperform OTM bots, high delta = assignment trap.

**deep_otm_safety** -- Expected smoothest equity curve. If it underperforms atm_premium_hunter on risk-adjusted terms, confirms that premium size dominates win rate in BTCs vol regime.

**no_hedge_naked** -- Direct hedge cost/benefit measurement. Beats hedged bots risk-adjusted = hedge is expensive insurance. Underperforms = hedge is genuinely protective in BTC tail events.

**short_dte_theta** -- Capital recycling velocity test. If weekly cycles produce higher annualised Sharpe than monthly equivalents, frequency beats per-trade premium quality for BTC.

**long_dte_monthly** -- Monthly cadence baseline. Should have fewer but larger loss events than short_dte_theta. Drawdown profile comparison is the main output.

**high_iv_only** -- IV rank as the key lever test. If this outperforms capital ROI V1 on Sharpe despite fewer trades, IV selectivity is the core edge and parameterisation is secondary.

**close_at_21dte** -- Classic rule vs data. If it beats hold-to-expiry equivalents, BTCs gamma environment confirms the theory. If not, BTC premiums are thin enough that every remaining day of theta matters.

**rl_agent_stress** -- RL diagnostic. P&L divergence from rl-agent-v1 shows exactly how much the risk limits are doing. Blowup here is informative. Outperformance here motivates a v2 with wider action space.
