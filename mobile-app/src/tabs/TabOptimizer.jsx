import { useState, useEffect, useRef } from 'react'
import { api } from '../api.js'

const C = { card: '#1e293b', green: '#22c55e', red: '#ef4444', amber: '#f59e0b', muted: '#94a3b8', blue: '#38bdf8' }

const ACCURACY_COLOR = { good: C.green, moderate: C.amber, poor: C.red }
const VERDICT_COLOR  = { robust: C.green, marginal: C.amber, 'likely overfit': C.red, 'fails under stress': C.red }

function SummaryCard({ label, value, color, sub }) {
  return (
    <div className="rounded-xl p-4 flex flex-col gap-1" style={{ background: C.card }}>
      <div className="text-xs" style={{ color: C.muted }}>{label}</div>
      <div className="text-lg font-bold" style={{ color: color || 'white' }}>{value ?? '—'}</div>
      {sub && <div className="text-xs" style={{ color: C.muted }}>{sub}</div>}
    </div>
  )
}

const OPT_MODES = [
  { value: 'sweep',        label: 'Sweep' },
  { value: 'evolve',       label: 'Evolve' },
  { value: 'walk_forward', label: 'Walk-Forward' },
  { value: 'monte_carlo',  label: 'Monte Carlo' },
  { value: 'reconcile',    label: 'Reconcile' },
]

export default function TabOptimizer() {
  const [summary,    setSummary]    = useState(null)
  const [running,    setRunning]    = useState(false)
  const [mode,       setMode]       = useState('sweep')
  const [busy,       setBusy]       = useState(false)
  const [msg,        setMsg]        = useState('')
  const [error,      setError]      = useState('')
  const pollRef = useRef(null)

  async function loadSummary() {
    try {
      const [s, r] = await Promise.all([api.optimizerSummary(), api.optimizerRunning()])
      setSummary(s)
      setRunning(r.running)
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => {
    loadSummary()
    pollRef.current = setInterval(loadSummary, 15_000)
    return () => clearInterval(pollRef.current)
  }, [])

  async function runOptimizer() {
    setBusy(true)
    setMsg('')
    try {
      await api.runOptimizer(mode)
      setMsg(`Started ${mode} run`)
      setRunning(true)
    } catch (e) {
      setMsg(`Error: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  const mc  = summary?.monte_carlo
  const wf  = summary?.walk_forward
  const rec = summary?.reconciliation

  return (
    <div className="p-4 flex flex-col gap-3" style={{ paddingTop: 'env(safe-area-inset-top,12px)' }}>
      <h1 className="text-lg font-bold text-white">Optimizer</h1>

      {error && (
        <div className="rounded-lg px-4 py-3 text-sm" style={{ background: '#ef444422', color: C.red }}>
          {error}
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-2">
        <SummaryCard
          label="Best Fitness"
          value={summary?.best_fitness?.toFixed(3) ?? '—'}
          color={C.blue}
        />
        <SummaryCard
          label="Monte Carlo"
          value={mc?.verdict ?? '—'}
          color={VERDICT_COLOR[mc?.verdict] ?? C.muted}
          sub={mc ? `${mc.pct_profitable?.toFixed(0)}% profitable` : undefined}
        />
        <SummaryCard
          label="Walk-Forward"
          value={wf ? `${(wf.robustness_score * 100)?.toFixed(0)}%` : '—'}
          color={VERDICT_COLOR[wf?.verdict] ?? C.muted}
          sub={wf?.verdict ?? undefined}
        />
        <SummaryCard
          label="Backtest Accuracy"
          value={rec?.accuracy?.toUpperCase() ?? '—'}
          color={ACCURACY_COLOR[rec?.accuracy] ?? C.muted}
          sub={rec ? `RMSE $${rec.premium_rmse?.toFixed(0)}` : undefined}
        />
      </div>

      {/* Run section */}
      <div className="rounded-xl p-4 flex flex-col gap-3" style={{ background: C.card }}>
        <div className="font-semibold text-white">Run Optimizer</div>

        <div>
          <label className="text-xs mb-1 block" style={{ color: C.muted }}>Mode</label>
          <select
            className="w-full rounded-lg px-3 py-2.5 text-sm text-white outline-none"
            style={{ background: '#0f172a', border: '1px solid #334155' }}
            value={mode}
            onChange={e => setMode(e.target.value)}
          >
            {OPT_MODES.map(m => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
        </div>

        <button
          className="rounded-lg py-2.5 text-sm font-semibold text-white disabled:opacity-50"
          style={{ background: running || busy ? C.amber : C.green }}
          disabled={busy}
          onClick={runOptimizer}
        >
          {running ? '⏳ Running…' : busy ? 'Starting…' : '▶ Run'}
        </button>

        {msg && (
          <p className="text-xs text-center" style={{ color: msg.startsWith('Error') ? C.red : C.green }}>
            {msg}
          </p>
        )}
      </div>

      {/* Best genome */}
      {summary?.best_genome && (
        <div className="rounded-xl p-4" style={{ background: C.card }}>
          <div className="font-semibold text-white mb-3">Best Genome</div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            {Object.entries(summary.best_genome)
              .filter(([k]) => !['starting_equity', 'use_regime_filter', 'regime_ma_days'].includes(k))
              .map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span style={{ color: C.muted }}>{k.replace(/_/g, ' ')}</span>
                  <span className="font-mono font-bold text-white">{typeof v === 'number' ? v.toFixed(3) : v}</span>
                </div>
              ))
            }
          </div>
        </div>
      )}
    </div>
  )
}
