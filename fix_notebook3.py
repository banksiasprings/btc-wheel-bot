#!/usr/bin/env python3.11
"""Final targeted fixes to the notebook."""
import json, uuid
from pathlib import Path

nb_path = Path.home() / 'Documents/btc-wheel-bot/wheel_bot_explainer.ipynb'
nb = json.loads(nb_path.read_text())

for cell in nb['cells']:
    if 'id' not in cell:
        cell['id'] = uuid.uuid4().hex[:8]
    if cell['cell_type'] == 'code':
        src = cell['source']
        # Fix cfg_map.get returning 'N/A' causing float() failure
        src = src.replace(
            "curr = cfg_map.get(param, 'N/A')",
            "curr = cfg_map.get(param)  # None if not in config"
        )
        # Fix match line to be safe with None
        src = src.replace(
            "match = '✅' if curr is not None and abs(float(curr) - float(best_val)) < abs(float(best_val))*0.05 + 0.001 else '⚠️'",
            "match = '✅' if curr is not None and abs(float(str(curr)) - float(str(best_val))) < abs(float(str(best_val)))*0.05 + 0.001 else ('⚠️' if curr is None else '❓')"
        )
        # Fix print to handle None curr
        src = src.replace(
            "print(f'  {match} {param_labels.get(param,param):<35}: best={best_val:>8.4g}  current={str(curr):>8}  fitness={max(fitness_vals):.3f}')",
            "curr_str = f'{float(str(curr)):.4g}' if curr is not None else 'N/A'\n    print(f'  {match} {param_labels.get(param,param):<35}: best={best_val:>8.4g}  current={curr_str:>8}  fitness={max(fitness_vals):.3f}')"
        )
        # Fix improvement ideas cell - strat.get('regime_ma_days')
        src = src.replace(
            "cfg.get('strategy',{}).get('regime_ma_days', '?')",
            "sizing.get('regime_ma_days', '?')"
        )
        # Fix stress test cell starting_equity unit issue
        src = src.replace(
            "equity_usd2 = start_eq * spot_e2",
            "# starting_equity in config is in USD (not BTC)\n    equity_usd2 = float(back.get('starting_equity', 1000))"
        )
        src = src.replace(
            "start_eq = float(back.get('starting_equity', 1.0))\n    equity_usd2 = # starting_equity",
            "# starting_equity in config is USD"
        )
        cell['source'] = src

nb_path.write_text(json.dumps(nb, indent=1))
print('Patch 3 applied. All cells have IDs:', all('id' in c for c in nb['cells']))
