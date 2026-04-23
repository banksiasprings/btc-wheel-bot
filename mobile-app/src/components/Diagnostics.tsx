import { useState, useEffect, useCallback } from 'react'
import {
  getOptimizerSummary,
  getOptimizerRunning,
  getSweepResults,
  runOptimizer,
  OptimizerSummary,
  SweepResults,
  SweepEntry,
} from '../api'
import InfoModal from './InfoModal'
import ConfigSelector from './ConfigSelector'
import { GLOSSARY } from '../lib/glossary'

// ── Formatting helpers ─────────────────────────────────────────────────────────

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch { return '—' }
}

// ── Sensitivity label ─────────────────────────────────────────────────────────

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

// ── Param row ─────────────────────────────────────────────────────────────────

function ParamRow({ param, entries, bestValue }: { param: string; entries: SweepEntry[]; bestValue: number | undefined }) {
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
              best={typeof bestValue === 'number' && bestValue % 1 !== 0 ? bestValue.toFixed(3) : bestValue}
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
                <span className={`text-xs font-mono w-16 text-right shrink-0 ${isBest ? 'text-green-400 font-bold' : 'text-slate-500'}`}>
                  {e.value % 1 !== 0 ? e.value.toFixed(3) : e.value}
                </span>
                <div className="flex-1 bg-slate-800 rounded-sm h-3 overflow-hidden">
                  <div
                    className="h-full rounded-sm"
                    style={{ width: `${pct}%`, background: isBest ? '#22c55e' : '#38bdf888' }}
                  />
                </div>
                <span className={`text-xs font-mono w-10 shrink-0 ${isBest ? 'text-green-400 font-bold' : 'text-slate-500'}`}>
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

// ── Verdict badge ─────────────────────────────────────────────────────────────

function VerdictBadge({ verdict }: { verdict?: string | null }) {
  if (!verdict) return <span className="text-slate-500 text-xs">—</span>
  const isGood = /robust|calibrated/i.test(verdict)
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${isGood ? 'bg-green-900 text-green-300' : 'bg-amber-900 text-amber-300'}`}>
      {verdict}
    </span>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function Diagnostics() {
  const [summary, setSummary]       = useState<OptimizerSummary | null>(null)
  const [sweepData, setSweepData]   = useState<SweepResults | null>(null)
  const [running, setRunning]       = useState(false)
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState('')
  const [info, setInfo]             = useState<{ title: string; body: string } | null>(null)
  const [sweepConfig, setSweepConfig]     = useState<string | null>(null)
  const [reconcileConfig, setReconcileConfig] = useState<string | null>(null)
  const [sweepLaunching, setSweepLaunching]     = useState(false)
  const [sweepMsg, setSweepMsg]                 = useState('')
  const [reconcileLaunching, setReconcileLaunching] = useState(false)
  const [reconcileMsg, setReconcileMsg]             = useState('')
  const [sweepOpen, setSweepOpen]     = useState(true)
  const [reconcileOpen, setReconcileOpen] = useState(false)

  const fetchAll = useCallback(async () => {
    try {
      const [s, r, sw] = await Promise.all([
        getOptimizerSummary(),
        getOptimizerRunning(),
        getSweepResults(),
      ])
      setSummary(s)
      setRunning(r.running)
      setSweepData(sw?.params?.length ? sw : null)
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 15_000)
    return () => clearInterval(id)
  }, [fetchAll])

  async function handleRunSweep() {
    setSweepLaunching(true)
    setSweepMsg('')
    try {
      const r = await runOptimizer('sweep', undefined, undefined, sweepConfig)
      setSweepMsg(`Started (PID ${r.pid}) — results will appear when done`)
      setRunning(true)
    } catch (e) {
      setSweepMsg(String(e))
    } finally {
      setSweepLaunching(false)
    }
  }

  async function handleRunReconcile() {
    setReconcileLaunching(true)
    setReconcileMsg('')
    try {
      const r = await runOptimizer('reconcile', undefined, undefined, reconcileConfig)
      setReconcileMsg(`Started (PID ${r.pid}) — results will appear when done`)
      setRunning(true)
    } catch (e) {
      setReconcileMsg(String(e))
    } finally {
      setReconcileLaunching(false)
    }
  }

  const rec = summary?.reconciliation as Record<string, unknown> | null

  // Sort sweep params by sensitivity (most sensitive first)
  const sortedSweepParams = sweepData
    ? [...sweepData.params].sort(
        (a, b) => sensitivityScore(sweepData.results[b] ?? []) - sensitivityScore(sweepData.results[a] ?? [])
      )
    : []

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-slate-400">Loading…</div>
  }

  return (
    <div className="p-4 space-y-4 pb-6">
      <h1 className="text-lg font-bold text-white pt-2">Diagnostics</h1>

      {info && <InfoModal title={info.title} body={info.body} onClose={() => setInfo(null)} />}

      {error && (
        <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

      {running && (
        <div className="bg-amber-950 border border-amber-800 rounded-xl px-4 py-3 text-amber-300 text-sm flex items-center gap-2">
          <span className="w-2 h-2 bg-amber-400 rounded-full animate-pulse" />
          Optimizer running — results update automatically
        </div>
      )}

      {/* ── Parameter Sweep ────────────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl border border-border overflow-hidden">
        <button
          className="w-full flex items-center justify-between px-4 py-3 text-left"
          onClick={() => setSweepOpen(o => !o)}
        >
          <div>
            <p className="text-sm font-semibold text-white">Parameter Sweep</p>
            <p className="text-xs text-slate-400 mt-0.5">
              {sweepData
                ? `${sweepData.params.length} params swept${sweepData.timestamp ? ` · ${fmtTime(sweepData.timestamp)}` : ''}`
                : 'Tests each parameter independently across its value range'}
            </p>
          </div>
          <span className="text-slate-500 text-xs">{sweepOpen ? '▲' : '▼'}</span>
        </button>

        {sweepOpen && (
          <div className="px-4 pb-4 space-y-3 border-t border-border/40">
            <div className="pt-3">
              <ConfigSelector
                value={sweepConfig}
                onChange={setSweepConfig}
                label="Sweeping config"
                showStats
              />
            </div>

            <p className="text-xs text-slate-500 leading-snug">
              Sweeps each strategy parameter independently and shows how sensitive the bot's fitness is to each setting.
              Sensitive parameters (⚡) need precise tuning — flat ones (-) can be set to any reasonable value.
            </p>

            {/* Metric legend */}
            <div className="flex gap-3 flex-wrap">
              {([
                ['Fitness', 'fitness_score'],
                ['Sharpe',  'sharpe_ratio'],
                ['Return %','return_pct'],
                ['Win Rate','win_rate'],
              ] as [string, string][]).map(([label, key]) => (
                <button
                  key={key}
                  onClick={() => setInfo(GLOSSARY[key])}
                  className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-0.5"
                >
                  {label} <span className="opacity-70">ⓘ</span>
                </button>
              ))}
            </div>

            {sweepMsg && (
              <p className={`text-xs px-3 py-2 rounded-lg border ${
                sweepMsg.startsWith('Started')
                  ? 'bg-green-950 border-green-800 text-green-300'
                  : 'bg-red-950 border-red-800 text-red-300'
              }`}>{sweepMsg}</p>
            )}

            <button
              onClick={handleRunSweep}
              disabled={running || sweepLaunching}
              className="w-full bg-amber-700 hover:bg-amber-600 disabled:opacity-40 text-white font-semibold py-3 rounded-xl text-sm"
            >
              {sweepLaunching ? 'Launching…' : running ? 'Already Running…' : 'Run Sweep'}
            </button>

            {/* Sweep results */}
            {sweepData && sortedSweepParams.length > 0 && (
              <div className="space-y-1.5 pt-1">
                <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">Results</p>
                {(() => {
                  let best: { param: string; value: number; fitness: number } | null = null
                  for (const p of sweepData.params) {
                    for (const e of sweepData.results[p] ?? []) {
                      if (!best || e.fitness > best.fitness) best = { param: p, value: e.value, fitness: e.fitness }
                    }
                  }
                  return best ? (
                    <div className="bg-navy rounded-xl px-3 py-2 text-xs text-slate-400">
                      Best: <span className="text-green-400 font-medium">
                        {best.param.replace(/_/g, ' ')}={best.value % 1 !== 0 ? best.value.toFixed(3) : best.value}
                      </span>{' '}
                      (fitness <span className="text-green-400 font-medium">{best.fitness.toFixed(2)}</span>)
                    </div>
                  ) : null
                })()}
                <div className="space-y-1">
                  {sortedSweepParams.map(param => (
                    <ParamRow
                      key={param}
                      param={param}
                      entries={sweepData.results[param] ?? []}
                      bestValue={sweepData.best_per_param[param]?.value}
                    />
                  ))}
                </div>
              </div>
            )}

            {!sweepData && !running && (
              <p className="text-xs text-slate-500 text-center pt-1">
                No sweep results yet — run a sweep to see parameter sensitivity here.
              </p>
            )}
          </div>
        )}
      </div>

      {/* ── Reconcile ─────────────────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl border border-border overflow-hidden">
        <button
          className="w-full flex items-center justify-between px-4 py-3 text-left"
          onClick={() => setReconcileOpen(o => !o)}
        >
          <div>
            <p className="text-sm font-semibold text-white">Reconcile</p>
            <p className="text-xs text-slate-400 mt-0.5">
              {rec
                ? <><VerdictBadge verdict={rec.accuracy as string} /></>
                : 'Checks if the model predictions match actual trade results'}
            </p>
          </div>
          <span className="text-slate-500 text-xs">{reconcileOpen ? '▲' : '▼'}</span>
        </button>

        {reconcileOpen && (
          <div className="px-4 pb-4 space-y-3 border-t border-border/40">
            <div className="pt-3">
              <ConfigSelector
                value={reconcileConfig}
                onChange={setReconcileConfig}
                label="Reconciling config"
                showStats
              />
            </div>

            <div className="bg-navy rounded-xl px-3 py-3">
              <p className="text-xs text-slate-300 leading-relaxed">
                This checks whether the Black-Scholes model's premium predictions match your actual closed trades.
                A well-calibrated model (low RMSE, low bias) means the backtest is trustworthy and the bot's signals are realistic.
                <span className="block mt-1 text-slate-500">Run this before going live.</span>
              </p>
            </div>

            {/* Reconcile results */}
            {rec && (
              <div className="space-y-2">
                <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">Results</p>
                <div className="grid grid-cols-2 gap-2">
                  <div className="bg-navy rounded-xl px-3 py-2">
                    <p className="text-xs text-slate-400 mb-1">Verdict</p>
                    <VerdictBadge verdict={rec.accuracy as string} />
                  </div>
                  {(rec.premium_rmse as number) != null && (
                    <div className="bg-navy rounded-xl px-3 py-2">
                      <p className="text-xs text-slate-400 mb-1">RMSE</p>
                      <p className="text-sm font-medium text-white">${(rec.premium_rmse as number).toFixed(0)}</p>
                    </div>
                  )}
                  {(rec.premium_bias as number) != null && (
                    <div className="bg-navy rounded-xl px-3 py-2">
                      <p className="text-xs text-slate-400 mb-1">Bias</p>
                      <p className={`text-sm font-medium ${Math.abs(rec.premium_bias as number) < 10 ? 'text-green-400' : 'text-amber-400'}`}>
                        ${(rec.premium_bias as number).toFixed(0)}
                      </p>
                    </div>
                  )}
                  {(rec.win_rate_accuracy as number) != null && (
                    <div className="bg-navy rounded-xl px-3 py-2">
                      <p className="text-xs text-slate-400 mb-1">Win Rate Accuracy</p>
                      <p className="text-sm font-medium text-white">
                        {((rec.win_rate_accuracy as number) * 100).toFixed(1)}%
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {reconcileMsg && (
              <p className={`text-xs px-3 py-2 rounded-lg border ${
                reconcileMsg.startsWith('Started')
                  ? 'bg-green-950 border-green-800 text-green-300'
                  : 'bg-red-950 border-red-800 text-red-300'
              }`}>{reconcileMsg}</p>
            )}

            <button
              onClick={handleRunReconcile}
              disabled={running || reconcileLaunching}
              className="w-full bg-sky-700 hover:bg-sky-600 disabled:opacity-40 text-white font-semibold py-3 rounded-xl text-sm"
            >
              {reconcileLaunching ? 'Launching…' : running ? 'Already Running…' : 'Run Reconcile'}
            </button>

            {!rec && !running && (
              <p className="text-xs text-slate-500 text-center pt-1">
                No reconcile results yet — run reconcile after you have at least a few closed trades.
              </p>
            )}
          </div>
        )}
      </div>

      {/* ── Best fitness summary ────────────────────────────────────────── */}
      {summary?.best_fitness != null && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1">
              <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">Best Sweep Fitness</p>
              <button onClick={() => setInfo(GLOSSARY.fitness_score)} className="text-slate-500 hover:text-slate-300 text-xs leading-none">ⓘ</button>
            </div>
            <p className="text-lg font-bold text-green-400 font-mono">{summary.best_fitness.toFixed(3)}</p>
          </div>
          {summary.sweep_params_count > 0 && (
            <p className="text-xs text-slate-500 mt-1">{summary.sweep_params_count} params swept</p>
          )}
        </div>
      )}
    </div>
  )
}
