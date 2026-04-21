import { useState, useEffect, useRef, useCallback } from 'react'
import {
  getOptimizerSummary, getOptimizerRunning, runOptimizer,
  getSweepResults, getEvolveResults,
  OptimizerSummary, SweepResults, EvolveResults, SweepEntry, EvolveGoal,
} from '../api'

type OptMode = 'sweep' | 'evolve' | 'walk_forward' | 'monte_carlo' | 'reconcile'

const FITNESS_GOALS: { id: EvolveGoal; icon: string; label: string; desc: string; activeCls: string }[] = [
  { id: 'balanced',  icon: '🎯', label: 'Balanced',  desc: 'All-round (default)',          activeCls: 'bg-green-900 border-green-600 text-white' },
  { id: 'max_yield', icon: '🚀', label: 'Max Yield', desc: 'Highest return. Aggressive.',   activeCls: 'bg-orange-900 border-orange-600 text-white' },
  { id: 'safest',    icon: '🛡', label: 'Safest',    desc: 'Lowest drawdown. Conservative.', activeCls: 'bg-sky-900 border-sky-600 text-white' },
  { id: 'sharpe',    icon: '⚖️', label: 'Sharpe',    desc: 'Best risk-adjusted return.',    activeCls: 'bg-purple-900 border-purple-600 text-white' },
]

const MODE_LABELS: Record<OptMode, string> = {
  sweep:        'Parameter Sweep',
  evolve:       'Genetic Evolution',
  walk_forward: 'Walk-Forward',
  monte_carlo:  'Monte Carlo',
  reconcile:    'Reconcile',
}

// ── Utility ───────────────────────────────────────────────────────────────────

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch { return '—' }
}

function VerdictBadge({ verdict }: { verdict?: string | null }) {
  if (!verdict) return <span className="text-slate-500 text-xs">—</span>
  const isGood = /robust|calibrated/i.test(verdict)
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
      isGood ? 'bg-green-900 text-green-300' : 'bg-amber-900 text-amber-300'
    }`}>
      {verdict}
    </span>
  )
}

function SummaryCard({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="bg-card rounded-2xl p-4 border border-border">
      <p className="text-xs text-slate-400 mb-1">{label}</p>
      <div className="font-bold text-white text-sm mb-0.5">{value}</div>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

// ── Sweep results ─────────────────────────────────────────────────────────────

function sensitivityScore(entries: SweepEntry[]): number {
  const fits = entries.map(e => e.fitness)
  const maxF = Math.max(...fits)
  return maxF > 0 ? (maxF - Math.min(...fits)) / maxF : 0
}

function SensLabel({ entries }: { entries: SweepEntry[] }) {
  const score = sensitivityScore(entries)
  if (score > 0.15) return (
    <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-900 text-amber-300">Sensitive ⚡</span>
  )
  if (score < 0.05) return (
    <span className="text-xs px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-400">Flat —</span>
  )
  return (
    <span className="text-xs px-1.5 py-0.5 rounded-full bg-sky-900 text-sky-300">Moderate</span>
  )
}

function ParamRow({
  param, entries, bestValue,
}: { param: string; entries: SweepEntry[]; bestValue: number | undefined }) {
  const [open, setOpen] = useState(false)
  const maxFit = Math.max(...entries.map(e => e.fitness), 0.001)

  return (
    <div className="rounded-xl overflow-hidden bg-navy">
      <button
        className="w-full flex items-center justify-between px-3 py-2.5 text-left gap-2"
        onClick={() => setOpen(o => !o)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm text-white truncate">{param.replace(/_/g, ' ')}</span>
          <SensLabel entries={entries} />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {bestValue != null && (
            <span className="text-xs font-mono text-green-400">
              best={typeof bestValue === 'number' && bestValue % 1 !== 0
                ? bestValue.toFixed(3) : bestValue}
            </span>
          )}
          <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
        </div>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-1">
          {entries.map((e, i) => {
            const isBest = e.value === bestValue
            const pct = Math.max((e.fitness / maxFit) * 100, 2)
            return (
              <div key={i} className="flex items-center gap-2">
                <span className={`text-xs font-mono w-16 text-right shrink-0 ${
                  isBest ? 'text-green-400 font-bold' : 'text-slate-500'
                }`}>
                  {e.value % 1 !== 0 ? e.value.toFixed(3) : e.value}
                </span>
                <div className="flex-1 bg-slate-800 rounded-sm h-3 overflow-hidden">
                  <div
                    className="h-full rounded-sm"
                    style={{
                      width: `${pct}%`,
                      background: isBest ? '#22c55e' : '#38bdf888',
                    }}
                  />
                </div>
                <span className={`text-xs font-mono w-10 shrink-0 ${
                  isBest ? 'text-green-400 font-bold' : 'text-slate-500'
                }`}>
                  {e.fitness.toFixed(2)}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function SweepSection({ data }: { data: SweepResults }) {
  // Sort params by sensitivity descending
  const sorted = [...data.params].sort(
    (a, b) => sensitivityScore(data.results[b] ?? []) - sensitivityScore(data.results[a] ?? [])
  )

  // Overall best point
  let overallBest: { param: string; value: number; fitness: number } | null = null
  for (const p of data.params) {
    for (const e of data.results[p] ?? []) {
      if (!overallBest || e.fitness > overallBest.fitness) {
        overallBest = { param: p, value: e.value, fitness: e.fitness }
      }
    }
  }

  return (
    <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-white">Sweep Results</p>
        {data.timestamp && (
          <span className="text-xs text-slate-500">{fmtTime(data.timestamp)}</span>
        )}
      </div>

      {/* Summary line */}
      {overallBest && (
        <div className="bg-navy rounded-xl px-3 py-2 text-xs text-slate-400">
          Swept{' '}
          <span className="text-white font-medium">{data.params.length}</span> params · Best:{' '}
          <span className="text-green-400 font-medium">
            {overallBest.param.replace(/_/g, ' ')}=
            {overallBest.value % 1 !== 0 ? overallBest.value.toFixed(3) : overallBest.value}
          </span>{' '}
          (fitness{' '}
          <span className="text-green-400 font-medium">{overallBest.fitness.toFixed(2)}</span>)
          {data.timestamp && <> · Run: {fmtTime(data.timestamp)}</>}
        </div>
      )}

      <div className="space-y-1.5">
        {sorted.map(param => (
          <ParamRow
            key={param}
            param={param}
            entries={data.results[param] ?? []}
            bestValue={data.best_per_param[param]?.value}
          />
        ))}
      </div>
    </div>
  )
}

// ── Evolve results ────────────────────────────────────────────────────────────

function EvolveSection({ data }: { data: EvolveResults }) {
  return (
    <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-white">Evolution Results</p>
        <div className="flex items-center gap-3">
          {data.total_evaluated > 0 && (
            <span className="text-xs text-slate-500">{data.total_evaluated} evaluated</span>
          )}
          {data.timestamp && (
            <span className="text-xs text-slate-500">{fmtTime(data.timestamp)}</span>
          )}
        </div>
      </div>

      <div className="space-y-2">
        {data.top_genomes.slice(0, 5).map((g, i) => {
          const isBest = i === 0
          return (
            <div
              key={i}
              className={`rounded-xl p-3 bg-navy ${isBest ? 'border border-green-700' : ''}`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className={`text-xs font-bold ${isBest ? 'text-green-400' : 'text-slate-500'}`}>
                  {isBest ? '★ #1 Best' : `#${i + 1}`}
                </span>
                <span className={`text-sm font-bold ${isBest ? 'text-green-400' : 'text-white'}`}>
                  {g.fitness.toFixed(3)}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
                <span className="text-slate-400">
                  Return:{' '}
                  <span className={g.return_pct >= 0 ? 'text-green-400 font-medium' : 'text-red-400 font-medium'}>
                    {g.return_pct >= 0 ? '+' : ''}{g.return_pct.toFixed(1)}%
                  </span>
                </span>
                <span className="text-slate-400">
                  Sharpe: <span className="text-white font-medium">{g.sharpe.toFixed(2)}</span>
                </span>
                <span className="text-slate-400">
                  Win Rate: <span className="text-white font-medium">{g.win_rate.toFixed(0)}%</span>
                </span>
                <span className="text-slate-400">
                  Max DD: <span className="text-red-400 font-medium">{g.drawdown.toFixed(1)}%</span>
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Optimizer() {
  const [summary,    setSummary]    = useState<OptimizerSummary | null>(null)
  const [sweepData,  setSweepData]  = useState<SweepResults | null>(null)
  const [evolveData, setEvolveData] = useState<EvolveResults | null>(null)
  const [running,    setRunning]    = useState(false)
  const [completed,  setCompleted]  = useState(false)
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState('')
  const [mode,        setMode]        = useState<OptMode>('sweep')
  const [fitnessGoal, setFitnessGoal] = useState<EvolveGoal>('balanced')
  const [launching,   setLaunching]   = useState(false)
  const [launchMsg,   setLaunchMsg]   = useState('')

  const fastPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const slowPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const [s, r, sw, ev] = await Promise.all([
        getOptimizerSummary(),
        getOptimizerRunning(),
        getSweepResults(),
        getEvolveResults(),
      ])
      setSummary(s)

      // Detect running→stopped transition
      setRunning(prev => {
        if (prev && !r.running) {
          setCompleted(true)
          setTimeout(() => setCompleted(false), 8_000)
        }
        return r.running
      })

      setSweepData(sw?.params?.length ? sw : null)
      setEvolveData(ev?.top_genomes?.length ? ev : null)
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  // Base 15s poll
  useEffect(() => {
    fetchAll()
    slowPollRef.current = setInterval(fetchAll, 15_000)
    return () => {
      clearInterval(slowPollRef.current!)
      clearInterval(fastPollRef.current!)
    }
  }, [fetchAll])

  // Upgrade to 5s while running
  useEffect(() => {
    if (fastPollRef.current) clearInterval(fastPollRef.current)
    if (running) fastPollRef.current = setInterval(fetchAll, 5_000)
  }, [running, fetchAll])

  async function handleRun() {
    setLaunching(true)
    setLaunchMsg('')
    setCompleted(false)
    try {
      const r = await runOptimizer(mode, undefined, mode === 'evolve' ? fitnessGoal : undefined)
      setLaunchMsg(`Started (PID ${r.pid})`)
      setRunning(true)
    } catch (e) {
      setLaunchMsg(String(e))
    } finally {
      setLaunching(false)
    }
  }

  const mc  = summary?.monte_carlo  as Record<string, unknown> | null
  const wf  = summary?.walk_forward as Record<string, unknown> | null
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

      {/* Completed banner */}
      {completed && (
        <div className="bg-green-950 border border-green-700 rounded-xl px-4 py-3 text-green-300 text-sm font-medium">
          ✓ Optimizer run completed — results updated
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3">
        <SummaryCard
          label="Best Fitness"
          value={summary?.best_fitness != null ? summary.best_fitness.toFixed(2) : '—'}
          sub={summary?.sweep_params_count
            ? `${summary.sweep_params_count} params swept`
            : `Last run: ${fmtTime(summary?.last_sweep_timestamp)}`}
        />
        <SummaryCard
          label="Monte Carlo"
          value={<VerdictBadge verdict={mc?.verdict as string} />}
          sub={mc ? `Median Sharpe: ${(mc.median_sharpe as number)?.toFixed(2) ?? '—'}` : 'No results yet'}
        />
        <SummaryCard
          label="Walk-Forward"
          value={wf ? <VerdictBadge verdict={wf.verdict as string} /> : <span className="text-slate-500 text-xs">—</span>}
          sub={wf ? `Robustness: ${((wf.robustness_score as number) * 100).toFixed(0)}%` : 'No results yet'}
        />
        <SummaryCard
          label="Backtest Accuracy"
          value={rec ? <VerdictBadge verdict={rec.accuracy as string} /> : <span className="text-slate-500 text-xs">—</span>}
          sub={rec ? `RMSE $${(rec.premium_rmse as number)?.toFixed(0) ?? '—'}` : 'No results yet'}
        />
      </div>

      {/* Run section */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-white">Run Optimizer</p>
          {running && (
            <span className="flex items-center gap-1.5 text-amber-400 text-xs font-medium">
              <span className="w-2 h-2 bg-amber-400 rounded-full animate-pulse" />
              Running… (5s refresh)
            </span>
          )}
        </div>

        <select
          value={mode}
          onChange={e => setMode(e.target.value as OptMode)}
          className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-green-500"
        >
          {(Object.entries(MODE_LABELS) as [OptMode, string][]).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>

        {/* Fitness goal selector — only shown for Evolve mode */}
        {mode === 'evolve' && (
          <div className="space-y-2">
            <p className="text-xs text-slate-400 font-medium">Fitness Goal</p>
            <div className="grid grid-cols-2 gap-2">
              {FITNESS_GOALS.map(g => (
                <button
                  key={g.id}
                  onClick={() => setFitnessGoal(g.id)}
                  className={`rounded-xl p-3 text-left border transition-colors ${
                    fitnessGoal === g.id
                      ? g.activeCls
                      : 'bg-navy border-border text-slate-400 hover:border-slate-500'
                  }`}
                >
                  <p className="text-xs font-semibold">{g.icon} {g.label}</p>
                  <p className="text-xs mt-0.5 opacity-70 leading-snug">{g.desc}</p>
                </button>
              ))}
            </div>
          </div>
        )}

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

      {/* Sweep results */}
      {sweepData && <SweepSection data={sweepData} />}

      {/* Evolve results */}
      {evolveData && <EvolveSection data={evolveData} />}

      {/* No results placeholder */}
      {!sweepData && !evolveData && !summary?.best_genome && (
        <div className="bg-card rounded-2xl p-6 border border-border text-center text-slate-500 text-sm">
          No results yet — run Sweep or Evolve to see results here.
        </div>
      )}

      {/* Best genome */}
      {summary?.best_genome && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-3">
            Best Genome (Applied)
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
