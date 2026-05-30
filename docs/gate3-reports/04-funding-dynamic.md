# Gate 3 Report — Funding-Dynamic Bot

*Phase 1, Strategy 4 of the BSF Bot R&D Program · 2026-05-31*

> Spec at [`bsf-research-briefs/specs/04-funding-dynamic-spec.md`](~/Documents/bsf-research-briefs/specs/04-funding-dynamic-spec.md).
> Research brief at [`bsf-research-briefs/03-funding-dynamic.md`](~/Documents/bsf-research-briefs/03-funding-dynamic.md).
> Implementation at `strategies/income_bots.py:FundingDynamicBot`; harness at `strategies/funding_dynamic_backtest.py`.
> Raw artifacts in [`./04-funding-dynamic-data/`](./04-funding-dynamic-data/).
> Topic branch: `feat/funding-dynamic-gate3` (NOT merged to main; the bot is NOT wired into `grid_farm.py` `VARIANTS`).

---

## Verdict — **FAIL-PARK (clean negative result)**

The bot does what the spec hoped — it's genuinely uncorrelated with the level-based cousins (median 30-day rolling corr **+0.016**, well under the 0.85 bar). But it is uncorrelated *in a losing direction*. Across all three available volatile windows AND the steady baseline, the pure-slope sizing systematically under-collects funding compared to `funding` and `funding-smart`, then pays Deribit's ~6 bps per rebalance into the bargain.

The brief was explicit that this was the most likely outcome:

> *Quality of evidence: mixed-to-good on funding rates themselves, **thin** on slope-conditioned implementations specifically. This is the most novel of the four briefs.* — `03-funding-dynamic.md`

> *The bot's job in Gate 3 is to disprove its own existence.* — `04-funding-dynamic-spec.md §6`

Gate 3 disproved its existence cleanly. Per the §6 plan, the bot is **parked** with this report filed as evidence-of-absence: the slope dimension does not contain marginal alpha over the level dimension in the Deribit data we have. No paper deploy; no `VARIANTS` entry; the implementation stays on the topic branch for future reference if the question is reopened (e.g., with a cross-venue slope signal — brief Q9 — under Family 5).

---

## Strict-bar scorecard (spec §8.1)

| Bar | Threshold | Result | Pass? |
|---|---|---|---|
| **Volatile windows: vs best `funding`/`funding-smart` cousin** | ≥ +2 %/yr in ≥ 2 of 3 windows | **0 / 3** (−10.0 pp, −34.9 pp, −48.9 pp) | ❌ **FAIL** |
| **Steady baseline vs `funding-smart`** | ≥ −2 %/yr (specialist allowance) | **−12.61 pp** | ❌ **FAIL** |
| **Correlation with `funding-smart` (30 d rolling, median)** | < 0.85 | **+0.016** (p95 +0.265) | ✅ **PASS** |
| **Max drawdown across windows** | ≤ `funding-smart` MDD + 1 ppt | **+1.05 ppt** worst window (vol_2021_ramp); cousins 0.00 % | ❌ FAIL (but cousin DD is rounding-zero, so bar is degenerate at this scale — see §5) |
| **Total trade cost paid (% of capital)** | reported, not threshold | **$74–$251 per 31-day window** at winner (0.7 %–2.5 % of capital/month) | ⚠️ headline |
| **Rebalances per month (typical regime)** | 2 – 10 | **42–134** (winner cfg, across regimes) | ❌ FAIL (~5–15× too many) |
| **No HALT firing in normal regimes** | True | `negative_funding_halt` did not fire once across all 108 sweep runs | ✅ PASS |
| **Catastrophic resistance** | hard pass | confirmed: spot-flat, perp short capped at 1.0×, no wipeout path; max DD 1.18 % in worst run | ✅ PASS |

**1 hard PASS + 1 PASS + 1 warning + 5 FAIL.** The correlation pass is real and informative — it tells us the bot *is* doing something different from the cousins. What it's doing different is just losing money. The volatile-window FAIL is the headline: 0 of 3 is the clearest possible kill condition for the slope-vs-level hypothesis.

---

## Per-regime head-to-head (winner config: `slope_threshold=4e-7/h`, `size_increment_step=0.20`, `slope_lookback_hours=32`, 6 bps cost)

| Regime | `funding` APR | `funding-smart` APR | **Dynamic APR** | Δ vs best cousin | Avg \|position\| | Rebalances | Trade cost |
|---|---|---|---|---|---|---|---|
| vol_2021_ramp (Feb 18 → Mar 21 2021) | +37.48 % | +37.60 % | **−11.31 %** | **−48.91 pp** | 0.38 | 85 | $240.57 |
| vol_luna_2022 (May 14 → Jun 14 2022) | +2.45 %  | +2.69 %  | **−7.33 %**  | **−10.02 pp** | 0.12 | 42 | $73.84 |
| vol_etf_2024  (Feb 3 → Mar 5 2024)   | +25.99 % | +26.03 % | **−8.85 %**  | **−34.88 pp** | 0.41 | 85 | $200.12 |
| steady_2023   (May 9 → Jun 9 2023)   | +3.98 %  | +4.03 %  | **−8.58 %**  | **−12.61 pp** (vs smart) | 0.18 | 54 | $94.68 |

The `funding` / `funding-smart` cousins are nearly identical (positive_only barely matters in this data — funding was net positive in every window) and sit at full position size for the entire window. The dynamic bot's average position runs at **12 %–41 %** of full size, missing most of the carry by construction.

---

## Trade-cost stress at winner config

The headline number was 6 bps; the spec mandated stressing 3 and 12 bps to bound where the result is robust vs fragile to fee assumptions.

| Regime | 3 bps | **6 bps (headline)** | 12 bps |
|---|---|---|---|
| vol_2021_ramp | +2.27 % | **−11.31 %** | −33.64 % |
| vol_luna_2022 | −3.19 % | **−7.33 %**  | −15.14 % |
| vol_etf_2024  | +2.60 % | **−8.85 %**  | −28.32 % |
| steady_2023   | −3.31 % | **−8.58 %**  | −18.35 % |

At **3 bps** (Deribit's lowest realistic perp round-trip — high-volume maker tier), the bot scrapes a positive APR in two of three volatile windows, but still loses by **−35 pp** to **−5.9 pp** vs the best cousin. There is no fee assumption in [3, 12] bps where the bot wins. The slope alpha that exists is smaller than the rebalance cost across the entire realistic fee range.

---

## Walk-forward 70 / 30 + quarterly sub-folds

Full Deribit series 2019-05 → 2026-05 split 70 % in-sample / 30 % held-out (with the 59-day data gaps left in place — the bot just steps the next record). Quarterly sub-folds added as a fragility check.

| Fold | Start | End | Dyn APR | `funding` APR | `smart` APR | Δ vs best | Rebalances |
|---|---|---|---|---|---|---|---|
| **in_sample_70pct**  | 2019-05-30 | 2024-05-11 | **−11.30 %** | +6.69 % | +10.17 % | **−21.47 pp** | 1,288 |
| **out_sample_30pct** | 2024-05-11 | 2026-05-21 | **−11.93 %** | +5.88 % | +6.29 %  | **−18.22 pp** | 622   |

The in-sample → out-of-sample delta is **−0.63 pp/yr** — the bot loses *more* on holdout, not less. This kills the "we just got unlucky in the regime windows" rebuttal. The pattern is stable across both halves of the 7-year series.

The quarterly sub-folds (see `walkforward_results.csv`) show the **single positive quarter** was 2024-08 (−1 pp vs best — best of any fold; still a loss). **No quarter** in 7 years cleared the +2 pp/yr bar against the best cousin.

---

## Correlation check — the only thing the bot got right

| Statistic | Value | Bar |
|---|---|---|
| Median 30-day rolling corr vs `funding-smart` | **+0.016** | < 0.85 ✅ |
| Mean 30-day rolling corr | +0.006 | — |
| 95th-percentile 30-day rolling corr | +0.265 | < 0.95 ✅ (no park-as-redundant trigger) |

Correlation is **dead flat near zero across the whole series.** The two strategies are doing genuinely different things at every point in time. From a portfolio perspective the dynamic bot would have meaningfully reduced funding-family concentration — if it had *any* edge. It does not.

This is the most interesting result in the report: pure slope IS orthogonal to pure level, in the way the brief predicted. The level cousins are short-and-collect; the dynamic bot is fractional-or-flat. The two equity curves co-move at +0.016. The brief's mental model was right about *what* slope captures. It was wrong about whether that thing is worth capturing in this data.

---

## Where it shines — and it shines on nothing

The brief listed two scenarios for the bot's edge:
1. **Funding spikes / rapid regime shifts** — sized up while level-based bots are anchored at last week's mean.
2. **Specialist regimes** where the slope dimension carries information level misses.

Empirically:
- **Funding spikes (LUNA cascade tail):** the bot's avg position dropped to **0.12** during this window. The cascade's sharp funding inversions drove slope estimates to flip sign rapidly, triggering rebalances that liquidated nascent positions before they could collect carry. Meanwhile `funding-smart` sat full-short and ate +2.69 %/yr through the noise. **The bot did the opposite of what the brief expected**: it sat smaller in the highest-acceleration window.
- **Sustained elevated funding (2021 ramp, ETF launch):** funding was in the +37 %/yr regime, but it was *steady*-high, not accelerating. Slope ≈ 0 over the 24h–32h windows → target position ≈ 0. The bot sat at avg 0.38–0.41 while the carry was on the table.

The closest the bot came to shining was the steady_2023 baseline, where it bled "only" −12.61 pp vs `funding-smart`. The 2× looser bar in the steady baseline (−2 pp) was supposed to be the specialist allowance. The bot blew through it by 6×.

## Where it bleeds — the trade cost is the dominant story

Trade-cost paid per 31-day window:
- vol_2021_ramp: **$240.57** (2.41 % of $10 k capital, on 85 rebalances)
- vol_etf_2024:  **$200.12** (2.00 %, on 85 rebalances)
- steady_2023:   **$94.68**  (0.95 %, on 54 rebalances)
- vol_luna_2022: **$73.84**  (0.74 %, on 42 rebalances)

**Annualised, the rebalance cost alone runs 8 %/yr to 29 %/yr.** That is more than the *best* funding cousin earns in three of the four windows. The brief's spec §3 listed `size_increment_step` as the single most important real-money knob; the sweep confirmed it by failing the bar even at the largest setting (0.20) the spec allowed.

The OLS-over-24h slope estimator from a noisy 8h-cadence-resampled-hourly funding stream produces sign flips at much higher frequency than the spec's "2–10 rebalances per month" target band. Across the four windows the winner config produced 42–134 rebalances per 31-day window — between **5× and 15× the target rate**. Larger `size_increment_step` would help; the spec's locked sweep ceiling is 0.20 and it isn't enough.

---

## Catastrophic resistance — the one part that worked

- **Max DD across all 108 sweep runs: 1.18 %** (vol_2021_ramp at the noisiest config — slope_threshold=1e-7, size_step=0.05, lookback=16). For comparison the COVID crash daily candles produce 50 % drawdowns in directional bots.
- **Liquidation events: 0.** Spot is flat by design (the bot does not actually hold a spot leg in this simulation — the parent `FundingBot`'s simplification carries forward; the perp leg's notional is capped at 1.0 × capital).
- **`negative_funding_halt` fires: 0** across all 108 sweep runs and the full walk-forward. The 168-hour streak threshold was never met in any 31-day regime window or in the 7-year walk-forward — consistent with the brief's 92 %-positive-funding stat and the spec's choice of a long streak guard.
- **`slope_saturation_streak` fires: tracked but unobserved as a halt-triggering event** at the winner config. The streak counter incremented at the high-slope spikes in vol_2021_ramp and vol_etf_2024 but did not reach the 6-hour cap-imposition threshold.

The wipeout-resistance design works. The bot just doesn't make money.

---

## Recommended config for paper deploy — **none**

Per spec §6 the no-result is a clean park. **No paper deploy. No `VARIANTS` entry.** The implementation lands on the topic branch as a permanent record of the negative finding.

If the question is reopened (e.g., for the cross-venue slope signal under Family 5, brief Q9), this harness and bot class are reusable — the slope estimator is well-tested, the persistence shape works, the comparison framework against the level-based cousins is the right structural contrast. The reusable artefacts are not wasted.

### Paste-ready VARIANTS entry — **DO NOT MERGE**

For completeness, the entry that *would* have shipped if Gate 3 had passed at the winner config:

```python
# ⚠️ DO NOT MERGE — Gate 3 FAILED. Kept here as the canonical disabled variant
# for the no-result record. If reopening, see docs/gate3-reports/04-funding-dynamic.md.
# {"slug": "funding-dynamic", "name": "Funding (dynamic)", "type": "funding_dynamic", "tab": "funding",
#  "style": "slope-conditioned short — sizes up into funding spikes, flat in steady regimes",
#  "slope_lookback_hours": 32, "slope_threshold": 4e-7, "size_increment_step": 0.20,
#  "trade_cost_bps": 6.0, "allow_long_perp": False, "positive_only": False, "leverage": 1.0,
#  "negative_funding_halt_hours": 168},
```

Wiring required if reopening: add a `type == "funding_dynamic"` branch in `make_bot()` and in `step_all()`'s funding dispatcher; add the warmup branch in `load_variant()`. ~15 lines total; nothing structural.

---

## Open questions surfaced during backtest

1. **Could a level+slope hybrid pass?** The pure-slope failure shows slope is missing what level captures (carry on steady-elevated funding). A multiplicative `level × slope` rule was rejected in Gate 2 §10 Q1 as non-falsifiable, but with the falsifiability test now complete (and answered: no), reopening the hybrid is the natural next step. **Recommendation:** if the funding family ever gets a re-think, design the hybrid first and call it Funding-Hybrid (slot for Phase 2 or 3 R&D), not Funding-Dynamic v2.
2. **Is the trade-cost model too punishing?** The bot pays 6 bps on the *change* in notional (`|Δposition| × notional × bps`). The brief flagged this as "first-class headline." With 42–134 rebalances per 31 days, even 3 bps wipes the edge. A more sophisticated execution model (e.g., providing liquidity on Deribit's perp book to earn the maker rebate) could halve the cost — but the maker-rebate game is its own R&D project, and the residual alpha would still need to be there. The 3 bps stress case shows it isn't.
3. **Are the substituted regime windows representative?** The original spec windows (April 2021 spike, full LUNA cascade, full ETF launch) sit in the Deribit data's 59-day gaps. The substitutes are the lead-in / tail of each — different micro-dynamics. The actual April 2021 peak may have produced larger slopes than the lead-in window we tested. **However:** the ETF-launch tail and LUNA tail are in-spec for "high-acceleration funding regime"; both lose by similar margins to the substituted 2021 ramp. The pattern is consistent; the calendar months are different.
4. **Does the 0.016 correlation result transfer to a working strategy?** The most useful artefact of this Gate 3 is the empirical confirmation that slope-conditioned and level-conditioned bots are *genuinely orthogonal*. If a future bot in the carry family (e.g., the Spec 03 Basis-Arb variants) has comparable orthogonality to `funding-smart` AND positive standalone alpha, it would be a portfolio diversifier even at modest edge. The orthogonality test is reusable.

---

## Data caveat — Deribit funding history has 59-day gaps

The cached `data/raw/deribit/funding_rates.json` (21,574 records, 2019-05-30 → 2026-05-22) is **monthly snapshots with 59-day gaps between blocks** — an artefact of the original fetch pattern (28 gaps of ~1417 hours each, plus one shorter gap, all between adjacent monthly blocks). The spec's three named volatile windows are partially or fully inside these gaps:

| Spec name | Spec window | Coverage in cached data |
|---|---|---|
| April 2021 alt-season spike | 2021-04-01 → 2021-05-15 | **0 % covered** (the entire window falls inside a single gap). Substituted: 2021-02-18 → 2021-03-21 (lead-in to the spike). |
| LUNA cascade May 2022 | 2022-05-05 → 2022-05-25 | **55 % covered.** Substituted: 2022-05-14 → 2022-06-14 (cascade tail + post-cascade unwind). |
| ETF launch Jan-Mar 2024 | 2024-01-08 → 2024-03-15 | **46 % covered.** Substituted: 2024-02-03 → 2024-03-05 (ETF-launch tail). |
| Steady baseline | 2024-07-01 → 2024-10-31 | **41 % covered.** Substituted: 2023-05-09 → 2023-06-09 (cleaner steady stretch in available data). |

**Why this caveat does not rescue the verdict:**
1. The substituted windows are functionally similar to the named ones (high acceleration, cascade dynamics, ETF flow). The pattern (slope misses level carry, trade costs dominate) reproduced across all four available windows AND the full walk-forward.
2. The walk-forward 70/30 split includes every record in the cache. The bot loses in both halves by similar margins (−21.47 pp in-sample, −18.22 pp out-of-sample).
3. No fee assumption in [3, 12] bps and no parameter setting within the locked sweep produces a positive verdict in any available window.

If Steven wants a tighter test, the cleanest next step is **re-fetching Deribit's full hourly funding history** (the gaps are a fetch artefact, not a Deribit limitation — `get_funding_rate_history` supports paginated pulls back to the perp's listing date). That's a one-evening project, but the strong consistency of the available-data result makes it low-priority unless the hybrid signal (Q1 above) gets reopened.

---

## Final verdict

**FAIL-PARK (clean negative result).**

The slope-vs-level alpha hypothesis is disconfirmed in the available Deribit funding-rate history at the spec's locked parameter ranges, locked fee model, and locked four-regime + walk-forward design. The bot's complementarity to `funding-smart` (median rolling corr +0.016) is real and notable, but the bot's standalone returns are uniformly negative.

Per spec §6, this is the structured park outcome. The bot stays on `feat/funding-dynamic-gate3` as evidence-of-absence; the next Phase 1 candidate is Spec 03 Basis-Arb (deferred pending its data infrastructure); no `grid_farm.py` change.

---

*Backtest run 2026-05-31. Spec author: Claude. Harness author: Claude. Verdict author: Claude. Reviewed against Steven's locked Gate 2 decisions: Q1 pure-slope, Q2 partial-reduction, Q3 cached data, Q4 April-2021 anchor (2e-7/h default), Q5 10 % `size_increment_step` headline.*
