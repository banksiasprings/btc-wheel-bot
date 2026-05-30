# Gate 3 Report — Infinity Grid Bot

*Phase 1, Strategy 1 of the BSF Bot R&D Program · v1 2026-05-30 · v2 rework 2026-05-31 · v3 halt-sweep 2026-05-31*

> Spec at [`bsf-research-briefs/specs/01-infinity-grid-spec.md`](~/Documents/bsf-research-briefs/specs/01-infinity-grid-spec.md).
> Implementation at `strategies/infinity_grid_bot.py`; harness at `strategies/infinity_grid_backtest.py`.
> Raw artifacts in [`./01-infinity-grid-data/`](./01-infinity-grid-data/).
> Topic branch: `feat/infinity-grid-gate3` (NOT merged to main; the bot is NOT yet wired into `grid_farm.py` `VARIANTS` — v3 proposes the entry, Steven signs off).

---

## Rework v3 — drawdown-halt sweep — 2026-05-31 — Verdict: **SHIP as specialist, halt = 50 %**

Steven picked option B after v2 came back FAIL: rather than retire the strategy, ship the v1 mechanism + config as a **specialist bull-leg-capture bot** in the paper farm, and run one more sweep over just the drawdown-halt threshold to find the right "bleed but don't wipe" setting.

This is **not** "the bot passed the master scorecard." It's "the bot is a useful specialist in a portfolio of bots." The acceptance criterion changed.

### What was locked vs swept

Locked at the v1 winning config (the bull-capture one — NOT v2):
- `tail_pct = 15 %`, `spacing = 3.0 %`, `MA = 45 d`
- Slow trend-stop hysteresis **6 / 12 bars** (v1 values, not v2's 2/4)
- **No fast 3 × ATR secondary trigger** (the v2 add over-fired in the holdout — disabled, plumbing kept in source for future research)
- Q1 (hold tail through COOLDOWN) and Q2 (re-anchor at MA + 0.5 × ATR) unchanged

Swept: `max_drawdown_halt_pct ∈ {0.25, 0.35, 0.50, 0.75, 1.0}` (1.0 = effectively no halt at leverage 1).

### The headline finding

**The drawdown halt makes zero difference inside any single regime window.** Bull DD 24.3 %, bear DD 20.3 %, crab DD 15.3 %, crash DD 0 % — none of the regime windows individually exceeds 25 % drawdown at the locked config, so the per-regime numbers are *identical* across all five halt settings. The halt only matters when **regimes string together** in long-horizon backtests (walk-forward + holdout), where drawdowns compound through cycle transitions.

### Per-regime APR % (constant across all halt settings)

| Regime | Balanced | Infinity v3 | BuyHold |
|---|---|---|---|
| bull  | +74.2 % | **+1 477.8 %** | +2 583.1 % |
| bear  | +23.2 % |    −17.3 % | −75.3 % |
| crab  | +23.2 % |    +34.0 % | +68.9 % |
| crash | +27.8 % |     +0.0 % | −83.1 % |

### Per-regime DD % (constant across all halt settings)

| Regime | Balanced | Infinity v3 | BuyHold |
|---|---|---|---|
| bull  | 1.1 % | 24.3 % | 28.8 % |
| bear  | 0.4 % | 20.3 % | 77.2 % |
| crab  | 0.2 % | 15.3 % | 21.7 % |
| crash | 0.7 % |  0.0 % | 54.9 % |

### The halt-sweep result — where it matters

Walk-forward (17 tuning folds at v1 winning config, 2019-2024) + the held-out 2024-09 → 2026-05 window.

| Halt setting | WF mean APR | WF median APR | WF worst DD | WF mean Sharpe | WF halts | Holdout APR | Holdout DD | Holdout Sharpe | Holdout halted |
|---|---|---|---|---|---|---|---|---|---|
| **0.25** (current) |  174 % | 45 % | 26.5 % | 1.66 | **2 / 17** | 21.0 % | 25.5 % | 0.85 | **YES** |
| **0.35** | 170 % | 45 % | 37.9 % | 1.63 | **1 / 17** | 11.7 % | 35.0 % | 0.53 | **YES** |
| **0.50** | 170 % | 45 % | 45.5 % | 1.63 | **0 / 17** | **20.8 %** | 37.8 % | 0.76 | NO |
| **0.75** | 170 % | 45 % | 45.5 % | 1.63 | **0 / 17** | 20.8 % | 37.8 % | 0.76 | NO |
| **1.00** (no halt) | 170 % | 45 % | 45.5 % | 1.63 | **0 / 17** | 20.8 % | 37.8 % | 0.76 | NO |

**Read:**
- **halt=0.25** *fires* in 2 walk-forward folds + the holdout. Each fire is a *terminal* event — the bot stops and refuses to re-enter until manual reset. The holdout APR of 21 % is what was banked before the halt fired; the bot then sat dead in cash through the rest of the rally. **Operationally this is a paper-farm pain point — Steven would wake up to a "DEAD" bot every few months.**
- **halt=0.35** is the worst of both worlds: it still halts (1 fold + holdout), and because the bot bled an extra 10 pp before stopping, the holdout APR drops to 11.7 % — *worse* than the tighter halt. Loose-but-still-fires is the wrong place to land.
- **halt=0.50, 0.75, 1.00** are *identical* in our test data because the worst observed long-horizon drawdown is 45.5 %, which never trips a 50 % halt. The bot bleeds through cycle transitions, recovers in the next leg, and the holdout APR returns to 20.8 % (essentially identical to halt-25 *without* the terminal kill).

### Recommendation: **`max_drawdown_halt_pct = 0.50`**

The framing: this bot is **a specialist, not a survivor**. Its job is to capture bull-leg vol + long-bias drift — that's the +1 478 % APR in the 2020-10 → 2021-04 window, the +449 % in 2020-10 → 2021-04 walk-forward fold, the +339 % in 2023-10 → 2024-04. It deliberately gives back 30-45 % through cycle transitions because that is the cost of staying loaded for the next bull leg.

A 25 % halt **conflicts with the specialist thesis** — it kills the bot exactly when its strategy says "hold and recover." A 50 % halt is a *true catastrophic backstop*: nothing we've seen in 7 years of BTC history (covid, FTX, LUNA, 2022 cycle low, the 2024 yen-carry wobble) hits it, but if BTC dropped 60 % in a week without recovering, it would stop the bleed. That's the right place for an emergency brake — not in the middle of normal cycle drawdowns.

**halt = 0.75 or no-halt is too loose** — identical to halt-50 in our data but gives up the catastrophic backstop for free. Cheap insurance, take it.

**halt = 0.35 is strictly worse than 0.25** — both still kill the bot, but the wider halt lets it bleed further first. Don't pick a value where the halt fires occasionally; pick one where it fires only in genuine wipeout scenarios.

### Proposed `VARIANTS` entry (paste-ready for `grid_farm.py`)

Not added yet — Steven signs off after seeing this. When approved, append the following dict to the `VARIANTS` list in `grid_farm.py:42-99` and add the two parallel branches (one in `make_bot()`, one in `step_all()`) per the Gate 2 spec §5.5:

```python
{"slug": "infinity-bull",  "name": "Infinity (bull specialist)",
 "type": "infinity_grid", "tab": "grid",
 "style": "open-top grid + 45d trend filter — bull-leg specialist, expects 30-45% dips",
 "spacing": 0.030, "max_lots": 20, "ma_hours": 1080,
 "leverage": 1.0, "borrow_rate": 0.0,
 "infinity_tail_pct": 0.15, "reentry_buffer_atr": 1.0,
 "restart_cooldown_days": 3, "min_below_ma_bars": 6, "min_above_ma_bars": 12,
 "lower_price_floor_frac": 0.5, "max_drawdown_halt_pct": 0.50},
```

Required parallel changes Steven will need to apply at deploy time (none of these belong in this report's branch; they go in the same commit that adds the dict):

1. `grid_farm.py` import: add `from infinity_grid_bot import InfinityGridBot` next to the existing `grid_bot` import.
2. `make_bot()`: new branch `elif t == "infinity_grid":` constructing `InfinityGridBot(...)` from the variant kwargs.
3. `step_all()` dispatch: new branch `elif t == "infinity_grid":` calling `for ev in bot.on_close(price, low=low): ...` (mirror the existing `t == "grid"` branch).
4. `_state_label()`: add cases for `RUNNING / STOPPED / COOLDOWN / HALTED_DRAWDOWN` returning plain-English labels for the dashboard ("running grid (tail 0.0123 BTC)", "trend-stopped (in cash)", "cooldown — waiting for re-anchor", "HALTED — manual reset needed").
5. `min_capital()` bucket: same value as `type="grid"` (≈$300 at defaults).
6. `api.py` per-bot page: a `works` block in `_bot_page()` explaining the specialist framing in plain English ("This bot is built for bull legs; expect bigger dips than the other grids; the trend stop pulls it to cash on a confirmed downtrend").
7. `telegram_weekly.py`: no label change needed if `tab: "grid"` keeps it grouped with the cousins.

**Tab placement:** `tab: "grid"` — keeps the head-to-head visible against Balanced and the other six grid variants. The spec recommended a separate "infinity" tab, but the bake-off is more useful when this bot sits next to the cousins it's meant to complement (not replace).

### Expected behavior in the paper farm (so Steven knows what "normal" looks like)

- **In a bull leg (≥1 month of rising MA):** bot trades aggressively, accumulates the infinity tail, expected to outperform Balanced by multiples of APR.
- **In a confirmed bear leg:** slow trend-stop fires after ~6 h below the MA, active grid liquidates to cash, infinity tail (~15 % of accumulated BTC) rides the bear down. Expect 10–20 % drawdown during the bear leg itself.
- **Across a cycle transition:** drawdown can reach 25–45 %. **This is by design — do not panic-restart.** The bot recovers in the next leg.
- **If drawdown hits 50 %:** the halt fires, bot liquidates everything (including tail) to cash, refuses to trade until manual reset. **This should happen approximately never** in normal markets — if it fires, something is genuinely wrong (BTC has done something it has never historically done) and a human needs to look.
- **Cadence of trend-stop fires (slow MA):** roughly 0.5–1.0 per month based on walk-forward. Each fire is a normal operational event, not a halt.

### What changed in the source

Compared to v2 head-of-branch:
- `infinity_grid_bot.py`: class defaults reverted to v1 — `MIN_BELOW_MA_BARS = 6`, `MIN_ABOVE_MA_BARS = 12`, `FAST_STOP_ATR_MULT = None` (fast trigger disabled by default; plumbing kept so the v2 prototype is recoverable as an opt-in).
- `infinity_grid_backtest.py`: new `halt_sweep_main()` + `--halt-sweep` CLI flag. `run_infinity()` extended with `max_drawdown_halt_pct` and `fast_stop_atr_mult` kwargs. The original 144-run v2 parameter sweep code remains intact for reproducibility of the prior verdict.

### Artifacts new in v3

Under `docs/gate3-reports/01-infinity-grid-data/`:
- `v3_halt_regime_results.csv` — per (halt × regime) 4 × 5 = 20 rows.
- `v3_halt_walkforward_results.csv` — per (halt × fold) 18 × 5 = 90 rows.
- `v3_halt_comparison_results.csv` — Balanced + BuyHold + Infinity at the locked config (same as v1's `comparison_results.csv` since the halt doesn't affect single-regime numbers).

Reproduce with:
```
cd ~/Documents/btc-wheel-bot/strategies
python3.11 infinity_grid_backtest.py --halt-sweep   # ~18 s
```

---

## Rework v2 — 2026-05-31 — Verdict: **FAIL → recommend PARK**

The v1 report (below from §2 onward, kept for context) concluded MIXED and proposed three reworks: shrink the slow trend-stop hysteresis from 6/12 to 2/4 bars, add a fast 3×ATR single-bar secondary trigger, and push the sweep corners outward (lower tail, wider spacing). Steven approved all three. This section records what happened.

### What changed in v2

1. **Slow trend-stop hysteresis: 6/12 → 2/4 bars.** Fires after 2 hourly bars below the MA (was 6); re-arms after 4 hourly bars above MA + 1×ATR (was 12). Same MA window (default 30 d), same 1×ATR re-entry buffer.
2. **New fast secondary trigger.** A single-bar price drop greater than `3 × ATR_14d` (default; configurable as `fast_stop_atr_mult`) immediately liquidates the active grid lots, holds the infinity tail per Q1, and transitions RUNNING → COOLDOWN with a 1.5-day timer (half the slow path's 3 days). Bypasses the slow MA hysteresis entirely. Emits `SELL_FAST` + `FAST_STOP` events.
3. **Re-sweep corners pushed outward.** `tail_pct ∈ {5, 10, 15, 20}%` (was {15-70}%; v1 data said lower-tail = more survival). `spacing ∈ {3, 5, 7}%` (was {1-3}%; v1 winning config sat at the 3% corner). `MA ∈ {20, 30, 45}d` unchanged. **4 × 3 × 3 = 36 configs × 4 regimes = 144 runs.**

### v2 scorecard

| Scorecard criterion | Threshold | v2 result | v1 result | Pass? |
|---|---|---|---|---|
| Walk-forward mean APR | ≥ 15 % | **52 %** (median 14 %) | 174 % (median 46 %) | ✅ |
| Walk-forward mean Sharpe | ≥ 1.0 | **1.32** | 1.66 | ✅ |
| Walk-forward worst-fold DD | < 25 % | **25.65 %** (2021-01→07) | 26.53 % | ❌ by 0.65 pp |
| **Holdout APR** (2024-09 → 2026-05) | ≥ 15 % | **6.1 %** | 21.0 % | ❌ by 9 pp |
| **Holdout DD** | < 25 % | **25.15 %** | 25.49 % | ❌ by 0.15 pp |
| **Holdout Sharpe** | ≥ 1.0 | **0.39** | 0.85 | ❌ |
| Bot self-halt fires in holdout | informational | **Yes** | Yes | ⚠️ |
| Forced liquidations (exchange) | = 0 | 0 | 0 | ✅ |
| Capacity ≥ $50 k | yes | identical at $10k/$50k/$250k | identical | ✅ |
| **Per-regime survival** (no halt in any of bull/bear/crab/crash) | n/a — quality signal | **36 / 36 configs (100 %)** | 2 / 48 (4 %) | ✅ huge gain |
| **Infinity-specific:** beat best fixed-range grid by ≥ +3 %/yr **and no worse DD** | yes | DD bigger than Balanced in every regime | DD bigger than Balanced in every regime | ❌ |
| **Infinity-specific:** beat BuyHold (APR ≥ 0 OR DD ≤ ½ BHO's) | yes | wins in bear + crash; loses in bull + crab | wins in bear + crash; loses in bull + crab | ⚠️ MIXED (unchanged) |

**v2 winner config:** `tail_pct=20 %`, `spacing=3.0 %`, `MA=30 d`. Mean APR 111.5 % across the four regime windows, worst-regime DD 19.7 %.

### What the v2 reworks did and didn't do

**They worked in the intended direction:**
- **Per-regime survival went from 4 % to 100 %.** Every one of the 36 swept configs cleared all four regime windows without tripping the 25 % drawdown halt. The fragility v1 flagged is gone *at the per-regime level*.
- **Per-regime drawdowns shrank.** Median walk-forward DD dropped from 13.9 % (v1) to 8.5 % (v2). The worst single-window drawdown in the head-to-head table dropped from 24.3 % to 19.7 % in bull, from 20.3 % to 10.6 % in bear, from 15.3 % to 7.2 % in crab.
- **The fast trigger fires frequently and demonstrably defends.** Across the 18 walk-forward folds, the fast trigger fired an average of **28.4 times per 6-month fold** (vs the slow trigger averaging 0.7 times). In the holdout it fired **60 times in 21 months** — roughly once every 10 days. This proves the fast trigger is doing real work; it is not vestigial code.

**They did not — and this is the killer — solve the underlying problem:**
- **The holdout window is meaningfully worse than v1.** APR dropped from 21.0 % to 6.1 %, Sharpe from 0.85 to 0.39, drawdown stayed at 25 %, and the halt still fires. The bot still cannot survive 2024-09 → 2026-05 cleanly.
- **The fast trigger over-fires in normal volatility.** 60 fast stops in the holdout is not protection — it's stop-flip-stop cycling. Each fast stop liquidates the grid, waits 1.5 days, then reanchors and starts trading again. The cumulative cost is the lost edge during 90 idle days plus the round-trip fees on every liquidate/rebuild.
- **The bot still cannot beat `GridBot(Balanced)` on the drawdown criterion in any regime.** Balanced's drawdowns (0.2 %, 0.4 %, 0.7 %, 1.1 %) remain an order of magnitude smaller than Infinity v2's (7.2 %, 10.6 %, 0.5 %, 19.7 %), and Balanced beats Infinity on APR in 3 of 4 regimes. The Infinity-specific scorecard criterion #1 fails just as it did in v1.
- **APR fell more than DD did.** The v2 reworks traded return for safety, but the trade was *not 1:1* — the safety gain didn't recover the lost return.

### Head-to-head, v2

**Annualised return by regime (chosen v2 config: tail=20 %, spacing=3 %, MA=30 d):**

| Regime | Balanced | Infinity v2 | BuyHold |
|---|---|---|---|
| bull  |  +74.2 % | +441.7 % | +2 583.1 % |
| bear  |  +23.2 % |    −8.4 % | −75.3 % |
| crab  |  +23.2 % |    +5.2 % | +68.9 % |
| crash |  +27.8 % |    +7.7 % | −83.1 % |

**Max drawdown by regime:**

| Regime | Balanced | Infinity v2 | BuyHold |
|---|---|---|---|
| bull  | 1.1 % | 19.7 % | 28.8 % |
| bear  | 0.4 % | 10.6 % | 77.2 % |
| crab  | 0.2 % |  7.2 % | 21.7 % |
| crash | 0.7 % |  0.5 % | 54.9 % |

**Sharpe by regime:**

| Regime | Balanced | Infinity v2 | BuyHold |
|---|---|---|---|
| bull  | 13.22 |  3.47 |  4.30 |
| bear  |  8.97 | −1.20 | −1.78 |
| crab  |  9.31 |  0.67 |  1.48 |
| crash |  7.09 |  3.76 | −0.40 |

Infinity v2 beats BuyHold on APR in bear + crash (the survival-first value prop) and beats Balanced on APR in bull only. **Balanced wins 3/4 on APR and 4/4 on drawdown.** The v2 reworks did not change this picture in any regime — Balanced remains structurally hard to beat.

### v2 walk-forward, all folds at the chosen config

| Test window | APR % | DD % | Sharpe | Trades | Slow-stops | Fast-stops |
|---|---|---|---|---|---|---|
| 2020-01 → 07          |  +33.1 | 11.7 |  1.34 | 150 | 0 | 33 |
| 2020-04 → 10          |  +51.6 | 11.8 |  1.89 | 140 | 1 | 30 |
| 2020-07 → 2021-01     | +168.1 |  6.4 |  4.72 | 138 | 0 | 38 |
| 2020-10 → 2021-04     | +449.8 | 19.7 |  3.45 | 254 | 1 | 49 |
| 2021-01 → 07          |   +5.8 | **25.7** |  0.34 | 171 | 3 | 27 |
| 2021-04 → 10          |   +7.0 | 10.8 |  0.53 | 108 | 2 | 23 |
| 2021-07 → 2022-01     |   +8.4 | 24.3 |  0.41 | 166 | 1 | 26 |
| 2021-10 → 2022-04     |   −3.2 | 19.6 | −0.09 | 100 | 0 | 18 |
| 2022-01 → 07          |   −7.7 |  6.2 | −2.21 |  20 | 0 | 12 |
| 2022-04 → 10          |   −3.2 |  5.0 | −0.57 |  48 | 2 | 15 |
| 2022-07 → 2023-01     |   −6.1 |  7.1 | −0.79 |  56 | 2 | 22 |
| 2022-10 → 2023-04     |  +14.0 |  2.8 |  2.73 |  41 | 0 | 30 |
| 2023-01 → 07          |  +15.6 |  4.2 |  2.10 |  62 | 0 | 30 |
| 2023-04 → 10          |   +1.6 |  4.2 |  0.36 |  46 | 0 | 20 |
| 2023-07 → 2024-01     |  +24.3 |  2.7 |  3.41 |  82 | 0 | 35 |
| 2023-10 → 2024-04     | +106.3 | 10.0 |  3.72 | 152 | 0 | 45 |
| 2024-01 → 07          |  +18.0 |  8.4 |  1.16 | 114 | 0 | 30 |
| **HOLDOUT 2024-09 → 2026-05** | **+6.1** | **25.2** | **0.39** | 189 | 0 | **60** |

The holdout's 60 fast stops is the smoking gun. The fast trigger fires once per ~10 days on average in the held-out window. That cadence is not "flash crash protection"; it is the bot being mechanically dragged into and out of position by 2024-2025 BTC's normal hourly volatility, which produces frequent single-bar moves above 3 × ATR_14d. Each cycle pays round-trip fees and sits idle for 1.5 days. The cumulative drag is the holdout's 6 % APR — well below the 15 % bar — and the residual drawdown comes from the few legitimate flash events the bot DID need to catch.

### v2 final verdict — **FAIL → PARK the strategy**

Per the Gate 2 spec §7.6 fail criteria, this is a "Kill it" outcome by the letter — the strategy fails the drawdown bar (worst-fold and holdout) AND fails to beat the best fixed-range cousin on Sharpe (Infinity holdout 0.39 vs Balanced regime-mean ~9.5).

By Steven's own standard ("if MIXED again, be honest about that — we don't need to keep grinding indefinitely"), the right call is to **park the strategy** and file it under "tested, didn't work, here's why."

**Why it didn't work** (concise diagnosis from two rounds of empirical testing):
1. The infinity-tail concept *works* but the accumulated tail bleeds heavily on the way down, undoing the bull-leg gains.
2. The slow MA trend-stop *works* in slow bears but is structurally too late to catch flash events.
3. The fast 3×ATR trigger *catches* flash events but cannot distinguish them from normal high-vol bars, so it fires constantly and bleeds the bot via stop-flip-stop cycles.
4. There is no parameter regime in the swept space that simultaneously satisfies (drawdown < 25 %, APR ≥ 15 %, Sharpe ≥ 1.0) in the held-out 2024-09 → 2026-05 window.
5. `GridBot(Balanced)` — 5 % spacing / 20 lots / 15-day fast MA — is a simpler, smaller-drawdown, comparable-return strategy in every regime tested. Replacing Balanced with Infinity is a net regression on the survival-first scorecard.

**What we keep from this work:**
- `InfinityGridBot` source stays on disk (under `feat/infinity-grid-gate3`, not merged) as documentation of the failed design and for future reference if someone wants to revisit the idea with a different trigger model (e.g. funding-rate-gated tail accumulation, vol-of-vol regime classifier, etc.).
- The backtest harness `infinity_grid_backtest.py` is reusable for any Phase-1 strategy that needs multi-regime walk-forward.
- The negative finding for `GridBot(Balanced)` is positive: the Phase-1 effort can stop trying to beat Balanced with grid variants and turn to the *other* edges in the R&D program (funding-dynamic, basis-arb, vol-of-vol, regime-router).

### Open follow-ups (not blocking the PARK decision)

If Steven later wants to revive the idea, here's what was *not* tried because they would require additional architecture beyond the spec:
- **Vol-of-vol-gated fast trigger.** Only arm the fast trigger when ATR-of-ATR exceeds a threshold (i.e. when vol is *changing*, not just high). Would filter the false-positive fast stops that killed the holdout.
- **Funding-rate gate on the infinity tail.** Set `infinity_tail_pct = 0` when 8-hour funding < −0.01 % for 24 h, per Gate 2 spec open Q #8. Couples to the funding bots.
- **Asymmetric spacing.** Sell at +3 % rungs, buy at −1 % rungs. Wins the long-bias capture without the round-trip fee drag that kills tighter spacings. Substantively a different strategy.

None of these are "small tweaks." Each is a Gate 1 → Gate 2 → Gate 3 cycle in its own right.

---

## v1 report (2026-05-30) — kept below for context

> *The TL;DR table immediately below is the v1 verdict. The v2 section above supersedes it. Everything from "What was tested" onward is the v1 report unchanged, so the v2 analysis remains comparable point-by-point.*

## TL;DR (v1) — Verdict: **MIXED, leaning FAIL → recommend rework before Gate 4**

| Scorecard criterion | Threshold | Result | Pass? |
|---|---|---|---|
| Annualised return, mean across walk-forward folds | ≥ 15 % | **174 %** (median 46 %) | ✅ |
| Max drawdown, worst walk-forward fold | < 25 % | **26.5 %** (2021-01→07 fold) | ❌ by 1.5 pp |
| Sharpe ratio, mean across walk-forward folds | ≥ 1.0 | **1.66** | ✅ |
| Held-out APR (2024-09 → 2026-05, untouched during tuning) | ≥ 15 % | **21.0 %** | ✅ |
| Held-out drawdown | < 25 % | **25.5 %** | ❌ by 0.5 pp |
| Held-out Sharpe | ≥ 1.0 | **0.85** | ❌ |
| Forced liquidations (exchange margin call) | = 0 | **0** | ✅ |
| Bot self-halt (drawdown circuit-breaker fired) | informational | 2 / 17 folds + holdout | ⚠️ |
| Capacity ≥ $50 k notional | yes | identical metrics at $10k/$50k/$250k | ✅ (caveats) |
| **Infinity-specific:** beat best fixed-range grid by ≥ +3 %/yr with no worse drawdown | yes | wins APR in bull/crab, loses APR in bear/crash; **bigger DD in every regime** | ❌ |
| **Infinity-specific:** beat BuyHold (APR ≥ 0 OR DD ≤ ½ BHO's) | yes | wins 2/4 (bear, crash); loses 2/4 (bull, crab) | ⚠️ MIXED |

**3 PASS / 4 FAIL / 2 MIXED.** The failures cluster on two themes: drawdowns just over the 25 % bar and the inability to beat the existing Balanced grid on risk-adjusted metrics. The bot does deliver the survival-first value proposition vs BuyHold in real bear / crash regimes, but the cost is wider drawdowns than the existing fixed-range cousin we'd be replacing.

**Final recommendation:** **REWORK before Gate 4 paper deploy.** Specific reworks below in §6.

---

## 1. What was tested

**Bot:** `InfinityGridBot` — subclass of `GridBot`. Implements the locked spec:
- 3-state FSM (RUNNING / STOPPED / COOLDOWN) plus a 4th HALTED_DRAWDOWN state.
- 6-bar hysteresis to enter STOPPED; 12-bar above MA + `reentry_buffer_atr × ATR_14d` to enter COOLDOWN; 3-day cooldown to resume RUNNING.
- Infinity tail held through STOPPED + COOLDOWN per Steven's Q1.
- New floor anchored at `MA + 0.5 × ATR_14d` after COOLDOWN per Steven's Q2.
- Drawdown circuit-breaker at 25 % from peak: liquidates everything including the tail and refuses to re-enter without manual reset.
- 1× leverage hard-coded.

**Data:** `data/raw/spot/btc_1h.csv`, hourly close + low + high, 2019-01-01 to 2026-05-22 (64 716 bars). 2017-18 cycle is not available (data starts 2019); reported numbers reflect only the four cycles in our hourly history.

**Fee model:** intrinsic 0.0006 (six basis points) per fill, baked into `GridBot` and matched in `InfinityGridBot`. Identical to the live farm's `grid_farm.py`. Each backtest run is `bot.on_close(close, low=low)` per hourly bar — the exact same dispatch path the live paper bots take.

**Per-config warmup:** 720 hourly bars (30 days) of pre-window history fed via `bot.warmup(...)` so the MA + ATR are real numbers from bar 1 of the test window.

---

## 2. Regime windows

Picked from the public BTC chart. Reported names are the dominant regime feature of each window.

| Regime | Window | Why |
|---|---|---|
| **bull**  | 2020-10-01 → 2021-04-15 | BTC ~$10k → ~$63k, the canonical creeping uptrend the strategy is designed to harvest. |
| **bear**  | 2021-11-10 → 2022-11-22 | Cycle top $69k → FTX low ~$16k. The trend-stop's reason for existing. |
| **crab**  | 2022-12-01 → 2023-10-16 | Post-FTX range, ~$17k → ~$28k with deep oscillations. The whipsaw stress test. |
| **crash** | 2020-03-01 → 2020-04-15 | Covid -50 % in 2 days, then v-bottom. The flash-crash failure mode. |

**Not tested (data unavailable):** 2017-18 cycle, 2018 bear, and any pre-2019 dislocation.

---

## 3. Parameter sweep

48 configs per regime × 4 regimes = **192 backtest runs**. Defended budget — no compute pressure to reduce the grid.

- `infinity_tail_pct` ∈ {15, 30, 50, 70 %} — Steven's Q3 locked range (no 0 % degenerate case).
- `spacing_pct` ∈ {1.0, 1.5, 2.0, 3.0 %}.
- `trend_ma_days` ∈ {20, 30, 45 days}.

Other parameters held at spec defaults: `max_lots=20`, `min_below_ma_bars=6`, `min_above_ma_bars=12`, `reentry_buffer_atr=1.0`, `restart_cooldown_days=3`, `lower_price_floor_frac=0.5`, `max_drawdown_halt_pct=0.25`.

### 3.1 Survival rate across the sweep

| Status | Count | Notes |
|---|---|---|
| Survived all four regimes (no drawdown halt) | **2 / 48 (4 %)** | Both at `tail_pct=15 %`, `spacing=3.0 %`, MA ∈ {20d, 45d} |
| Halted in the bear regime | 46 / 48 | All wider-tail or tighter-spacing configs blow through the 25 % halt during the 2022 bear |
| Halted in the bull regime | 40 / 48 | Surprising — the parabolic top of 2021 produces enough pullback to trip the halt at higher tail_pct |
| Halted in crab / crash | 0 / 48 | These windows are short and oscillate; no sustained drawdown |

**Read:** the bot's design is fragile across the parameter space. Only the lowest tail / widest spacing corner survives every regime. This is by itself a yellow flag for parameter robustness.

### 3.2 Per-regime winners (best APR / (DD + 0.5pp) score)

| Regime | tail_pct | spacing | MA days | APR % | DD % | Sharpe |
|---|---|---|---|---|---|---|
| bull  | 50 % | 3.0 % | 20 | +1 910.6 | 24.7 | 4.32 |
| bear  | 30 % | 2.0 % | 30 |    −19.0 | 25.2 | −0.89 |
| crab  | 50 % | 1.5 % | 20 |     +70.8 | 19.3 | 1.67 |
| crash | 15 % | 3.0 % | 30 |     +20.7 |  1.1 | 4.25 |

**Read:** no single config dominates. Bull and crab prefer the wider tail (more long-bias on the way up); bear and crash prefer the narrower tail (less inventory to drag through the down-leg). This is a design tension the spec did not fully anticipate.

### 3.3 Global winner (survival-first composite, no-halt configs only)

The picker:
1. Eliminates any config that halts in **any** regime.
2. Among survivors, ranks by mean (APR / (DD + 0.5)) across the four regimes.

**Winner: `tail_pct=15 %`, `spacing=3.0 %`, `MA=45 d`.** Mean APR 374 %, worst regime DD 24.3 %. Score 15.24.

This is the only config Gate 3 would propose for Gate 4 paper deploy — and it's the corner of the search grid, not an interior point, which means the optimum is likely *outside* the swept range (lower tail, wider spacing). A second-pass sweep at `tail_pct ∈ {5, 10, 15 %}` and `spacing ∈ {3, 5 %}` would be the obvious follow-up if this strategy stays in scope.

---

## 4. Walk-forward at the winning config

Spec §7.4 protocol: 6-month test windows, 3-month stride, holdout = 2024-09-01 onward, never seen during sweep.

| Test window | APR % | DD % | Sharpe | Trades | Stops | Bot state |
|---|---|---|---|---|---|---|
| 2020-01 → 07 |    +45.6 | 20.8 |  1.27 | 288 |  8 | STOPPED |
| 2020-04 → 10 |    +78.8 | 13.9 |  2.05 | 308 | 11 | STOPPED |
| 2020-07 → 2021-01 |   +443.1 | 11.7 |  5.27 | 410 |  5 | RUNNING |
| 2020-10 → 2021-04 | +1 611.2 | 24.3 |  4.25 | 847 |  8 | RUNNING |
| 2021-01 → 07 |   +135.2 | **26.5** |  1.60 | 479 | 11 | **HALTED** |
| 2021-04 → 10 |    +12.7 | 17.8 |  0.58 | 242 | 10 | STOPPED |
| 2021-07 → 2022-01 |    +37.9 | **25.5** |  1.07 | 317 | 11 | **HALTED** |
| 2021-10 → 2022-04 |    −0.9 | 24.8 |  0.08 | 178 |  7 | RUNNING |
| 2022-01 → 07 |    −15.8 | 12.2 | −2.27 |  62 |  6 | STOPPED |
| 2022-04 → 10 |    −4.7 |  7.8 | −0.56 |  90 |  8 | STOPPED |
| 2022-07 → 2023-01 |    −8.7 | 10.9 | −0.76 | 102 |  8 | STOPPED |
| 2022-10 → 2023-04 |    +71.1 | 10.8 |  2.89 | 205 |  6 | RUNNING |
| 2023-01 → 07 |    +88.8 | 13.9 |  2.42 | 252 |  9 | RUNNING |
| 2023-04 → 10 |     +5.7 |  6.8 |  0.66 | 116 |  8 | COOLDOWN |
| 2023-07 → 2024-01 |    +77.9 |  5.8 |  3.83 | 224 |  3 | RUNNING |
| 2023-10 → 2024-04 |   +339.0 | 16.1 |  4.50 | 403 |  5 | RUNNING |
| 2024-01 → 07 |    +43.9 | 15.9 |  1.34 | 300 | 11 | STOPPED |
| **HOLDOUT 2024-09 → 2026-05** | **+21.0** | **25.5** | **0.85** | 525 | 19 | **HALTED** |

**Summary across 17 tuning folds:** Mean APR **174 %**, median 46 %, worst −15.8 %. Mean Sharpe **1.66**, worst −2.27. Worst DD **26.5 %**. Drawdown halt fired in **2 of 17** folds.

**Holdout:** **APR +21 %**, **DD 25.5 %**, **Sharpe 0.85**, **halt fired**. The holdout passes return but misses Sharpe and grazes the DD bar.

---

## 5. Head-to-head vs Balanced vs BuyHold

Same regime windows, same fee model. `GridBot(Balanced)` = 5 % spacing / 20 lots / 15-day trend-stop / no leverage. `BuyHoldBot` = buy at start, hold to end.

### 5.1 Annualised return by regime

| Regime | Balanced | Infinity | BuyHold |
|---|---|---|---|
| bull  | +74.2 % | **+1 477.8 %** | +2 583.1 % |
| bear  | **+23.2 %** | −17.3 % | −75.3 % |
| crab  | +23.2 % | **+34.0 %** | +68.9 % |
| crash | **+27.8 %** |  +0.0 % | −83.1 % |

### 5.2 Max drawdown by regime

| Regime | Balanced | Infinity | BuyHold |
|---|---|---|---|
| bull  | **+1.1 %** | +24.3 % | +28.8 % |
| bear  | **+0.4 %** | +20.3 % | +77.2 % |
| crab  | **+0.2 %** | +15.3 % | +21.7 % |
| crash | **+0.7 %** |  +0.0 % | +54.9 % |

### 5.3 Sharpe by regime

| Regime | Balanced | Infinity | BuyHold |
|---|---|---|---|
| bull  | **13.22** |  4.23 |  4.30 |
| bear  | **8.97**  | −1.38 | −1.78 |
| crab  | **9.31**  |  1.29 |  1.48 |
| crash | **7.09**  |  0.00 | −0.40 |

### 5.4 Honest reading of the comparison

**Balanced's drawdowns are suspiciously small** (0.2–1.1 %) and its Sharpe values are suspiciously large (7–13). Both are partially artifacts of the close-only fill model + 15-day MA firing on a single bar — the bot spends large fractions of these windows in cash, which mechanically suppresses both drawdown and volatility. In live trading with intra-bar action and false-positive stops, Balanced's drawdowns would be larger and its Sharpe meaningfully lower. **But the same fill model is used for Infinity, so the relative comparison stands.**

**Infinity vs Balanced — the verdict:**
- Infinity beats Balanced on APR in 2 / 4 regimes (bull by 20× the absolute amount; crab by 11pp).
- Infinity loses on APR in 2 / 4 (bear by 40 pp; crash by 28 pp).
- Infinity has wider drawdowns in **all four** regimes (by 15–24 pp).
- **The spec's Infinity-specific scorecard requires Infinity to beat Balanced by ≥ +3 %/yr AND with no worse drawdown. Infinity fails the drawdown clause in every regime.** Hard fail of this criterion.

**Infinity vs BuyHold — the verdict:**
- Bull: BuyHold wins decisively on APR (2 583 % vs 1 478 %), and Infinity's DD is not less than half BHO's (24.3 % vs 14.4 % half-BHO). **BuyHold wins.**
- Bear: Infinity wins decisively. −17 % vs −75 % APR; 20 % vs 38 % half-BHO DD. **Infinity wins on both criteria.**
- Crab: BuyHold beats Infinity on APR (69 % vs 34 %), and Infinity's DD is not less than half BHO's (15.3 % vs 10.9 % half-BHO). **BuyHold wins.**
- Crash: Infinity stayed flat in cash (the trend-stop fired at the start). 0 % vs −83 % APR. **Infinity wins.**
- **2 / 4 wins — passes the spec's "OR" criterion in the bear and crash, fails in bull and crab.** This IS the survival-first value proposition the strategy was designed for, but it's not unconditional.

---

## 6. Where this bot wins, where it loses

### 6.1 Where it wins

1. **Bear-leg drawdown vs BuyHold.** In the 2021-11 → 2022-11 cycle-top-to-FTX bear, the bot returned −17 % while BuyHold lost 75 %. The 30-day MA trend-stop fires once the macro trend rolls over, the active grid liquidates, and the tail rides the rest of the way down — losing some, but a small fraction of what a long-only position would lose.
2. **Flash-crash protection.** In the March 2020 Covid window the bot ended flat (0 %) while BuyHold lost 83 %. The mechanism: the 30-day MA was only slightly above price entering March; the first multi-bar break below MA fired the stop within the first day of the crash and the bot stayed in cash through the rest of it. This validates the trend-stop's core purpose.
3. **Bull-leg vol harvest + long-bias capture.** Walk-forward folds covering the 2020-2021 climb and 2023-2024 rally generated +443 %, +1 611 %, +339 %, +88 % APR — multiples of the headline buy-and-hold return because the bot harvested every wiggle on the way up AND retained an infinity tail. This is the source-of-edge the strategy was designed around, and it works.
4. **Capacity is unconstrained by the bot's logic.** Identical results at $10k / $50k / $250k — the bot is purely fractional. Real-world slippage at $250k is a separate question the harness can't answer from candle data, but the spec's $50k target is well inside Deribit BTC depth (per Gate 2 spec §9).

### 6.2 Where it loses

1. **Vs Balanced on every drawdown metric.** Balanced's 15-day MA firing on a single bar puts it in cash faster than Infinity's 30-day MA + 6-bar hysteresis. In every regime, Balanced's drawdown is 15–24 percentage points smaller. The spec's hysteresis was added to defend against whipsaw, but on the historical data the whipsaw cost to Balanced is invisible while the lag cost to Infinity is large. This is the most serious finding in the report.
2. **2021-Q2 / 2022 bear walk-forward folds.** Three consecutive folds (2022-01→07, 2022-04→10, 2022-07→2023-01) produced negative APR with negative Sharpe. The bot took small losses on each fold's local trend-stop fires while sitting in cash, then bled the infinity tail. Across these three folds the equity drifted down ~25 % cumulative — exactly the territory the 25 % halt is designed to trip on.
3. **Drawdown halt fires 3 times across 18 walk-forward folds.** In live trading this is a *terminal* event — the bot stops trading and refuses re-entry until manual reset. Three terminal events in ~6 years of historical data is too many for an unsupervised paper deploy, let alone real money. **This is the single strongest signal that the strategy needs rework before Gate 4.**
4. **Holdout window (the cleanest out-of-sample test) misses Sharpe and grazes DD.** Sharpe 0.85 vs threshold 1.0; DD 25.5 % vs threshold 25 %. The bot halted in the holdout. The strategy that "works on backtest" does not survive the held-out 2024-09 → 2026-05 window without rework.
5. **Crash regime returns 0 %, not positive.** The bot's trend-stop fired right at the start of the Covid crash window, so the bot stayed in cash through the whole window. Defensive but not productive — and the spec's vision of "long-vol-overlay-via-grid" requires the bot to actually grid through micro-crashes, not just sit out.
6. **Winning config sits on the corner of the search grid.** `tail=15 %` is the lowest swept; `spacing=3.0 %` is the widest. The optimum is plausibly outside the search range entirely, which means Gate 3 has not actually located the strategy's true optimum — it has only located the best survivor inside the brief's search box.

---

## 7. Issues / open questions surfaced during backtest

These were not in the spec but emerged from the runs.

1. **Should the drawdown halt be tighter, looser, or removed?** At 25 % it fires too often to be acceptable (3× in 18 folds). At 15 % it would fire even more. At 35 % it would mostly stop firing, but at that point it's not really providing protection. The spec's choice of 25 % matches the scorecard's pass threshold, so a tighter halt would be self-inconsistent. **Steven decision needed.**
2. **The 6-bar / 12-bar hysteresis is too slow for bear regimes.** The lag costs ~15–20 pp of drawdown vs Balanced's 1-bar trigger. Either reduce hysteresis to 1–2 bars (accepting whipsaw cost) or add the fast secondary trigger from Gate 2 spec §10 open Q #12 (4-hour ATR-breach emergency stop).
3. **The Q3 sweep range `{15, 30, 50, 70}` looks too high.** Best survival is at 15 %. A follow-up sweep at `{5, 10, 15, 20}` would tell us whether there's a lower-tail config that survives more robustly.
4. **The Q2 re-anchor at `MA + 0.5 × ATR` is rarely tested.** Most walk-forward folds end in RUNNING or STOPPED rather than COOLDOWN-then-resume, so the re-anchor logic is exercised infrequently in this dataset. It is correct per the spec but unmeasured by these runs.
5. **Bull-regime APRs are inflated by annualization of short windows.** A 6.5-month +311 % return annualizes to +1611 % APR. The report cites both APR and absolute return so the reader can see which dominates, but Steven should anchor on the absolute-return columns when comparing across regimes of different length.
6. **Balanced's small drawdowns may be a fill-model artifact** (see §5.4). A proper paper deploy of Infinity *and* Balanced over 8–12 weeks would clarify whether Balanced's edge holds up in live trading. This question is Gate 4-blocking for *both* bots, not just Infinity.

---

## 8. Final verdict

**MIXED — recommend REWORK before Gate 4 paper deploy.**

The bot delivers the survival-first value proposition vs BuyHold in the regimes it was designed for (bear, crash), but loses to the existing `GridBot(Balanced)` on every drawdown metric and on APR in two of four regimes. The drawdown halt fires 3 times across 18 walk-forward folds — too often for an unsupervised deploy. The holdout window fails the Sharpe bar and grazes the DD bar. The winning config sits on the corner of the search grid, meaning the true optimum has not been located.

**Specific reworks to attempt before re-submitting to Gate 3:**

1. **Reduce stop-trigger hysteresis to 2 bars** (from 6). The spec's whipsaw defence is real but the cost is larger than the benefit on the historical data.
2. **Add a fast secondary stop trigger** (spec open Q #12): liquidate active grid if the close drops more than 3 × ATR_14d in a single bar. This is what would have helped in the holdout's 25.5 % drawdown event.
3. **Extend the `infinity_tail_pct` sweep to `{5, 10, 15, 20 %}`** — the data says lower tail = more survival, and the current sweep cuts off at 15 %.
4. **Extend the `spacing_pct` sweep to include `{3, 5, 7 %}`** — the winning corner is the widest tested, suggesting wider could win.
5. **Walk-forward Balanced with the same protocol** so the head-to-head comparison is fold-by-fold (not just full-regime), neutralising the close-only fill-model artifact noted in §5.4.

**If Steven decides to proceed with Gate 4 anyway** (e.g., the "ship with caveats" option from the spec), the deploy config is `tail=15 %, spacing=3.0 %, MA=45 d`, the bot is paper-only, the dashboard label should read "Infinity (research — drawdown-halt-prone)" and the 8-week digest should flag the bot specifically if the halt fires. The bot should be paired with Balanced in the same tab so the head-to-head is visible.

**If the rework path is taken:** target a re-run of Gate 3 with the extended sweep and the hysteresis / secondary-trigger changes inside 2 weeks. The harness is in place; only the bot file needs editing.

---

## 9. Artifacts

All under `docs/gate3-reports/01-infinity-grid-data/`:

- `sweep_results.csv` — 192 (regime, config) → 14-metric rows.
- `regime_winners.csv` — per-regime winners table.
- `winner.json` — chosen global winner config + score.
- `walkforward_results.csv` — 17 tuning folds + 1 holdout fold at the winning config.
- `comparison_results.csv` — Infinity vs Balanced vs BuyHold per regime.
- `capacity_results.csv` — winning config at $10k / $50k / $250k notional.

Reproduce with:
```
cd ~/Documents/btc-wheel-bot/strategies
python3.11 infinity_grid_backtest.py         # full Gate 3 run, ~10 s
python3.11 infinity_grid_backtest.py --quick # smaller sweep, ~5 s
```

---

*End of Gate 3 report. Bot at `strategies/infinity_grid_bot.py`. NOT wired into `grid_farm.py` `VARIANTS` — that is the Gate 4 decision.*
