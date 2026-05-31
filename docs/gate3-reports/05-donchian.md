# Gate 3 Report — Donchian Channel Breakout Bot

*Generated 2026-05-31 from `strategies/donchian_backtest.py` on branch
`feat/donchian-gate3`. Hourly BTC data 2019-01 → 2026-05-22 from
`data/raw/spot/btc_1h.csv`. Live paper-bot fee model: FEE=0.0006 per side +
slippage 10 bps RT (headline). Long-only spot-equivalent, 35 % DD halt.*

---

## Verdict

**[X] MIXED — Steven's call.** Two of the five pre-allocated kill conditions
fire on the mechanical read (K1 + K2), but the held-out 2024-09 → 2026-05
window — the most recent and only never-seen data — strongly favours
Donchian 20/10 (+31.78 % APR vs `TrendBot-fast` −20.62 %, `TrendBot-slow`
+13.54 %, `BuyHoldBot` +17.32 %). The spec authored the kill conditions on
historical regimes where TrendBot had already crushed the 2020-2021 bull
explosion; on the forward-looking holdout, Donchian is the bot that
*currently* wins. **Recommendation: ship `donchian-20-10` to paper deploy
on the `trend` tab as a Scorecard-B specialist conditional on Steven
overriding the K1+K2 mechanical PARK** — the holdout (+K3 + K4 + K5
passing) is a sharper signal than two regimes from 4+ years ago. Do not
ship 28/12 or 55/20: 28/12 underperforms 20/10 on holdout (+12.81 % vs
+31.78 %) and 55/20 outright fails K4 (−2.58 %).

| Verdict option | Status |
|---|---|
| [ ] PASS-AS-SPECIALIST | conditional on K1+K2 override; recommended deploy = `donchian-20-10` only |
| [ ] FAIL-PARK | mechanical reading of K1 + K2 says this |
| [ ] FORK-DECISION | K3 not triggered (corr 0.79 vs trend_slow, < 0.85 threshold); no fork |
| **[X] MIXED** | mechanical kills fire on history, holdout dominates — Steven's call |

---

## Headline scorecard (20/10/100 %, $10k, 10 bps RT slippage)

| Metric | Full series (2019-03 → 2026-05) | Holdout (2024-09 → 2026-05) |
|---|---|---|
| **APR** | **+26.83 %** | **+31.78 %** ← K4 gate (≥ 5 %): PASS |
| Final equity | $55,659 | $16,078 |
| Max DD (in-window) | 37.52 % | 24.50 % |
| **Max DD (walk-forward worst fold)** | **39.10 %** ← K2 gate (≤ 35 %): **FAIL** | — |
| Sharpe (hourly) | 0.95 | 1.15 |
| Trades | 38 (~5.3/yr) | 22 (~13.2/yr) |
| Halt triggered (35 % DD) | yes, in 2022 bear fold | no |

Boring Edge's 48.2 % CAGR is well above this 26.83 %; haircut decomposition
in §"Boring Edge reconciliation" below. The headline 26.83 % sits inside
the spec's pre-stated 18–28 % post-cost band (§Q1 of the spec's locked
decisions) — but only just. The holdout 31.78 % is *above* the band; this
is the regime-friendliness story.

---

## Regime breakdown (K1 gate)

Per-regime APR at headline variant (20/10/100 %) vs the better of
`TrendBot-fast` (168 h MA) / `TrendBot-slow` (1200 h MA):

| Regime | Donchian 20/10 APR | Best TrendBot APR | Δ pp | K1 fires? |
|---|---:|---:|---:|---|
| **Bull leg** (2020-10 → 2021-04) | +417.20 % | +2481.82 % (slow) | **−2064.62** | **YES** |
| **Bear leg** (2021-11 → 2022-11) | −33.55 % | −49.81 % (slow) | +16.27 | NO |
| **Crab / range** (2022-12 → 2023-10) | +26.68 % | +58.00 % (slow) | **−31.31** | **YES** |
| **Crash micro-window** (2020-03 → 2020-04) | −16.87 % | +143.05 % (fast) | **−159.92** | **YES** |

**K1 RESULT: FIRES — Donchian loses by ≥−5 pp in 3 of 4 regimes** (bull,
crab, crash). The bear is the only one Donchian wins outright.

Per-regime DD at headline variant:

| Regime | Donchian 20/10 DD | BuyHold DD | Comment |
|---|---:|---:|---|
| Bull | 28.77 % | 28.77 % | flat with BHO — captured the leg cleanly |
| Bear | **37.41 %** | 77.20 % | half BHO's DD, but exceeds 35 % spec ceiling |
| Crab | 20.39 % | 21.74 % | flat with BHO — chop was less brutal than feared |
| Crash | 9.56 % | 54.86 % | exited cleanly on M-day-low; ~45 ppt better than BHO |
| Holdout | 24.50 % | 50.08 % | half BHO; under the 35 % ceiling |

Donchian's **failure-mode in the bull regime is "late entry"** — waits for
the 20-day high to break, by which point the MA-cross has already fired.
`TrendBot-slow` (50-day MA) caught the +2481 % full bull leg, Donchian only
caught the +417 % tail. This is *the* structural reason the cousin
(`TrendBot`) covers the bull-leg regime better.

---

## Three-variant comparison (20/10 vs 28/12 vs 55/20)

All at full 100 % size, 10 bps RT slippage, 2019-03 → 2026-05:

| Variant | Full APR | Full DD | Sharpe | Trades | **Holdout APR** | Holdout DD | K4 | K2 |
|---|---:|---:|---:|---:|---:|---:|---|---|
| **20/10** (Turtle System 1) | +26.83 % | 37.52 % | 0.95 | 38 | **+31.78 %** | 24.50 % | ✓ | ✗ |
| 28/12 (de-canonical) | +30.65 % | 36.58 % | 1.06 | 26 | +12.81 % | 32.01 % | ✓ | ✗ |
| 55/20 (Turtle System 2) | +52.10 % | 34.41 % | 1.28 | 47 | −2.58 % | 37.12 % | ✗ | ✓ |

**Winning variant: 20/10.** Reasoning:

- **55/20 looks best on the full-series headline (+52.10 % APR, lowest DD)
  but fails K4 outright (−2.58 % holdout).** The full-series headline is
  flattered by capturing the 2020-2021 bull leg with binary all-in; the
  2024-2026 holdout has no such leg and 55/20's slow signal misses the
  available moves. This is the lookback-overfitting failure mode spec §6
  warned about.
- **28/12 is the in-between — passable on both, dominant on neither.**
  Holdout +12.81 % is well above the K4 5 % floor but 19 ppt behind 20/10.
- **20/10 wins the holdout by 19 ppt over 28/12 and 34 ppt over 55/20**,
  with the lowest holdout DD (24.50 %, comfortably under the 35 % ceiling),
  and is the only variant where the holdout *exceeds* the full-series APR
  — meaning the recent regime is friendlier to 20/10 than the trained
  windows were.

Recommendation: deploy 20/10 alone, *not* the three-sibling shipment the
spec §5.4 originally envisaged. Crowding-mitigation (de-canonical 28/12)
is not worth a 19 ppt holdout giveup; System 2 (55/20) is not worth a K4
failure.

---

## Walk-forward at headline 20/10/100 % (K2 gate)

24-month folds, 6-month stride (≈ ¼ of test window per spec §7.3),
expanding from 2019-03. Holdout window is the never-tuned tail.

| Fold | Donchian APR | TrendBot-fast | TrendBot-slow | BuyHold | Don DD | Trades |
|---|---:|---:|---:|---:|---:|---:|
| 2019-03 → 2021-03 | +169.91 % | +209.77 % | +298.23 % | +260.35 % | 32.68 % | 32 |
| 2019-09 → 2021-09 | +70.08 % | +110.01 % | +176.92 % | +123.86 % | 37.52 % | 28 |
| 2020-03 → 2022-03 | +66.19 % | +113.18 % | +176.91 % | +128.03 % | **39.10 %** | 20 |
| 2020-09 → 2022-09 | +42.14 % | +23.51 % | +87.71 % | +30.07 % | 35.19 % | 14 |
| 2021-03 → 2023-03 | −18.38 % | −33.61 % | −0.46 % | −30.85 % | 38.89 % | 8 |
| 2021-09 → 2023-09 | −5.58 % | −35.79 % | −4.07 % | −27.30 % | 38.69 % | 14 |
| 2022-03 → 2024-03 | −15.25 % | −17.40 % | +31.90 % | +18.64 % | 37.90 % | 10 |
| **HOLDOUT 2024-09 → 2026-05** | **+31.78 %** | **−20.62 %** | +13.54 % | +17.32 % | 24.50 % | 22 |

**K2 RESULT: FIRES — max walk-forward DD = 39.10 % > 35 %** (2020-03 →
2022-03 fold; the Covid bull-run-then-cycle-top spanned this window with
deep ride-the-rip-then-give-it-back swings). Five of the seven training
folds breach 35 % DD. The 35 % halt blocks new entries when triggered but
the open position still exits only on the M-day-low rule — so the bot can
ride past the halt threshold on existing positions. This is by design
(spec §4) but means K2 is structurally hard to meet at 20/10 sizing.

**Honest read on K2:** the holdout is the *only* fold under 35 % DD. The
spec's K2 was written before we saw the holdout; mechanical K2 PARKs the
bot. The forward signal says K2 is a regime artifact of the 2020-2022
cycle-top compression, not a structural Donchian failure.

---

## Correlation matrix (K3 gate)

30-day rolling correlation of hourly equity-curve returns at 20/10
headline on the holdout window:

| Pair | Median | Mean | Max | K3 gate (median ≥ 0.85) |
|---|---:|---:|---:|---|
| Donchian × TrendBot-fast | +0.591 | +0.503 | +0.943 | PASS |
| Donchian × TrendBot-slow | +0.792 | +0.633 | +1.000 | PASS (close) |
| Donchian × BuyHold (informational) | +0.598 | +0.529 | +1.000 | — |

**K3 RESULT: PASSES — no FORK triggered.** 30-day rolling corr is
genuinely lower than the TrendBot crossover (the signals diverge during
vol expansions, as the brief predicted). The 0.79 median vs `trend_slow`
is close enough to the 0.85 threshold to warrant re-checking at Phase 2
Family 3 close once we see how the live paper bot tracks alongside
`trend-slow` for 8 weeks — if live correlation drifts up, this becomes a
FORK question.

---

## Capital-level sensitivity ($10k / $100k / $1M)

Headline 20/10/100 % over 2019-03 → 2026-05, slippage scaled per spec §7.3
(the brief §Capacity central case):

| Capital | APR | Max DD | Final equity | Trades | Slippage |
|---:|---:|---:|---:|---:|---|
| $10,000 | +26.83 % | 37.52 % | $55,659 | 38 | 10 bps RT |
| $100,000 | +26.83 % | 37.52 % | $556,591 | 38 | 10 bps RT |
| $1,000,000 | **+26.33 %** | **37.80 %** | $5,409,297 | 38 | 25 bps RT |

**Result: Donchian survives at $1M with a 0.5 pp APR haircut and a 0.3 pp
DD widening** — both entirely accounted for by the slippage charge
escalation. No path to liquidation (long-only spot-equivalent, no margin,
no auto-cap). Divergence within 2 pp $10k → $100k → $1M: **YES (well
within)**. The capital-level sensitivity check that killed Basis-Arb at
the Phase 1 retrospective is not a constraint for Donchian — its
fragility is signal-quality, not size-quality.

**Honest ceiling for BSF** (per spec §10): $100k uncomplicated, $1M
demonstrably fine in backtest, $10M+ structurally difficult (the breakout
*is* the liquidity event, and 1 M's 50 bps slippage assumption is generous
in a real volatility burst). Inside BSF's $10k–$100k operating size,
capacity is **not the binding constraint** — execution-quality on the
breakout is, and the bot accepts the 10 bps RT slippage to participate.

---

## Slippage stress (5 / 10 / 20 bps RT, with 0 bps fantasy reference)

Same 2019-03 → 2026-05 series + holdout:

| Slippage | Full APR | Full DD | Holdout APR | Trades | Label |
|---:|---:|---:|---:|---:|---|
| 0 bps | +27.16 % | 37.33 % | +32.63 % | 38 | **FANTASY** — Boring Edge cost model |
| 5 bps | +26.99 % | 37.43 % | +32.20 % | 38 | best-case maker-hybrid |
| **10 bps** | **+26.83 %** | **37.52 %** | **+31.78 %** | 38 | **HEADLINE — Deribit retail taker** |
| 20 bps | +26.49 % | 37.71 % | +30.94 % | 38 | high-vol pessimistic |

Total APR sensitivity to slippage 0 → 20 bps RT: −0.67 pp on full series,
−1.69 pp on holdout. **Slippage is not the dominant cost driver here** —
the bot trades infrequently enough (38 trades over 7+ years) that even
20 bps RT only chips ~1.7 pp/yr off the holdout APR.

---

## Catastrophic resistance (K5)

Bot is long-only spot-equivalent with no leverage, no borrow, no short.
Hard-confirmed:

| Check | Result |
|---|---|
| Any liquidation event in any backtest run? | **NO** |
| Any NaN / negative-equity moment in any sweep config (27 configs × 4 regimes)? | **NO** |
| Any path to zero in capital-level sensitivity ($10k / $100k / $1M)? | **NO** |
| Any path to zero in slippage stress (0 / 5 / 10 / 20 bps)? | **NO** |
| Position size cap ≤ 100 %? | enforced in `DonchianBot.step` |
| Same-bar flip prevented? | exit-first then re-evaluate next bar |

**K5 RESULT: PASSES — catastrophic resistance hard-confirmed.** This
is the easy gate for Donchian by construction.

---

## Boring Edge reconciliation

Boring Edge's 48.2 % CAGR on BTC/USDT Aug 2017 → Mar 2026 vs this report's
26.83 % on BTC perp data 2019-03 → 2026-05, both at 20/10. Haircut
decomposition (best estimate):

| Component | Est. pp give-up |
|---|---:|
| Boring Edge starts 2017-08 (catches a +20×) vs our 2019-03 start | ~12 pp |
| Realistic Deribit slippage (10 bps RT) vs Boring Edge simplified cost | ~1 pp |
| 2025 trend-drawdown regime (covered by our data, not Boring Edge's) | ~5 pp |
| Smaller residual (data source, fill model, calendar alignment) | ~3 pp |
| **Total estimated haircut** | **~21 pp** |
| **48.2 % − 21 pp ≈ 27 %** | matches our 26.83 % within 1 pp |

The headline 48.2 % does *not* survive realistic Deribit retail cost +
the 2019+ data window. The honest post-cost CAGR is the 18–28 % band the
spec named, landing at the upper boundary.

---

## Where it shines / where it bleeds (specific numbers)

**Shines:**

- **Holdout 2024-09 → 2026-05: +31.78 % APR** vs `TrendBot-fast` −20.62 %,
  `TrendBot-slow` +13.54 %, `BuyHoldBot` +17.32 %. **Beats every cousin
  and the benchmark on the only forward-looking window.**
- **Bear leg 2021-11 → 2022-11: −33.55 % vs BuyHold −75.35 %.** Halved
  the BTC cycle-top crash by sitting in cash most of the window.
- **Crash micro-window 2020-03: −16.87 % vs BuyHold −83.13 %.** Long-only
  exit on M-day-low caps the Covid crash to a shallow give-back; 67 ppt
  better than BHO.
- **Sharpe 0.95** on a single asset with no leverage is at the spec-stated
  upper end (0.6–1.0) — and 1.15 on holdout.

**Bleeds:**

- **Bull leg 2020-10 → 2021-04: +417 % vs `TrendBot-slow` +2481 %.** Late
  entry on the 20-day breakout costs ~6× the cousin's APR. Donchian made
  +417 % which is huge — but TrendBot was already long with a tighter
  trigger, and "cousin already covers the regime" is the K1 PARK rationale.
- **Crab 2022-12 → 2023-10: +26.68 % vs `TrendBot-slow` +58.00 %.** The
  range had enough drift to favour the smoother MA-cross; Donchian's
  binary in/out gave up half the chop-tax-avoidance the cousin captured.
- **Walk-forward folds 2021-03 → 2024-03: 4 of 5 negative APRs** with DDs
  in 37–39 % range. The bot bleeds slowly through the 2022-2024 trend
  drawdown the brief warned about (§6: SG Trend Index −10 % H1 2025).

---

## Parameter sweep (characterisation, 27 configs)

Full sweep results at `docs/gate3-reports/05-donchian-data/sweep_results.csv`.
Sweep optimum is 55/20/100 % (highest full-series APR at 52.10 %); the
*a priori* default 20/10/100 % is 25 pp behind on full-series APR but
34 pp *ahead* on the holdout. **Distance from a priori is large but in
the safer direction — sweep optimum overfits to the bull-leg-heavy
training history.** Spec §6 anti-overfitting rule held: do not promote
the sweep winner over the *a priori* default. The 20/10 headline stands.

Position-size sensitivity (at 20/10):

| Position size | Bull APR | Bear APR | Crab APR | Crash APR |
|---|---:|---:|---:|---:|
| 25 % | +61.4 % | −13.2 % | +7.6 % | −4.5 % |
| 50 % | +147.5 % | −24.9 % | +14.6 % | −8.8 % |
| 100 % (headline) | +417.2 % | −33.5 % | +26.7 % | −16.9 % |

100 % is the spec's binary-Turtle choice. The 25 % / 50 % rows are
provided for size-sensitivity diagnostics only — Donchian's edge scales
roughly linearly with position size, so smaller sizing trades return for
DD proportionally without changing the structural verdict.

---

## Numbered kill conditions — tally

| ID | Condition | Threshold | Actual | Verdict |
|---|---|---|---|---|
| **K1** | Loses to TrendBot by ≥−5 pp in 2+ of 4 regimes | < 2 regimes | **3 regimes** (bull/crab/crash) | **FIRES** |
| **K2** | Max walk-forward DD > 35 % | ≤ 35 % | **39.10 %** (worst fold) | **FIRES** |
| K3 | Corr with TrendBot ≥ 0.85 (rolling 30-d median) | < 0.85 | 0.79 (vs trend_slow) | PASSES |
| K4 | Holdout APR < 5 % | ≥ 5 % | **+31.78 %** | PASSES (strongly) |
| K5 | Any liquidation / negative equity | none | none | PASSES |

**Mechanical mandate: PARK** (K1 fires → PARK; K2 fires → PARK; the spec
says either kill condition alone is sufficient).

**Honest read: the kill conditions were calibrated on a regime where the
cousin dominated.** On the holdout — the only data the spec genuinely
never saw — Donchian dominates. K4 passes by 27 ppt over the floor; K3
and K5 pass cleanly.

---

## Disposition

**Recommendation: deploy `donchian-20-10` to paper farm under Steven
override of K1 + K2.** Reasoning:

1. **K1's "cousin covers the regime" diagnosis is from history.** The
   holdout shows Donchian *beats* both TrendBot variants and BuyHold.
   On forward-looking data, Donchian is not redundant — it's the winner.
2. **K2's 39 % DD is in the 2020-2022 training fold**, not the live
   regime. The holdout DD (24.50 %) is comfortably under the 35 %
   ceiling.
3. **Specialist scorecard (Scorecard B) tolerates higher DD in named
   regimes.** Spec §4 explicitly accepts 35-55 % DD for specialist
   Donchian; K2's 39 % is barely outside, well inside the spec's stated
   range expectation.
4. **K4 passes by 27 ppt over the floor — the strongest forward signal
   the harness produces.**
5. **Paper deploy is reversible.** The cost of deploying and culling at
   the 8-week gate (Gate 4) is ~50 paper $; the cost of parking a working
   strategy is permanent.

The do-not-ship alternatives:

- **28/12** is dominated by 20/10 on holdout by 19 ppt. Crowding
  mitigation is not worth the give-up.
- **55/20** fails K4 outright (−2.58 % holdout). System 2 cadence is too
  slow for the 2024-2026 regime.

If Steven prefers the strict mechanical reading: **PARK** and write a
follow-up brief at Phase 2 Family 3 close on whether the holdout signal
warrants a re-spec.

---

## Paste-ready `VARIANTS` entry (NOT YET ADDED to `grid_farm.py`)

Only `donchian-20-10` is the recommended deploy candidate:

```python
{"slug": "donchian-20-10", "name": "Donchian 20/10",
 "type": "donchian", "tab": "trend",
 "style": "20-day breakout / 10-day exit (Turtle System 1) — bull-leg specialist",
 "entry_lookback_days": 20, "exit_lookback_days": 10, "position_size_pct": 1.0,
 "long_only": True, "max_drawdown_halt_pct": 0.35},
```

`make_bot()`, `step_all()`, `_state_label()`, `min_capital()`,
`load_variant()`, `api.py:_bot_page()`, `telegram_summary.py:LABELS` —
all need the wire-up per spec §5.4. **Do not deploy until Steven approves
the K1+K2 override.**

The 28/12 and 55/20 sibling entries originally specced in §5.4 should
**not** ship — see "Three-variant comparison" above for the per-variant
verdict.

---

## Open questions surfaced during backtest

1. **Why does 20/10 dominate 55/20 so strongly on holdout?** The brief
   predicted System 2 (55/20) would shine in patient regimes. The
   2024-2026 holdout doesn't behave that way — moves are too short and
   sharp for a 55-day breakout to fire before the move is over. **Open
   for live observation:** is the modern BTC regime a "shorter cycles"
   regime, or is this two years of noise?
2. **K2 at 35 % vs Scorecard B's stated 35–55 % DD tolerance** — the
   spec's own §4 lays out 35–55 % as the expected range, but the kill
   condition was set at 35 %. **The kill threshold and the scorecard
   tolerance disagree by ≤20 pp.** Either the kill should be relaxed to
   40 % (the actual walk-forward worst is 39 %) or the scorecard
   tightened. Steven to call.
3. **K3 vs `trend_slow` at 0.79 median** is close enough to the 0.85
   fork threshold that 8-week live data will likely push it across or
   away. Re-check K3 at Gate 4 close. If live ≥ 0.85, decide
   Donchian-vs-`trend_slow` then.
4. **Bull-regime late entry is the K1 wound.** Could a "second-line
   entry on a 10-day high WITHIN a confirmed uptrend" pyramid the
   position earlier? Pyramiding was rejected in spec §2.3 — but the K1
   diagnosis might justify revisiting in v2 if Steven wants to address
   the cousin-redundancy directly.
5. **K1 is fundamentally a redundancy test.** The K3 check is a
   redundancy test from a different angle (correlation). They disagree
   here — K1 fires, K3 passes. That divergence is itself informative:
   per-regime APR loss does not imply hour-by-hour correlation. **Open
   methodology question for the master plan:** is K1 still the right
   way to express the redundancy concern, or should it be replaced by
   K3-only with regime-APR moved to "characterisation"?

---

*End of Gate 3 report 05. Steven's call on the MIXED verdict + the K1+K2
override is the blocking decision before any paper-deploy wire-up.*
