"""run_tests.py — batch backtest sweep, writes results to /tmp/backtest_results.json"""
import sys, copy, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

from loguru import logger
logger.remove()

from config import cfg
from backtester import Backtester

print("Fetching market data once...", flush=True)
bt_base = Backtester()
df = bt_base._build_dataset()
print(f"Dataset: {len(df)} days [{df['date'].iloc[0].date()} .. {df['date'].iloc[-1].date()}]", flush=True)

def run(label, strategy=None, sizing=None, backtest=None):
    custom = copy.deepcopy(cfg)
    if strategy:
        for k, v in strategy.items(): setattr(custom.strategy, k, v)
    if sizing:
        for k, v in sizing.items():   setattr(custom.sizing, k, v)
    if backtest:
        for k, v in backtest.items(): setattr(custom.backtest, k, v)
    bt = Backtester(config=custom)
    r = bt._simulate(df)
    out = dict(
        label=label, group="", param_val=None,
        sharpe=round(r.sharpe_ratio, 3),
        total_return=round(r.total_return_pct, 2),
        ann_return=round(r.annualized_return_pct, 2),
        max_dd=round(r.max_drawdown_pct, 2),
        win_rate=round(r.win_rate_pct, 1),
        trades=r.num_cycles,
        end_equity=round(r.ending_equity, 0),
        avg_yield=round(r.avg_premium_yield_pct, 3),
    )
    print(f"  {label:42s} ret={out['total_return']:+6.1f}%  sharpe={out['sharpe']:+.2f}  win={out['win_rate']:.0f}%  trades={out['trades']}", flush=True)
    return out

BASE_S  = dict(iv_rank_threshold=0.50, target_delta_min=0.15, target_delta_max=0.25,
               initial_cycle="put", expiry_preference=["weekly", "monthly"])
BASE_SZ = dict(max_equity_per_leg=0.80, min_free_equity_fraction=0.0)
BASE_BT = dict(starting_equity=10000.0, lookback_months=18)

results = []

# ── IV Rank Threshold ──────────────────────────────────────────────────────────
print("\n── IV Rank Threshold ──")
for ivr in [0.20, 0.30, 0.40, 0.50, 0.60, 0.70]:
    r = run(f"IV rank >= {int(ivr*100)}%",
            strategy={**BASE_S, "iv_rank_threshold": ivr},
            sizing=BASE_SZ, backtest=BASE_BT)
    r["group"] = "iv_rank_threshold"; r["param_val"] = ivr
    results.append(r)

# ── Delta Range ────────────────────────────────────────────────────────────────
print("\n── Delta Range ──")
for dmin, dmax, lbl in [
    (0.10, 0.15, "Delta 10-15% (very deep OTM)"),
    (0.15, 0.20, "Delta 15-20% (conservative OTM)"),
    (0.15, 0.25, "Delta 15-25% (balanced)"),
    (0.20, 0.30, "Delta 20-30% (standard)"),
    (0.25, 0.35, "Delta 25-35% (moderate)"),
    (0.30, 0.40, "Delta 30-40% (aggressive)"),
]:
    r = run(lbl, strategy={**BASE_S, "target_delta_min": dmin, "target_delta_max": dmax},
            sizing=BASE_SZ, backtest=BASE_BT)
    r["group"] = "delta"; r["param_val"] = round((dmin + dmax) / 2, 3)
    results.append(r)

# ── Weekly vs Monthly ──────────────────────────────────────────────────────────
print("\n── Weekly vs Monthly ──")
for pref, lbl, val in [
    (["weekly", "monthly"], "Weekly options (7 DTE)", 7),
    (["monthly", "weekly"], "Monthly options (28 DTE)", 28),
]:
    r = run(lbl, strategy={**BASE_S, "expiry_preference": pref},
            sizing=BASE_SZ, backtest=BASE_BT)
    r["group"] = "dte"; r["param_val"] = val
    results.append(r)

# ── Max Equity per Leg ────────────────────────────────────────────────────────
print("\n── Max Equity per Leg ──")
for frac in [0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00]:
    r = run(f"Max equity/leg = {int(frac*100)}%",
            strategy=BASE_S,
            sizing={**BASE_SZ, "max_equity_per_leg": frac},
            backtest=BASE_BT)
    r["group"] = "equity_per_leg"; r["param_val"] = frac
    results.append(r)

# ── Free Capital Buffer ────────────────────────────────────────────────────────
print("\n── Free Capital Buffer ──")
for buf in [0.0, 0.10, 0.20, 0.30]:
    r = run(f"Free buffer = {int(buf*100)}%",
            strategy=BASE_S,
            sizing={**BASE_SZ, "min_free_equity_fraction": buf},
            backtest=BASE_BT)
    r["group"] = "free_margin"; r["param_val"] = buf
    results.append(r)

# ── Account Size ───────────────────────────────────────────────────────────────
print("\n── Account Size ──")
for eq in [10000, 25000, 50000, 100000]:
    sz = {"max_equity_per_leg": 0.25, "min_free_equity_fraction": 0.15} if eq >= 25000 else BASE_SZ
    r = run(f"Starting equity ${eq:,}",
            strategy=BASE_S, sizing=sz,
            backtest={**BASE_BT, "starting_equity": float(eq)})
    r["group"] = "starting_equity"; r["param_val"] = eq
    results.append(r)

# ── Best Combinations ──────────────────────────────────────────────────────────
print("\n── Best Combinations ──")
combos = [
    ("Conservative $10k",  dict(strategy={**BASE_S, "iv_rank_threshold": 0.60, "target_delta_min": 0.15, "target_delta_max": 0.20, "expiry_preference": ["monthly", "weekly"]}, sizing=BASE_SZ, backtest=BASE_BT)),
    ("Balanced $10k",      dict(strategy={**BASE_S, "iv_rank_threshold": 0.50, "target_delta_min": 0.15, "target_delta_max": 0.25, "expiry_preference": ["weekly", "monthly"]}, sizing=BASE_SZ, backtest=BASE_BT)),
    ("Aggressive $10k",    dict(strategy={**BASE_S, "iv_rank_threshold": 0.30, "target_delta_min": 0.20, "target_delta_max": 0.30, "expiry_preference": ["weekly", "monthly"]}, sizing=BASE_SZ, backtest=BASE_BT)),
    ("Monthly+LowIV $10k", dict(strategy={**BASE_S, "iv_rank_threshold": 0.40, "target_delta_min": 0.15, "target_delta_max": 0.25, "expiry_preference": ["monthly", "weekly"]}, sizing=BASE_SZ, backtest=BASE_BT)),
    ("Conservative $50k",  dict(strategy={**BASE_S, "iv_rank_threshold": 0.60, "target_delta_min": 0.15, "target_delta_max": 0.20, "expiry_preference": ["monthly", "weekly"]}, sizing={"max_equity_per_leg": 0.25, "min_free_equity_fraction": 0.20}, backtest={**BASE_BT, "starting_equity": 50000.0})),
    ("Balanced $50k",      dict(strategy={**BASE_S, "iv_rank_threshold": 0.50, "target_delta_min": 0.15, "target_delta_max": 0.25, "expiry_preference": ["weekly", "monthly"]}, sizing={"max_equity_per_leg": 0.25, "min_free_equity_fraction": 0.20}, backtest={**BASE_BT, "starting_equity": 50000.0})),
    ("Aggressive $50k",    dict(strategy={**BASE_S, "iv_rank_threshold": 0.30, "target_delta_min": 0.20, "target_delta_max": 0.30, "expiry_preference": ["weekly", "monthly"]}, sizing={"max_equity_per_leg": 0.25, "min_free_equity_fraction": 0.20}, backtest={**BASE_BT, "starting_equity": 50000.0})),
]
for lbl, kw in combos:
    r = run(lbl, **kw)
    r["group"] = "combo"; r["param_val"] = lbl
    results.append(r)

with open("/tmp/backtest_results.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\nDone. {len(results)} tests written to /tmp/backtest_results.json")
