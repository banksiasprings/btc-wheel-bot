# Widget migration — Freyr becomes primary (2026-06-09)

The mobile dashboard at **https://bot.banksiaspringsfarm.com** (served by `api.py`,
launchd `com.wheelbot.api`, Cloudflare tunnel → `:8765`) was repurposed to lead with
the **Freyr ensemble** instead of the 34-bot BTC farm. The farm is kept, but only a
tight survivor set is surfaced on the home page.

> **URL note:** the brief referenced `t.banksiaspringsfarm.com`. The live Cloudflare
> tunnel (`~/.cloudflared/config.yml`) only routes `bot.banksiaspringsfarm.com` and
> `voice.…` to this server — there is no `t.` ingress. The canonical bookmarked URL is
> **bot.banksiaspringsfarm.com**, which is what was updated. No URL changed.

## What changed (all in `api.py`, no new files, no deletions)

- **New home `/`** — server-rendered "Banksia Springs — Live Bots":
  1. **Freyr ensemble** — three big cards (🛡️ v0.1.1 conservative, ⚖️ v0.2 moderate,
     🚀 v0.3 aggressive). Each shows equity ($ on $10k + %), drawdown, leverage, regime,
     books active, kill-switch (ARMED/TRIPPED), escape tier (T0–T3 + reason), heartbeat,
     a 30-day model-equity sparkline, and taps through to a per-book breakdown.
  2. **BTC farm survivors** — 4 cards (see selection below).
  3. **Combined leaderboard** — Freyr + survivors head-to-head by annualised pace.
- **`/freyr/{variant}`** — new detail page: stat grid + 60-day model chart + per-book
  table (weight, cum P&L, Sharpe, book DD, active/dormant, disarmed flag).
- **`/farm?tab=…`** — the old 34-bot family/tab view moved here from `/` (header renamed
  to "BTC Farm — all 34 bots", with a "← Home" link). `/widget` now also serves home.
- Nav back-links repointed (`/bot/*`, `/leaderboard`, `/btc` → `/farm`).

## Data flow

Both systems live on the same Mac, so `api.py` reads Freyr snapshots **directly off
disk** — no network fetch, no cross-origin:

```
~/Documents/freyr/paper/snapshots/<variant>/index.json   → latest date + kill_status
~/Documents/freyr/paper/snapshots/<variant>/<date>.json  → portfolio / escape / books / model_track
```

The farm side is unchanged: `grid_farm/status.json` + per-bot `equity.csv`.

## Untouched (load-bearing)

- **`/farm/status` and `/farm/equity`** JSON (the Android widget contract, `WHEEL_API_KEY`)
  are byte-for-byte unchanged — verified 34 bots, same keys, post-deploy.
- No bot code or bot processes touched. Retired farm bots still run; they're just hidden
  from the home page (visible under `/farm`).

## Survivor selection (the 4 kept visible)

Criterion: **unleveraged only** (leveraged 2×/3× bots are paper-only "for kicks" per
CONTEXT hard rule — never candidates), then the **best performer of each strategy family**
that earns its slot, ranked by the survival-first metric **return − worst-dip** (the same
metric the Sunday digest uses).

| Bot | Family | Return | Worst dip | Why kept |
|-----|--------|--------|-----------|----------|
| **Aggressive** | Grid | +0.97% | −0.10% | Best unleveraged grid — grid is the lead strategy. |
| **Long-Vol** | Long-Vol | +0.93% | −0.04% | Best crash-hedge; smoothest curve. |
| **Gamma Scalp** | Convex | +0.92% | −0.04% | Long-vol that actually trades; tiny dip. |
| **Funding (smart)** | Funding | +0.03% | −0.00% | The near-zero-risk carry floor. |

**Excluded:** all leveraged bots (Degen, Long-Vol 2×/3×, Funding 2×/3×, Premium 2×); and
the **Trend / Premium / Stack** families (flat or losing — Buy&Hold −13.6% is the
benchmark to beat, not a survivor).

Selection lives in `api.py` as `SURVIVORS`. To change it, edit that list — no other change needed.

### Revision (2026-06-09, later same day) — favourites + bracket tags

Steven's framework is **"don't die ≠ don't drawdown"**: high leverage and deep dips are
fine as long as the escape strategy works, so the survival-first filter (return − worst-dip)
was too conservative. He asked three leveraged favourites back onto the home page, and the
set is now **7 bots grouped by specialist bracket** (a preview of the bracket-header
restructure coming next):

| Bot | Bracket | Note |
|-----|---------|------|
| Long-Vol 3× ⚠️ (`longvol-3x`) | 🔥 Crash specialist | un-gated 3× crash hedge |
| Long-Vol 3× DVOL≤65 ⚠️ (`longvol-3x-dvol`) | 🔥 Crash specialist | "Long-Vol 65" — fires only when implied vol is cheap |
| Long-Vol (`longvol`) | 🔥 Crash specialist | smoothest, unleveraged |
| Degen ⚠️ (`degen`) | 🐂 Bull / Aggressive | 3× grid |
| Aggressive (`aggressive`) | 🐂 Bull / Aggressive | best unleveraged grid |
| Gamma Scalp (`gamma-scalp`) | 🌪 Chop / Convex | long-vol that trades |
| Funding (smart) (`funding-smart`) | 😴 Calm / Carry | near-zero-risk carry floor |

Bracket map = `BRACKETS` dict in `api.py`. Each card shows a colour-matched bracket chip;
⚠️ = leveraged (paper-only). The header is now "BTC farm favourites". All bots remain
browseable at `/farm`.

### Revision (2026-06-09, later same day) — tabs + per-card annualised pace + switching cost

The home `/` is no longer one long scroll. The three sections are now **tabs** (sticky
bar under the title, ~45px tap targets, dark-theme active state in `#2563eb`):

| Tab | Default | Content |
|-----|---------|---------|
| **⚡ Freyr** | yes | the 3 Freyr variant cards |
| **🌾 Farm** | — | the 7 farm survivors with bracket tags |
| **🏆 Board** | — | combined Freyr + survivor leaderboard |

Switching is **client-side** (all three panels rendered; JS toggles visibility — instant,
no round-trip). The active tab is held in the URL `#hash`; the 60s soft refresh is now a
JS `location.reload()` (which preserves the hash), so the auto-refresh lands you back on the
tab you were reading. The old `<meta http-equiv=refresh>` was removed in favour of this.

**Annualised pace pinned to every card** (`_ann_strip` + `_ann_windows`): four mini-cells —
the **realised** trailing return scaled to a year (NOT a forecast — `realised_return ×
365/elapsed`, i.e. 1w ≈ ×52, 1mo ≈ ×12, 1y ≈ ×1), for the **1w / 1mo / 1y** windows, plus
**realised YTD**. It's the same formula the rest of the widget already uses for day/week/month.
1y reads **TBD** until a track spans a full year (every bot, for now — paper is weeks old);
once it does, it's simply the actual 365-day return. Survivor strips derive from the paper
equity curve; Freyr strips from the 180-day `model_track` (paper is only days old).
`_annualised` (day/week/month) is still used by `/farm`.

**Switching cost on every card** (`_switch_cost` + `_switch_chip`): one number —
*round-trip cost to fully exit + re-enter the position, as % of NAV* — colour-coded
green `<0.1%` / amber `0.1–0.5%` / red `>0.5%`. Methodology reuses Freyr's crypto cost
model (`~/Documents/freyr/switching.py` + `rules/registry.yaml: crypto-cost-bps=3.0/side`):
round-trip = 2 × 3.0 = **6.0 bps** of gross notional, scaled by the bot's gross leverage —
so 1× ≈ 0.06% (green, cheap-exit specialist; can take narrow edges), 3× ≈ 0.18% (amber;
needs a fatter edge). Freyr cards use the snapshot's live `leverage`; survivors use the
`leverage` field on `grid_farm/status.json`. This is the input for the cheap-exit-specialist
analysis (green-switch bots take narrow edges; red-switch bots need fat edges).

## Caveats

- Combined-leaderboard pace mixes bases (Freyr = model-track CAGR since paper history is
  only days old; survivors = 30-day annualised paper pace). Labelled on the page.
- Freyr `$ on $10k` is a notional display so the two systems compare in %; Freyr's real
  unit is fraction-of-capital (1.0 = start), not a $10k account.
- Forecast strips likewise mix bases (survivor = paper curve, Freyr = model track); each
  strip is labelled with its basis. Switching cost assumes the 3.0 bps/side crypto cost is
  accurate — it's a modelled (not yet realised-fill-reconciled) figure, same as Freyr's.
