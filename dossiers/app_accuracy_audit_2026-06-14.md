# Widget app accuracy audit + Freyr ensemble investigation — 2026-06-14

**Scope.** (1) Full accuracy sweep of the 6-tab phone dashboard at
`bot.banksiaspringsfarm.com` (FastAPI `api.py` → `:8765` → cloudflared). (2) Investigate
why the Freyr ensembles (v0.1.1 / v0.2 / v0.3 / v0.4) look "rubbish" on the app —
display/data bug vs honest underperformance.

**Method.** Read every tab builder in `api.py`; spot-checked rendered values against the
raw Freyr snapshots (`~/Documents/freyr/paper/snapshots/`), `book_performance_profiles.json`,
`dispatcher_league.json`, `testnet_live.json`, and the OHLC cache. Offline render harness
(`/usr/local/bin/python3.11` importing `api`) used throughout.

**Headline verdict.** The widget is **rendering the data faithfully** on every surface.
The Freyr ensembles look bad because of a **Freyr-side stale-data artifact**, not a widget
display bug and not a genuine trading loss. Two real widget gaps were found and fixed
(v0.4 missing; staleness never surfaced). Constraints honoured: `feedback_show_raw_signals`
(staleness surfaced, not hidden; numbers unchanged) and `feedback_evidence_over_deadlines`
(no massaging — the honest read is that Freyr's first week of forward paper is
*uninformative*, not good).

---

## SECTION A — Fixes shipped

### A1. Freyr **v0.4** ensemble was active but never rendered  *(commit `6aa270f`)*
- **Bug.** `v0.4` ("hot baseline" — v0.1.1 + vol-target 12%→18%) shipped 2026-06-11,
  `active:true`, ticks daily in `--variant all`, has snapshots through 2026-06-13. But it
  was absent from `FREYR_VARIANTS`, `FREYR_ENSEMBLES`, and `FREYR_META`, so it appeared on
  **no** surface — not Portfolios, Board, the union table, or the `/freyr/v0.4` detail
  route. The dashboard was silently one ensemble short of reality (and short of what the
  v0.4 ship note intended: "active:true → … + dashboard").
- **Fix.** Added `v0.4` to all three. Now renders a Portfolios card, a Board row
  (🌶️ Freyr v0.4, **−0.09%**, Model CAGR **20.3%**, Sharpe 1.07, DD −12.6%), a union row,
  and a working detail page. Verified against the raw snapshot (`paper_equity` 0.9991,
  `cagr` 0.2031).
- **Not cosmetic:** this is a completeness gap in the data feed → UI mapping, not a number
  patch.

### A2. Model **data-staleness** was hidden behind a green "Heartbeat OK"  *(commit `ca29632`)*
- **Bug.** Freyr stamps `vol_staleness` (per-book `last_updated`/`age_days`/`stale`) and
  `target_as_of` into every snapshot, but `api.py` **never read either field** (`grep`
  confirms zero references). So the ensembles showed a green "Heartbeat OK" chip and a
  flat/negative forward number while **7 of 12 books sat on price/vol data frozen since
  2026-06-05** (8 days stale), with the model evaluating **as-of 2026-06-05**, not the
  2026-06-13 the card implied. A negative forward figure on a frozen panel reads as "Freyr
  is losing money trading" when it is actually rebalance-cost decay — a misleading surface.
- **Fix.** Added `_freyr_staleness()` + `_stale_banner()`: an amber "stale model data" bar
  on the Portfolios card and the detail header (*N/12 books frozen since DATE; model as-of
  gap*), plus a **⚠ marker on the Board rows** for the 7 affected Freyr portfolios (4
  ensembles + 3 dispatchers). The 11 specialists carry no `vol_staleness` block and are
  correctly left unflagged. **The numbers are unchanged** — this is honest context, the
  opposite of a cosmetic patch (`feedback_show_raw_signals`).

### Surfaces audited and found **correct** (no change needed)
| Tab | Check | Result |
|---|---|---|
| 🏆 Board | 3 rows spot-checked vs raw (v0.1.1 −0.18% / CAGR 18.36% / Sh 1.174; v0.3 CAGR 27.91%; v0.4 −0.09%) | ✅ exact |
| 🏆 Board | Return / window / Model-CAGR / Sharpe / DD / Switch math | ✅ (basis notes below) |
| 🤖 Books | each book's badge tone ↔ its `reason` code (badge is derived from the same dict) | ✅ consistent |
| 🤖 Books | click-through `paper_ret` vs raw `paper_track` (tail_hedge −0.153%, funding_carry −0.015%) | ✅ matches |
| 👤 Mine | NAV mark-to-market via `steven_portfolio` (+0.90%, $10,089.88), honest "reading this fairly" disclaimer | ✅ correct |
| 🔌 Testnet | post-teardown flat: $979 spot, 0 positions / 0 orders / 0 leverage; 24h −7.72% = the teardown close | ✅ reflects reality |
| 📊 Review | renders Freyr's weekly md verbatim; "best dispatcher +0.00% vs Mine +0.90% — too early to call (3d<30d)" | ✅ faithful + honestly hedged |

**Two pre-existing basis notes (not bugs, flagged for awareness):**
- Board **Sharpe** column mixes basis: Freyr rows show the *model/backtest* Sharpe
  (~1.17), farm survivors show a *paper-track* Sharpe. Defensible (Freyr's 7-day paper
  track is too short for a real Sharpe) but it is one column on two bases.
- Board **Max DD** for Freyr uses the *model track* worst peak-to-trough (e.g. v0.1.1
  −11.1%), while the card's "Drawdown" chip shows *current* model DD (−3.8%). Different
  metrics, both legitimate; legend says "worst peak-to-trough".

---

## SECTION B — Honest findings (real issues the data shows)

### B1. The Freyr "underperformance" is a **stale-feed artifact**, not a trading loss — and the root cause is in Freyr's data pipeline
This is the answer to "why does the ensemble look rubbish."

**The forward paper curve is a flat cost decay, identical across every variant:**

| variant | forward paper return | days | per-day Δ | Model CAGR (backtest) |
|---|---|---|---|---|
| v0.1.1 | **−0.18%** | 7 | −0.045% | +18.4% |
| v0.2 | **−0.135%** | 5 | −0.045% | +19.3% |
| v0.3 | **−0.135%** | 5 | −0.045% | +27.9% |
| v0.4 | **−0.09%** | 2 | −0.045% | +20.3% |

Every variant, every day, loses **exactly −4.5 bps** — regardless of leverage (v0.1.1 at
1.6× and v0.3 at 2.4× bleed the *identical* amount). Real mark-to-market P&L can't be
leverage- and regime-independent. Tracing the engine
(`freyr/paper/engine.py:331`): `paper_day_ret = gauge["day_return"] − slip`, and in every
snapshot **`dd_gauge.day_return == −0.0`** while `slip` (3 daily rebalance fills) == 4.5 bps.
So the forward curve is **pure cost with zero market P&L credited.**

**Why `day_return` is 0:** the model equity is **frozen** — `model_equity = 4.09569` for
v0.1.1 is byte-identical across 2026-06-08/-11/-12/-13, and the `model_track` appends new
dates (6/12, 6/13) at the *same* value. The model isn't advancing because its joint
cross-asset backtest panel is bounded by the **stalest feed**:

| feed | last bar | status |
|---|---|---|
| BTC / ETH / SOL (crypto) | 2026-06-13 | ✅ fresh (updates daily) |
| QQQ / SPY / GLD / TLT | 2026-06-05 (Fri) | ❌ stale — **5 trading days missing** (6/08–6/12) |
| DXY | 2026-06-07 | ❌ stale |
| VIX | 2026-06-04 | ❌ stale |

(6/06–6/07 was a weekend, but today is Sun 6/14 — the equity/macro side has missed every
trading day since 6/05, so this is a genuine pipeline stall, not a weekend gap.) The
snapshot's own `vol_staleness` confirms it: **7/12 books stale, `last_updated: 2026-06-05`,
`age_days: 8`**, and `target_as_of: 2026-06-05`.

**Conclusion.** The ensembles look bad on the Board (they're the only negative rows, dead
last, while fresh-crypto farm bots float to the top at +2–7%) because their equity sleeve
is marking against an 8-day-frozen panel → 0 model return → the forward "paper" line is
just the daily rebalance cost. **It is neither a widget bug nor a real market loss — the
forward track is currently *uninformative*.** We cannot yet say whether the Freyr ensemble
makes or loses money forward; the first week measured cost on a frozen model.

**Owner: Freyr, not this repo.** The fix is to refresh the equity/macro OHLC feeds
(`freyr/data/equities.py`, `data/sources/macro.py`, `data/fx.py`, `data/dvol.py` — the
crypto puller `data/crypto.py` is fine) and re-tick. This is **not** patched in the widget
(that would be cosmetic). The widget now *surfaces* the staleness (B-fix A2).

### B2. Freyr's own `heartbeat.all_ok` and `data_staleness.stale` are wrong
Within the same snapshot, `heartbeat.all_ok: true` and `data_staleness.stale: false` while
`vol_staleness.all_fresh: false` and 7 books are 8 days stale. The heartbeat counts
"misses vs grid" (and `grid_last` is itself pinned to 2026-06-05), so it never trips on a
wholesale equity-feed stall. **Freyr-side bug** to flag: the staleness/heartbeat monitors
don't catch a frozen cross-asset panel. (The widget now derives its own staleness from
`vol_staleness`, which is correct, rather than trusting `heartbeat.all_ok`.)

### B3. The dispatchers "beat" the ensembles for a structural reason, not skill
`dispatcher_league.json`: dispatch_legacy/momentum/mtf all show **+0.00%** forward while
the ensembles show −0.09…−0.18%. That's because in chop the dispatchers hold **cash** (0
fills → 0 cost), whereas the ensembles hold 5/12 books and pay the 4.5 bps/day rebalance
cost. So "dispatchers flat, ensembles slightly red" is an artifact of *who's holding
during a stale-data chop window*, not selection alpha. The league verdict already says this
honestly: **"building — best dispatcher +0.00% vs Mine +0.90% (too early to call — BSF P5)"**.
Mine is +0.90% because it holds fresh-crypto farm bots, not the frozen-sleeve books.

### B4. Pending: the parallel fee-fix will move these numbers (directionally *worse*, honest)
A parallel task corrected the crypto taker fee 2.0 → 4.5 bps/leg (`freyr` commits
`a4dd46b`, `b8c5a67`; `crypto-cost-bps` 3.0 → **5.5** bps/side). **As of this writing the
snapshots have not regenerated** (latest is still `9ed14c7`), so every number above is on
the *old* cost. When the paper tick + backtests re-run:
- **Model CAGR drops** (higher cost over full history) — e.g. v0.1.1 18.4% will fall.
- **Forward bleed grows** (>4.5 bps/day) until the feeds are refreshed.

Both make Freyr look *slightly worse*, which is correct — do not mask it
(`feedback_evidence_over_deadlines`). The widget reads snapshots live off disk, so it picks
up the new numbers automatically once Freyr regenerates them (an api restart only reloads
code, not data). This audit will be updated if/when the regeneration lands.

---

## Update — root cause fixed (Freyr-side), 2026-06-14 same session

The stale feed was traced to a **missing auto-refresh**: `paper/run.py` refreshed only the
crypto caches each tick (the "O20" anti-freeze fix), while the equity/FX/VIX caches had
none — `load_equity`/`load_fx` default `refresh=False` and `regime._load_vix` only
downloads when the file is **absent**. So once seeded, SPY/QQQ/GLD/TLT/DXY/VIX never
advanced. yfinance was verified working (pulls cleanly through 2026-06-12), so this was
purely a wiring gap, not a broken puller. **Fixed in two parts (Steven: "do what you need to"):**
- **Immediate:** hand-refreshed all caches → equities/FX/VIX now through **2026-06-12**,
  crypto **2026-06-13**. (Steven approved; no manual re-tick — see below.)
- **Durable (Freyr commit `0eac721`):** added a mtime-gated `refresh_stale_caches` to
  `data/equities.py` + `data/fx.py` and `refresh_stale_vix` to `regime.py`, mirroring
  crypto's exact contract (≤once/tick, graceful per-file fallback, `FREYR_OFFLINE` no-op),
  and wired all four into `run.py` before variants load. Verified end-to-end: aging a cache
  to 48h triggers a clean re-pull; fresh caches are skipped.

**Deliberately did NOT force a manual re-tick.** The forward paper track is the live
record, and how the 6 frozen days reconcile is a Freyr-engine call — not something to
decide unilaterally mid-fee-task. The model unfreezes **on the next scheduled tick**
(tonight's 19:00 UTC cron runs the new `run.py` → refreshes nothing-now-stale → marks
v0.1.1 against the fresh panel → publishes), and the widget's amber banner auto-clears once
`vol_staleness.all_fresh` flips true. The unfreeze-to-6/12 is deductively certain (the
panel is now bounded by fresh feeds, not 6/05).

## Recommended next steps (in priority order)
1. **[done — Freyr `0eac721`]** Auto-refresh equity/FX/VIX caches each tick. The single
   highest-impact fix for "Freyr looks rubbish" — turns the forward track from cost-decay
   into a real signal from the next tick on.
2. **[Freyr — flag]** Fix the staleness/heartbeat monitors (B2) so a frozen cross-asset
   panel trips `data_staleness.stale` / `heartbeat.all_ok` and a `target_as_of` divergence
   raises a `config_warning` — the data already knew it was stale; the gauges should too.
   (This is a *detection* gap; the *cause* above is now fixed, but the monitor should still
   catch any future freeze.)
3. **[done] Widget surfaces staleness + v0.4** — `api.py` `6aa270f` / `ca29632`; uvicorn
   restarted, live.
4. **[watch]** After tonight's tick (or the fee-fix forward re-run), re-read snapshots and
   append the real Model-CAGR / forward-return deltas here. Expect: Model CAGR down a touch
   (5.5 vs 3.0 bps cost), forward track finally showing *real* daily P&L instead of −4.5bps
   flat — direction unknown, and that's the point (it'll be the first honest reading).

## Evidence index
- Forward bleed / frozen model: `freyr/paper/snapshots/{v0.1.1,v0.2,v0.3,v0.4}/2026-06-13.json`
  (`paper_track`, `dd_gauge.day_return`, `model_track`), engine at `freyr/paper/engine.py:331`.
- Stale feeds: `freyr/data/cache/*_1d.parquet` last-bar dates; snapshot `vol_staleness` /
  `target_as_of` / `data_through`.
- League / Mine: `freyr/paper/dispatcher_league.json`. Testnet: `…/testnet_live.json`.
- Widget changes: btc-wheel-bot `api.py` commits `6aa270f` (v0.4), `ca29632` (staleness).
