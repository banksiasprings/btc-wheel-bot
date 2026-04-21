import { useState, useEffect, useRef, useCallback } from 'react'
import {
  getOptimizerSummary, getOptimizerRunning, runOptimizer,
  getSweepResults, getEvolveResults, getOptimizerProgress,
  OptimizerSummary, SweepResults, EvolveResults, SweepEntry, EvolveGoal, EvolutionProgress,
} from '../api'
import InfoModal from './InfoModal'
import { GLOSSARY } from '../lib/glossary'

type OptMode = 'sweep' | 'evolve' | 'walk_forward' | 'monte_carlo' | 'reconcile'

const FITNESS_GOALS: { id: EvolveGoal; icon: string; label: string; desc: string; activeCls: string }[] = [
  { id: 'balanced',  icon: '🎯', label: 'Balanced',  desc: 'All-round (default)',           activeCls: 'bg-green-900 border-green-600 text-white' },
  { id: 'max_yield', icon: '🚀', label: 'Max Yield', desc: 'Highest return. Aggressive.',    activeCls: 'bg-orange-900 border-orange-600 text-white' },
  { id: 'safest',    icon: '🛡', label: 'Safest',    desc: 'Lowest drawdown. Conservative.', activeCls: 'bg-sky-900 border-sky-600 text-white' },
  { id: 'sharpe',    icon: '⚖️', label: 'Sharpe',    desc: 'Best risk-adjusted return.',     activeCls: 'bg-purple-900 border-purple-600 text-white' },
]

const MODE_LABELS: Record<OptMode, string> = {
  sweep:        'Parameter Sweep',
  evolve:       'Genetic Evolution',
  walk_forward: 'Walk-Forward',
  monte_carlo:  'Monte Carlo',
  reconcile:    'Reconcile',
}

const MODE_GLOSSARY_KEYS: Partial<Record<OptMode, string>> = {
  sweep:        'sweep_mode',
  evolve:       'evolve_mode_desc',
  walk_forward: 'walk_forward',
  monte_carlo:  'monte_carlo',
  reconcile:    'reconcile_mode',
}

type InfoEntry = { title: string; body: string }

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

function SummaryCard({ label, value, sub, onInfo }: { label: string; value: React.ReactNode; sub?: string; onInfo?: () => void }) {
  return (
    <div className="bg-card rounded-2xl p-4 border border-border">
      <div className="flex items-center gap-1 mb-1">
        <p className="text-xs text-slate-400">{label}</p>
        {onInfo && (
          <button onClick={onInfo} className="text-slate-500 hover:text-slate-300 text-xs leading-none">ⓘ</button>
        )}
      </div>
      <div className="font-bold text-white text-sm mb-0.5">{value}</div>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

function InfoBtn({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} className="text-slate-500 hover:text-slate-300 text-xs leading-none flex-shrink-0">ⓘ</button>
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

function SweepSection({ data, onInfo }: { data: SweepResults; onInfo?: (e: InfoEntry) => void }) {
  const sorted = [...data.params].sort(
    (a, b) => sensitivityScore(data.results[b] ?? []) - sensitivityScore(data.results[a] ?? [])
  )

  let overallBest: { param: string; value: number; fitness: number } | null = null
  for (const p of data.params) {
    for (const e of data.results[p] ?? []) {
      if (!overallBest || e.fitness > overallBest.fitness) {
        overallBest = { param: p, value: e.value, fitness: e.fitness }
      }
    }
  }

  return (
    <div className="space-y-3">
      {/* Metric legend */}
      <div className="flex gap-3 flex-wrap">
        {([
          ['Fitness', 'fitness_score'],
          ['Sharpe',  'sharpe_ratio'],
          ['Return %','return_pct'],
          ['Win Rate','win_rate'],
          ['Drawdown','max_drawdown'],
        ] as [string, string][]).map(([label, key]) => (
          <button
            key={key}
            onClick={() => onInfo?.(GLOSSARY[key])}
            className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-0.5 transition-colors"
          >
            {label} <span className="opacity-70">ⓘ</span>
          </button>
        ))}
      </div>

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

function EvolveSection({ data, onInfo }: { data: EvolveResults; onInfo?: (e: InfoEntry) => void }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        {data.total_evaluated > 0 && (
          <span className="text-xs text-slate-500">{data.total_evaluated} evaluated</span>
        )}
        {data.timestamp && (
          <span className="text-xs text-slate-500">{fmtTime(data.timestamp)}</span>
        )}
      </div>
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
              <div className="flex items-center gap-1">
                <span className={`text-sm font-bold ${isBest ? 'text-green-400' : 'text-white'}`}>
                  {g.fitness.toFixed(3)}
                </span>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.fitness_score)} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
              <span className="text-slate-400 flex items-center gap-1">
                Return:{' '}
                <span className={g.return_pct >= 0 ? 'text-green-400 font-medium' : 'text-red-400 font-medium'}>
                  {g.return_pct >= 0 ? '+' : ''}{g.return_pct.toFixed(1)}%
                </span>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.return_pct)} />
              </span>
              <span className="text-slate-400 flex items-center gap-1">
                Sharpe: <span className="text-white font-medium">{g.sharpe.toFixed(2)}</span>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.sharpe_ratio)} />
              </span>
              <span className="text-slate-400 flex items-center gap-1">
                Win Rate: <span className="text-white font-medium">{g.win_rate.toFixed(0)}%</span>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.win_rate)} />
              </span>
              <span className="text-slate-400 flex items-center gap-1">
                Max DD: <span className="text-red-400 font-medium">{g.drawdown.toFixed(1)}%</span>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.max_drawdown)} />
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Walk-Forward detail section ───────────────────────────────────────────────

function WalkForwardSection({ data, onInfo }: { data: Record<string, unknown>; onInfo?: (e: InfoEntry) => void }) {
  const [open, setOpen] = useState(false)
  const isF  = data.is_fitness      as number | undefined
  const oosF = data.oos_fitness     as number | undefined
  const rob  = data.robustness_score as number | undefined
  const verd = data.verdict         as string | undefined

  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left"
        onClick={() => setOpen(o => !o)}
      >
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-white">Walk-Forward Results</p>
          {verd && <VerdictBadge verdict={verd} />}
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="px-4 pb-4 grid grid-cols-2 gap-3">
          {isF != null && (
            <div className="bg-navy rounded-xl px-3 py-2">
              <div className="flex items-center gap-1 mb-1">
                <p className="text-xs text-slate-400">IS Fitness</p>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.is_fitness)} />
              </div>
              <p className="text-sm font-medium text-white">{isF.toFixed(3)}</p>
            </div>
          )}
          {oosF != null && (
            <div className="bg-navy rounded-xl px-3 py-2">
              <div className="flex items-center gap-1 mb-1">
                <p className="text-xs text-slate-400">OOS Fitness</p>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.oos_fitness)} />
              </div>
              <p className="text-sm font-medium text-white">{oosF.toFixed(3)}</p>
            </div>
          )}
          {rob != null && (
            <div className="bg-navy rounded-xl px-3 py-2 col-span-2">
              <div className="flex items-center gap-1 mb-1">
                <p className="text-xs text-slate-400">Robustness Score</p>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.robustness_score)} />
              </div>
              <p className={`text-sm font-medium ${
                rob >= 0.7 ? 'text-green-400' : rob >= 0.5 ? 'text-amber-400' : 'text-red-400'
              }`}>{(rob * 100).toFixed(0)}%</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Monte Carlo detail section ────────────────────────────────────────────────

function MonteCarloSection({ data, onInfo }: { data: Record<string, unknown>; onInfo?: (e: InfoEntry) => void }) {
  const [open, setOpen] = useState(false)
  const verd   = data.verdict       as string | undefined
  const prob   = data.prob_profit   as number | undefined
  const p5     = data.p5            as number | undefined
  const p50    = data.p50           as number | undefined
  const p95    = data.p95           as number | undefined
  const sharpe = data.median_sharpe as number | undefined

  const hasPercentiles = p5 != null || p50 != null || p95 != null

  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left"
        onClick={() => setOpen(o => !o)}
      >
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-white">Monte Carlo Results</p>
          {verd && <VerdictBadge verdict={verd} />}
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3">
          {prob != null && (
            <div className="bg-navy rounded-xl px-3 py-2">
              <div className="flex items-center gap-1 mb-1">
                <p className="text-xs text-slate-400">Probability of Profit</p>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.prob_profit)} />
              </div>
              <p className={`text-sm font-medium ${
                prob >= 0.8 ? 'text-green-400' : prob >= 0.6 ? 'text-amber-400' : 'text-red-400'
              }`}>{(prob * 100).toFixed(0)}%</p>
            </div>
          )}
          {hasPercentiles && (
            <div>
              <div className="flex items-center gap-1 mb-2">
                <p className="text-xs text-slate-400 font-medium">Outcome Range (6-month windows)</p>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.mc_percentiles)} />
              </div>
              <div className="grid grid-cols-3 gap-2">
                {([['p5', p5], ['p50', p50], ['p95', p95]] as [string, number | undefined][]).map(([label, val]) =>
                  val != null ? (
                    <div key={label} className="bg-navy rounded-xl px-3 py-2 text-center">
                      <p className="text-xs text-slate-500 mb-0.5">{label}</p>
                      <p className={`text-sm font-medium ${val >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {val >= 0 ? '+' : ''}{val.toFixed(1)}%
                      </p>
                    </div>
                  ) : null
                )}
              </div>
            </div>
          )}
          {sharpe != null && (
            <div className="bg-navy rounded-xl px-3 py-2">
              <div className="flex items-center gap-1 mb-1">
                <p className="text-xs text-slate-400">Median Sharpe Ratio</p>
                <InfoBtn onClick={() => onInfo?.(GLOSSARY.sharpe_ratio)} />
              </div>
              <p className="text-sm font-medium text-white">{sharpe.toFixed(2)}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Collapsible section wrapper ───────────────────────────────────────────────

function CollapsibleSection({
  title, badge, open, onToggle, children,
}: {
  title: string
  badge?: React.ReactNode
  open: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left"
        onClick={onToggle}
      >
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-white">{title}</p>
          {badge}
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
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
  const [info,        setInfo]        = useState<InfoEntry | null>(null)
  const [sweepOpen,   setSweepOpen]   = useState(false)
  const [evolveOpen,  setEvolveOpen]  = useState(false)
  const [wfOpen,      setWfOpen]      = useState(false)
  const [mcOpen,      setMcOpen]      = useState(false)
  const [progress,    setProgress]    = useState<EvolutionProgress | null>(null)

  const fastPollRef     = useRef<ReturnType<typeof setInterval> | null>(null)
  const slowPollRef     = useRef<ReturnType<typeof setInterval> | null>(null)
  const progressPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const [s, r, sw, ev] = await Promise.all([
        getOptimizerSummary(),
        getOptimizerRunning(),
        getSweepResults(),
        getEvolveResults(),
      ])
      setSummary(s)

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

  useEffect(() => {
    fetchAll()
    slowPollRef.current = setInterval(fetchAll, 15_000)
    return () => {
      clearInterval(slowPollRef.current!)
      clearInterval(fastPollRef.current!)
      clearInterval(progressPollRef.current!)
    }
  }, [fetchAll])

  useEffect(() => {
    if (fastPollRef.current) clearInterval(fastPollRef.current)
    if (running) fastPollRef.current = setInterval(fetchAll, 5_000)
  }, [running, fetchAll])

  useEffect(() => {
    if (progressPollRef.current) clearInterval(progressPollRef.current)
    if (running && mode === 'evolve') {
      const poll = () => getOptimizerProgress().then(setProgress).catch(() => null)
      poll()
      progressPollRef.current = setInterval(poll, 3_000)
    } else {
      setProgress(null)
    }
  }, [running, mode])

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

  const mc  = summary?.monte_carlo   as Record<string, unknown> | null
  const wf  = summary?.walk_forward  as Record<string, unknown> | null
  const rec = summary?.reconciliation as Record<string, unknown> | null

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-slate-400">Loading…</div>
  }

  return (
    <div className="p-4 space-y-4 pb-4">
      <h1 className="text-lg font-bold text-white pt-2">Optimizer</h1>

      {info && <InfoModal title={info.title} body={info.body} onClose={() => setInfo(null)} />}

      {error && (
        <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

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
          onInfo={() => setInfo(GLOSSARY.fitness_score)}
        />
        <SummaryCard
          label="Monte Carlo"
          value={<VerdictBadge verdict={mc?.verdict as string} />}
          sub={mc ? `Median Sharpe: ${(mc.median_sharpe as number)?.toFixed(2) ?? '—'}` : 'No results yet'}
          onInfo={() => setInfo(GLOSSARY.monte_carlo)}
        />
        <SummaryCard
          label="Walk-Forward"
          value={wf ? <VerdictBadge verdict={wf.verdict as string} /> : <span className="text-slate-500 text-xs">—</span>}
          sub={wf ? `Robustness: ${((wf.robustness_score as number) * 100).toFixed(0)}%` : 'No results yet'}
          onInfo={() => setInfo(GLOSSARY.walk_forward)}
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
              Running…
            </span>
          )}
        </div>

        {/* Evolution progress bar */}
        {running && mode === 'evolve' && progress?.running && progress.generation != null && progress.total_generations != null && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>Generation {progress.generation} / {progress.total_generations}</span>
              <span>{progress.elapsed_sec != null ? `${Math.floor(progress.elapsed_sec)}s` : ''}</span>
            </div>
            <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
              <div
                className="h-full bg-green-500 rounded-full transition-all duration-700"
                style={{ width: `${(progress.generation / progress.total_generations) * 100}%` }}
              />
            </div>
            {progress.best_fitness != null && (
              <div className="flex gap-4 text-xs text-slate-500">
                <span>Best fitness: <span className="text-green-400 font-mono">{progress.best_fitness.toFixed(3)}</span></span>
                {progress.best_return_pct != null && (
                  <span>Return: <span className={progress.best_return_pct >= 0 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>{progress.best_return_pct >= 0 ? '+' : ''}{progress.best_return_pct.toFixed(1)}%</span></span>
                )}
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-2">
          <select
            value={mode}
            onChange={e => setMode(e.target.value as OptMode)}
            className="flex-1 bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-green-500"
          >
            {(Object.entries(MODE_LABELS) as [OptMode, string][]).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          {MODE_GLOSSARY_KEYS[mode] && (
            <button
              onClick={() => setInfo(GLOSSARY[MODE_GLOSSARY_KEYS[mode]!])}
              className="text-slate-500 hover:text-slate-300 text-base leading-none flex-shrink-0 px-1"
            >ⓘ</button>
          )}
        </div>

        {/* Fitness goal selector — only shown for Evolve mode */}
        {mode === 'evolve' && (
          <div className="space-y-2">
            <p className="text-xs text-slate-400 font-medium">Fitness Goal</p>
            <div className="grid grid-cols-2 gap-2">
              {FITNESS_GOALS.map(g => (
                <div
                  key={g.id}
                  onClick={() => setFitnessGoal(g.id)}
                  className={`rounded-xl p-3 text-left border transition-colors cursor-pointer ${
                    fitnessGoal === g.id
                      ? g.activeCls
                      : 'bg-navy border-border text-slate-400 hover:border-slate-500'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold">{g.icon} {g.label}</p>
                    <button
                      onClick={e => { e.stopPropagation(); setInfo(GLOSSARY[`fitness_${g.id}`]) }}
                      className="text-slate-500 hover:text-slate-300 text-xs leading-none ml-1 shrink-0"
                    >ⓘ</button>
                  </div>
                  <p className="text-xs mt-0.5 opacity-70 leading-snug">{g.desc}</p>
                </div>
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

      {/* Walk-Forward detail */}
      {wf && (
        <WalkForwardSection data={wf} onInfo={setInfo} />
      )}

      {/* Monte Carlo detail */}
      {mc && (
        <MonteCarloSection data={mc} onInfo={setInfo} />
      )}

      {/* Sweep results — collapsible */}
      {sweepData && (
        <CollapsibleSection
          title="Sweep Results"
          badge={sweepData.timestamp ? <span className="text-xs text-slate-500">{fmtTime(sweepData.timestamp)}</span> : undefined}
          open={sweepOpen}
          onToggle={() => setSweepOpen(o => !o)}
        >
          <SweepSection data={sweepData} onInfo={setInfo} />
        </CollapsibleSection>
      )}

      {/* Evolve results — collapsible */}
      {evolveData && (
        <CollapsibleSection
          title="Evolution Results"
          badge={evolveData.total_evaluated > 0 ? <span className="text-xs text-slate-500">{evolveData.total_evaluated} evaluated</span> : undefined}
          open={evolveOpen}
          onToggle={() => setEvolveOpen(o => !o)}
        >
          <EvolveSection data={evolveData} onInfo={setInfo} />
        </CollapsibleSection>
      )}

      {/* No results placeholder */}
      {!sweepData && !evolveData && !summary?.best_genome && (
        <div className="bg-card rounded-2xl p-6 border border-border text-center text-slate-500 text-sm">
          No results yet — run Sweep or Evolve to see results here.
        </div>
      )}
    </div>
  )
}
