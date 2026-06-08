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

Selection lives in `api.py` as `SURVIVORS = ["aggressive","longvol","gamma-scalp","funding-smart"]`.
To change it, edit that list — no other change needed.

## Caveats

- Combined-leaderboard pace mixes bases (Freyr = model-track CAGR since paper history is
  only days old; survivors = 30-day annualised paper pace). Labelled on the page.
- Freyr `$ on $10k` is a notional display so the two systems compare in %; Freyr's real
  unit is fraction-of-capital (1.0 = start), not a $10k account.
