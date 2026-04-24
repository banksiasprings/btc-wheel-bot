#!/usr/bin/env python3.11
"""Fix sweep cell and other remaining issues in the notebook."""
import json, uuid
from pathlib import Path

nb_path = Path.home() / 'Documents/btc-wheel-bot/wheel_bot_explainer.ipynb'
nb = json.loads(nb_path.read_text())

# Ensure all cells have IDs
for cell in nb['cells']:
    if 'id' not in cell:
        cell['id'] = uuid.uuid4().hex[:8]

# ── Fix sweep cell ───────────────────────────────────────────────────────
# The sweep_results.json IS the results dict (no 'results' sub-key)
# Each value is a list of {bot_id, params, fitness, sharpe_ratio, total_return_pct, ...}
# The swept value is entry['params'][param_name]
NEW_SWEEP_CELL = '''\
sweep = sweep_raw   # top-level keys ARE the param names

param_labels = {
    'iv_rank_threshold':        'IV Rank Threshold',
    'target_delta_min':         'Delta Min (abs)',
    'target_delta_max':         'Delta Max (abs)',
    'approx_otm_offset':        'OTM Offset',
    'max_dte':                  'Max DTE',
    'min_dte':                  'Min DTE',
    'max_equity_per_leg':       'Max Equity/Leg (BTC)',
    'premium_fraction_of_spot': 'Premium Fraction of Spot',
    'iv_rank_window_days':      'IV Rank Window (days)',
    'min_free_equity_fraction': 'Min Free Equity Fraction',
    'starting_equity':          'Starting Equity',
}

params_found = [p for p in param_labels if p in sweep]
n    = len(params_found)
cols = 3
rows = max(1, math.ceil(n / cols))

fig, axes = plt.subplots(rows, cols, figsize=(14, rows * 3.8))
fig.suptitle('Parameter Sweep — How Each Config Knob Affects Performance', fontsize=14, color='white')
axes_flat = axes.flatten() if hasattr(axes, 'flatten') else [axes]

for i, param in enumerate(params_found):
    ax = axes_flat[i]
    entries = sweep[param]
    # x = the value of this parameter in each sweep entry
    xs      = [e['params'][param]       for e in entries]
    fitness = [e['fitness']             for e in entries]
    returns = [e.get('total_return_pct', e.get('return_pct', 0)) for e in entries]

    ax.plot(xs, fitness, color=ACCENT, linewidth=2.5, marker='o', ms=5, label='Fitness')

    ax2 = ax.twinx()
    ax2.plot(xs, returns, color=ACCENT3, linewidth=1.5, linestyle='--',
             marker='s', ms=4, alpha=0.7, label='Return %')
    ax2.tick_params(axis='y', colors=ACCENT3, labelsize=8)
    ax2.set_ylabel('Return %', color=ACCENT3, fontsize=8)

    best_idx = fitness.index(max(fitness))
    ax.axvline(xs[best_idx], color=ACCENT2, linewidth=1.5, linestyle='--',
               label=f'Best: {xs[best_idx]:.4g}')

    # Mark current config value
    cfg_map = {
        'iv_rank_threshold':        strat.get('iv_rank_threshold'),
        'target_delta_min':         strat.get('target_delta_min'),
        'target_delta_max':         strat.get('target_delta_max'),
        'approx_otm_offset':        back.get('approx_otm_offset'),
        'max_dte':                  strat.get('max_dte'),
        'min_dte':                  strat.get('min_dte'),
        'max_equity_per_leg':       sizing.get('max_equity_per_leg'),
        'premium_fraction_of_spot': back.get('premium_fraction_of_spot'),
        'min_free_equity_fraction': sizing.get('min_free_equity_fraction'),
        'starting_equity':          back.get('starting_equity'),
    }
    current_val = cfg_map.get(param)
    if current_val is not None:
        ax.axvline(float(current_val), color='white', linewidth=1.2, linestyle=':',
                   alpha=0.6, label=f'Current: {float(current_val):.4g}')

    ax.set_title(param_labels.get(param, param), color='white', fontsize=10)
    ax.set_xlabel('Value', fontsize=8)
    ax.set_ylabel('Fitness', fontsize=8, color=ACCENT)
    ax.tick_params(axis='y', colors=ACCENT, labelsize=8)
    ax.legend(fontsize=7, loc='lower right')
    ax.grid(True, alpha=0.25)

for j in range(n, len(axes_flat)):
    axes_flat[j].set_visible(False)

plt.tight_layout()
plt.savefig(BASE / 'data' / 'chart_sweep_sensitivity.png', dpi=150, bbox_inches='tight', facecolor=BG)
plt.show()

print('=== BEST VALUE PER PARAMETER ===')
for param in params_found:
    entries = sweep[param]
    fitness_vals = [e['fitness'] for e in entries]
    best = entries[fitness_vals.index(max(fitness_vals))]
    best_val = best['params'][param]
    curr = cfg_map.get(param, 'N/A')
    match = '✅' if curr is not None and abs(float(curr) - float(best_val)) < abs(float(best_val))*0.05 + 0.001 else '⚠️'
    print(f'  {match} {param_labels.get(param,param):<35}: best={best_val:>8.4g}  current={str(curr):>8}  fitness={max(fitness_vals):.3f}')
'''

# ── Fix the evolution leaderboard cell ──────────────────────────────────
# leaderboard columns: bot_id, params, fitness, sharpe_ratio, total_return_pct, max_drawdown_pct, win_rate_pct, num_cycles
# params is a JSON string - need to parse it
NEW_LEADERBOARD_SETUP = '''\
lb = leaderboard.copy()
# Parse 'params' column if it's a JSON string
if 'params' in lb.columns and lb['params'].dtype == object:
    try:
        lb['params_parsed'] = lb['params'].apply(lambda x: json.loads(x) if isinstance(x, str) else x)
    except Exception:
        pass

# Normalise column names (handle both naming conventions)
if 'sharpe_ratio' in lb.columns and 'sharpe' not in lb.columns:
    lb['sharpe'] = lb['sharpe_ratio']
if 'total_return_pct' in lb.columns and 'return_pct' not in lb.columns:
    lb['return_pct'] = lb['total_return_pct']

print(f'Leaderboard: {len(lb)} bots')
print('Columns:', list(lb.columns))
'''

# ── Replace the cells ────────────────────────────────────────────────────
for i, cell in enumerate(nb['cells']):
    if cell['cell_type'] == 'code':
        src = cell['source']
        # Replace sweep cell
        if "sweep = sweep_raw.get('results', {})" in src or "sweep = sweep_raw   # top-level" in src:
            cell['source'] = NEW_SWEEP_CELL
            print(f'Replaced sweep cell at index {i}')

        # Replace leaderboard setup cell
        if "lb = leaderboard.copy()" in src and "nlargest" not in src and "scatter" not in src:
            cell['source'] = NEW_LEADERBOARD_SETUP
            print(f'Replaced leaderboard setup cell at index {i}')

        # Fix leaderboard analysis cell - use correct column names
        if "lb['total_return_pct']" in src or "lb['sharpe_ratio']" in src:
            src = src.replace("lb['sharpe_ratio']", "lb['sharpe']")
            src = src.replace("lb['total_return_pct']", "lb['return_pct']")
            src = src.replace(
                "col_show = [c for c in ['fitness','total_return_pct','sharpe_ratio',",
                "col_show = [c for c in ['fitness','return_pct','sharpe',"
            )
            src = src.replace(
                "col_s = [c for c in ['fitness','total_return_pct','sharpe_ratio','win_rate_pct','max_drawdown_pct','num_cycles'] if c in lb.columns]",
                "col_s = [c for c in ['fitness','return_pct','sharpe','win_rate_pct','max_drawdown_pct','num_cycles'] if c in lb.columns]"
            )
            src = src.replace(
                "colLabels=['Fitness','Return%','Sharpe','Win%','Drawdown%','Cycles'][:len(col_show)]",
                "colLabels=(['Fitness','Return%','Sharpe','Win%','Drawdown%','Cycles'] + ['']*(10))[:len(col_show)]"
            )
            cell['source'] = src

nb['nbformat_minor'] = 5
nb_path.write_text(json.dumps(nb, indent=1))
print('Done. All cells have IDs:', all('id' in c for c in nb['cells']))
