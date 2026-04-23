#!/usr/bin/env python3.11
"""Final cell replacement — overwrite broken cells with clean versions."""
import json, uuid
from pathlib import Path

nb_path = Path.home() / 'Documents/btc-wheel-bot/wheel_bot_explainer.ipynb'
nb = json.loads(nb_path.read_text())

# Ensure IDs
for cell in nb['cells']:
    if 'id' not in cell:
        cell['id'] = uuid.uuid4().hex[:8]

# ── Clean stress test cell (Section 12) ──────────────────────────────────
CLEAN_STRESS = """\
spot_e2   = 85_000
otm       = float(back.get('approx_otm_offset', 0.10))
strike_e2 = spot_e2 * (1 - otm)
prem_frac2 = float(back.get('premium_fraction_of_spot', 0.006))
prem_e2   = spot_e2 * prem_frac2
leg_btc   = float(sizing.get('max_equity_per_leg', 0.5))
equity_usd2 = float(back.get('starting_equity', 10000))  # USD
delta_h   = float(strat.get('target_delta_min', 0.10))

scenarios = {
    'BTC +5%   (minor rally)':     spot_e2 * 1.05,
    'BTC flat  (stays put)':       spot_e2 * 1.00,
    'BTC -5%   (normal dip)':      spot_e2 * 0.95,
    'BTC -10%  (moderate drop)':   spot_e2 * 0.90,
    'BTC -20%  (hard drop)':       spot_e2 * 0.80,
    'BTC -30%  (severe crash)':    spot_e2 * 0.70,
    'BTC -40%  (black swan)':      spot_e2 * 0.60,
    'BTC -60%  (bear market)':     spot_e2 * 0.40,
}

results_stress = []
for name, price in scenarios.items():
    if price >= strike_e2:
        put_pnl_btc = prem_e2
    else:
        put_pnl_btc = prem_e2 - (strike_e2 - price)
    total_put    = put_pnl_btc * leg_btc
    hedge_pnl    = delta_h * leg_btc * (spot_e2 - price)
    total_hedged = total_put + hedge_pnl
    pct_eq       = total_put / max(equity_usd2, 1) * 100
    pct_hedged   = total_hedged / max(equity_usd2, 1) * 100
    results_stress.append({
        'scenario': name, 'price': price,
        'put_pnl': total_put, 'hedged_pnl': total_hedged,
        'pct_eq': pct_eq, 'pct_hedged': pct_hedged,
    })

df_s = pd.DataFrame(results_stress)

fig, axes = plt.subplots(1, 2, figsize=(14, 7))
fig.suptitle('Black Swan Stress Test', fontsize=14, color='white')

ax = axes[0]
y  = np.arange(len(df_s))
w  = 0.38
c_put    = [ACCENT if v >= 0 else DANGER for v in df_s['put_pnl']]
c_hedged = [ACCENT if v >= 0 else ACCENT2 for v in df_s['hedged_pnl']]
ax.barh(y + w/2, df_s['put_pnl'],    w, color=c_put,    alpha=0.8, label='Unhedged put P&L')
ax.barh(y - w/2, df_s['hedged_pnl'], w, color=c_hedged, alpha=0.8, label='Hedged P&L')
ax.set_yticks(y)
ax.set_yticklabels(df_s['scenario'], fontsize=9)
ax.axvline(0, color='white', linewidth=1.5)
ax.set_xlabel('P&L (USD)')
ax.set_title('P&L per Scenario: Unhedged vs Hedged', color='white')
ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f'${x:,.0f}'))
ax.legend(fontsize=9)
ax.grid(True, axis='x', alpha=0.3)

ax = axes[1]
c_eq = [ACCENT if v >= 0 else DANGER for v in df_s['pct_eq']]
ax.barh(y, df_s['pct_eq'], color=c_eq, alpha=0.85, edgecolor=BG)
ax.axvline(0, color='white', linewidth=1.5)
for idx, row in df_s.iterrows():
    v = row['pct_eq']
    ax.text(v + (0.05 if v >= 0 else -0.05), idx,
            f'{v:.1f}%', va='center', ha='left' if v >= 0 else 'right',
            fontsize=8, color='white')
ax.set_yticks(y)
ax.set_yticklabels(df_s['scenario'], fontsize=9)
ax.set_xlabel('P&L as % of total equity')
ax.set_title('Impact as % of Total Equity', color='white')
ax.grid(True, axis='x', alpha=0.3)

plt.tight_layout()
plt.savefig(BASE / 'data' / 'chart_black_swan.png', dpi=150, bbox_inches='tight', facecolor=BG)
plt.show()

print(f'Strike: ${strike_e2:,.0f} | Premium: ${prem_e2:,.2f} | Leg: {leg_btc:.3f} BTC | Equity: ${equity_usd2:,.0f}')
be = strike_e2 - prem_e2
print(f'Breakeven: ${be:,.0f}  (BTC must fall {(1-be/spot_e2)*100:.1f}% to lose money)')
print()
print(f'{"Scenario":<30} {"Put P&L":>12} {"Hedged P&L":>13} {"Equity %":>10}')
print('-' * 70)
for _, row in df_s.iterrows():
    print(f'{row["scenario"]:<30} ${row["put_pnl"]:>10,.0f}  ${row["hedged_pnl"]:>10,.0f}  {row["pct_eq"]:>8.1f}%')
"""

# ── Replace broken cells ──────────────────────────────────────────────────
for i, cell in enumerate(nb['cells']):
    if cell['cell_type'] == 'code':
        src = cell['source']

        # Replace stress test cell
        if 'spot_e2 = 85_000' in src and 'scenarios' in src:
            cell['source'] = CLEAN_STRESS
            print(f'Replaced stress test cell at index {i}')

        # Fix leaderboard column references (use original names)
        if "lb['total_return_pct']" in src or "lb['return_pct']" in src:
            src = src.replace("lb['return_pct']",    "lb['total_return_pct']")
            src = src.replace("lb['sharpe']",         "lb['sharpe_ratio']")
            src = src.replace(
                "['fitness','return_pct','sharpe',",
                "['fitness','total_return_pct','sharpe_ratio',"
            )
            src = src.replace(
                "['fitness','return_pct','sharpe','win_rate_pct',",
                "['fitness','total_return_pct','sharpe_ratio','win_rate_pct',"
            )
            cell['source'] = src
            print(f'Fixed leaderboard columns at index {i}')

nb['nbformat_minor'] = 5
nb_path.write_text(json.dumps(nb, indent=1))
print('Final patch applied. IDs ok:', all('id' in c for c in nb['cells']))
