# `data/raw/hyperliquid/` — daily Hyperliquid leaderboard + HLP snapshots

Append-only daily JSONL snapshots of Hyperliquid's public leaderboard and
the HLP protocol-vault tape. **Pure data accumulation — no strategy logic
touches this directory.** The goal is to have the time series in place
when Phase 4 / Family 6 (ML regime + strategy router) work begins; see
the [Hyperliquid spike](../../../bsf-research-briefs/hyperliquid-spike.md)
§4 (forward-test design) and §7 (recommended next actions).

This directory is gitignored except for this README — the JSONL files
are regenerable from the public API, and 30 days of snapshots is several
MB which we don't need in git.

---

## What's captured per day

**Producer:** `scripts/fetch_hyperliquid_leaderboard.py` (single Python
script, no daemon — fired once per day by launchd, see below).

**Output file:** one JSONL per UTC snapshot date,
`leaderboard_YYYY-MM-DD.jsonl`. The file is written atomically (`.tmp`
then rename) so a crash mid-fetch never leaves a half-written snapshot.

**Each file contains N+1 rows:**
- **N rows** with `"kind": "trader"` — one per top-N active trader
  (default N=100).
- **1 row** with `"kind": "hlp_vault"` — the HLP protocol vault.

### Active-trader filter (mandatory)

The raw leaderboard ranks **everything with a Hyperliquid wallet** — and
17 of the raw top-20 by 30d PnL are HYPE token-holders with $0 monthly
trading volume, not traders ([spike §1.4](../../../bsf-research-briefs/hyperliquid-spike.md#14-what-top-trader-means-on-hyperliquid)).
The snapshot job applies `month_vlm > $1,000,000` (≈ cuts the 37 918-row
leaderboard down to ~7 590 active traders), sorts by 30d PnL desc, and
takes the top N from there. Any later analysis joining on `address`
should expect to find only addresses that cleared this floor on the day
they were snapshotted.

### Trader-row schema (one JSONL line)

```jsonc
{
  "kind": "trader",
  "snapshot_date": "2026-05-31",          // UTC date
  "snapshot_ts_ms": 1780197636199,        // wall-clock at fetch time
  "rank_by_30d_pnl_active": 1,            // 1..N within active universe
  "address": "0xbdfa...",                 // ethAddress from leaderboard
  "display_name": null,                   // usually null
  "lb_account_value": "73938009.59...",   // leaderboard-side total NAV
  "month_vlm": "2116165.54",              // 30d traded volume, USD
  "month_pnl": "30158923.68",             // 30d realised PnL, USD
  "month_roi": "0.6873...",               // 30d ROI (fraction)
  "window_performances": {                // all four windows raw
    "day":     {"pnl": "...", "roi": "...", "vlm": "..."},
    "week":    {...},
    "month":   {...},
    "allTime": {...}
  },
  "clearinghouse": {                      // perp account at snapshot time
    "marginSummary":       {"accountValue":"...", "totalNtlPos":"...", ...},
    "crossMarginSummary":  {...},
    "assetPositions":      [              // open perp positions
      {"type":"oneWay",
       "position": {"coin":"VVV", "szi":"-1540.46",
                    "leverage": {"type":"cross","value":3},
                    "entryPx":"5.2049", "positionValue":"26223.25",
                    "unrealizedPnl":"-18205.31",
                    "liquidationPx":"161.94", "marginUsed":"8741.08",
                    "maxLeverage":3,
                    "cumFunding":{"allTime":"-...", "sinceOpen":"-...",
                                  "sinceChange":"-..."}}}
    ],
    "withdrawable":               "...",
    "crossMaintenanceMarginUsed": "...",
    "time":                       1780197636193     // server-side ms
  }
}
```

**All numeric fields are kept as strings on disk.** Hyperliquid serves
decimals as strings to avoid IEEE-754 precision loss on whole-Bitcoin
quantities; the dataset builder casts to float where needed but the raw
files preserve full precision.

### HLP-row schema

```jsonc
{
  "kind": "hlp_vault",
  "snapshot_date": "2026-05-31",
  "snapshot_ts_ms": 1780197636199,
  "vault_address": "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
  "name": "Hyperliquidity Provider (HLP)",
  "apr_trailing_day_annualised": 0.01163,   // ← daily, not lifetime!
  "max_distributable_usd": 317001917.74,
  "max_withdrawable_usd":  0.0,
  "is_closed":             false,
  "allow_deposits":        true,
  "follower_count":        100,
  "lifetime_pnl_usd":      "136799386.42",  // last point of allTime pnlHistory
  "current_nav_usd":       "356890005.54",
  "lifetime_last_point_ts_ms": 1780197495282,
  "portfolio_windows":     ["day","week","month","allTime",
                            "perpDay","perpWeek","perpMonth","perpAllTime"],
  "portfolio_raw":         [["day", {"accountValueHistory":[[ts,nav],...],
                                     "pnlHistory":[[ts,pnl],...],
                                     "vlm":"..."}], ...]
}
```

The headline `apr` field is **trailing-day-annualised**, not lifetime.
[Spike §1.5](../../../bsf-research-briefs/hyperliquid-spike.md#15-the-hlp-protocol-vault-tape)
computes the lifetime CAGR (~17%) from the `allTime` history; the dataset
builder script does this on roll-up.

### Known gotchas (verified against the live API 2026-05-31)

- **Leaderboard accountValue ≠ clearinghouse accountValue** for the
  majority of top-PnL addresses. The leaderboard reports the master
  wallet's total holdings (including spot, vault deposits, token
  positions); `clearinghouseState` returns only the perp sub-account on
  the master wallet itself. Many big traders run via API/agent wallets
  whose addresses are not on the leaderboard. The snapshot keeps **both**
  values (`lb_account_value` and `clearinghouse.marginSummary.accountValue`)
  so the ratio is preserved in the time series. In the 2026-05-31 smoke
  test, 28 of the top-30-by-30d-PnL had >50% gap — even more extreme
  than the spike's "about a third" stratified sample.

- **Top-N-by-30d-PnL is biased towards low-volume addresses with one big
  win.** This is real and structural — the spike documents it (§6.6
  *short-window survivor selection*) and any downstream signal work
  should weight by cross-window persistence, not raw monthly rank.

- **Decimal strings, not floats.** Don't `json.load(...)["lb_account_value"]`
  and then arithmetic — cast explicitly. Many values exceed safe-integer
  precision when accumulated.

---

## Endpoints and rate limits

| Endpoint                                                          | Method | Weight | Use                              |
|-------------------------------------------------------------------|--------|--------|----------------------------------|
| `https://stats-data.hyperliquid.xyz/Mainnet/leaderboard`          | GET    | (n/a)  | Full leaderboard, ~30 MB         |
| `https://api.hyperliquid.xyz/info`  body `{type:"clearinghouseState"}` | POST   | 2      | Per-trader perp account snapshot |
| `https://api.hyperliquid.xyz/info`  body `{type:"vaultDetails"}`  | POST   | 20     | HLP vault portfolio + metadata   |

Hyperliquid's published IP rate limit is **1 200 weight units / minute**
([rate-limits docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)).
A top-100 daily run consumes ~220 weight units (1 leaderboard + 100×2
clearinghouseState + 1×20 vaultDetails) — well under budget. The
fetcher sleeps 150 ms between per-trader calls, which is conservative;
larger N values are safe to push.

No authentication required for any of these endpoints. **Do not** add
auth headers — there is no Hyperliquid private API in this project, and
adding keys would be a security regression for no benefit.

Official docs: [info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).

---

## Cron cadence — install the launchd job

The plist `scripts/com.bsf.hyperliquid.snapshot.plist` runs the fetcher
once per day. **launchd does not auto-install** — Steven needs to copy
it into `~/Library/LaunchAgents/` and `launchctl load` it before it
fires for the first time.

```bash
# From the repo root:
cp scripts/com.bsf.hyperliquid.snapshot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.bsf.hyperliquid.snapshot.plist

# Optional: fire it once now to verify it runs.
launchctl kickstart -k gui/$(id -u)/com.bsf.hyperliquid.snapshot

# Unload:
launchctl unload ~/Library/LaunchAgents/com.bsf.hyperliquid.snapshot.plist
```

**Schedule:** 10:30 local (AEST, UTC+10, no DST) = **00:30 UTC**.
Hyperliquid funding rolls hourly on the hour; 30 minutes past gives the
leaderboard a moment to settle before we snapshot. launchd's
`StartCalendarInterval` is interpreted in the system's *local*
timezone, so the plist hard-codes Hour=10 / Minute=30. If the Mac's
timezone changes, edit the plist or the UTC alignment drifts.

**Manual one-shot** (for testing or back-filling a missed day):

```bash
python3.11 scripts/fetch_hyperliquid_leaderboard.py             # top 100, today
python3.11 scripts/fetch_hyperliquid_leaderboard.py --top 30    # smoke-test
python3.11 scripts/fetch_hyperliquid_leaderboard.py --force     # overwrite today
python3.11 scripts/fetch_hyperliquid_leaderboard.py --date 2026-05-30  # back-fill
```

The fetcher is idempotent on date — it exits cleanly if today's JSONL
already exists. Missed days **cannot** be retroactively pulled — the
spike's central finding (§4.1) is that Hyperliquid does not expose a
historical leaderboard time series. The whole purpose of this job is to
accumulate the snapshots forward from now.

---

## Roll-up into the processed dataset

`scripts/build_hyperliquid_dataset.py` stitches every daily JSONL in
this directory into a single long-format CSV at
`data/processed/hyperliquid_leaderboard_history.csv`. Re-running is safe
and cheap — it always rebuilds from scratch (one snapshot per day × ~100
traders × ~5 positions each ≈ 200k rows/year; full rebuild in seconds).

The CSV is long-format with one row per (date, address, asset). See the
docstring at the top of `build_hyperliquid_dataset.py` for the full
column list. Three `kind` values:
- `trader_aggregate` — one per (date, address). Window-performance
  columns and account totals.
- `trader_position` — one per (date, address, coin). Per-position
  fields (`position_szi`, `position_side`, `leverage_value`, etc).
- `hlp_vault` — one per date. HLP-only columns (`hlp_lifetime_pnl`,
  `hlp_current_nav`).

Run it manually after each snapshot, or wire a second launchd job. (The
spike doesn't require an automated rebuild — once a fortnight by hand is
fine until there's a consumer.)
