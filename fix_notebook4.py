#!/usr/bin/env python3.11
"""Fix leaderboard column name issues."""
import json, uuid
from pathlib import Path

nb_path = Path.home() / 'Documents/btc-wheel-bot/wheel_bot_explainer.ipynb'
nb = json.loads(nb_path.read_text())

for cell in nb['cells']:
    if 'id' not in cell:
        cell['id'] = uuid.uuid4().hex[:8]
    if cell['cell_type'] == 'code':
        src = cell['source']

        # Use original column names throughout the leaderboard cell
        src = src.replace("lb['return_pct']",   "lb['total_return_pct']")
        src = src.replace("lb['sharpe']",        "lb['sharpe_ratio']")
        src = src.replace(
            "'fitness','return_pct','sharpe',",
            "'fitness','total_return_pct','sharpe_ratio',"
        )
        src = src.replace(
            "col_s = [c for c in ['fitness','return_pct','sharpe','win_rate_pct','max_drawdown_pct','num_cycles']",
            "col_s = [c for c in ['fitness','total_return_pct','sharpe_ratio','win_rate_pct','max_drawdown_pct','num_cycles']"
        )

        # Fix the stress test starting_equity confusion
        # back.get('starting_equity') returns 1255.89 (USD) — use it directly as equity_usd
        if 'start_eq = float(back.get' in src and 'equity_usd2' in src:
            src = src.replace(
                "start_eq = float(back.get('starting_equity', 1.0))\n    equity_usd2 = # starting_equity in config is USD",
                "equity_usd2 = float(back.get('starting_equity', 10000))"
            )
            src = src.replace(
                "equity_usd2 = # starting_equity in config is USD",
                ""
            )
            src = src.replace(
                "start_eq = float(back.get('starting_equity', 1.0))",
                ""
            )

        cell['source'] = src

nb_path.write_text(json.dumps(nb, indent=1))
print('Patch 4 applied.')
