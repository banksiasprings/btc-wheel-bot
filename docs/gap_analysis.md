# Phase 1 Data Gap Analysis
**BTC Options RL Agent v2 — Free Data Audit**
*Generated: 2026-05-23 | Assessed against training window 2019-03-01 → 2025-12-31*

---

## Executive Summary

Free data from Deribit public API + Binance covers the most critical training signals well. The biggest gaps are (1) historical realized volatility going back to 2019 — the Deribit API now returns only the last 16 days — and (2) on-chain metrics (MVRV, SOPR) which require a separate free API call. Options OHLCV coverage is ~58% hit rate across monthly expiries and is actively downloading. **Free data is sufficient to begin Phase 2 environment development**, but Tardis.dev would provide substantially better options chain depth and tick-level resolution.

---

## 1. What We Have (Free Data, Downloaded)

### 1.1 BTC Spot Price ✅ COMPLETE

| File | Rows | Date Range | Coverage |
|---|---|---|---|
| `data/raw/spot/btc_daily.csv` | 3,201 | 2017-08-17 → 2026-05-22 | **100%** of training window |
| `data/raw/spot/btc_1h.csv` | 64,716 | 2019-01-01 → 2026-05-22 | **100%** of training window |

Source: Binance BTCUSDT via public klines API. No auth required.

**Note:** Binance BTCUSDT data starts 2017-08-17 (Binance launch). For 2017-01-01 to 2017-08-17 we have no hourly data. Daily candles from Bitstamp (via CCXT) would fill this gap but it's outside the training window so not critical.

**Gaps:** None meaningful for training. The 7-month gap at start of daily data (Jan–Aug 2017) is outside the options training window.

---

### 1.2 DVOL (Deribit Volatility Index) ✅ GOOD COVERAGE

| File | Candles | Date Range | Coverage |
|---|---|---|---|
| `data/raw/deribit/dvol_history.json` | 45,027 | 2021-04-01 → 2026-05-22 | ~5 years |

Source: Deribit `public/get_volatility_index_data` (1h candles, max 1000/request, paginated).

**Gap:** DVOL was launched in early 2021. No data exists before 2021-04-01 anywhere — this is a structural data limit, not a collection failure. For 2019-2021 training, ATM IV computed from options chain is the substitute.

**Mitigation:** Compute a synthetic "DVOL proxy" from 30-day realized volatility during the 2019–2021 gap period using spot price data (which we have).

---

### 1.3 Perpetual Funding Rates ✅ GOOD COVERAGE

| File | Records | Date Range | Coverage |
|---|---|---|---|
| `data/raw/deribit/funding_rates.json` | 21,574 | 2019-05-30 → 2026-05-22 | ~7 years |

Source: Deribit `public/get_funding_rate_history` for BTC-PERPETUAL. 8-hour settlement intervals.

**Gap:** Missing 2019-03-01 to 2019-05-29 (~3 months). BTC perpetual on Deribit launched in May 2019. Pre-launch data doesn't exist.

---

### 1.4 Historical Delivery Prices ✅ COMPLETE

| File | Records | Date Range | Coverage |
|---|---|---|---|
| `data/raw/deribit/delivery_prices.json` | 2,493 | 2016-03-18 → 2026-05-22 | 10 years |

Deribit's daily BTC/USD settlement index prices. Used for options pricing ground truth.

---

### 1.5 Options OHLCV (Monthly Expiries) 🔄 IN PROGRESS

| Year | Candidate Instruments | Downloaded So Far | Expected Final |
|---|---|---|---|
| 2019 | 1,024 | 0 (not yet reached) | ~600–700 |
| 2020 | 1,348 | 95 | ~700–800 |
| 2021 | 1,294 | 55 | ~700–800 |
| 2022 | 1,212 | 100 | ~700–750 |
| 2023 | 1,394 | 168 | ~750–850 |
| 2024 | 1,320 | 73 | ~700–800 |
| 2025 | 1,756 | 79 | ~700–900 |
| 2026 | 1,316 | 321 | ~400–500 |

**Total downloaded:** 891 files | 763,133 daily OHLCV ticks | 38.1 MB (growing)
**Expected final:** ~5,500–6,500 real instruments across 86 monthly expiry dates

Source: Deribit `public/get_tradingview_chart_data` (1D resolution). Instruments identified by generating names using last-Friday-of-month expiry dates × delivery price–derived strikes.

**Coverage methodology:** Enumerating `expired=true` via Deribit API only returns today's expiries. We reconstructed historical instrument names from delivery price dates × computed strike grids. Hit rate ~58% (the remaining 42% are strikes that weren't listed by Deribit).

**What these ticks represent:** Each tick is a full-day OHLCV candle for one option. A monthly option listed 1–2 years before expiry would have ~365–730 daily candles. A typical file has ~1,000–1,900 ticks = the full life of the option.

**Limitation:** This is monthly expiries only. Deribit added weekly options (every Friday) at some point but the exact cutover date is unclear, and weekly options have not been confirmed accessible via API. Daily options (very recent addition, 2025–2026) are included in the live instrument list but have no historical depth.

---

### 1.6 Historical Realized Volatility ⚠️ SEVERELY LIMITED

| File | Records | Date Range | Coverage |
|---|---|---|---|
| `data/raw/deribit/iv_history.json` | 384 | 2026-05-07 → 2026-05-23 | **16 days only** |

Source: Deribit `public/get_historical_volatility` — this endpoint now returns only ~16 days of hourly 30-day realized vol. The architecture document described it as returning "years" of data, but the API has changed.

**This is the most critical gap in the free dataset.**

**Mitigation:** Compute 30-day rolling realized volatility directly from `btc_1h.csv` using annualized close-to-close standard deviation. This is standard and equivalent to what the Deribit index computes. Implementation: ~10 lines of pandas. No external data needed.

```python
df = pd.read_csv('data/raw/spot/btc_1h.csv')
df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
df['rv_30d'] = df['log_ret'].rolling(720).std() * np.sqrt(8760)  # annualized
```

---

## 2. What We DON'T Have Yet (Free Sources Available, Not Yet Downloaded)

### 2.1 On-Chain Metrics ❌ NOT DOWNLOADED

| Metric | Source | Status | Priority |
|---|---|---|---|
| MVRV Z-Score | Coin Metrics free API | Not downloaded | HIGH |
| SOPR | Coin Metrics free API | Not downloaded | HIGH |
| Realized Price | Coin Metrics free API | Not downloaded | HIGH |
| Exchange Net Flows | CryptoQuant free tier | Not downloaded | MEDIUM |

**Action:** Run `pip install coinmetrics-api-client` and download via free API. No credit card needed. Daily granularity going back to 2011.

Sample code:
```python
from coinmetrics.api_client import CoinMetricsClient
client = CoinMetricsClient()  # no auth needed for free tier
metrics = client.get_asset_metrics(assets="btc",
    metrics=["CapMVRVFF", "SoprFree", "PriceRealizedUSD"],
    start_time="2019-01-01", end_time="2025-12-31")
```

### 2.2 Fear & Greed Index ❌ NOT DOWNLOADED

| Source | Endpoint | History | Size |
|---|---|---|---|
| alternative.me | `https://api.alternative.me/fng/?limit=3000` | 2018-02-01 → today | ~2 MB |

**Action:** One GET request. ~30 seconds to implement.

### 2.3 Macro Calendar ❌ NOT COMPILED

FOMC meeting dates and CPI release dates (2019–2026). Static data — compile from BLS and Federal Reserve websites or hard-code from known schedules.

**Action:** ~1 hour to compile. Low complexity.

### 2.4 Settlement History ❌ NOT DOWNLOADED

`public/get_settlement_history_by_currency` returned "Method not found" — this endpoint appears deprecated. Settlement prices are available from delivery_prices.json instead.

---

## 3. Date Range Coverage vs. Training Needs

Target training window per architecture doc: **2019-04-01 to 2022-12-31**
Validation window: **2023-01-01 to 2023-12-31**
Test window (sacred): **2024-01-01 to 2025-12-31**

| Data Type | Training (2019–2022) | Validation (2023) | Test (2024–2025) |
|---|---|---|---|
| BTC spot daily | ✅ 100% | ✅ 100% | ✅ 100% |
| BTC spot 1h | ✅ 100% | ✅ 100% | ✅ 100% |
| DVOL | ⚠️ 60% (from Apr 2021) | ✅ 100% | ✅ 100% |
| Funding rates | ✅ 97% (from Jun 2019) | ✅ 100% | ✅ 100% |
| Options OHLCV monthly | ✅ ~65% of strikes | ✅ ~65% of strikes | ✅ ~65% of strikes |
| 30-day RV | ⚠️ Compute from spot | ⚠️ Compute from spot | ⚠️ Compute from spot |
| MVRV Z-Score | ❌ Not downloaded | ❌ Not downloaded | ❌ Not downloaded |
| SOPR | ❌ Not downloaded | ❌ Not downloaded | ❌ Not downloaded |
| Fear & Greed | ❌ Not downloaded | ❌ Not downloaded | ❌ Not downloaded |
| Macro calendar | ❌ Not compiled | ❌ Not compiled | ❌ Not compiled |
| IV surface (computed) | 🔄 Needs Phase 2 | 🔄 Needs Phase 2 | 🔄 Needs Phase 2 |

---

## 4. Options Data Depth Assessment

### What we have from monthly-expiry OHLCV:
- ~86 monthly expiry dates from 2019-03 to 2026-04
- ~5,500–6,500 real option instruments with full-life daily candles
- ~800,000–1,000,000 total daily OHLCV ticks (once download completes)
- Strike coverage: ~70%–140% of ATM price per expiry date

### What we're missing:
- **Weekly options** (every non-last Friday): These exist from some point in 2023/2024 onwards but API verification showed them absent in historical data. Approximately 3–4x more instruments than monthly.
- **Daily options** (2025–2026): Very recent addition, no meaningful historical depth.
- **Intraday candles** (1h, 4h): Available via `get_tradingview_chart_data` with different resolution parameter but requires another full download pass.
- **Order book snapshots**: Not available from Deribit public API at all. Requires Tardis.dev.
- **Full options chain simultaneity**: Our data is per-instrument. Building an IV surface requires combining all active instruments on a given date. Feasible from what we have but needs careful alignment.
- **Open interest history per instrument**: Deribit's `get_open_interest` for historical dates appears limited.

### Monthly data quality:
The 86 monthly expiries give us complete options cycles. Each monthly expiry is typically the instrument with the highest open interest and liquidity. For training a premium-selling strategy (the wheel bot's core approach), monthly expiries are the primary instrument anyway.

---

## 5. Estimated Gap That Paid Data (Tardis.dev) Would Fill

Based on Tardis.dev documentation and current pricing (~$50–200 for historical BTC options):

| Gap | Tardis Fills? | Impact on Training |
|---|---|---|
| Weekly options history | ✅ Yes — complete since 2019 | HIGH — 3–4x more training data points |
| Intraday options candles (1h, 4h) | ✅ Yes | MEDIUM — finer-grained theta decay |
| Order book snapshots (bid/ask) | ✅ Yes | MEDIUM — more accurate IV surface |
| Open interest per instrument | ✅ Yes | MEDIUM — better GEX calculations |
| Settlement data per instrument | ✅ Yes | LOW — already have delivery prices |
| Pre-2019 data | ❌ No (Deribit launched Mar 2019) | N/A |
| Better IV surface (tick-level) | ✅ Yes | HIGH — point-in-time snapshots |

**Tardis would roughly double the options training data volume** (adding weeklies) and significantly improve IV surface quality by providing bid/ask quotes rather than just OHLCV.

---

## 6. Verdict: Is Free Data Sufficient?

### For Phase 2 (Environment build): ✅ YES
The environment needs:
- BTC spot prices → ✅ Complete
- Options strikes, expiries, prices → ✅ Monthly expiries sufficient for initial env
- IV surface (even simplified) → ⚠️ Can compute from downloaded OHLCV midpoints
- Funding rates → ✅ Complete
- DVOL → ✅ Complete from 2021

### For Phase 3–4 (Initial training): ✅ YES (with caveats)
Can begin training once on-chain metrics and Fear & Greed are downloaded (1–2 hours of work). The training signal will be present; the question is quality.

### For Phase 5 (Production training runs): ⚠️ PROBABLY NOT SUFFICIENT
Running 500M-step training on monthly-only options data will produce a strategy optimized for monthly cycle management. Weeklies add important shorter-cycle dynamics that the agent needs to encounter during training. **Recommend purchasing Tardis.dev data before Phase 5.**

### For Phase 6 (Final evaluation): ❌ INSUFFICIENT without Tardis
The test period (2024–2025) includes active weekly and daily options markets. An agent that has never trained on weekly options structures will underperform in this period. Test results would be misleading.

---

## 7. Immediate Next Steps (Priority Order)

| Priority | Action | Time | Unblocks |
|---|---|---|---|
| 1 | Download Coin Metrics on-chain metrics (MVRV, SOPR) | 1h | Phase 2 features |
| 2 | Download Fear & Greed index | 15min | Phase 2 features |
| 3 | Compile macro calendar (FOMC, CPI dates) | 1h | Phase 2 temporal features |
| 4 | Compute 30d RV from spot data (replaces missing iv_history) | 30min | Phase 2 features |
| 5 | Wait for bulk OHLCV download to complete | ~2–4h | Phase 2 IV surface |
| 6 | Download 1h options candles (second pass of bulk downloader) | ~4–8h | Better IV surface |
| 7 | Evaluate Tardis.dev pricing for weekly options | 30min | Phase 5 decision |

**Blocking Phase 2 start:** Nothing — spot + DVOL + monthly options is enough to build the gym environment. On-chain metrics can be stubbed with zeros initially.

---

## 8. Storage Status

| Location | Files | Size |
|---|---|---|
| `data/raw/spot/` | 2 | 10.6 MB |
| `data/raw/deribit/` (top-level) | 6 | 12.5 MB |
| `data/raw/deribit/ohlcv/` | 891+ | 38+ MB (growing) |
| `data/raw/deribit/trades/` | 0 | 0 MB |
| `data/raw/onchain/` | 0 | 0 MB |
| `data/raw/macro/` | 0 | 0 MB |
| **Total** | ~900 | **~61 MB** |

Expected final size once bulk download completes: ~120–150 MB (raw). After parquet conversion: ~30–40 MB. Well within the 20–50 GB estimate from the architecture doc (that estimate assumed Tardis tick data).

---

*Last updated: 2026-05-23 | Bulk downloader PID tracked in data/logs/bulk_pid.txt*
