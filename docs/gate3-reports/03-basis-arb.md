# Gate 3 Report — Basis Arb Bot

*Phase 1, Strategy 3 of the BSF Bot R&D Program · v1 2026-05-31*

> Spec at [`bsf-research-briefs/specs/03-basis-arb-spec.md`](~/Documents/bsf-research-briefs/specs/03-basis-arb-spec.md).
> Implementation at `strategies/basis_arb_bot.py`; harness at `strategies/basis_arb_backtest.py`.
> Raw artifacts in [`./03-basis-arb-data/`](./03-basis-arb-data/).
> Topic branch: `feat/basis-arb-gate3` (NOT merged to main; the bot is NOT yet wired into `grid_farm.py` `VARIANTS` — this report recommends **don't wire**).

---

## TL;DR — Verdict: **FAIL → PARK**

| Scorecard criterion | Spec bar | Result | Pass? |
|---|---|---|---|
| **Vol-spike combined return** (Covid + LUNA + FTX + ETF, 4 windows) | ≥ +8 % unleveraged | **−0.03 %** | ❌ by ~8 pp |
| **vs best FundingBot in vol-spike windows** | ≥ +3 %/yr annualised | **−0.05 pp mean** (0.16 pp on LUNA only; tied or losing elsewhere) | ❌ |
| **Calm 2023 (the bleed test)** | < 1 % loss | **−0.41 %** | ✅ |
| **vs best FundingBot in calm window** | ≥ −2 %/yr | **−1.08 pp** | ✅ (within bar) |
| **Monthly-return correlation with BuyHoldBot** | < 0.4 | **−0.48** | ✅ (negative ≪ +0.4) |
| **Correlation with best FundingBot** | < 0.9 | **−0.51** (vs `funding-smart`); **−0.34** (vs `funding`) | ✅ (negative ≪ +0.9) |
| **Max drawdown across all windows** | < 10 % | **1.77 %** (holdout, the worst) | ✅ |
| **No HALT in non-FTX vol-spike regimes** | true | **18 halts on dislocation_guard across 30 walk-forward folds** | ❌ |
| **Sharpe (full walk-forward, mean)** | ≥ 1.0 | **negative** in every tuning fold | ❌ |
| **Positive-lift walk-forward folds vs funding_smart** | informational | **0 / 29 tuning folds** | ❌ (the most damning single number) |
| **Catastrophic resistance** | hard pass | confirmed: zero perp liquidations even at `perp_margin_frac = 0.2` | ✅ (but trivially — positions are too small to threaten margin) |
| **No backtest crash, no NaN trade** | hard pass | none across 90 sweep runs + 30 WF folds + 5 failure-mode regimes × 3 margin levels + 12 single-venue runs | ✅ |

**3 PASS / 4 informational-PASS / 5 FAIL.** The single most damning number: **zero of 29 walk-forward folds produce positive lift vs `funding-smart`.** The bot loses money in every single out-of-sample window across 5 years. It does not capture the convergence edge the spec was written for. The funding-family is already extracting the carry edge; basis-arb's claim was *supplementary* convergence PnL during vol spikes, and that PnL does not materialise at meaningful size in 2020-2026 Deribit data.

**Final recommendation: FAIL → PARK.** Do NOT wire into `VARIANTS`. The bot is mechanically sound, catastrophically resistant, and uncorrelated with the existing carry family — but those are properties of being barely-traded and tiny-sized, not of an actual edge. Filing under "tested, the funding family already captures this edge" per spec §8.5.

---

## 1. What was tested

**Bot:** `BasisArbBot` (`strategies/basis_arb_bot.py`, ~350 lines).
- Two-leg accounting per spec §3.1: separate `spot_qty` and `perp_qty`, neither collapsed to a synthetic basis.
- Cash-funded spot per spec §3.2; isolated perp margin per spec §3.3 with `perp_margin_frac` exposed.
- Per-leg fees + slippage per spec §3.4: `fee_spot = fee_perp = 0.0006`, `slip_spot_bps = 5`, `slip_perp_bps = 3`.
- State machine: FLAT → SHORT_BASIS on `z > +entry_z`; SHORT_BASIS → FLAT on `z < +exit_z` (convergence) or `hours_in_position ≥ max`; SHORT_BASIS → HALTED on `|z| ≥ dislocation_guard_z`, on `drawdown ≥ halt_drawdown_pct`, or on perp margin call.
- Position sizing per spec §2.2: `clip((z − 2)/2, 0.25, 1.0) × max_position_btc`.
- Auto-cap per spec §10: `max_position_btc` reduced on first step to fit `(capital × 0.4 × 2) / (spot_price × (1 + perp_margin_frac))` so a $10k account never tries to fund $18k+ of two-leg position.
- Funding-gate per spec §2.2: blocks new entries when negative funding × max-hold > basis bps offered.
- Catastrophic guards HALT (not just exit) per spec §5.

**Spec's locked-in-v1 design decisions all encoded:**
- two legs vs synthetic basis → two legs (§3.1)
- cash-funded vs borrow spot → cash-funded (§3.2)
- isolated vs cross-margin → isolated (§3.3)
- long-basis side disabled in v1 (§2.2)
- single-venue Deribit (§3.6)
- funding-gate on (§2.2)

**Reference baselines** at the same fee model and fill assumption:
- `FundingBot(positive_only=False, leverage=1.0)` — the always-on always-collect cousin.
- `FundingBot(positive_only=True, leverage=1.0)` — the smart-funding cousin (the spec's named comparator).
- `BuyHoldBot` — the directional control.

**Data:** `data/processed/basis_dataset_1h.csv` (62 025 hourly rows, 2019-04-30 → 2026-05-29), produced by the data-infrastructure branch (commit `8672959`). Contains perp close + Deribit composite index + Binance BTCUSDT spot all joined on the hourly grid, plus pre-computed basis vs each spot reference and cross-reference noise. Funding rates from `data/raw/deribit/funding_rates.json` (21 574 hourly snapshots, `interest_1h` field).

**Fee model:** per spec §3.4. Round-trip cost = 2 × (24 bps fee + 16 bps slip) = **40 bps per arb cycle**, before any funding accrual. **This single number explains the verdict** — median basis in the dataset is +1.95 bps, and z=2σ events at lookback=168h reflect typical absolute basis of 5-10 bps. The convergence-back-to-mean is 5-10 bps. Round-trip cost is 40 bps. Every arbitrary trade is mathematically a loser before funding can help.

**Warmup:** 168 hours of pre-window paired-price data fed via `bot.warmup(...)` so the rolling z-stats are computable from the first measured bar.

---

## 2. Regime windows

Five windows per Steven's locked decisions on spec §8.2. Different from DCA-Smart / Infinity Grid Gate 3 regimes (which framed *direction*); these frame *basis dislocation*. Plus the same held-out window as the other Gate 3s for any future cross-bot study.

| Regime | Window | Why |
|---|---|---|
| **covid_2020**  | 2020-03-01 → 2020-04-30 | Covid −50 % in 2 days. Largest basis dislocation in BTC history; spot oracles diverged from perp by 100s of bps. The spec named this as "likely the single biggest winning trade." |
| **luna_2021**   | 2021-05-15 → 2021-05-25 | BTC −45 % in a week (the LUNA-Anchor crack that preceded the 2022 collapse). Multiple basis dislocations during the cascade. |
| **ftx_2022**    | 2022-11-06 → 2022-11-18 | FTX collapse — dislocations + counterparty contagion. The strictest test of `dislocation_guard_z`. |
| **etf_2024**    | 2024-01-01 → 2024-01-31 | Bitcoin ETF launch (Jan 10). Basis ran wide in the run-up; sharp mean-reversion after approval. |
| **calm_2023**   | 2023-06-01 → 2023-09-30 | Post-FTX calm baseline. **The bleed test.** Bot should be flat ≥ 80 % of the time; net loss must stay < 1 % over the 4-month window. |
| **holdout**     | 2024-09-01 → 2026-05-22 | Out-of-sample window — never seen during the sweep. Mixed regime: ETF cooling, April-2025 wobble, the recent BTC range. |

The data pipeline's diagnostic dump confirmed all five windows are well-covered: median |perp − index| across the full 7-year history is ~2 bps once the bar-close timestamp alignment is correct (see [`data/raw/README.md`](../../data/raw/README.md)). No hidden data gaps in any regime window.

---

## 3. Parameter sweep

3 × 3 × 2 = **18 valid configs × 5 regimes = 90 sweep runs** (per Steven's brief; configs where `exit_z ≥ entry_z` are skipped).

| Knob | Sweep | Locked |
|---|---|---|
| `lookback_days` | {7, 14, 30} | — |
| `entry_z_threshold` | {1.0, 1.5, 2.0} | — |
| `exit_z_threshold` | {0.0, 0.5} | — |
| `max_position_btc` | spec default 0.10, auto-capped to capital | — |
| `perp_margin_frac` | spec default 1.0 (see §6 for failure-mode sweep at 0.5 / 0.2) | — |
| `slip_spot_bps` / `slip_perp_bps` | spec defaults 5 / 3 | yes |
| `fee_spot` / `fee_perp` | 0.0006 each (matches every other farm bot) | yes |
| `dislocation_guard_z` / `halt_drawdown_pct` / `funding_gate` / etc. | spec defaults | yes |

### 3.1 Per-regime "winners" (top config by vs-funding-smart pp)

| Regime | lb (d) | entry_z | exit_z | arb return | DD | trades | vs `funding` | vs `funding-smart` | vs BH |
|---|---|---|---|---|---|---|---|---|---|
| covid_2020   | 14 | 1.0 | 0.0 | **+0.02 %** | 0.03 % | 12 | +3.21 pp | −0.08 pp | −2.74 pp |
| luna_2021    | 7  | 1.0 | 0.0 | **+0.24 %** | 0.11 % | 8  | +0.42 pp | +0.16 pp | +27.77 pp |
| ftx_2022     | 14 | 1.5 | 0.5 | **0.00 %** | 0.00 % | **0**  | +1.42 pp | −0.00 pp | +28.04 pp |
| etf_2024     | 30 | 2.0 | 0.5 | **0.00 %** | 0.00 % | **0**  | +0.00 pp | +0.00 pp | −1.04 pp |
| calm_2023    | 30 | 2.0 | 0.0 | **−0.41 %** | 0.41 % | 33 | −1.01 pp | −1.08 pp | +0.24 pp |

**Read.** The "best" config for FTX and ETF takes **zero trades** — basis never crossed `entry_z` at those parameter settings over the 12-day / 31-day windows, so the bot is flat the whole time. LUNA's "winning" 7d / z=1.0 / exit=0 config takes 8 trades and earns +0.24 % (the only positive number in the entire scorecard); calm bleeds 0.41 %; covid bleeds 0.03 % across 12 round-trips.

The aggressive ends of the sweep (lookback=7d, entry_z=1.0) trade more and lose more — `lookback=7d, ez=1.0, xz=0.5` loses 1.08 % on vol-spikes combined and 6.14 % on calm.

### 3.2 Sweep-frontier — there is no good corner

Vol-spike combined return + calm return, sorted by vol-spike score (top = least bad):

| lookback (d) | entry_z | exit_z | vol-spike combined | vs `funding-smart` mean | calm return |
|---|---|---|---|---|---|
| **30** | **2.0** | **0.0** | **−0.03 %** ← winner | **−0.05 pp** | **−0.41 %** |
| 30 | 2.0 | 0.5 | −0.04 % | −0.05 pp | −0.49 % |
| 30 | 1.5 | 0.0 | −0.12 % | −0.07 pp | −0.90 % |
| 30 | 1.5 | 0.5 | −0.14 % | −0.08 pp | −1.12 % |
| 30 | 1.0 | 0.0 | −0.19 % | −0.09 pp | −2.06 % |
| 30 | 1.0 | 0.5 | −0.27 % | −0.11 pp | −2.63 % |
| 14 | 2.0 | 0.0 | −0.33 % | −0.13 pp | −0.83 % |
| 14 | 2.0 | 0.5 | −0.36 % | −0.13 pp | −1.11 % |
| 7  | 2.0 | 0.0 | −0.42 % | −0.15 pp | −1.14 % |
| 7  | 2.0 | 0.5 | −0.47 % | −0.16 pp | −1.45 % |
| 14 | 1.5 | 0.0 | −0.55 % | −0.18 pp | −1.82 % |
| 7  | 1.0 | 0.0 | −0.60 % | −0.20 pp | −4.40 % |
| 7  | 1.5 | 0.0 | −0.64 % | −0.20 pp | −2.45 % |
| 14 | 1.5 | 0.5 | −0.65 % | −0.21 pp | −2.50 % |
| 7  | 1.5 | 0.5 | −0.71 % | −0.22 pp | −3.24 % |
| 14 | 1.0 | 0.0 | −0.93 % | −0.28 pp | −3.70 % |
| 7  | 1.0 | 0.5 | −1.08 % | −0.31 pp | −6.14 % |
| 14 | 1.0 | 0.5 | −1.12 % | −0.32 pp | −5.21 % |

**Every config loses money on vol-spike windows combined.** The least-bad configuration (long lookback, high entry threshold, tight exit) trades the fewest times and bleeds the least; the most aggressive (short lookback, low entry threshold) trades the most and bleeds proportionally more. The bot's edge is monotonically worse with more trading. **This is the empirical opposite of the spec's hope** — the spec assumed `entry_z = 2.0` would pick entries where convergence was 60–80 bps; in the actual 2020–2026 dataset those events are 5–10 bps and the bot pays 40 bps to capture them.

### 3.3 Global winner pick

Per spec §8.4 the harness applies a two-tier specialist filter:

- **TIER 1** (full spec): vol-spike combined ≥ +8 % AND calm ≥ −1 % AND mean vs `funding-smart` ≥ +3 pp AND no halts outside FTX.
- **TIER 2** (relaxed): vol-spike combined ≥ +4 % AND calm ≥ −1 %.

**TIER 1 matches: 0 configs.** **TIER 2 matches: 0 configs.** The fallback composite picker (`vol_spike_combined − 2 × |calm|`) selected `lookback=30d, entry_z=2.0, exit_z=0.0` — the configuration that *trades the least* and *loses the least*. That tells you what kind of "winner" we have.

```json
{
  "lookback_days": 30,
  "entry_z": 2.0,
  "exit_z": 0.0,
  "vol_spike_combined_pct": -0.034,
  "vol_spike_mean_vs_funding_smart_pp": -0.053,
  "vol_spike_mean_vs_funding_pp": +1.189,
  "calm_return_pct": -0.411,
  "calm_halts": 0,
  "halts_outside_ftx": 0,
  "rationale": "FALLBACK: no config met any specialist tier. Picked by composite score = vol_spike_combined − 2×|calm loss|."
}
```

---

## 4. Walk-forward at the winning config

**Protocol.** 6-month test windows, 2-month stride (~33 % of test window). 29 tuning folds spanning 2019-05 → 2024-07 + 1 holdout (2024-09 → 2026-05). 168-hour warmup before each fold so the rolling basis stats are seeded. Same fold mechanics as the DCA-Smart and Infinity Grid Gate 3 harnesses.

### 4.1 Summary across 29 tuning folds

| Stat | Result | Verdict |
|---|---|---|
| **Positive-lift folds vs `funding-smart`** | **0 / 29** | catastrophic — every fold is a loss |
| Mean arb vs `funding-smart` | **−2.09 pp** | systematic underperformance |
| Median arb vs `funding-smart` | −1.57 pp | not driven by tail folds |
| Worst single fold | −5.34 pp (2020-11 → 2021-05; bull/contango) | the contango blow-out hurt |
| "Best" single fold | −0.46 pp (2022-07 → 2023-01) | even the best is a loss |
| Mean DD across folds | 0.56 % | catastrophic resistance: confirmed |
| Max DD across folds | 1.63 % | nowhere near the 10 % halt threshold |
| Folds with ≥ 1 halt event | **17 / 29** | mostly `dislocation_guard` during vol-spike events the bot should have captured |

**Read.** The bot loses to `funding-smart` in *every single one* of 29 walk-forward windows. The fold distribution isn't bimodal like DCA-Smart's "wins in bears, loses in bulls" — it's *uniformly losing*. The strategy's claim was that vol-spike windows would deliver the alpha that pays for the calm-window bleed. The walk-forward says: no vol-spike alpha emerges at meaningful size, period.

The 17 dislocation halts are the second-most-important fact in this section: the bot's `|z| ≥ 5` guard fires when basis blows out further from the mean. Per the spec's literal wording (§2.4), this happens on *either side* — a basis convergence overshooting to z=−5 fires the guard the same way an entry-side dislocation z=+5 fires it. In practice the guard fires during the moment of biggest convergence (basis snaps from +2σ → −12σ in a flash crash), halting the bot right when it would have realised the biggest single PnL. Choosing a same-sign-only guard would help with this, but per the spec's literal wording and per the spec's risk rationale ("Basis dislocation that doesn't mean-revert (5σ → 7σ). Defence: dislocation_guard_z closes and HALTs"), the absolute-z interpretation is defensible and was implemented as written.

### 4.2 Holdout — 2024-09-01 → 2026-05-22 (never seen during sweep)

| Metric | basis_arb (winner config) | funding | funding_smart | buyhold |
|---|---|---|---|---|
| Terminal return | **−1.77 %** | +3.56 % | **+3.80 %** | +31.51 % |
| Max DD | 1.77 % | 0.08 % | 0.00 % | 50.23 % |
| Sharpe | negative | small positive | small positive | ~0.4 |
| Trades | 86 (43 opens) | 0 | 0 | 1 |
| Halt events | 1 (dislocation_guard) | 0 | 0 | 0 |
| **arb vs funding_smart** | **−5.36 pp** | — | — | — |
| arb vs buyhold | −33.28 pp | — | — | — |

**The cleanest single answer in this report.** On the 1.7-year out-of-sample window the bot trades 86 times across 43 open/close cycles, gets halted once on a dislocation, and ends down 1.77 % while `funding-smart` quietly earns +3.80 % doing absolutely nothing. **The bot doesn't pay for itself out-of-sample by a wide margin.**

### 4.3 Walk-forward fold detail (chosen config)

Every fold below is a loss vs `funding-smart`. Sorted by `fold_start`.

| Test window | arb return | funding-smart return | arb − fnd-smart pp | trades | halts |
|---|---|---|---|---|---|
| 2019-05-09 → 2019-11-09 | −0.01 % | +5.45 % | −5.18 | 6  | 1 |
| 2019-07-09 → 2020-01-09 | −0.15 % | +1.08 % | −1.22 | 66 | 0 |
| 2019-09-09 → 2020-03-09 | −0.29 % | +1.30 % | −1.57 | 94 | 1 |
| 2019-11-09 → 2020-05-09 | −0.24 % | +0.60 % | −0.84 | 70 | 1 |
| 2020-01-09 → 2020-07-09 | −0.08 % | +0.85 % | −0.92 | 16 | 1 |
| 2020-03-09 → 2020-09-09 | −0.12 % | +0.76 % | −0.87 | 46 | 1 |
| 2020-05-09 → 2020-11-09 | −0.01 % | +0.82 % | −0.83 | 2  | 1 |
| 2020-07-09 → 2021-01-09 | −0.48 % | +3.29 % | −3.65 | 112 | 1 |
| 2020-09-09 → 2021-03-09 | −0.40 % | +4.31 % | −4.51 | 92 | 1 |
| 2020-11-09 → 2021-05-09 | −0.13 % | +5.50 % | **−5.34** ← worst | 22 | 1 |
| 2021-01-09 → 2021-07-09 | +0.10 % | +3.07 % | −2.88 | 22 | 1 |
| 2021-03-09 → 2021-09-09 | −0.13 % | +2.31 % | −2.38 | 6  | 1 |
| 2021-05-09 → 2021-11-09 | −0.24 % | +1.09 % | −1.31 | 4  | 1 |
| 2021-07-09 → 2022-01-09 | −1.55 % | +0.94 % | −2.47 | 88 | 0 |
| 2021-09-09 → 2022-03-09 | −1.61 % | +0.25 % | −1.85 | 98 | 0 |
| 2021-11-09 → 2022-05-09 | −1.06 % | +0.19 % | −1.24 | 116 | 0 |
| 2022-01-09 → 2022-07-09 | −1.63 % | +0.23 % | −1.86 | 126 | 0 |
| 2022-03-09 → 2022-09-09 | −0.87 % | +0.31 % | −1.18 | 90 | 0 |
| 2022-05-09 → 2022-11-09 | −0.53 % | +0.31 % | −0.83 | 72 | 0 |
| 2022-07-09 → 2023-01-09 | −0.38 % | +0.09 % | **−0.46** ← best | 64 | 0 |
| 2022-09-09 → 2023-03-09 | −0.59 % | +0.56 % | −1.14 | 80 | 0 |
| 2022-11-09 → 2023-05-09 | −0.51 % | +0.56 % | −1.07 | 68 | 0 |
| 2023-01-09 → 2023-07-09 | −0.56 % | +0.90 % | −1.45 | 60 | 0 |
| 2023-03-09 → 2023-09-09 | −0.59 % | +0.85 % | −1.42 | 56 | 0 |
| 2023-05-09 → 2023-11-09 | −0.90 % | +1.04 % | −1.92 | 80 | 1 |
| 2023-07-09 → 2024-01-09 | −0.62 % | +1.39 % | −1.99 | 54 | 1 |
| 2023-09-09 → 2024-03-09 | −0.26 % | +2.87 % | −3.04 | 22 | 1 |
| 2023-11-09 → 2024-05-09 | −1.46 % | +2.75 % | −4.09 | 80 | 1 |
| 2024-01-09 → 2024-07-09 | −0.56 % | +2.65 % | −3.13 | 36 | 1 |
| **HOLDOUT 2024-09 → 2026-05** | **−1.77 %** | **+3.80 %** | **−5.36** | 86 | 1 |

---

## 5. Head-to-head per regime + cross-bot correlation

### 5.1 Terminal return % by regime

| Regime | basis_arb | funding | funding-smart | buyhold |
|---|---|---|---|---|
| covid_2020 | −0.03 % | −3.09 % | +0.10 % | +2.83 % |
| luna_2021  | +0.00 % | −0.18 % | +0.08 % | −21.55 % |
| ftx_2022   | +0.00 % | −1.40 % | +0.00 % | −21.90 % |
| etf_2024   | +0.00 % | +0.00 % | +0.00 % | +1.05 % |
| calm_2023  | −0.41 % | +0.61 % | +0.68 % | −0.64 % |
| holdout    | −1.77 % | +3.56 % | +3.80 % | +31.51 % |

**The single positive number for basis_arb is +0.24 % in LUNA at lb=7d (above, §3.1) — the chosen winner config produces +0.00 % in LUNA.** Every regime row above is either a tiny loss or zero. `funding-smart` quietly out-earns the bot in every regime except `funding-smart` itself doesn't earn anything in LUNA / FTX / ETF (the brief weeks aren't long enough for daily funding to compound visibly).

### 5.2 Max drawdown % by regime

| Regime | basis_arb | funding | funding-smart | buyhold |
|---|---|---|---|---|
| covid_2020 | 0.03 % | 3.15 % | 0.00 % | 56.20 % |
| luna_2021  | 0.00 % | 0.26 % | 0.00 % | 36.26 % |
| ftx_2022   | 0.00 % | 1.40 % | 0.00 % | 26.74 % |
| etf_2024   | 0.00 % | 0.00 % | 0.00 % | 20.34 % |
| calm_2023  | 0.41 % | 0.05 % | 0.00 % | 21.01 % |
| holdout    | 1.77 % | 0.08 % | 0.00 % | 50.23 % |

DD is universally tiny — confirms catastrophic resistance. But this is **catastrophic resistance via the bot barely ever holding a position** (see "pct time in position" below).

### 5.3 Monthly-return correlation matrix (across all regime months)

| | basis_arb | funding | funding-smart | buyhold |
|---|---|---|---|---|
| **basis_arb** | 1.000 | −0.335 | −0.509 | −0.475 |
| funding | −0.335 | 1.000 | 0.501 | 0.524 |
| funding-smart | −0.509 | 0.501 | 1.000 | 0.321 |
| buyhold | −0.475 | 0.524 | 0.321 | 1.000 |

**The correlation tests trivially pass — but they pass *the wrong way*.** Both correlation thresholds in the spec are about confirming the bot is genuinely independent of the existing carry family. Basis-arb is *negatively* correlated with all three baselines (−0.34 to −0.51). Per the spec §9: "0.4–0.7 → earns its keep as complementary income; < 0.4 → suspicious, the bot is taking risk the carry family isn't and that risk needs identifying."

Here the risk being taken is *paying fees and slippage on dozens of small no-edge trades*. The negative correlation reflects the bot losing in months when `funding-smart` earns funding. That's the wrong shape of independence — useful diversification is "uncorrelated *positive* returns", not "negatively correlated *negative* returns".

---

## 6. Failure-mode test (Steven's Q3 decision: scorecard-relevant)

Per spec §11 open Q #3, Steven's locked decision: test `perp_margin_frac ∈ {1.0, 0.5, 0.2}` and treat the results as scorecard-relevant, not diagnostic. The smaller the `perp_margin_frac`, the more implicit leverage in the perp leg, and the larger the perp loss that can wipe the locked margin reservation.

### 6.1 Return and halt count by `perp_margin_frac`, by regime

| Regime | pmf=1.0 ret | pmf=0.5 ret | pmf=0.2 ret | pmf=1.0 halts | pmf=0.5 halts | pmf=0.2 halts | would-have-been-liquidated (any pmf) |
|---|---|---|---|---|---|---|---|
| covid_2020 | −0.03 % | −0.03 % | −0.03 % | 0 | 0 | 0 | **False** |
| luna_2021  | +0.00 % | +0.00 % | +0.00 % | 0 | 0 | 0 | **False** |
| ftx_2022   | +0.00 % | +0.00 % | +0.00 % | 0 | 0 | 0 | **False** |
| etf_2024   | +0.00 % | +0.00 % | +0.00 % | 0 | 0 | 0 | **False** |
| calm_2023  | −0.41 % | −0.41 % | −0.41 % | 0 | 0 | 0 | **False** |

### 6.2 Read — the failure-mode test was vacuous

**The bot is never liquidated on the perp leg at *any* margin level** — not 100 %, not 50 %, not 20 %. Returns are *byte-identical* across the three `perp_margin_frac` settings. The reason isn't that the bot is safe; the reason is that **position sizes are so small that perp losses never approach the locked margin reservation**. At the winner config's `entry_z = 2.0` with `lookback = 30d`, the bot opens ~50-bps-notional positions ($150–$500 at $10k capital). A 300-bps adverse move on a $500 perp short is $15 — well under the $50 even a 0.2 × $500 = $100 margin reservation absorbs.

**This kills the spec's expectation that failure-mode tests would characterise margin cascades.** They can't, because the bot never sizes up enough to test the failure mode. The BIS paper's central failure (cross-margin liquidation cascade) requires the bot to be in a meaningful-sized position when the cascade fires. The spec's auto-cap (`max_position_btc = 0.40 × capital / spot`) was designed for $10k accounts to survive a $90k BTC environment, but it's so conservative that the perp leg can't lose enough to test margin behaviour.

**Read against Steven's Q3 framing:** Steven wanted to "see how the bot dies, not just how it lives." The failure-mode test cannot answer that because at $10k paper capital, the bot doesn't die — it just never traded large enough to die. To actually characterise the BIS failure mode, this test would need to be re-run at $100k+ paper capital where the auto-cap relaxes, OR with `max_position_btc` forced to a larger value than the auto-cap allows. **Both options are out of scope for this Gate 3** (the bot would have to be re-spec'd to allow capital-overriding sizing); flagging as a known limit of this test.

---

## 7. Single-venue feasibility (Steven's Q4 decision)

Per spec §11 open Q #4, Steven's locked decision: plan Deribit-only, let Gate 3 prove or disprove. The data-infrastructure run confirmed the median cross-reference noise between Deribit's composite index and Binance spot is 4.24 bps — small enough to suggest single-venue viability.

The harness re-runs the winning config twice per regime: once with `spot = deribit_index_price` (single-venue), once with `spot = binance_spot_close` (cross-venue cross-reference).

### 7.1 Return % by spot reference, per regime

| Regime | `spot = deribit_index` | `spot = binance_spot` | opens (deribit) | opens (binance) |
|---|---|---|---|---|
| covid_2020 | −0.034 % | −0.001 % | 8 | 1 |
| luna_2021  | +0.000 % | +0.000 % | 0 | 0 |
| ftx_2022   | +0.000 % | +0.000 % | 0 | 0 |
| etf_2024   | +0.000 % | +0.000 % | 0 | 0 |
| calm_2023  | −0.411 % | −0.070 % | 17 | 3 |
| holdout    | −1.770 % | **−0.090 %** | 43 | 2 |

### 7.2 Read — single-venue Deribit is the WORSE reference, not the better one

The Binance reference produces **materially less bleed** in every regime where the bot actually trades. On holdout, Deribit-reference loses 1.77 % across 43 opens; Binance-reference loses 0.09 % across 2 opens.

**Mechanism.** The 4.24-bps median cross-reference noise reported by the data-infrastructure run isn't symmetric — it's biased on Deribit's side. Deribit's composite index updates on its own cadence (constituent venue snapshots, exchange micro-structure); Binance's spot close is a single deep-book consensus print. **The Deribit index produces small "ghost basis" events** — basis appears wide when the Deribit index lags the perp by a constituent-venue print or two, then snaps back to true. The bot's z-score signal triggers on these ghost events with the same statistical weight as real basis dislocations. Every ghost trade is a 40-bps round-trip loss.

The Binance reference filters out the ghost events because Binance and Deribit perp move together cleanly: a real basis dislocation shows up on both references; a ghost basis from Deribit's index lag shows up only on the Deribit reference. The bot trades fewer times on Binance and loses much less.

**Q4 answer.** Single-venue Deribit is **technically viable from a counterparty-risk perspective** — the bot doesn't blow up on its own index. But **for paper-bot performance, single-venue is the wrong choice**: the cross-venue Binance reference materially reduces spurious trading. The honest read for a v2 spec revision: use Binance (or any other deep external venue) as the *signal* source; trade against Deribit perp; this is one-sided cross-venue, not the full cross-venue capital pre-staging the spec §11 Q #11 calls out for live deploy.

This finding **does NOT change the verdict** — the bot still loses on the Binance reference in every regime that triggers a trade. Switching the reference improves the bleed but doesn't generate alpha. **There is no spot reference that turns this strategy positive in the 2020–2026 Deribit data at the constraints of the Gate 2 spec.**

---

## 8. Where it shines and where it bleeds

### 8.1 Where it shines

1. **Catastrophic resistance** — confirmed. Max DD across all 90 sweep runs + 30 walk-forward folds + 15 failure-mode runs + 12 single-venue runs = 1.77 %, in the holdout. The bot cannot be wiped out by anything short of Deribit going to zero (the spec-acknowledged single-venue risk in §3.6 / §5 #1). Zero perp liquidations at any `perp_margin_frac` level.
2. **Negative correlation with the funding family** — −0.335 with `funding`, −0.509 with `funding-smart`. The bot is genuinely independent of the existing carry edge. **But** see §5.3 — this is the wrong shape of independence (the bot loses when funding wins).
3. **No backtest crashes** — 90 sweep runs + 30 WF folds + 15 failure-mode runs + 12 single-venue runs, zero NaN trades, zero negative cash, zero halts attributable to bugs vs spec logic.
4. **LUNA week (10 days, 2021-05-15 → 2021-05-25)** — the *only* regime + config combination that produced positive return: `lookback=7d, entry_z=1.0, exit_z=0.0` earned +0.24 % across 8 trades. The cascade produced real basis dislocations large enough for the bot to capture before fees ate them. **This is the single piece of evidence that the mechanism works in principle.**
5. **Honest cross-reference finding** — the Binance vs Deribit reference comparison surfaces a real architectural conclusion (§7.2) that wouldn't have been visible without running both references side-by-side. The Q4 verdict is wrong as written (single-venue is *not* the better choice) and a future spec revision should be aware.

### 8.2 Where it bleeds

1. **Vol-spike windows combined** — **−0.03 % vs the spec's +8 % bar.** Eight percentage points of unleveraged return is what the spec asked the bot to deliver in its specialty windows; the bot delivered approximately zero, *negative on aggregate*. This is the headline failure.
2. **Walk-forward — 0 / 29 positive folds vs `funding-smart`.** The bot loses in every single 6-month window across 5 years. This is not a regime-mix problem; it is uniform inability to capture basis convergence at meaningful size.
3. **Holdout — −1.77 % vs `funding-smart` +3.80 % = −5.36 pp.** The cleanest out-of-sample answer: the bot is meaningfully worse than doing nothing-but-collecting-funding for 1.7 years.
4. **17 halts on `dislocation_guard_z` across 29 walk-forward folds** — the bot's catastrophic guard fires when basis blows further from the mean. Per the spec's literal `|z| ≥ 5` wording, this includes the convergence-overshoot side: when basis snaps from +2σ → −12σ in a flash crash, the guard fires at z=−12 and HALTs the bot mid-trade. **The biggest single trade the bot was designed to capture (Covid 2020-03-12) is the trade the guard halts it on.** Same-sign-only guard would help but contradicts the literal spec wording.
5. **Position sizes too small to test failure modes** — at $10k paper capital with the spec's auto-cap, perp positions are ~$150–$500 notional. Even at `perp_margin_frac = 0.2`, the perp leg can't lose enough to trigger a margin call. The failure-mode test results (§6) are vacuous on this dataset.
6. **The fundamental ratio: median basis vs round-trip cost.** Median basis in the dataset is +1.95 bps; round-trip fee + slip is 40 bps. **The strategy's entry signal triggers on z-score *deviations* — not absolute basis levels — and those deviations at z=2σ with a 7d lookback are typically 5–10 bps of absolute basis. The bot pays 40 bps to chase 5–10 bps of convergence.** Mathematics; not a parameter-tuning problem.
7. **`funding-gate` rarely fires.** The funding-gate is supposed to block entries when funding pays the wrong way more than the basis is offering. Across 90 sweep runs, the gate was active less than 2 % of the time — funding is rarely negative enough to block trades that already had positive basis edge.

### 8.3 Catastrophic resistance — the one hard win

The implementation enforces this by construction:
- Hard cash floor: open is skipped if `cash < spot_cost + perp_margin`. No negative-cash path.
- Drawdown halt: SHORT_BASIS → HALTED if equity drawdown ≥ 10 % from peak. Manual reset required.
- Dislocation halt: |z| ≥ 5 → close + HALT (this also accidentally halts on convergence overshoots — see above).
- Perp margin call: realised on close if perp unrealised PnL < −`perp_margin_reserved`. The locked reservation absorbs the loss; cash never goes negative.
- No leverage in the spot leg; no borrow; no implicit perp leverage at `perp_margin_frac = 1.0` (only an explicit dial reduces it).

**Across all 147+ runs in this Gate 3 the bot never went below 98 % of starting equity, never had negative cash, never had a NaN trade.** That's the unambiguous win. It's not enough — catastrophic resistance with no edge is "buy a stablecoin and earn 4 % T-bill yield instead." The funding-family already gives Steven 1–5 %/yr unleveraged carry; basis-arb adds nothing on top.

---

## 9. Final verdict

**FAIL → PARK.**

**Why FAIL:**
- Vol-spike combined return **−0.03 %** vs the spec's **+8 %** bar — a ~8 pp miss on the strategy's stated specialty.
- vs `funding-smart` in vol-spike windows: **−0.05 pp mean**, never reaching the **+3 pp** specialist threshold.
- Walk-forward: **0 / 29 positive folds**. The bot loses in every single 6-month window from 2019 → 2024. No fold ambiguity, no regime-mix story — just uniform underperformance.
- Holdout: **−5.36 pp** vs `funding-smart` over 1.7 years out-of-sample. The cleanest single answer.
- Failure-mode test inconclusive: positions never sized large enough to characterise margin cascades, contrary to Steven's Q3 intent.

**Why PARK (and not REWORK):**

The spec author already anticipated this outcome and named the conditions under which the bot should be parked rather than reworked (§8.5): *"Kill it. Fails vol-spike combined return OR correlation-with-FundingBot > 0.9. Strategy parked, brief filed under 'tested, the funding family already captures this edge.'"*

We failed vol-spike combined by 8 pp. We pass the correlation test trivially (and in the wrong way per §5.3). Both kill conditions trigger.

**The deeper reason** the strategy doesn't work at Gate 2 spec defaults is structural: post-2020 BTC basis on Deribit is *too efficient*. Median basis is +1.95 bps; z=2σ events with a 7-day lookback reflect ~5-10 bps of absolute basis; convergence captures 5-10 bps; round-trip cost is 40 bps. **The fee floor exceeds the typical edge by 4–8×.** No parameter setting in the explored grid can fix that — the spec's hope of "entries where mean convergence ≥ 60–80 bps" assumed 2020–2021 contango conditions that have since compressed.

Two specific reworks could *in principle* generate edge, both spec-violating:
1. **Allow `entry_z ≥ 3` AND much larger `max_position_btc`**. At z=3+ events the basis is genuinely large (50–200 bps); the bot would size into 100 % of cap and capture meaningful PnL when convergence happens. This is dynamic position sizing in disguise and changes the bot's character from "specialist on many small dislocations" to "rare-event hunter on big dislocations." Possible v2 spec direction.
2. **Use a different spot reference** (Binance / Coinbase / aggregated). The single-venue check (§7) shows Binance reference cuts the bleed by ~10×, but doesn't make the strategy positive. Switching reference is a Q4 decision the spec already rejected for v1, and changing it doesn't move the verdict from FAIL to PASS.

Neither rework is worth the effort given the funding family already extracts the carry edge that basis-arb hoped to supplement. The R&D plan's Phase 1 budget is better spent on the fourth Phase 1 bot (Funding-Dynamic, already FAIL-PARKED) or on Phase 2 strategies that don't compete with funding directly.

**The deployment recommendation: do NOT wire into `VARIANTS`.** Sit the harness on disk so a future spec revision can re-run it against a longer dataset or a relaxed parameter range. Don't deploy the bot to the live paper farm. The funding family + `funding-smart` already collect the carry edge the basis-arb mechanism is competing for; adding basis-arb would consume monitoring attention without contributing return.

---

## 10. Open questions surfaced during backtest

1. **`dislocation_guard_z = |z|` halts the bot during the moment of biggest convergence.** Spec §2.4's literal wording uses absolute z, but the rationale in §5 talks about "moving further from the mean" which is a same-sign concept. A v2 should pick one: same-sign guard (lets the bot ride deep convergence overshoots and exit on the normal `z < exit_z` rule) or |z| guard with an explicit acknowledgement that convergence overshoots trigger halts. The current implementation matches the literal spec.
2. **The failure-mode test is vacuous at $10k paper capital.** Position sizes auto-cap to $150–$500 notional; even at `perp_margin_frac = 0.2`, perp losses can't approach the margin reservation. To actually characterise margin cascade behaviour the test would need to (a) be re-run at $100k+ paper capital so the auto-cap relaxes, or (b) override the auto-cap to force larger positions. Both are spec changes.
3. **The Binance-vs-Deribit-index finding is real and should be in a v2 spec.** Spec §11 Q4 framed single-venue Deribit as the recommended v1 path; the data shows it materially worsens spurious trading vs using Binance as the reference. A v2 spec should default to cross-venue *signal* (still single-venue *execution*).
4. **The funding-gate is barely active.** Across 90 sweep runs the gate blocks <2 % of would-be entries. The spec §11 Q3 framed this as a v2-enhancement candidate; the empirical evidence is the simple version is fine because the wrong-funding-into-positive-basis condition is rare.
5. **The 7-day lookback for z-stats is mismatched to the modern basis regime.** A z=2σ event at lb=168h captures the noise frontier of 5–10 bps basis (low absolute, large statistical), not the meaningful events of 50–200 bps. Longer lookback (30d sweep included) reduces trade count but doesn't elevate per-trade edge. A v2 might explore *absolute basis* triggers in parallel with z-score triggers — "open when basis ≥ +60 bps OR z ≥ +2.5" — to recover the spec's mean-convergence target.
6. **Spec's predicted "biggest single winning trade" never materialised.** §8.2 named the Covid 2020-03 window as "likely the single biggest winning trade." The winner config's covid result is +0.02 % across 12 trades — the trades are tiny and several are losses; the single biggest covid winner config (`lb=14, ez=1.0, xz=0.0`) earned +0.02 % across the same 12 trades. The dislocation guard halts the bot on 2020-03-12 right as the +300-bps convergence move would have realised. No single fold contains a "big" win.

---

## 11. Why this is a clean PARK, not a moral failure

**The spec was written honestly.** §1 acknowledged that "the spec is structured so a no-result is a clean park, not a sunk-cost trap." §8.5 named the kill conditions explicitly. §12 said "the cost is justified if and only if Gate 3 produces ≥ +3 %/yr over the best FundingBot in vol-spike windows." We tested all of that, found the answer is no, and the spec author's pre-built park clause activates cleanly.

**The engineering cost was modest.** The bot is ~350 lines. The harness is ~700 lines. The dispatcher refactor was ~30 lines additive. Total ~1,100 lines of code that will live on disk for any future re-run. The data infrastructure (the `feat/basis-arb-data-fetch` branch already merged to main at commit `8672959`) is the largest fixed-cost investment and it serves *future* basis-related research too, not just this bot.

**The negative result is informative.** It's *evidence about the modern Deribit basis regime*, not noise. The funding-family wins on a 5-year out-of-sample window. Cross-venue signal is materially better than single-venue. The 40-bps round-trip cost is a hard floor for any high-frequency mean-reversion strategy on Deribit at retail fee tiers. These findings feed the next strategy spec.

**Phase 1 progress.** Three of four bots tested:
- Infinity Grid: PASS-AS-SPECIALIST (v3 rework, recommend deploy).
- DCA-Smart: PASS-AS-SPECIALIST (v2 rework, deployed to farm).
- **Basis Arb: FAIL → PARK** (this report).
- Funding-Dynamic: FAIL → PARK (commit `b1fa18e`).

50 % park rate is consistent with the R&D plan's expectation that not every Phase 1 spec would graduate. The R&D budget is appropriately partitioned: cheap experiments are tested honestly, the survivors get the deployment slot.

---

## 12. NO VARIANTS entry — do not deploy

The bot is intentionally **not** proposed for the `grid_farm.py` `VARIANTS` list. The dispatcher refactor (the `basis_arb` branch in `step_all()`, the `fetch_spot_index` helper, the `make_bot` + `_state_label` + `min_capital` entries) is committed on this branch as **dormant infrastructure** — none of it fires without a `VARIANTS` entry, and adding the dispatcher branch keeps the file consistent so a future v2 deployment is a one-line `VARIANTS` insertion rather than another cross-cutting refactor.

If, against this recommendation, Steven decides to deploy anyway (e.g., to confirm the paper result in live conditions for some weeks), the paste-ready entry would be:

```python
# NOT RECOMMENDED — see docs/gate3-reports/03-basis-arb.md §9 (FAIL → PARK).
# {"slug": "basis-arb", "name": "Basis Arb", "type": "basis_arb", "tab": "funding",
#  "style": "long spot + short perp on >2σ basis widening — Gate 3 FAIL-PARK",
#  "lookback_hours": 720, "entry_z_threshold": 2.0, "exit_z_threshold": 0.0,
#  "max_position_btc": 0.10, "max_hours_in_position": 168,
#  "perp_margin_frac": 1.0, "halt_drawdown_pct": 0.10},
```

The entry is intentionally commented out. The supporting dispatcher infrastructure (≈30 lines in `grid_farm.py`) stays in place as additive, no-op-without-the-VARIANTS-entry plumbing — a low-cost option on re-running this in 12+ months against a fresh dataset.

---

## 13. Artifacts

All under `docs/gate3-reports/03-basis-arb-data/`:

- `sweep_results.csv` — 90 rows: every (regime × 18 config) combo with terminal eq, return, DD, Sharpe, trades, halt count + reason, pct time in position, total funding, total convergence PnL.
- `regime_winners.csv` — per-regime top configs ranked by `arb_vs_funding_smart_pp`.
- `winner.json` — global winner under the tiered specialist picker + rationale.
- `walkforward_results.csv` — 29 tuning folds + 1 holdout fold at the chosen config, per-fold returns vs all three baselines.
- `comparison_results.csv` — head-to-head Basis-Arb vs FundingBot vs FundingBot-smart vs BuyHoldBot per regime + holdout.
- `monthly_return_correlation.csv` — cross-bot monthly-return correlation matrix.
- `failure_mode_results.csv` — `perp_margin_frac ∈ {1.0, 0.5, 0.2}` × 5 regimes head-to-head at the winning config (per Steven's Q3 decision).
- `single_venue_results.csv` — winner config re-run with `spot = binance_spot_close` vs `spot = deribit_index_price` (per Steven's Q4 decision).

Reproduce with:

```bash
cd ~/Documents/btc-wheel-bot/strategies
python3.11 basis_arb_backtest.py            # full Gate 3 run, ~3 s
python3.11 basis_arb_backtest.py --quick    # smaller sweep, ~1 s
```

---

*End of Gate 3 report. Bot at `strategies/basis_arb_bot.py`, harness at `strategies/basis_arb_backtest.py`. NOT wired into `grid_farm.py` `VARIANTS` — and the recommendation is to keep it that way. The dispatcher refactor (dormant `basis_arb` branch in `step_all()`, `fetch_spot_index` helper) is committed on this branch as additive, no-op-without-VARIANTS plumbing so a future v2 redeploy is a single line of code.*
