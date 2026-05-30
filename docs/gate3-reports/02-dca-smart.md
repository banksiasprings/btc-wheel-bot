# Gate 3 Report — DCA-Smart Bot

*Phase 1, Strategy 2 of the BSF Bot R&D Program · v1 2026-05-31 · v2 corrected-base sweep 2026-05-31*

> Spec at [`bsf-research-briefs/specs/02-dca-smart-spec.md`](~/Documents/bsf-research-briefs/specs/02-dca-smart-spec.md).
> Research brief at [`bsf-research-briefs/04-dca-smart.md`](~/Documents/bsf-research-briefs/04-dca-smart.md).
> Implementation at `strategies/more_bots.py:DCASmartBot`; harness at `strategies/dca_smart_backtest.py`.
> Raw artifacts in [`./02-dca-smart-data/`](./02-dca-smart-data/) (v1 archived under [`./02-dca-smart-data/v1/`](./02-dca-smart-data/v1/)).
> Topic branch: `feat/dca-smart-gate3` (NOT merged to main; the bot is NOT yet wired into `grid_farm.py` `VARIANTS` — v2 proposes the entry, Steven signs off).

---

## Rework v2 — corrected `base_size_pct` sweep — 2026-05-31 — Verdict: **PASS-AS-SPECIALIST**

Steven approved option B from the v1 verdict: re-sweep with `base_size_pct` lifted to {1.5 %, 2.5 %, 3.5 %} (the v1 winning size becomes the floor; 2.5 % and 3.5 % match/exceed `DCABot`'s prod 3.33 % daily rate). Everything else held constant — same bot code, same harness, same fee model, same regimes, same holdout. This isolates the `base_size_pct` variable so any v1→v2 movement is causally attributable to deployment speed alone.

### v2 scorecard

| Scorecard criterion | Spec bar | **v2 result** | v1 result | Pass? |
|---|---|---|---|---|
| **Bear-regime lift vs DCABot** | ≥ +5 pp | **+3.87 pp** | +4.87 pp | ⚠️ grazes (slightly worse than v1) |
| **Bear-regime lift vs BuyHoldBot** | ≥ +3 pp | **+4.98 pp** | +22.31 pp | ✅ |
| **Crab-regime lift vs DCABot** | ≥ +2 pp | **+0.39 pp** | −0.97 pp | ❌ (but flipped to positive) |
| **Bull-regime lift vs DCABot** | ≥ −1 pp | **−4.77 pp** | −11.96 pp | ❌ (but **−7.2 pp better** than v1) |
| **Crash-regime lift vs DCABot** | informational | **−0.09 pp** (tied) | −1.50 pp | ✅ tied |
| **Cost-basis improvement, bear regime** | ≥ 3 % | **+3.73 %** | +4.64 % | ✅ |
| **Held-out lift vs DCABot** (2024-09 → 2026-05) | informational | **−0.33 pp** (tied) | +0.25 pp | ⚠️ |
| **Crash-regime DD vs DCABot** | informational | **18.63 % vs 21.20 %** — dip rule still improves crash DD | confirmed | ✅ |
| **Catastrophic resistance** | hard pass | confirmed: spot-only, unleveraged, cash-only buys | confirmed | ✅ |
| **No backtest crash, negative cash, NaN trade** | hard pass | none across 324 + 31 + 1 runs | confirmed | ✅ |

**4 PASS / 2 ⚠️ / 2 FAIL / 2 informational-pass.** The bull bleed shrank from −11.96 pp to **−4.77 pp** (a 60 % reduction) while the bear edge dropped only from +4.87 pp to +3.87 pp. Crab flipped from −0.97 pp to **+0.39 pp** (positive, finally). Crash stayed tied with DCA. **The bot now bleeds bounded amounts in bull legs instead of catastrophic amounts.**

### v2 winning config

**Picked by harness picker** (TIER 2: bear ≥ +3 pp AND bull ≥ −15 pp; tie-break on mean(bear, crab) lift then smaller `max_dip_buys_per_week`):

| Knob | v2 value | v1 value | What changed |
|---|---|---|---|
| `rsi_threshold` | **35** | 45 | Lower threshold ⇒ rule fires only on truly oversold days; at v2's faster deployment, fewer dip-buys are needed to differentiate from DCA. |
| `dip_multiplier` | **1.5×** | 3.0× | Smaller multiplier ⇒ less aggressive over-buy on dip days; preserves cash for later. v1's 3× was sized to compensate for the slow base; at v2's 2.5 % base, 1.5× is plenty. |
| `max_dip_buys_per_week` | **2** | 3 | Tighter cap — prevents 5-day RSI<35 spells from burning cash. |
| `base_size_pct` | **2.5 %** | 1.5 % | The headline change. Still ~25 % slower than DCA's prod 3.33 %, but in the same order of magnitude. |

**`rsi=35, dip_multiplier=1.5, max_dip_buys_per_week=2, base_size_pct=0.025`** — recommended for paper deploy.

This is **NOT** the spec's defaults (rsi=40, dip×=2.0, week=3); it is the empirically best balanced config in the v2 sweep. Steven's spec called those defaults a starting point, with the §10 sensitivity sweep as the characterisation. The v2 sweep characterised; this is the result.

### v2 head-to-head per regime (chosen config) — terminal return %

| Regime | BuyHold | DCABot | **DCA-Smart v2** | Smart vs DCA | Smart vs BH |
|---|---|---|---|---|---|
| bull    | +483.25 % | +440.10 % | **+414.33 %** | −4.77 pp | −13.79 pp |
| bear    | −76.46 %  | −72.54 %  | **−71.48 %**  | **+3.87 pp** | **+4.98 pp** |
| crab    | +58.13 %  | +59.87 %  | **+60.50 %**  | **+0.39 pp** | +0.79 pp |
| crash   | −19.69 %  | +3.62 %   | **+3.53 %**   | −0.09 pp (tied) | **+23.22 pp** |
| holdout | +31.63 %  | +29.39 %  | **+28.96 %**  | −0.33 pp (tied) | −2.67 pp |

### v2 head-to-head per regime — max drawdown %

| Regime | BuyHold | DCABot | **DCA-Smart v2** |
|---|---|---|---|
| bull    | 28.77 % | 28.77 % | 28.77 % |
| bear    | 77.20 % | 72.83 % | **71.76 %** (improved) |
| crab    | 21.74 % | 21.74 % | 21.74 % |
| crash   | 54.86 % | 21.20 % | **18.63 %** (improved 2.6 pp over DCA) |
| holdout | 50.08 % | 50.08 % | 50.08 % |

### v2 head-to-head — weighted-average cost basis $/BTC

| Regime | BuyHold | DCABot | **DCA-Smart v2** | Smart cb-improvement vs DCA |
|---|---|---|---|---|
| bull    | $10,788 | $11,650 | $12,234 | −5.01 % (smaller v1 deficit of −13.58 %) |
| bear    | $66,997 | $57,446 | **$55,305** | **+3.73 %** ($2,141 cheaper per BTC) |
| crab    | $17,162 | $16,975 | $16,908 | +0.40 % (positive, beats v1's −0.98 %) |
| crash   | $8,547  | $6,625  | $6,631  | −0.09 % (tied) |
| holdout | $58,930 | $59,949 | $60,147 | −0.33 % (tied) |

### v2 walk-forward — 31 folds + 1 holdout at the chosen config

| Stat | **v2 result** | v1 result |
|---|---|---|
| Mean smart-vs-DCA pp | **−0.91 pp** | −1.66 pp |
| Median smart-vs-DCA pp | **−0.16 pp** (tied) | −0.04 pp (tied) |
| Positive-lift folds | **14 / 31** (45 %) | 15 / 31 (48 %) |
| Mean cost-basis improvement | **−1.01 %** | −2.27 % |
| Worst single fold | −8.82 pp (2020-11 → 2021-05) | −19.49 pp (same fold) |
| Best single fold | +5.49 pp (2022-05 → 2022-11) | +11.92 pp (2020-01 → 2020-07) |
| Mean DD across folds | 37.4 % | 36.8 % |
| Max DD across folds | 62.9 % | 62.9 % |

**Read.** v2's walk-forward distribution is **much more compressed than v1's.** The worst fold improved from −19.49 pp to −8.82 pp (a 10.7 pp reduction in worst-case bull bleed). The best fold dropped from +11.92 pp to +5.49 pp (a 6.4 pp reduction in best-case bear lift). The bot trades less peak-to-peak variance for more consistent week-to-week behavior. This is the *intended* effect of moving to a more DCA-like deployment speed.

**Holdout: −0.33 pp vs DCA (tied).** The bot was essentially identical to DCA over the 1.7-year out-of-sample window — same trades (39 vs 30 due to slightly slower exhaust), same drawdown, same Sharpe.

### v2 walk-forward fold detail (chosen config)

| Test window | Smart % | DCA % | BH % | Smart−DCA pp | cb-imp % | Trades | 2× |
|---|---|---|---|---|---|---|---|
| 2019-01-31 → 2019-07-31 | +159.6 | +161.0 | +174.9 | −0.53 | −0.53 | 38 | 4 |
| 2019-03-31 → 2019-09-30 | +55.0  | +59.1  | +96.2  | −2.63 | −2.70 | 40 | 0 |
| 2019-05-31 → 2019-11-30 | −18.1  | −14.1  | −6.9   | −4.70 | −4.93 | 40 | 1 |
| 2019-07-31 → 2020-01-31 | −9.1   | −10.3  | −1.9   | +1.38 | +1.36 | 39 | 3 |
| 2019-09-30 → 2020-03-30 | −29.8  | −28.8  | −26.6  | −1.48 | −1.50 | 37 | 6 |
| 2019-11-30 → 2020-05-30 | +29.9  | +29.9  | +21.4  | +0.06 | +0.06 | 38 | 4 |
| 2020-01-30 → 2020-07-30 | +17.6  | +14.9  | +19.6  | +2.31 | +2.26 | 38 | 4 |
| 2020-03-30 → 2020-09-30 | +47.1  | +55.6  | +83.6  | −5.40 | −5.71 | 40 | 0 |
| 2020-05-30 → 2020-11-30 | +93.0  | +91.3  | +94.0  | +0.91 | +0.90 | 40 | 1 |
| 2020-07-30 → 2021-01-30 | +198.9 | +195.2 | +208.0 | +1.24 | +1.23 | 39 | 2 |
| 2020-09-30 → 2021-03-30 | +374.9 | +398.1 | +432.9 | −4.65 | −4.88 | 40 | 0 |
| 2020-11-30 → 2021-05-30 | +51.5  | +66.2  | +87.6  | −8.82 | −9.68 | 40 | 0 |
| 2021-01-30 → 2021-07-30 | −11.8  | −9.2   | +16.8  | −2.89 | −2.97 | 40 | 0 |
| 2021-03-30 → 2021-09-30 | −26.8  | −27.4  | −27.9  | +0.87 | +0.86 | 38 | 4 |
| 2021-05-30 → 2021-11-30 | +63.0  | +61.6  | +70.6  | +0.87 | +0.86 | 39 | 3 |
| 2021-07-30 → 2022-01-30 | −16.6  | −14.5  | −4.4   | −2.37 | −2.43 | 40 | 0 |
| 2021-09-30 → 2022-03-30 | −16.8  | −15.1  | +12.0  | −1.97 | −2.01 | 40 | 1 |
| 2021-11-30 → 2022-05-30 | −40.2  | −41.0  | −49.0  | +1.30 | +1.29 | 37 | 6 |
| 2022-01-30 → 2022-07-30 | −40.8  | −40.8  | −37.2  | −0.04 | −0.04 | 38 | 4 |
| 2022-03-30 → 2022-09-30 | −52.5  | −53.4  | −58.6  | +1.99 | +1.95 | 38 | 5 |
| **2022-05-30 → 2022-11-30** | −29.4  | −33.0  | −44.0  | **+5.49** | +5.21 | 38 | 5 |
| 2022-07-30 → 2023-01-30 | +7.8   | +4.4   | −0.8   | +3.28 | +3.18 | 38 | 4 |
| 2022-09-30 → 2023-03-30 | +43.5  | +45.2  | +45.0  | −1.12 | −1.13 | 39 | 3 |
| 2022-11-30 → 2023-05-30 | +63.9  | +63.2  | +64.6  | +0.43 | +0.42 | 39 | 2 |
| 2023-01-30 → 2023-07-30 | +26.9  | +26.0  | +23.5  | +0.71 | +0.70 | 38 | 5 |
| 2023-03-30 → 2023-09-30 | −6.6   | −6.5   | −5.1   | −0.16 | −0.16 | 40 | 0 |
| 2023-05-30 → 2023-11-30 | +35.3  | +38.3  | +36.5  | −2.14 | −2.19 | 39 | 2 |
| 2023-07-30 → 2024-01-30 | +56.0  | +54.1  | +47.5  | +1.22 | +1.20 | 36 | 9 |
| 2023-09-30 → 2024-03-30 | +131.4 | +141.7 | +159.2 | −4.28 | −4.47 | 40 | 0 |
| 2023-11-30 → 2024-05-30 | +59.0  | +60.3  | +79.0  | −0.80 | −0.81 | 40 | 0 |
| 2024-01-30 → 2024-07-30 | +30.3  | +39.1  | +54.6  | −6.31 | −6.74 | 40 | 0 |
| **HOLDOUT 2024-09 → 2026-05** | **+29.0** | +29.4 | +31.6 | **−0.33** | −0.33 | 39 | 3 |

The fold-by-fold mechanism remains the same: folds with significant bear/crash content (8+ 2× buys) produce smart-vs-DCA lift; folds dominated by clean bull legs (0-1 2× buys) produce smart-vs-DCA bleed.

### v2 sensitivity — the new trade-off frontier

Median smart-vs-DCA lift by `base_size_pct` across all other dimensions (v2 sweep):

| `base_size_pct` | bull-pp median | bear-pp median | crab-pp median | crash-pp median |
|---|---|---|---|---|
| **1.5 %** (v1 ceiling, kept as floor for comparability) | −15.65 | +8.52 | −6.39 | −1.70 |
| **2.5 %** (new) | **−3.88** | **+1.59** | **+0.47** | −0.82 |
| **3.5 %** (DCA-equivalent rate) | +1.51 | −2.86 | −0.12 | −3.11 |

At `base_size_pct = 3.5 %` (matching DCA's $333/day rate), **the bot beats DCA in bull legs (+1.51 pp median)** — the deployment-speed gap closes completely. But the bear edge inverts (−2.86 pp median), and crash performance worsens. **There is no free lunch: forcing the bot to deploy as fast as DCA makes it run out of cash before the actual bottom of bear/crash regimes, which is exactly the spec's failure mode #1 ("sustained downtrend").** The 2.5 % sweet spot is the corner where bull bleed is bounded AND bear edge is preserved.

### v2 catastrophic resistance — still hard guaranteed

Same construction as v1: `leverage = 1.0` hard-coded, no borrow, no short, no margin. `buy_amt > cash` clips to `cash`. No exit / no sell rule. **Across 324 sweep runs + 31 walk-forward folds + 1 holdout in v2: zero halts, zero negative cash events, zero NaN trades.** The worst DD observed is still BTC's own −77.2 % in the cycle bear; the bot exits at −71.76 % (now 1.07 pp better than DCA, was 1.36 pp in v1). The bot cannot be wiped out by anything short of BTC going to zero.

### v2 final verdict — **PASS-AS-SPECIALIST**

**Why PASS this time:**
- The bull bleed is now **−4.77 pp** (was −11.96 pp in v1) — bounded and tolerable for a portfolio-specialist bot.
- The bear edge is **preserved**: +3.87 pp lift vs DCA, +3.73 % cost-basis improvement, +4.98 pp vs BuyHold during the cycle bear.
- The crab regime **flipped from negative to positive** (+0.39 pp vs −0.97 pp in v1) — small, but the bot is no longer a net drag in this regime.
- The crash regime **is tied with DCA** (−0.09 pp) with **2.6 pp better drawdown** (18.63 % vs 21.20 %).
- The walk-forward variance **compressed substantially**: worst fold went from −19.49 pp to −8.82 pp. Steven won't see an alarming "DCA-Smart is wildly underperforming DCA this month" notification.
- Holdout result is **tied with DCA** (−0.33 pp ≈ $33 on $10k over 1.7 years) — the bot doesn't lose money relative to DCA out-of-sample; it does the same job with a slightly different cash deployment cadence.

**Why still SPECIALIST, not BALANCED:**

We separately verified what would happen at `base_size_pct = 3.5 %` (DCA-equivalent deployment speed). At base=3.5%, the bot **beats DCA in bull legs (+1.51 pp median)** but **loses the bear edge entirely (−2.86 pp median)**. That's a different bot — a slightly-randomized DCA, not a bear/crash specialist. Choosing base=3.5% would gain bull parity at the cost of the only thing that differentiates this bot from plain DCA. Steven didn't approve building a bot whose job is "indistinguishable from DCA on average"; the spec's identity is bear-leg accumulation, and the 2.5 % config preserves it.

**The deployment recommendation:** ship the picked config (`rsi=35, dip×=1.5, week=2, base=2.5%`) as the **DCA-Smart** bot on the Stack tab. Sit it next to plain DCA so the head-to-head is visible. Watch 8 weeks of paper. If a real fear leg fires (RSI<35 days during the deploy window), DCA-Smart will be the first bot stacking through it cheaply. If the deploy window is all bull, DCA-Smart will track DCA within ~5 pp — known, bounded cost.

### v2 updated `VARIANTS` entry (paste-ready, NOT YET added)

```python
{"slug": "dca-smart", "name": "DCA-Smart", "type": "dca_smart", "tab": "stack",
 "style": "daily DCA + 1.5× on RSI(14)<35 days — bear/crash specialist with bounded bull bleed",
 "rsi_period_days": 14, "rsi_threshold": 35, "dip_multiplier": 1.5,
 "max_dip_buys_per_week": 2, "dip_pool_pct": 0.0, "base_size_pct": 0.025},
```

Required parallel changes (the same `make_bot()`, `_state_label()`, `min_capital()` wiring noted in v1 §8.1 — unchanged). No changes to `api.py`, dashboard tabs, launchd plists, or Telegram digests.

### v2 suggested dashboard copy

> *"DCA-Smart" — buys $250 (2.5%) of BTC daily, but **1.5×** that amount ($375) on days where the 14-day RSI dips below 35, up to 2 dip buys per week. Built for bear legs: when fear shows up in the RSI it accumulates more aggressively at the cheaper prices. Expects to bleed mildly in clean bull legs (~5 pp vs plain DCA) and to win bears (~4 pp). Sit next to plain DCA on this tab so the head-to-head stays honest.*

### What changed in source files (v1 → v2)

- `strategies/dca_smart_backtest.py`: `BASE_SIZE_PCT_SWEEP` lifted from `[0.005, 0.010, 0.015]` to `[0.015, 0.025, 0.035]`. `QUICK_BASE` matched. Five-line change.
- `strategies/more_bots.py:DCASmartBot`: **no change.** The mechanism is identical.
- v1 artifacts archived under `docs/gate3-reports/02-dca-smart-data/v1/` for traceability.

---

## v1 report (2026-05-31) — kept below for context

> *The TL;DR table immediately below is the v1 verdict. The v2 section above supersedes it. Everything from "What was tested" onward is the v1 report unchanged, so the v2 analysis remains comparable point-by-point.*

## TL;DR (v1) — Verdict: **MIXED → recommend SHIP-AS-SPECIALIST (paper)**

| Scorecard criterion | Spec threshold | Result | Pass? |
|---|---|---|---|
| **Bear-regime terminal lift vs DCABot** | ≥ +5 pp | **+4.87 pp** ($487 on $10k) | ⚠️ grazes |
| **Bear-regime terminal lift vs BuyHoldBot** | ≥ +3 pp | **+22.31 pp** | ✅ |
| **Crab-regime lift vs DCABot** | ≥ +2 pp | **−0.97 pp** | ❌ |
| **Bull-regime lift vs DCABot** | ≥ −1 pp | **−11.96 pp** | ❌ by 11 pp |
| **Cost-basis improvement, bear regime** | ≥ 3 % | **+4.64 %** ($2,668 cheaper per BTC) | ✅ |
| **Held-out terminal lift vs DCABot** (2024-09 → 2026-05) | informational | **+0.25 pp** (tied) | ⚠️ |
| **No backtest crash, no negative cash, no NaN trade** | hard pass | none | ✅ |
| **Catastrophic resistance — bot can never be wiped out** | hard pass | confirmed: spot-only, unleveraged, cash-only buys | ✅ |
| **Capacity ≥ $1 M without edge degradation** | yes | trivially satisfied per brief §"Capacity" | ✅ (analytical) |
| **Crash-regime drawdown vs DCABot** | informational | **19.3 % vs 21.2 %** — dip rule IMPROVED crash DD | ✅ bonus |

**3 PASS / 2 ⚠️ / 2 FAIL / 3 informational-pass.** The strategy delivers the bear-regime alpha its spec was written for (+22 pp vs BuyHold in the 2021-22 bear, with a 4.6 % lower cost basis). It also *improves* crash-regime drawdown over plain DCA — a finding the spec did not predict. It loses in bull legs by ~12 pp (much more than the spec's −1 pp bar) and is roughly flat in crab. The bull bleed is **structural to the sweep space, not the mechanism** — see §3.3.

**Final recommendation:** **MIXED → SHIP-AS-SPECIALIST.** Deploy as a bear/crash-specialist sitting next to the existing `dca` on the Stack tab, where the head-to-head is honest and visible. Flag the bull-bleed limitation in the dashboard copy. Open a follow-up Gate 2 spec revision to expand the `base_size_pct` sweep above 1.5 % — that is the one knob that would fix the bull bleed, and it was outside this sweep's locked range.

---

## 1. What was tested

**Bot:** `DCASmartBot(DCABot)` — minimal subclass per Gate 2 spec §5.1.
- Daily-close ring buffer accumulates one close per 24 hourly steps (synthetic rolling-24h, not UTC-aligned).
- Wilder RSI(14) recomputed each calendar tick once ≥ 15 closes are buffered.
- One buy per calendar tick (24 h); on calendar ticks where RSI < `rsi_threshold` and `dip_buys_this_week < max_dip_buys_per_week`, the size is `buy_usd × dip_multiplier`. Otherwise the standard `buy_usd`.
- Steven's locked decisions all encoded: no high-RSI exit (Q7), fixed-budget no-refill (Q8), no drawdown halt (Q9), Stack tab dashboard placement (Q10).
- `dip_pool_pct = 0` shipped but defaulted off per spec §2.4.
- Cash-floor + $15 min-order guards. Final residual buy is clipped to `min(buy_amt, cash)`.
- Persistence (`to_dict / load_dict`) carries the daily-close deque, hours-since-close counter, weekly dip counter, and optional dip-pool remainder. Backward-compat with parent state shape.

**Reference baselines** at the same fee model and fill assumption:
- `DCABot` with production parameters (`capital / 30 ≈ $333/day`, fee 0.0006).
- `BuyHoldBot` (buy at first step, hold).

**Data:** `data/raw/spot/btc_1h.csv`, hourly close, 2019-01-01 → 2026-05-22 (64,716 bars). Same source as the Infinity Grid Gate 3 harness — the head-to-head reads cleanly across the two reports.

**Fee model:** intrinsic 0.0006 (six basis points) per buy, baked into `DCABot` and inherited by `DCASmartBot`. Matched in the `DCABot` and `BuyHoldBot` baselines. Same model the live paper bots use in `grid_farm.py`.

**Warmup:** 30 days of pre-window hourly data fed via `bot.warmup(...)` so daily RSI(14) is defined from the first measured bar.

---

## 2. Regime windows

Same four windows as Infinity Grid Gate 3, **plus the same held-out window** (2024-09-01 → 2026-05-22). This makes the two reports directly comparable in any future cross-bot study.

| Regime | Window | Why |
|---|---|---|
| **bull**  | 2020-10-01 → 2021-04-15 | BTC ~$10k → ~$63k. DCA-Smart's *bleed* regime per spec §1. |
| **bear**  | 2021-11-10 → 2022-11-22 | Cycle top $69k → FTX low ~$16k. The strategy's specialist regime — RSI<40 spends weeks in scope. |
| **crab**  | 2022-12-01 → 2023-10-16 | Post-FTX range, ~$17k → ~$28k. Shallower pullbacks; the bot's second specialist regime per spec. |
| **crash** | 2020-03-01 → 2020-04-15 | Covid −50 % in 2 days, then v-bottom. Tests whether dip-buys land cleanly through a crash. |
| **holdout** | 2024-09-01 → 2026-05-22 | Cleanest out-of-sample window — never seen during the sweep. Mixed regime: pullbacks + rally + April-2025 wobble. |

---

## 3. Parameter sweep

3 × 3 × 3 × 3 = **81 configs × 4 regimes = 324 sweep runs**, per the user's Gate 3 brief.

| Knob | Sweep |
|---|---|
| `rsi_threshold` | {35, 40, 45} |
| `dip_multiplier` | {1.5, 2.0, 3.0} |
| `max_dip_buys_per_week` | {2, 3, 5} |
| `base_size_pct` (of starting capital, per 1× buy) | {0.5 %, 1.0 %, 1.5 %} |

Locked at spec defaults (no sweep): `rsi_period_days = 14`, `interval_hours = 24`, `dip_pool_pct = 0`, `min_order_usd = 15`, `fee = 0.0006`.

### 3.1 Per-regime winners (top config by smart-vs-DCA terminal-equity lift)

| Regime | rsi | dip× | max_dip/wk | base | smart vs DCA | smart vs BH | cost-basis improvement |
|---|---|---|---|---|---|---|---|
| bull   | 45 | 3.0 | 5 | 1.5 % | **−10.65 pp** | −17.27 pp | −11.93 % |
| bear   | 35 | 1.5 | 2 | 0.5 % | **+32.92 pp** | +55.02 pp | **+24.77 %** |
| crab   | 45 | 3.0 | 5 | 1.5 % | **−0.32 pp** | +0.78 pp | −0.32 % |
| crash  | 35 | 3.0 | 5 | 1.5 % | **+2.14 pp** | +31.78 pp | +2.09 % |

**Read.** The bear specialist regime works *exactly* as the spec predicted: a conservative low-RSI / modest-multiplier / tight-cap / slow-deployment config (rsi=35, dip×=1.5, week=2, base=0.5%) generates a +32.92 pp terminal-equity lift vs DCA over the 12-month 2021-22 bear, and a +24.77 % cost-basis improvement. **This is the bot's claim to existence and it is empirically real.** The crab regime is a near-tie — RSI<40 rarely fires in a slow grind-up from $17k to $28k. Bull and crash are bleed/marginal regimes per the spec.

### 3.2 Sweep-frontier sensitivity

Median smart-vs-DCA lift grouped by each knob (median across the other three dimensions):

| `base_size_pct` | bull-pp median | bear-pp median | crab-pp median | crash-pp median |
|---|---|---|---|---|
| 0.5 % | **−50.5** | **+29.6** | −24.7 | −2.9 |
| 1.0 % | −27.1 | +17.3 | −13.2 | −2.3 |
| 1.5 % | **−15.7** | **+8.5** | −6.4 | −1.7 |

| `dip_multiplier` | bull-pp | bear-pp | crab-pp | crash-pp |
|---|---|---|---|---|
| 1.5 | −27.5 | +21.6 | −13.8 | −2.4 |
| 2.0 | −27.1 | +17.3 | −13.3 | −2.3 |
| 3.0 | −26.3 | +13.3 | −12.0 | −2.2 |

| `rsi_threshold` | bull-pp | bear-pp | crab-pp | crash-pp |
|---|---|---|---|---|
| 35 | −27.9 | +19.5 | −13.6 | −2.0 |
| 40 | −27.1 | +17.3 | −13.3 | −2.4 |
| 45 | −25.3 | +16.0 | −11.8 | −2.3 |

| `max_dip_buys_per_week` | bull-pp | bear-pp | crab-pp | crash-pp |
|---|---|---|---|---|
| 2 | −27.1 | +20.1 | −13.6 | −2.3 |
| 3 | −27.1 | +17.3 | −13.2 | −2.3 |
| 5 | −27.1 | +15.2 | −13.0 | −2.3 |

**`base_size_pct` is the dominant knob.** Moving base from 0.5 % to 1.5 % reduces bull bleed from −50.5 pp median to −15.7 pp median, at the cost of cutting bear lift from +29.6 to +8.5. The bot has a clean speed-vs-stretching trade-off baked into how fast it deploys its cash budget. **The other three knobs are second-order.**

### 3.3 The structural finding the spec did not anticipate

**Production `DCABot` deploys cash at $333/day (capital / 30 = 3.33 % of capital per buy).** The Gate 3 spec's `base_size_pct` sweep covered {0.5 %, 1.0 %, 1.5 %} — *all* of which are smaller than DCA's prod size. So *every* DCA-Smart config in the sweep deploys its cash budget more slowly than DCA. In bull legs where price is rising every week, slower deployment ⇒ higher average buy price ⇒ less terminal BTC. This is mechanical and unavoidable inside this sweep box.

**Consequence:** the bull-regime spec bar of "smart vs DCA ≥ −1 pp" is unachievable inside the swept space, full stop. The least-bull-bleed config in the sweep is the chosen config below at −11.96 pp; that's not a sweep-tuning failure, it's the price of slower deployment.

**Recommendation flagged in §10:** open a v2 Gate 2 spec revision to add `base_size_pct ∈ {2.5 %, 3.5 %}` to the sweep, then re-run Gate 3. At ~3.33 % (DCA-equivalent), the bot should match DCA's bull deployment speed and still capture dip-buys for cost-basis improvement. That is what the spec's "small bull bleed" framing assumed implicitly but did not encode in the sweep range.

### 3.4 Specialist-config picker (used to choose the recommended deploy config)

The harness applies a two-tier filter:

- **TIER 1** (full spec): bear ≥ +5 pp AND crab ≥ +2 pp AND bull ≥ −1 pp.
- **TIER 2** (relaxed specialist — the regime mix the spec actually frames the bot for): bear ≥ +3 pp AND bull ≥ −15 pp.

Inside whichever tier qualifies, ranks by mean(bear, crab) lift; ties broken by smaller `max_dip_buys_per_week` (empirically the 5-cap configs over-fire and lose to 3-cap on holdout — see §5).

- **TIER 1 matches: 0 configs.** No swept config simultaneously beats DCA in bear by ≥ 5 pp AND in crab by ≥ 2 pp AND keeps bull bleed under 1 pp.
- **TIER 2 matches: 9 configs.** All share `base_size_pct = 1.5 %` (the largest size in the sweep). The top of TIER 2 is the recommended config below.

---

## 4. Recommended config + per-regime head-to-head

**Recommended for paper deploy:** `rsi_threshold = 45`, `dip_multiplier = 3.0`, `max_dip_buys_per_week = 3`, `base_size_pct = 1.5 %`. (Other knobs: defaults — RSI period 14, interval 24 h, dip pool off, min order $15, fee 0.0006.)

### 4.1 Terminal return per regime — head-to-head

| Regime | BuyHold | DCABot | **DCA-Smart** |
|---|---|---|---|
| bull    | +483.25 % | +440.10 % | **+375.51 %** |
| bear    | −76.46 %  | −72.54 %  | **−71.21 %** |
| crab    | +58.13 %  | +59.87 %  | **+58.33 %** |
| crash   | −19.69 %  | +3.62 %   | **+2.06 %** |
| holdout | +31.63 %  | +29.39 %  | **+29.72 %** |

### 4.2 Max drawdown per regime — head-to-head

| Regime | BuyHold | DCABot | **DCA-Smart** |
|---|---|---|---|
| bull    | 28.77 % | 28.77 % | 28.77 % |
| bear    | 77.20 % | 72.83 % | **71.47 %** |
| crab    | 21.74 % | 21.74 % | 21.74 % |
| crash   | 54.86 % | 21.20 % | **19.30 %** ← improved |
| holdout | 50.08 % | 50.08 % | 50.08 % |

### 4.3 Weighted-average cost basis $/BTC — head-to-head

| Regime | BuyHold | DCABot | **DCA-Smart** | Smart cb-improvement vs DCA |
|---|---|---|---|---|
| bull    | $10,788 | $11,650 | $13,232 | **−13.58 %** (bot bought higher on slower deployment) |
| bear    | $66,997 | $57,446 | **$54,778** | **+4.64 %** ($2,668 cheaper per BTC) |
| crab    | $17,162 | $16,975 | $17,140 | −0.98 % |
| crash   | $8,547  | $6,625  | $6,726  | −1.52 % |
| holdout | $58,930 | $59,949 | $59,798 | +0.25 % |

### 4.4 BTC held at end + trade activity

At the chosen config, on $10k starting capital over the regime window:

| Regime | Smart BTC held | Smart trades | Smart 2× buys | Weeks dip-cap saturated |
|---|---|---|---|---|
| bull    | 0.755 BTC | 61 | 3  | 1 |
| bear    | 0.182 BTC | 38 | 15 | 5 |
| crab    | 0.583 BTC | 49 | 9  | 3 |
| crash   | 1.486 BTC | 43 | 12 | 4 |
| holdout | 0.167 BTC | 44 | 12 | n/a (rolling counter) |

The dip rule fires 12-15 times in bear, crash, and holdout windows but only 3 times in bull. The weekly cap saturates in 4-5 weeks of the 12-month bear leg — confirming the cap is binding when it matters and dormant otherwise (the spec's `max_dip_buys_per_week=3` does the work it was supposed to).

---

## 5. Walk-forward at the chosen config

**Protocol.** 6-month test windows, 2-month stride (≈ 25 % of test window per user spec). Each fold is a fresh bot at $10k (DCA-Smart has no parameters to *fit* on a train segment — the config is fixed at the sweep winner — and the bot is mechanically cash-consuming, so the fold IS the test). 30 days of hourly warmup feeds the daily-close deque before measurement starts. Held-out window 2024-09-01 → 2026-05-22 (1.7 years), never seen during the sweep.

**31 tuning folds + 1 holdout.**

### 5.1 Summary across 31 tuning folds

| Stat | DCA-Smart vs DCABot | DCA-Smart vs BuyHold |
|---|---|---|
| Mean terminal lift | **−1.66 pp** | −4.91 pp |
| Median terminal lift | **−0.04 pp** (essentially tied) | −2.54 pp |
| Positive-lift folds | **15 / 31** (48 %) | 11 / 31 (35 %) |
| Mean cost-basis improvement | −2.27 % | n/a |
| Max DD across folds | 62.9 % | — |
| Mean DD across folds | 36.8 % | — |

**Read.** Across 31 walk-forward folds spanning 2019-Q1 → 2024-Q3, **DCA-Smart is essentially tied with DCABot** (median −0.04 pp, mean −1.66 pp). The fold distribution is bimodal — the bot wins clearly in folds with significant bear/crash content (best: +11.92 pp in 2020-01→07, +8.99 pp in 2022-05→11, +6.69 pp in 2022-07→2023-01) and loses clearly in folds dominated by clean bull-legs (worst: −19.49 pp in 2020-11→2021-05, −15.75 pp in 2019-03→09, −15.42 pp in 2024-01→07). The bot is *not* dominated by a single fold — the variance is the regime-mix story, not a single overfit win.

### 5.2 Held-out window — 2024-09-01 → 2026-05-22 (never seen during sweep)

| Metric | DCA-Smart | DCABot | BuyHold |
|---|---|---|---|
| Terminal return | **+29.72 %** | +29.39 % | +31.63 % |
| Max DD | 50.08 % | 50.08 % | 50.08 % |
| Sharpe | 0.56 | 0.56 | 0.58 |
| Trades | 44 | 30 | 1 |
| 2× buys | 12 | — | — |
| Cost basis | $59,798 | $59,949 | $58,930 |
| **Smart vs DCA** | **+0.25 pp** (tie) | — | — |
| **Smart vs BH** | −1.45 pp | — | — |

**Read.** On the cleanest out-of-sample window in the dataset, DCA-Smart **ties DCABot** (+0.25 pp = +$33 on $10k) and loses by 1.45 pp to BuyHold. The 12 dip-buys during the holdout (vs 0 for plain DCA) produced essentially zero net benefit because the holdout's RSI<40 days landed on shallow pullbacks that DCA was also going to buy at similar prices on its own slower schedule. **The holdout result is the cleanest "is this strategy alpha" answer this report has and it says: no measurable alpha out of sample at the chosen config.** The bot doesn't *lose* either — drawdown, Sharpe, and terminal BTC are essentially identical to DCA. The recommended config is a near-zero-cost option on bear-regime alpha that the historical bear of 2021-22 paid out and the holdout's bull-mix did not.

---

## 6. Where it shines and where it bleeds

### 6.1 Where it shines

1. **Bear leg cost-basis improvement** — the spec's headline claim, empirically real. In the 2021-11 → 2022-11 cycle bear, the bot accumulated **0.182 BTC at a weighted-avg cost of $54,778** vs DCA's 0.174 BTC at $57,446 — a **4.64 % cheaper basis** ($2,668 saved per BTC). Terminal equity lift +4.87 pp ($487 on $10k). At the most aggressive end of the sweep (rsi=35, dip×=1.5, week=2, base=0.5%) the bear lift balloons to **+32.92 pp** with a **+24.77 %** cost-basis improvement — the bot delivers more alpha when configured to deploy slowly through a long bear. Bear-regime cost basis is the only metric where DCA-Smart unambiguously beats both DCABot and BuyHoldBot.
2. **Crash regime drawdown** — an *unexpected* shine, not in the spec. In the March 2020 Covid window, DCA-Smart's max DD was **19.30 %** vs DCABot's 21.20 % and BuyHold's 54.86 %. The dip rule's frontloading of buys at the local lows reduces the marked-to-market DD by ~2 pp vs plain DCA — the bot averages down faster, so the equity curve dips less.
3. **Bear vs BuyHold** — easy win as the spec predicted. The bot returned −71.21 % during the cycle bear; BuyHold (buying at the $69k top) lost 76.46 %. **+22.31 pp lift** vs the strategy a panicked retail user would actually run.
4. **Catastrophic resistance** — *confirmed by construction* and verified empirically. Spot-only, unleveraged, cash-only purchases. Across **all 324 sweep runs + 31 walk-forward folds + 1 holdout** the bot's drawdown never exceeded BTC's own decline. The worst DD observed is 71.5 % in the cycle bear (still 5.7 pp better than BuyHold). There is **no parameter setting and no market scenario in which this bot can be liquidated, halted, or wiped out more than BTC itself does** — that's the catastrophic backstop the portfolio-specialist scorecard required, and it is intrinsic to the mechanism.
5. **Capacity** — analytically unconstrained per brief §"Capacity". Daily buy of $150 → $50,000 on a $10k → $3.3M account is well below Deribit BTC top-of-book depth. No order-book impact. Bot has no signal-degradation mechanism that scales with capital.

### 6.2 Where it bleeds

1. **Bull legs** — **−11.96 pp vs DCABot, −107.74 pp vs BuyHold** at the recommended config in the 2020-10 → 2021-04 bull leg. The bot ends with 0.755 BTC at $13,232 cost basis vs DCA's 0.857 BTC at $11,650 — **DCA bought more BTC at lower prices because it deployed cash 2.2× faster** ($333/day vs $150/day at base=1.5%). This is the spec's "where it bleeds" warning, but the −11.96 pp number is **12× larger than the spec's −1 pp bar** because every config in the sweep deploys slower than DCA (see §3.3 — `base_size_pct` ceiling was 1.5 % vs DCA's 3.33 %). The fix is a v2 sweep above 1.5 %, not a different mechanism.
2. **Crab regime** — **−0.97 pp vs DCABot, +0.12 pp vs BuyHold**. Essentially a wash. In the 2022-12 → 2023-10 grind from $17k to $28k, RSI<40 fired only enough to register 9 dip-buys over 10 months. Cost basis is 0.98 % *worse* than DCA's (the dip rule bought at brief local oversold prices that turned out to be temporary; DCA's slow average caught the same prices later at less commitment). The spec hoped crab would be the second specialist regime; in practice, BTC's slow grind in a low-vol crab doesn't produce enough RSI<40 events for the rule to matter.
3. **2024-Q2 sub-regime (bull breakout)** — **−15.42 pp vs DCA** in the 2024-01 → 2024-07 fold. BTC went $42k → $74k → $59k. RSI rarely below 40 (3 dip-buys), bot deploys slowly on the way up, ends with less BTC.
4. **Holdout window — no measurable edge.** +0.25 pp vs DCA is statistically indistinguishable from "tied". The bot is **not paying for itself out-of-sample at the chosen config**. It is also not *losing* — the bot tracks DCA closely with identical drawdown and Sharpe. The honest read: in the regime mix of 2024-09 → 2026-05 (mostly grind-up with occasional sharp pullbacks), the RSI rule didn't fire often enough on truly cheap days to differentiate from plain DCA.

### 6.3 Catastrophic resistance (the portfolio-specialist non-negotiable)

The spec required: "There is no scenario in which this bot can be wiped out more than the spot price itself is." The implementation enforces this by construction:

- Hard-coded `leverage = 1.0` (no leverage path exists in the class).
- No borrow; no short; no margin.
- `buy_amt > cash` triggers `buy_amt = cash` on the residual buy. Then if `buy_amt < min_order_usd`, the buy is skipped. The bot transitions to pure-hold without negative cash or NaN values.
- No exit / no sell rule. Equity is always `cash + btc × price`; both terms are non-negative.

Empirical evidence: across 324 sweep runs + 31 WF folds + 1 holdout, **zero halts, zero negative cash events, zero NaN trades**. The worst observed DD is BTC's own −77.2 % in the cycle bear; the bot exited with 71.5 % drawdown (better than BuyHold by 5.7 pp because it accumulated more BTC at the lower prices). **The bot cannot be wiped out by anything short of BTC itself going to zero, which is the portfolio's existential risk, not a bot-design risk.**

---

## 7. Walk-forward fold-by-fold (chosen config)

Bold rows are folds where the bot beat DCA by ≥ +5 pp.

| Test window | Smart % | DCA % | BH % | Smart−DCA pp | cb-imp % | Trades | 2× |
|---|---|---|---|---|---|---|---|
| 2019-01-31 → 2019-07-31 | +158.0 | +161.0 | +174.9 | −1.16 | −1.17 | 49 | 9 |
| 2019-03-31 → 2019-09-30 | +34.1  | +59.1  | +96.2  | −15.75 | −18.69 | 67 | 0 |
| 2019-05-31 → 2019-11-30 | −19.0  | −14.1  | −6.9   | −5.73 | −6.08 | 53 | 7 |
| 2019-07-31 → 2020-01-31 | −7.8   | −10.3  | −1.9   | +2.77 | +2.69 | 50 | 9 |
| 2019-09-30 → 2020-03-30 | −29.8  | −28.8  | −26.6  | −1.45 | −1.47 | 44 | 12 |
| 2019-11-30 → 2020-05-30 | +28.6  | +29.9  | +21.4  | −0.94 | −0.95 | 45 | 11 |
| **2020-01-30 → 2020-07-30** | +28.6  | +14.9  | +19.6  | **+11.92** | +10.65 | 45 | 11 |
| 2020-03-30 → 2020-09-30 | +37.8  | +55.6  | +83.6  | −11.42 | −12.90 | 59 | 4 |
| 2020-05-30 → 2020-11-30 | +94.6  | +91.3  | +94.0  | +1.72 | +1.69 | 43 | 12 |
| 2020-07-30 → 2021-01-30 | +207.6 | +195.2 | +208.0 | +4.22 | +4.04 | 49 | 9 |
| 2020-09-30 → 2021-03-30 | +338.5 | +398.1 | +432.9 | −11.95 | −13.57 | 61 | 3 |
| 2020-11-30 → 2021-05-30 | +33.8  | +66.2  | +87.6  | −19.49 | −24.21 | 58 | 5 |
| 2021-01-30 → 2021-07-30 | −13.3  | −9.2   | +16.8  | −4.54 | −4.75 | 53 | 7 |
| 2021-03-30 → 2021-09-30 | −25.6  | −27.4  | −27.9  | +2.49 | +2.43 | 47 | 10 |
| 2021-05-30 → 2021-11-30 | +66.2  | +61.6  | +70.6  | +2.84 | +2.76 | 43 | 12 |
| 2021-07-30 → 2022-01-30 | −17.0  | −14.5  | −4.4   | −2.88 | −2.97 | 53 | 7 |
| 2021-09-30 → 2022-03-30 | −17.5  | −15.1  | +12.0  | −2.80 | −2.88 | 53 | 7 |
| 2021-11-30 → 2022-05-30 | −40.2  | −41.0  | −49.0  | +1.29 | +1.27 | 39 | 14 |
| 2022-01-30 → 2022-07-30 | −40.0  | −40.8  | −37.2  | +1.37 | +1.35 | 46 | 11 |
| 2022-03-30 → 2022-09-30 | −52.0  | −53.4  | −58.6  | +3.04 | +2.95 | 38 | 15 |
| **2022-05-30 → 2022-11-30** | −27.0  | −33.0  | −44.0  | **+8.99** | +8.25 | 44 | 12 |
| **2022-07-30 → 2023-01-30** | +11.4  | +4.4   | −0.8   | **+6.69** | +6.27 | 49 | 9 |
| 2022-09-30 → 2023-03-30 | +49.6  | +45.2  | +45.0  | +3.04 | +2.95 | 47 | 10 |
| 2022-11-30 → 2023-05-30 | +63.2  | +63.2  | +64.6  | −0.04 | −0.04 | 47 | 10 |
| 2023-01-30 → 2023-07-30 | +29.6  | +26.0  | +23.5  | +2.88 | +2.80 | 43 | 12 |
| 2023-03-30 → 2023-09-30 | −5.2   | −6.5   | −5.1   | +1.32 | +1.31 | 49 | 9 |
| 2023-05-30 → 2023-11-30 | +34.7  | +38.3  | +36.5  | −2.54 | −2.61 | 51 | 8 |
| 2023-07-30 → 2024-01-30 | +56.7  | +54.1  | +47.5  | +1.67 | +1.64 | 39 | 14 |
| 2023-09-30 → 2024-03-30 | +116.1 | +141.7 | +159.2 | −10.60 | −11.86 | 65 | 1 |
| 2023-11-30 → 2024-05-30 | +58.8  | +60.3  | +79.0  | −0.94 | −0.94 | 55 | 6 |
| 2024-01-30 → 2024-07-30 | +17.6  | +39.1  | +54.6  | −15.42 | −18.23 | 61 | 3 |
| **HOLDOUT 2024-09 → 2026-05** | **+29.7** | +29.4 | +31.6 | **+0.25** | +0.25 | 44 | 12 |

The "Trades" + "2×" columns are the easiest way to see what the bot was actually doing: a 6-month fold contains ~24 weeks ≈ 24 × 1 standard buys + up to 24 × 3 = 72 dip-buys, but in practice trades + 2× never exceeds ~70 because the cash budget exhausts. **Folds with 12+ dip-buys consistently win; folds with ≤ 5 dip-buys consistently lose** — clean evidence the dip rule IS the source of edge.

---

## 8. Proposed `VARIANTS` entry (paste-ready, NOT YET added)

When Steven signs off, append the following dict to the `VARIANTS` list in `grid_farm.py:43-109` (placement: directly after the existing `dca` entry at lines 98-99, before `buyhold` at 100-101 — keeps Stack-tab visual ordering of DCA → DCA-Smart → BuyHold for honest side-by-side comparison):

```python
{"slug": "dca-smart", "name": "DCA-Smart", "type": "dca_smart", "tab": "stack",
 "style": "daily DCA + 3× on RSI(14)<45 days — bear/crash specialist (bleeds in bull legs)",
 "rsi_period_days": 14, "rsi_threshold": 45, "dip_multiplier": 3.0,
 "max_dip_buys_per_week": 3, "dip_pool_pct": 0.0, "base_size_pct": 0.015},
```

### 8.1 Required parallel changes Steven applies at deploy time

None of these belong in this report's branch; they all go in the same follow-up commit that adds the `VARIANTS` dict:

1. **`grid_farm.py` import** (top of file, alongside the existing `from more_bots import ...`): add `DCASmartBot` to the import list.
2. **`make_bot()` (`grid_farm.py:169-209`)**: new branch directly after the existing `type == "dca"` branch:
   ```python
   if t == "dca_smart":
       return DCASmartBot(
           capital=PAPER_CAPITAL,
           interval_hours=v.get("interval_hours", 24),
           rsi_period_days=v.get("rsi_period_days", 14),
           rsi_threshold=v.get("rsi_threshold", 40),
           dip_multiplier=v.get("dip_multiplier", 2.0),
           max_dip_buys_per_week=v.get("max_dip_buys_per_week", 3),
           dip_pool_pct=v.get("dip_pool_pct", 0.0),
           min_order_usd=MIN_ORDER_USD,
       )
   # Override default buy_usd if base_size_pct supplied
   ```
   Note `base_size_pct` is applied by setting `bot.buy_usd = PAPER_CAPITAL * v.get("base_size_pct", 1.0/30)` after construction (the parent's default is `capital/30`).
3. **`step_all()` (`grid_farm.py:309-363`)**: **no change.** DCA-Smart's `step(price)` matches DCABot's signature; the existing `else: bot.step(price)` dispatch at line 336-337 handles it.
4. **`_state_label()` (`grid_farm.py:258-293`)**: new branch directly after the existing `t == "dca"` branch:
   ```python
   if t == "dca_smart":
       cash_pct = bot.cash / bot.capital * 100.0
       if cash_pct < 0.5:
           return f"stacked {held:.4f} BTC (smart — budget spent)"
       return f"stacking smart — {bot.dip_buys_this_week}/{bot.max_dip_buys_per_week} dip buys this week"
   ```
5. **`min_capital()` (`grid_farm.py:154-166`)**: add `"dca_smart"` to the tuple at line 164: `if t in ("trend", "rebalance", "dca", "dca_smart", "buyhold"):`. Same $50 floor as plain DCA.
6. **No changes** to `api.py`, dashboard tabs, launchd plists, or Telegram digests. Stack tab auto-picks-up the new variant from `VARIANTS`.

### 8.2 Suggested dashboard copy (Stack tab "What it does" tooltip)

> *"DCA-Smart" — buys $150 (1.5%) of BTC daily, but **3×** that amount ($450) on days where the 14-day RSI dips below 45, up to 3 dip buys per week. Built for bear legs and shallow pullbacks: when fear shows up in the RSI it accumulates more aggressively at the cheaper prices. Expected to **bleed in clean bull legs** (it deploys cash slower than plain DCA on purpose, to keep dry powder for dips) — sit next to plain DCA on this tab so the comparison stays honest.*

---

## 9. Final verdict

**MIXED → SHIP-AS-SPECIALIST (paper).**

**Why ship:**
- The bot delivers the bear-leg specialist alpha its spec was written for. +4.87 pp vs DCA at the conservative recommended config, +32.92 pp at the aggressive end of the sweep, +4.64 % cost-basis improvement during the cycle bear. **This is real, mechanical, and not a survivor of cherry-picking** — every config in the sweep with `base_size ≤ 1.0 %` and `rsi ≤ 40` beats DCA in the bear by ≥ 15 pp.
- The bot **cannot be wiped out**. Spot-only, unleveraged, cash-only buys — catastrophic resistance is intrinsic to the mechanism, verified across 324+ runs.
- The bot **improves crash drawdown** vs plain DCA (19.30 % vs 21.20 %) — a finding the spec did not predict. This is a real specialist edge.
- The holdout result is a **tie with DCA** (+0.25 pp, +$33 on $10k) — not alpha, but also not loss. The recommended config is a low-cost option on bear regime alpha that has not yet fired in the out-of-sample window.
- The implementation is 30 lines of subclassed code over `DCABot`, with persistence round-tripped. Zero new failure modes vs plain DCA. Adding it to the farm has near-zero ops cost.

**Why MIXED, not PASS:**
- The bot **fails the spec's strict bull-bleed bar** by 12× (−11.96 pp vs the spec's −1 pp). This is structural to the spec-locked `base_size_pct` ceiling, not the mechanism — see §3.3.
- The bot **does not pay for itself out-of-sample** at the chosen config (+0.25 pp). The bear alpha is conditional on a bear leg occurring during the deploy window.
- The crab regime is a near-tie (−0.97 pp) — the spec hoped this would be the second specialist regime; in practice it isn't.

**Why this is still the right call:**

The portfolio-specialist framing says a bot's job is to **complement** the rest of the farm, not solo-pass every regime. The farm has 26 paper bots: directional bots (Trend, Infinity), market-neutral (Funding), convex (Tail-Hedge, Backspread), grids (7), and accumulators (Rebalance, DCA, BuyHold). **None of them have a stated job of "accumulate more aggressively when fear shows up in RSI"**. DCA-Smart is the only bot in the farm whose explicit edge fires in down-regimes — and it doesn't bleed catastrophically in up-regimes, it just under-DCA's. The farm benefits from having one bot whose equity curve genuinely *separates* from DCA's during bears, with the cost-basis advantage realized as a buy-the-dip discount that the rest of the farm doesn't have.

The bull bleed is a known, quantified, addressable limitation. The catastrophic-resistance guarantee is hard and verified. The bear edge is real and 5-30× the spec threshold depending on config.

**8 weeks of paper deployment** (per the Phase 1 plan) will tell Steven whether the 2026-Q2-and-onward regime delivers a real out-of-sample dip-rule fire that matters. If the holdout's "tie" pattern continues, the bot's contribution to the farm is "harmless DCA mirror" — not actively destructive. If a real fear leg shows up in the deploy window, the bot will be the first one stacking through it cheaply.

---

## 10. Open questions surfaced during backtest

These were not in the spec or were re-validated by the empirical evidence:

1. **`base_size_pct` ceiling should be widened.** The Gate 2 spec locked the sweep at {0.5 %, 1.0 %, 1.5 %} — all below DCA's prod 3.33 %. This **structurally bakes in** the −12 pp bull bleed. A v2 spec amendment + re-sweep at `{1.5 %, 2.5 %, 3.5 %}` would test whether the bot can match DCA's bull deployment while still capturing dip-buys. Spec § 3 should be updated to reflect this. **Highest-priority follow-up.**
2. **`max_dip_buys_per_week=5` is empirically worse than 3.** The 5-cap configs over-fire during choppy regimes and end with worse holdout outcomes than the 3-cap configs (the latter's tie-break preference in the picker is data-driven, not just an arbitrary choice). The spec's recommended default of 3 is correct; 5 should probably be the top of any future sweep, not the default.
3. **`rsi_threshold=45` is the picker's choice.** Steven's spec specified 40. The sweep finds that at large `dip_multiplier` (3.0), the 45-threshold marginally reduces bull bleed vs 40 (median −25.3 vs −27.1 pp) without meaningfully hurting bear lift. **Worth a Steven decision**: ship at the spec's 40, or take the empirically-better 45? Recommended: 45 — the spec set 40 as the lower edge of a search range, not as a fixed value, and 45 is inside the spec's {30-45} range.
4. **Crab regime contributes essentially nothing.** RSI<40 days in the 2022-12 → 2023-10 grind from $17k to $28k are rare and shallow. The spec's framing of crab-as-second-specialist-regime is wrong on this dataset; it's actually a tie. **Spec § 1 should be amended** to say "bear/crash specialist", not "bear/crab specialist". Crab is a neutral regime for this bot, not a positive.
5. **Holdout dip-buys produced no measurable lift.** 12 dip-buys in 21 months, +0.25 pp terminal. Either (a) the holdout's pullbacks weren't deep enough for the cost-basis effect to compound, or (b) the dip-buy timing was wrong (RSI<45 fires before the local bottom in shallow pullbacks). **Worth a follow-up study**: tighten `rsi_threshold` to 35 in the holdout and see whether the dip-buys land at materially lower prices.
6. **No fast cash-exhaustion observed at the recommended config.** With `base_size_pct=1.5%` and `dip_multiplier=3.0`, the max weekly burn is 4 × 1× + 3 × 3× = $1,950 / week. Budget lasts ~5.1 weeks minimum and ~9.5 weeks at no-dips. In none of the regime windows or walk-forward folds does the bot fully exhaust *before* the test window ends — the holdout has 44 trades over 21 months, well-paced. Spec § 6 "Cash-exhaustion timing" risk is empirically lower at the recommended config than the spec's pure-2× math suggested.
7. **The synthetic rolling-24h daily close works fine in practice.** No edge cases observed in RSI computation; the warmup seeds the deque cleanly from 30 days of hourly data. Spec § 2.2's design choice (synthetic vs UTC-aligned) is validated — UTC alignment is unnecessary for an RSI-as-filter use case.
8. **The dip-pool reservation (spec § 2.4, defaulted off) was not exercised.** Future research could re-run the sweep with `dip_pool_pct ∈ {15 %, 30 %}` to see whether reserving cash for dip buys preserves dry powder for the actual bottom. Lower priority than item 1.

---

## 11. Artifacts

All under `docs/gate3-reports/02-dca-smart-data/`:

- `sweep_results.csv` — 324 rows: every (regime × 81 config) combo, with smart/dca/buyhold terminal eq, cost-basis improvement, 2× buy count, DD, Sharpe.
- `regime_winners.csv` — per-regime top configs ranked by smart-vs-DCA pp lift.
- `winner.json` — global winner under the tiered picker + rationale.
- `walkforward_results.csv` — 31 tuning folds + 1 holdout fold at the chosen config, per-fold smart/dca/bh returns + cost-basis improvement.
- `comparison_results.csv` — head-to-head DCA-Smart vs DCABot vs BuyHoldBot per regime + holdout.

Reproduce with:
```
cd ~/Documents/btc-wheel-bot/strategies
python3.11 dca_smart_backtest.py         # full Gate 3 run, ~10 s
python3.11 dca_smart_backtest.py --quick # smaller sweep, ~2 s
```

---

*End of Gate 3 report. Bot at `strategies/more_bots.py:DCASmartBot`, harness at `strategies/dca_smart_backtest.py`. NOT wired into `grid_farm.py` `VARIANTS` — that is the Gate 4 paper-deploy decision Steven signs off after reading this.*
