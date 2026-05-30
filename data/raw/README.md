# `data/raw/` — raw market data caches

Append-only CSV/JSON caches that the strategy backtests read. Idempotent
fetchers in `scripts/` and `data_pipeline/` populate these; everything
under here is recreatable from public APIs and is gitignored by default —
each fetcher is the source of truth for the schema it writes.

This README documents the files used by the **Basis Arb Gate 3** backtest
(spec: `~/Documents/bsf-research-briefs/specs/03-basis-arb-spec.md`).
Other historical files (options OHLCV, on-chain, macro, dvol) are
documented by the scripts that own them in `data_pipeline/`.

---

## Files used by Basis Arb Gate 3

### `deribit/btc_perp_1h.csv`

Hourly OHLCV of **`BTC-PERPETUAL`** on Deribit, plus the synchronised
**Deribit BTC composite index** price and pre-computed basis.

Producer: `scripts/fetch_deribit_perp_history.py`
Endpoints:
  - `public/get_tradingview_chart_data` (perp OHLCV — `close` = perp mark close)
  - `public/get_funding_rate_history`   (hourly `index_price` snapshots)

**Schema**

| Column           | Type   | Notes |
|------------------|--------|-------|
| `timestamp_utc`  | str    | RFC 3339 UTC, start-of-hour, e.g. `2020-03-12T08:00:00Z`. The bar covers `[ts, ts+1h)`. |
| `timestamp_ms`   | int    | UNIX ms (start-of-hour). Authoritative join key. |
| `open` / `high` / `low` / `close` | float | Perp mark price, USD. `close` = mark at `ts + 1h`. |
| `volume`         | float  | Base BTC volume traded that hour on the perp |
| `volume_usd`     | float  | USD notional traded (Deribit `cost` field) |
| `mark_price`     | float  | Alias of `close` (kept explicit so downstream readers don't have to guess) |
| `index_price`    | float  | Deribit BTC composite index at the bar's **close** (`ts + 1h`), USD. Blank if no funding-snapshot inside the 6-hour fill window. |
| `index_source`   | str    | `funding_snapshot` (exact match), `forward_fill_<N>h` (most-recent snapshot was N hours stale), or blank. |
| `basis_abs`      | float  | `mark_price - index_price`, USD. Blank if index missing. |
| `basis_pct`      | float  | `basis_abs / index_price`, fraction. Multiply by 10 000 for bps. Blank if index missing. |

**Timestamp alignment** — the perp's `close` is the mark at `ts + 1h`,
and the funding-snapshot at funding-`timestamp` T reflects the index at
T. The fetcher pairs each perp bar with the funding-snapshot at `ts + 1h`
so `mark_price` and `index_price` represent the same wall-clock moment.
Median `|mark − index|` across the full history is ~2 bps, confirming
the alignment.

**Known limits and gaps**

  - **`index_price` is blank for ~705 of 62 784 bars** (≈ 1.1 %), all
    concentrated in the first weeks of 2019. The Deribit
    `get_funding_rate_history` endpoint silently caps responses at ~744
    records per call; the fetcher uses 20-day chunks (~480 records) to
    stay under the cap, which gives reliable hourly coverage from
    2019-04-30 onward.
  - **Pre-2019-04-01 perp data is not available** from the chart-data
    endpoint (Deribit returns `no_data`). The fetcher defaults to
    `--start 2019-04-01`.
  - **Forward-fill is rare in practice** (<5 bars in the current
    dataset) because the funding endpoint is hourly after 2019-08.
    Consumers wanting only direct samples can filter
    `index_source == "funding_snapshot"`.

### `binance/btc_spot_1h.csv`

Hourly OHLCV of **`BTCUSDT`** on Binance — used purely as a
cross-reference spot price to quantify cross-venue index noise.
This is the answer to Basis Arb spec open Q #2 (spot-reference choice):
how much does Deribit's own composite index drift from a deep
external spot reference?

Producer: `scripts/fetch_binance_spot_history.py`
Endpoint: `api.binance.com/api/v3/klines`

**Schema**

| Column           | Type   | Notes |
|------------------|--------|-------|
| `timestamp_utc`  | str    | RFC 3339 UTC, start-of-hour |
| `timestamp_ms`   | int    | UNIX ms (kline `open_time`) |
| `open` / `high` / `low` / `close` | float | Spot price, USDT |
| `volume`         | float  | Base BTC volume |
| `volume_usd`     | float  | Quote USDT notional (Binance `quote_volume`) |
| `num_trades`     | int    | Trades that hour |

**Known gaps**

  - Binance very rarely emits zero-volume hours during exchange
    downtime; the kline endpoint silently skips them. The Gate 3
    inner-join with the perp file is the safety net — any unmatched
    timestamps are dropped from the processed dataset.

**Related — not used by Basis Arb**: `spot/btc_1h.csv` is a pre-existing
raw Binance file with a different schema (raw kline tuples, no
timestamp_utc column). It is consumed by other backtests; the
basis-arb pipeline writes a fresh, semantically-tagged file in
`binance/` rather than coupling to it.

### `deribit/funding_rates.json` *(pre-existing)*

8-hour funding rate history for `BTC-PERPETUAL` from 2019-05-29. Used
by the existing FundingBot backtests. Same data source the perp
fetcher hits for `index_price`; documented here for completeness.

---

## Fetch cadence

The Basis Arb files are designed for a **monthly cron refresh**:

```cron
# crontab: monthly on the 1st at 04:00 local, refresh basis data
0 4 1 * * cd /Users/openclaw/Documents/btc-wheel-bot && \
    /usr/local/bin/python3.11 scripts/fetch_deribit_perp_history.py && \
    /usr/local/bin/python3.11 scripts/fetch_binance_spot_history.py && \
    /usr/local/bin/python3.11 scripts/build_basis_dataset.py
```

Both fetchers are **idempotent and resumable**: they read the last
`timestamp_ms` in the existing CSV and continue from there. A monthly
re-run downloads only the previous ~720 hourly bars and rebuilds the
processed dataset in seconds.

Manual one-shot:

```bash
python3.11 scripts/fetch_deribit_perp_history.py    # ~minutes on cold start
python3.11 scripts/fetch_binance_spot_history.py    # ~minutes on cold start
python3.11 scripts/build_basis_dataset.py           # ~seconds
```

---

## Why hourly, not daily

The Basis Arb signal is z-scored on a 168-hour (7-day) deque of basis
observations. Daily granularity would smooth out the multi-hour basis
spikes the strategy is designed to exploit (LUNA, FTX, contango
blow-outs). Hourly is the smallest granularity the strategy needs;
finer (1-minute) is not currently fetched because the strategy steps
hourly anyway.
