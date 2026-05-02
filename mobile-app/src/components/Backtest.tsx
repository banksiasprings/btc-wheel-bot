import { useState, useCallback } from 'react'
import { runBacktest, BacktestParams, BacktestResult } from '../api'

// Touch-first parameter sliders for the mobile Backtest tab. Mirrors the
// dashboard's Backtest tab — same param set, same backend endpoint, same
// metric output. Makes "what if I changed IV threshold to 0.45?" answerable
// from a phone instead of requiring a laptop.

const PRESETS: Record<string, BacktestParams & { _label: string; _desc: string }> = {
  conservative: {
    _label: 'Conservative',
    _desc:  'Δ15-20% OTM · monthly · very selective IV',
    iv_rank_threshold: 0.60, target_delta_min: 0.15, target_delta_max: 0.20,
    min_dte: 21, max_dte: 35, max_equity_per_leg: 0.10,
    min_free_equity_fraction: 0.05, lookback_months: 12, starting_equity: 100000,
  },
  balanced: {
    _label: 'Balanced',
    _desc:  'Δ15-25% OTM · weekly · moderate IV',
    iv_rank_threshold: 0.50, target_delta_min: 0.15, target_delta_max: 0.25,
    min_dte: 5, max_dte: 14, max_equity_per_leg: 0.10,
    min_free_equity_fraction: 0.05, lookback_months: 12, starting_equity: 100000,
  },
  aggressive: {
    _label: 'Aggressive',
    _desc:  'Δ20-35% OTM · weekly · low IV filter',
    iv_rank_threshold: 0.30, target_delta_min: 0.20, target_delta_max: 0.35,
    min_dte: 5, max_dte: 14, max_equity_per_leg: 0.10,
    min_free_equity_fraction: 0.05, lookback_months: 12, starting_equity: 100000,
  },
  small_bot: {
    _label: 'Small bot',
    _desc:  'Tiny capital, hunt for $10k floor',
    iv_rank_threshold: 0.30, target_delta_min: 0.10, target_delta_max: 0.20,
    min_dte: 7, max_dte: 30, max_equity_per_leg: 0.20,
    min_free_equity_fraction: 0.05, lookback_months: 12, starting_equity: 50000,
  },
}

interface SliderProps {
  label:    string
  hint?:    string
  value:    number
  min:      number
  max:      number
  step:     number
  format?:  (n: number) => string
  onChange: (v: number) => void
}

function Slider({ label, hint, value, min, max, step, format, onChange }: SliderProps) {
  const display = format ? format(value) : String(value)
  return (
    <div className="bg-card rounded-2xl border border-border p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold text-slate-300">{label}</span>
        <span className="text-sm font-bold text-green-400 font-mono">{display}</span>
      </div>
      {hint ? <p className="text-[10px] text-slate-500 mb-2">{hint}</p> : null}
      <input
        type="range"
        min={min} max={max} step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full accent-green-500"
      />
      <div className="flex justify-between text-[10px] text-slate-600 mt-0.5">
        <span>{format ? format(min) : min}</span>
        <span>{format ? format(max) : max}</span>
      </div>
    </div>
  )
}

function MetricTile({ label, value, sub, color = 'text-white' }: {
  label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="bg-slate-900/60 rounded-xl py-2 px-3 text-center">
      <p className="text-[10px] text-slate-500 uppercase tracking-wide">{label}</p>
      <p className={`text-sm font-bold ${color} font-mono`}>{value}</p>
      {sub ? <p className="text-[10px] text-slate-500 mt-0.5">{sub}</p> : null}
    </div>
  )
}

export default function Backtest() {
  const [params, setParams] = useState<Required<BacktestParams>>({
    iv_rank_threshold: 0.30,
    target_delta_min:  0.20,
    target_delta_max:  0.35,
    min_dte:           5,
    max_dte:           14,
    max_equity_per_leg: 0.10,
    min_free_equity_fraction: 0.05,
    lookback_months:   12,
    starting_equity:   100000,
  })
  const [running, setRunning] = useState(false)
  const [result, setResult]   = useState<BacktestResult | null>(null)
  const [error, setError]     = useState<string | null>(null)

  const setParam = <K extends keyof BacktestParams>(k: K, v: number) =>
    setParams(p => ({ ...p, [k]: v }))

  const loadPreset = (name: keyof typeof PRESETS) => {
    const p = PRESETS[name]
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { _label, _desc, ...rest } = p
    setParams(prev => ({ ...prev, ...rest } as Required<BacktestParams>))
  }

  const onRun = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      const r = await runBacktest(params)
      setResult(r)
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }, [params])

  const m = result?.metrics

  return (
    <div className="min-h-screen bg-navy text-white pb-24">
      <header className="sticky top-0 z-10 bg-navy border-b border-slate-800 px-4 py-3">
        <h1 className="text-lg font-bold">📊 Backtest</h1>
        <p className="text-[11px] text-slate-400">Tune params, hit Run, see headline metrics</p>
      </header>

      <div className="px-4 py-3 space-y-3">

        {/* ── Quick presets ─────────────────────────────────────────────── */}
        <div>
          <p className="text-[10px] uppercase text-slate-500 mb-1.5 tracking-wide">Quick Presets</p>
          <div className="grid grid-cols-2 gap-2">
            {(Object.keys(PRESETS) as (keyof typeof PRESETS)[]).map(name => (
              <button
                key={name}
                onClick={() => loadPreset(name)}
                className="bg-card border border-border rounded-xl p-2 text-left active:bg-slate-800"
              >
                <p className="text-xs font-semibold text-slate-200">{PRESETS[name]._label}</p>
                <p className="text-[10px] text-slate-500 leading-tight mt-0.5">{PRESETS[name]._desc}</p>
              </button>
            ))}
          </div>
        </div>

        {/* ── Parameter sliders ─────────────────────────────────────────── */}
        <div className="space-y-2">
          <Slider
            label="IV Rank Threshold"
            hint="Below this, the bot waits."
            value={params.iv_rank_threshold}
            min={0.0} max={1.0} step={0.05}
            format={n => `${(n * 100).toFixed(0)}%`}
            onChange={v => setParam('iv_rank_threshold', v)}
          />
          <Slider
            label="Target Δ Min"
            value={params.target_delta_min}
            min={0.05} max={0.45} step={0.01}
            format={n => `Δ${(n * 100).toFixed(0)}%`}
            onChange={v => setParam('target_delta_min', v)}
          />
          <Slider
            label="Target Δ Max"
            hint="Strikes between min and max delta. Smaller = deeper OTM."
            value={params.target_delta_max}
            min={params.target_delta_min} max={0.55} step={0.01}
            format={n => `Δ${(n * 100).toFixed(0)}%`}
            onChange={v => setParam('target_delta_max', v)}
          />
          <Slider
            label="Min DTE"
            value={params.min_dte}
            min={1} max={45} step={1}
            format={n => `${n}d`}
            onChange={v => setParam('min_dte', v)}
          />
          <Slider
            label="Max DTE"
            hint="Days-to-expiry window"
            value={params.max_dte}
            min={params.min_dte} max={60} step={1}
            format={n => `${n}d`}
            onChange={v => setParam('max_dte', v)}
          />
          <Slider
            label="Max Equity per Leg"
            hint="What fraction of equity backs each trade"
            value={params.max_equity_per_leg}
            min={0.01} max={0.50} step={0.01}
            format={n => `${(n * 100).toFixed(0)}%`}
            onChange={v => setParam('max_equity_per_leg', v)}
          />
          <Slider
            label="Lookback (months)"
            value={params.lookback_months}
            min={1} max={24} step={1}
            format={n => `${n}m`}
            onChange={v => setParam('lookback_months', v)}
          />
          <Slider
            label="Starting Equity"
            hint="Capital floor — too small means no qualifying trades fire"
            value={params.starting_equity}
            min={1000} max={1_000_000} step={1000}
            format={n => `$${n >= 1000 ? (n / 1000).toFixed(0) + 'k' : n}`}
            onChange={v => setParam('starting_equity', v)}
          />
        </div>

        {/* ── Run button ────────────────────────────────────────────────── */}
        <button
          onClick={onRun}
          disabled={running}
          className={`w-full py-3 rounded-xl text-white font-semibold text-sm ${
            running ? 'bg-slate-700' : 'bg-green-700 active:bg-green-600'
          }`}
        >
          {running ? '⏳ Running backtest…' : '▶ Run Backtest'}
        </button>

        {error ? (
          <div className="bg-red-900/40 border border-red-700 rounded-xl p-3 text-sm text-red-300">
            {error}
          </div>
        ) : null}

        {/* ── Results ───────────────────────────────────────────────────── */}
        {m ? (
          <div className="space-y-2 pt-2 border-t border-slate-800">
            <p className="text-[10px] uppercase text-slate-500 tracking-wide">Headline metrics</p>
            <div className="grid grid-cols-3 gap-1.5">
              <MetricTile
                label="Return"
                value={`${m.total_return_pct >= 0 ? '+' : ''}${m.total_return_pct.toFixed(1)}%`}
                color={m.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}
              />
              <MetricTile
                label="Annualised"
                value={`${m.annualized_return_pct >= 0 ? '+' : ''}${m.annualized_return_pct.toFixed(1)}%`}
                color={m.annualized_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}
              />
              <MetricTile
                label="Sharpe"
                value={m.sharpe_ratio.toFixed(2)}
                color={m.sharpe_ratio >= 1 ? 'text-green-400' : m.sharpe_ratio >= 0 ? 'text-amber-400' : 'text-red-400'}
              />
              <MetricTile
                label="Win rate"
                value={`${m.win_rate_pct.toFixed(0)}%`}
                color={m.win_rate_pct >= 60 ? 'text-green-400' : m.win_rate_pct >= 50 ? 'text-amber-400' : 'text-red-400'}
              />
              <MetricTile
                label="Max DD"
                value={`${m.max_drawdown_pct.toFixed(1)}%`}
                color={m.max_drawdown_pct > -5 ? 'text-green-400' : m.max_drawdown_pct > -15 ? 'text-amber-400' : 'text-red-400'}
              />
              <MetricTile
                label="Trades"
                value={String(m.num_cycles)}
                sub={`${m.trades_per_year.toFixed(1)}/yr`}
              />
            </div>

            <p className="text-[10px] uppercase text-slate-500 tracking-wide pt-2">Capital efficiency</p>
            <div className="grid grid-cols-2 gap-1.5">
              <MetricTile
                label="Min Capital"
                value={m.min_viable_capital > 0 ? `$${(m.min_viable_capital / 1000).toFixed(0)}k` : '—'}
                color="text-amber-300"
              />
              <MetricTile
                label="Margin ROI / yr"
                value={`${(m.annualised_margin_roi * 100).toFixed(0)}%`}
                color={m.annualised_margin_roi >= 0 ? 'text-green-400' : 'text-red-400'}
              />
              <MetricTile
                label="Premium / Margin"
                value={`${(m.premium_on_margin * 100).toFixed(1)}%`}
                color="text-green-400"
              />
              <MetricTile
                label="Avg Margin Util"
                value={`${(m.avg_margin_utilization * 100).toFixed(1)}%`}
                color={m.avg_margin_utilization > 0.5 ? 'text-amber-400' : 'text-white'}
              />
            </div>

            <div className="bg-slate-900/40 border border-slate-700 rounded-xl p-2.5 mt-2">
              <p className="text-[10px] text-slate-500 leading-relaxed">
                Equity went <span className="text-white">${m.starting_equity.toLocaleString()}</span> →{' '}
                <span className={m.ending_equity >= m.starting_equity ? 'text-green-400' : 'text-red-400'}>
                  ${m.ending_equity.toLocaleString()}
                </span> across {m.num_cycles} closed trades. Avg P&L/trade{' '}
                <span className={m.avg_pnl_per_trade_usd >= 0 ? 'text-green-400' : 'text-red-400'}>
                  ${m.avg_pnl_per_trade_usd >= 0 ? '+' : ''}{m.avg_pnl_per_trade_usd.toFixed(0)}
                </span>.
              </p>
            </div>
          </div>
        ) : null}

      </div>
    </div>
  )
}
