#!/usr/bin/env python3.11
"""Fix the notebook: add cell IDs and correct config section references."""
import json, uuid
from pathlib import Path

nb_path = Path.home() / 'Documents/btc-wheel-bot/wheel_bot_explainer.ipynb'
nb = json.loads(nb_path.read_text())

# 1. Add unique IDs to all cells (required by nbformat 5.1+)
for cell in nb['cells']:
    if 'id' not in cell:
        cell['id'] = uuid.uuid4().hex[:8]

# 2. Fix config section references throughout all code cells
#    approx_otm_offset, premium_fraction_of_spot -> live in 'backtest' not 'strategy'
#    regime_ma_days -> lives in 'sizing' not 'strategy'
replacements = [
    ("strat.get('approx_otm_offset'",          "back.get('approx_otm_offset'"),
    ("strat.get('premium_fraction_of_spot'",    "back.get('premium_fraction_of_spot'"),
    ("strat.get('regime_ma_days'",              "sizing.get('regime_ma_days'"),
    # Guard None * int in f-strings
    ("back.get('approx_otm_offset')*100",       "float(back.get('approx_otm_offset') or 0)*100"),
    ("back.get('premium_fraction_of_spot')*100", "float(back.get('premium_fraction_of_spot') or 0)*100"),
    # target_delta values in config are POSITIVE absolute deltas (0.15, 0.38)
    # code uses abs() on them so it's fine, but fix sign confusion in display
    # Fix the cfg_map in sweep analysis
    ("'target_delta_min':         strat.get('target_delta_min'),",
     "'target_delta_min':         strat.get('target_delta_min'),  # positive abs delta in config"),
    # Fix starting_equity display: it's in backtest, might be USD not BTC
    # (starting_equity: 1255.89 - leave as-is, let notebook display it)
]

for cell in nb['cells']:
    if cell['cell_type'] == 'code':
        src = cell['source']
        for old, new in replacements:
            src = src.replace(old, new)
        cell['source'] = src

# 3. Fix the active config visualisation cell:
#    target_delta_min/max in config are POSITIVE. The code uses abs() which is correct,
#    but the initial variable assignment does:
#    d_min = abs(float(strat.get('target_delta_max', -0.20)))
#    d_max = abs(float(strat.get('target_delta_min', -0.10)))
#    Since config values are already positive, abs() is fine but defaults need fixing
for cell in nb['cells']:
    if cell['cell_type'] == 'code' and 'approx_strike_from_delta' in cell['source']:
        src = cell['source']
        src = src.replace(
            "d_min      = abs(float(strat.get('target_delta_max', -0.20)))  # higher abs = lower strike",
            "d_min      = float(strat.get('target_delta_min', 0.10))   # conservative (far OTM)"
        )
        src = src.replace(
            "d_max      = abs(float(strat.get('target_delta_min', -0.10)))",
            "d_max      = float(strat.get('target_delta_max', 0.20))   # aggressive (closer to ATM)"
        )
        src = src.replace(
            "otm_offset = float(strat.get('approx_otm_offset', 0.10))",
            "otm_offset = float(back.get('approx_otm_offset', 0.10))"
        )
        src = src.replace(
            "prem_frac    = float(strat.get('premium_fraction_of_spot', 0.006))",
            "prem_frac    = float(back.get('premium_fraction_of_spot', 0.006))"
        )
        cell['source'] = src

# 4. Fix the config print cell (Section 4):
for cell in nb['cells']:
    if cell['cell_type'] == 'code' and 'ACTIVE CONFIG' in cell['source'] and 'print' in cell['source']:
        src = cell['source']
        # Fix OTM offset line (already replaced above, but just make safe)
        src = src.replace(
            "strat.get('approx_otm_offset')} ({float(back.get('approx_otm_offset') or 0)*100:.0f}% below spot)",
            "back.get('approx_otm_offset')} ({float(back.get('approx_otm_offset') or 0)*100:.0f}% below spot)"
        )
        src = src.replace(
            "back.get('premium_fraction_of_spot', 0)*100:.2f}% of spot",
            "float(back.get('premium_fraction_of_spot') or 0)*100:.2f}% of spot"
        )
        # Fix regime line
        src = src.replace(
            "strat.get('use_regime_filter'",
            "sizing.get('use_regime_filter'"
        )
        cell['source'] = src

# 5. Fix stress test cell - otm, prem_frac, leg_btc references
for cell in nb['cells']:
    if cell['cell_type'] == 'code' and 'Black Swan' in str(cell.get('source','')) or (
        cell['cell_type'] == 'code' and 'otm = float(strat.get' in cell.get('source','')):
        src = cell['source']
        src = src.replace(
            "otm = float(strat.get('approx_otm_offset', 0.10))",
            "otm = float(back.get('approx_otm_offset', 0.10))"
        )
        src = src.replace(
            "prem_frac2 = float(strat.get('premium_fraction_of_spot', 0.006))",
            "prem_frac2 = float(back.get('premium_fraction_of_spot', 0.006))"
        )
        src = src.replace(
            "delta_h = abs(float(strat.get('target_delta_min', -0.10)))",
            "delta_h = float(strat.get('target_delta_min', 0.10))"
        )
        cell['source'] = src

# 6. Fix Section 13 improvements - regime_ma_days
for cell in nb['cells']:
    if cell['cell_type'] == 'code' and 'regime_ma_days' in cell.get('source',''):
        src = cell['source']
        src = src.replace(
            "cfg.get('strategy',{}).get('regime_ma_days', '?')",
            "sizing.get('regime_ma_days', '?')"
        )
        cell['source'] = src

nb['nbformat_minor'] = 5
nb_path.write_text(json.dumps(nb, indent=1))
print(f"Fixed {len(nb['cells'])} cells")
print("All cells have IDs: " + str(all('id' in c for c in nb['cells'])))
