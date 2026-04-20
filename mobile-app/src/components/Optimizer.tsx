import { useState, useEffect } from 'react'
import { getOptimizerSummary, getOptimizerRunning, runOptimizer, OptimizerSummary } from '../api'

type OptMode = 'sweep' | 'evolve' | 'walk_forward' | 'monte_carlo' | 'reconcile'

const MODE_LABELS: Record<OptMode, string> = {
  sweep: 'Parameter Sweep',
  evolve: 'Genetic Evolution',
  walk_forward: 'Walk-Forward',
  monte_carlo: 'Monte Carlo',
  reconcile: 'Reconcile',
}

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function VerdictBadge({ verdict }: { verdict?: string }) {
  if (!verdict) return <span className="text-slate-500 text-xs">—</span>
  const isGood = verdict.toLowerCase().includes('robust') || verdict.toLowerCase().includes('calibrated')
  return (
    <span
      className={`text-xs font-medium px-2 py-0.5 rounded-full ${
        isGood ? 'bg-green-900 text-green-300' : 'bg-amber-900 text-amber-300'
      }`}
    >
      {verdict}
    </span>
  )
}

export default function Optimizer() {
  const [summary, setSummary] = useState<OptimizerSummary | null>(null)
  const [running, setRunning] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<OptMode>('sweep')
  const [launching, setLaunching] = useState(false)
  const [launchMsg, setLaunchMsg] = useState('')

  async function fetchAll() {
    try {
      const [s, r] = await Promise.all([getOptimizerSummary(), getOptimizerRunning()])
      setSummary(s)
      setRunning(r.running)
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 15_000)
    return () => clearInterval(id)
  }, [])

  async function handleRun() {
    setLaunching(true)
    setLaunchMsg('')
    try {
      const r = await runOptimizer(mode)
      setLaunchMsg(`Started (PID ${r.pid})`)
      setRunning(true)
      setTimeout(fetchAll, 2000)
    } catch (e) {
      setLaunchMsg(String(e))
    } finally {
      setLaunching(false)
    }
  }

  const mc = summary?.monte_carlo as Record<string, unknown> | null
  const wf = summary?.walk_forward as Record<string, unknown> | null
  const rec = summary?.reconciliation as Record<string, unknown> | null

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-slate-400">Loading…</div>
  }

  return (
    <div className="p-4 space-y-4 pb-4">
      <h1 className="text-lg font-bold text-white pt-2">Optimizer</h1>

      {error && (
        <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3">
        <SummaryCard
          label="Best Fitness"
          value={summary?.best_fitness != null ? summary.best_fitness.toFixed(2) : '—'}
          sub={`Last run: ${fmtDate(summary?.last_run ?? null)}`}
        />
        <SummaryCard
          label="Monte Carlo"
          value={<VerdictBadge verdict={mc?.verdict as string} />}
          sub={
            mc
              ? `Median Sharpe: ${(mc.median_sharpe as number)?.toFixed(2) ?? '—'}`
              : 'No results yet'
          }
        />
        <SummaryCard
          label="Walk-Forward"
          value={
            wf ? (
              <VerdictBadge verdict={wf.verdict as string} />
            ) : (
              <span className="text-slate-500 text-xs">—</span>
            )
          }
          sub={
            wf
              ? `Robustness: ${((wf.robustness_score as number) * 100).toFixed(0)}%`
              : 'No results yet'
          }
        />
        <SummaryCard
          label="Backtest Accuracy"
          value={
            rec ? (
              <VerdictBadge verdict={rec.verdict as string} />
            ) : (
              <span className="text-slate-500 text-xs">—</span>
            )
          }
          sub={
            rec
              ? `Premium err: ${(rec.premium_accuracy_pct as number)?.toFixed(1) ?? '—'}%`
              : 'No results yet'
          }
        />
      </div>

      {/* Run optimizer */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-white">Run Optimizer</p>
          {running && (
            <span className="flex items-center gap-1.5 text-amber-400 text-xs font-medium">
              <span className="w-2 h-2 bg-amber-400 rounded-full animate-pulse" />
              Running…
            </span>
          )}
        </div>

        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as OptMode)}
          className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-green-500"
        >
          {(Object.entries(MODE_LABELS) as [OptMode, string][]).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>

        {launchMsg && (
          <p className="text-xs text-green-400 bg-green-950 border border-green-800 rounded-lg px-3 py-2">
            {launchMsg}
          </p>
        )}

        <button
          onClick={handleRun}
          disabled={running || launching}
          className="w-full bg-green-700 hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-xl transition-colors text-sm"
        >
          {launching ? 'Launching…' : running ? 'Already Running' : `Run ${MODE_LABELS[mode]}`}
        </button>
      </div>

      {/* Best genome */}
      {summary?.best_genome && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-3">
            Best Genome
          </p>
          <div className="space-y-1.5">
            {Object.entries(summary.best_genome).map(([k, v]) => (
              <div key={k} className="flex justify-between text-sm">
                <span className="text-slate-400 font-mono text-xs">{k}</span>
                <span className="text-white font-mono text-xs">{String(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function SummaryCard({
  label,
  value,
  sub,
}: {
  label: string
  value: React.ReactNode
  sub: string
}) {
  return (
    <div className="bg-card rounded-2xl p-4 border border-border">
      <p className="text-xs text-slate-400 mb-1">{label}</p>
      <div className="font-bold text-white text-sm mb-0.5">{value}</div>
      <p className="text-xs text-slate-500">{sub}</p>
    </div>
  )
}
