import { useState, useEffect, useCallback, useRef } from 'react'
import {
  getEvolveResultsAll, EvolveAllResults, EvolveGoal,
  getFarmStatus, FarmStatus, BotFarmEntry,
  listConfigs, NamedConfig, ConfigStatus,
  runOptimizer, saveConfig as apiSaveConfig,
  assignBotConfig, promoteConfig,
  startPaperTesting, stopPaperTesting,
  startFarm, stopFarm,
  getSweepResults, SweepResults,
  getOptimizerSummary, OptimizerSummary,
  updateConfigParams,
  getOptimizerProgress, EvolutionProgress,
  getEvolveResults, EvolveResults, EvolveGenome,
  getOptimizerRunning, WalkForwardResults, MonteCarloResults, getWalkForwardResults, getMonteCarloResults,
} from '../api'
import {
  LineChart, Line,
  ScatterChart, Scatter,
  BarChart, Bar, Cell, ReferenceLine,
  XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts'
import ConfigSelector from './ConfigSelector'

// ── Types ─────────────────────────────────────────────────────────────────────

type StepStatus = 'not_started' | 'in_progress' | 'complete' | 'locked'

const EVOLVE_GOALS: { id: EvolveGoal; icon: string; label: string; desc?: string }[] = [
  { id: 'balanced',     icon: '🎯', label: 'Balanced'                                            },
  { id: 'max_yield',    icon: '🚀', label: 'Max Yield'                                           },
  { id: 'safest',       icon: '🛡', label: 'Safest'                                              },
  { id: 'sharpe',       icon: '⚖️', label: 'Sharpe'                                              },
  { id: 'capital_roi',  icon: '📊', label: 'Capital ROI'                                         },
  { id: 'daily_trader', icon: '⚡', label: 'Daily Trader', desc: 'max trades · test the pipeline' },
]

// Hardcoded "reckless" params for the Daily Trader quick-start preset.
// Very low entry barriers = many signals = trade flow within hours not weeks.
const DAILY_TRADER_PARAMS: Record<string, unknown> = {
  iv_rank_threshold:        0.05,   // trade almost always
  target_delta_min:         0.20,   // aggressive strikes
  target_delta_max:         0.35,
  approx_otm_offset:        0.05,
  min_dte:                  1,      // enter same-day / next-day expiries
  max_dte:                  7,      // weekly options only
  max_equity_per_leg:       0.10,   // deploy up to 10% per trade
  premium_fraction_of_spot: 0.002,  // accept tiny premiums
  iv_rank_window_days:      30,     // short IV lookback
  min_free_equity_fraction: 0.10,   // keep 10% buffer
}

const SWEEP_PARAMS: { key: string; label: string }[] = [
  { key: 'iv_rank_threshold',        label: 'IV Rank Threshold'  },
  { key: 'target_delta_min',         label: 'Min Delta'          },
  { key: 'target_delta_max',         label: 'Max Delta'          },
  { key: 'min_dte',                  label: 'Min DTE'            },
  { key: 'max_dte',                  label: 'Max DTE'            },
  { key: 'max_equity_per_leg',       label: 'Max Equity / Leg'   },
  { key: 'premium_fraction_of_spot', label: 'Premium Fraction'   },
]

// Per-generation data point accumulated while evolution runs
interface GenPoint { gen: number; bestFitness: number; genFitness: number }

// Plain-English explanation of why the winner scored highest
function goalExplainer(goal: EvolveGoal, w: EvolveGenome): string {
  const ret = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`
  switch (goal) {
    case 'daily_trader':
      return `Most active genome in the pool — ${w.num_cycles} trades over the backtest period. Low entry barriers mean frequent signals while keeping win rate at ${w.win_rate.toFixed(0)}% and drawdown under ${w.drawdown.toFixed(1)}%.`
    case 'max_yield':
      return `Pure return maximiser. ${ret(w.return_pct)} total return across ${w.num_cycles} trades. Sharpe ${w.sharpe.toFixed(2)} — the optimizer accepted higher risk in exchange for raw yield.`
    case 'safest':
      return `Capital preservation priority. ${w.win_rate.toFixed(0)}% win rate with only ${w.drawdown.toFixed(1)}% max drawdown. Returns ${ret(w.return_pct)} — steady income, minimal swings.`
    case 'sharpe':
      return `Best risk-adjusted performance. Sharpe ${w.sharpe.toFixed(2)} — the highest return per unit of volatility in the population. Drawdown held at ${w.drawdown.toFixed(1)}%.`
    case 'capital_roi':
      return `Capital efficiency winner. ${ret(w.return_pct)} return on deployed margin with Sharpe ${w.sharpe.toFixed(2)}. The optimizer balanced deployment aggressiveness against drawdown risk.`
    default:
      return `Balanced across return, risk, and win rate. ${ret(w.return_pct)} return, Sharpe ${w.sharpe.toFixed(2)}, ${w.win_rate.toFixed(0)}% win rate, ${w.drawdown.toFixed(1)}% max drawdown.`
  }
}

// ── Helper components ─────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: StepStatus }) {
  if (status === 'complete')    return <span className="text-green-400 text-lg">✅</span>
  if (status === 'in_progress') return <span className="text-amber-400 text-lg animate-pulse">🔄</span>
  if (status === 'locked')      return <span className="text-slate-600 text-lg">🔒</span>
  return <span className="text-slate-500 text-lg">⬜</span>
}

function StepConnector() {
  return (
    <div className="flex flex-col items-center py-1">
      <div className="w-0.5 h-6 bg-slate-700" />
      <div className="text-slate-600 text-xs">↓</div>
    </div>
  )
}

function ReadinessProgressBar({ score, total }: { score: number; total: number }) {
  const pct = total > 0 ? (score / total) * 100 : 0
  const isReady = score === total
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className={isReady ? 'text-green-400 font-medium' : 'text-slate-400'}>
          {isReady ? '✅ Ready for live' : `${score}/${total} checks passed`}
        </span>
        <span className="text-slate-500 font-mono">{score}/{total}</span>
      </div>
      <div className="h-3 rounded-full bg-slate-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            isReady ? 'bg-green-500' : score >= 5 ? 'bg-orange-400' : 'bg-amber-500'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── Step 1 — Evolve ───────────────────────────────────────────────────────────

function StepEvolve({
  open, onToggle, evolveAll, onSaved,
}: {
  open: boolean
  onToggle: () => void
  evolveAll: EvolveAllResults | null
  onSaved: (name: string) => void
}) {
  const [goal, setGoal]               = useState<EvolveGoal>('capital_roi')
  const [view, setView]               = useState<'setup' | 'running' | 'results'>('setup')
  const [launching, setLaunching]     = useState(false)
  const [launchErr, setLaunchErr]     = useState('')
  const [progress, setProgress]       = useState<EvolutionProgress | null>(null)
  const [genHistory, setGenHistory]   = useState<GenPoint[]>([])
  const [leaderboard, setLeaderboard] = useState<EvolveResults | null>(null)
  // Save
  const [saveName, setSaveName]   = useState('')
  const [saveNotes, setSaveNotes] = useState('')
  const [saving, setSaving]       = useState(false)
  const [saveMsg, setSaveMsg]     = useState('')
  // Seed config (selective evolution)
  const [seedConfigName, setSeedConfigName] = useState<string>('')
  const [availableConfigs, setAvailableConfigs] = useState<NamedConfig[]>([])

  const goalData = evolveAll?.[goal]
  const hasData  = (goalData?.version ?? 0) > 0 && goalData?.current != null
  const cur      = goalData?.current
  const goalMeta = EVOLVE_GOALS.find(g => g.id === goal)

  const totalGens  = progress?.total_generations ?? 8
  const currentGen = progress?.generation ?? 0
  const pct        = totalGens > 0 ? (currentGen / totalGens) * 100 : 0
  const elapsed    = progress?.elapsed_sec ?? null
  const elapsedStr = elapsed != null
    ? `${Math.floor(elapsed / 60)}m ${Math.round(elapsed % 60)}s`
    : null

  const status: StepStatus = view === 'running' ? 'in_progress'
    : hasData ? 'complete' : 'not_started'

  // Detect if an evolution is already running on mount/open
  useEffect(() => {
    if (!open || view !== 'setup') return
    getOptimizerProgress().then(p => {
      if (p.running) setView('running')
    }).catch(() => {})
  }, [open])


  // Load available configs for seed picker
  useEffect(() => {
    if (!open) return
    listConfigs(false).then(cs => {
      setAvailableConfigs(cs.filter(c => c.status !== 'archived'))
    }).catch(() => {})
  }, [open])

  // Poll while running
  useEffect(() => {
    if (view !== 'running') return
    let cancelled = false

    async function poll() {
      try {
        const p = await getOptimizerProgress()
        if (cancelled) return
        setProgress(p)

        if (p.generation != null) {
          setGenHistory(prev => {
            if (prev.find(g => g.gen === p.generation)) return prev
            return [...prev, {
              gen: p.generation!,
              bestFitness: +(p.best_fitness ?? 0).toFixed(4),
              genFitness:  +(p.gen_best_fitness ?? 0).toFixed(4),
            }]
          })
        }

        if (!p.running && p.completed) {
          const results = await getEvolveResults()
          if (!cancelled) {
            setLeaderboard(results)
            const ts = new Date().toISOString().slice(0, 10).replace(/-/g, '')
            setSaveName(`${goal}_${ts}`)
            setSaveMsg('')
            setView('results')
          }
        }
      } catch { /* ignore polling errors */ }
    }

    poll()
    const id = setInterval(poll, 3_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [view, goal])

  async function handleRun() {
    setLaunching(true)
    setLaunchErr('')
    setGenHistory([])
    setProgress(null)
    setLeaderboard(null)
    try {
      await runOptimizer('evolve', undefined, goal, undefined, seedConfigName || null)
      setView('running')
    } catch (e) {
      setLaunchErr(String(e))
    } finally {
      setLaunching(false)
    }
  }

  async function handleSaveConfig() {
    if (!saveName.trim()) return
    const winner = leaderboard?.top_genomes?.[0]
    setSaving(true)
    setSaveMsg('')
    try {
      await apiSaveConfig({
        name: saveName.trim(),
        source: 'evolved',
        notes: saveNotes.trim() || `Evolved — goal: ${goal}`,
        fitness: winner?.fitness ?? null,
        total_return_pct: winner?.return_pct ?? null,
        sharpe: winner?.sharpe ?? null,
        params: {},
      })
      setSaveMsg(`✅ Saved as '${saveName.trim()}' — go to Step 2 to validate`)
      onSaved(saveName.trim())
    } catch (e) {
      setSaveMsg(String(e))
    } finally {
      setSaving(false)
    }
  }

  const winner = leaderboard?.top_genomes?.[0]

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      status === 'complete' ? 'border-green-800'
      : status === 'in_progress' ? 'border-amber-800'
      : 'border-border'
    }`}>
      {/* ── Header (always visible) ── */}
      <button className="w-full flex items-center gap-3 px-4 py-3 text-left" onClick={onToggle}>
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-white uppercase tracking-wide">Step 1 · Evolve</span>
            {view === 'running' && (
              <span className="text-xs text-amber-400 animate-pulse">running…</span>
            )}
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            {view === 'running'
              ? `Gen ${currentGen}/${totalGens} · ${goalMeta?.label}`
              : view === 'results' && winner
              ? `🏆 Done — Fitness ${winner.fitness.toFixed(3)} · ${winner.return_pct >= 0 ? '+' : ''}${winner.return_pct.toFixed(1)}% · Sharpe ${winner.sharpe.toFixed(2)}`
              : hasData && cur
              ? `Last: ${goalMeta?.label} · Fitness ${cur.fitness.toFixed(2)} · ${cur.return_pct >= 0 ? '+' : ''}${cur.return_pct.toFixed(1)}%`
              : 'Find the best config via genetic evolution'}
          </p>
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {/* ── SETUP VIEW ── */}
      {open && view === 'setup' && (
        <div className="px-4 pb-4 space-y-3">
          {/* Goal grid */}
          <div className="space-y-1.5">
            <p className="text-xs text-slate-400 font-medium">Fitness Goal</p>
            <div className="grid grid-cols-2 gap-2">
              {EVOLVE_GOALS.map((g, idx) => (
                <button
                  key={g.id}
                  onClick={() => setGoal(g.id)}
                  className={`rounded-xl p-2.5 text-left border transition-colors ${
                    idx === EVOLVE_GOALS.length - 1 && EVOLVE_GOALS.length % 2 !== 0 ? 'col-span-2' : ''
                  } ${
                    goal === g.id
                      ? 'bg-amber-900 border-amber-600 text-white'
                      : 'bg-navy border-border text-slate-400 hover:border-slate-500'
                  }`}
                >
                  <p className="text-xs font-medium">{g.icon} {g.label}</p>
                  {g.desc && <p className="text-xs text-slate-500 mt-0.5">{g.desc}</p>}
                </button>
              ))}
            </div>
          </div>

          {/* Seed config picker — optional selective evolution */}
          <div className="space-y-1.5">
            <p className="text-xs text-slate-400 font-medium">Seed From Config <span className="text-slate-600">(optional)</span></p>
            <p className="text-xs text-slate-600">Pick an existing config to focus the search near those parameters. Evolution still explores new territory — ~40% of the population starts random.</p>
            <select
              value={seedConfigName}
              onChange={e => setSeedConfigName(e.target.value)}
              className="w-full bg-navy border border-border rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500 appearance-none"
            >
              <option value="">— Start from scratch —</option>
              {availableConfigs.map(c => (
                <option key={c.name} value={c.name}>
                  {c.name}{c.fitness != null ? ` · fit ${c.fitness.toFixed(2)}` : ''}{c.source === 'evolved' ? ' 🧬' : c.source === 'manual' ? ' ✍️' : ''}
                </option>
              ))}
            </select>
            {seedConfigName && (
              <p className="text-xs text-amber-400">🧬 Seeding from <span className="font-mono">{seedConfigName}</span> — evolution will mutate around this baseline</p>
            )}
          </div>

          {launchErr && (
            <p className="text-xs px-3 py-2 rounded-lg border bg-red-950 border-red-800 text-red-300">{launchErr}</p>
          )}

          <button
            onClick={handleRun}
            disabled={launching}
            className="w-full bg-amber-700 hover:bg-amber-600 disabled:opacity-40 text-white font-semibold py-3 rounded-xl text-sm"
          >
            {launching ? 'Launching…' : seedConfigName ? `Evolve From '${seedConfigName}'` : `Run ${goalMeta?.label ?? ''} Evolution`}
          </button>

          {/* Previous result summary */}
          {hasData && cur && (
            <div className="bg-navy rounded-xl px-3 py-2.5 space-y-1">
              <p className="text-xs text-slate-500 uppercase tracking-wide">Previous Run · {goalMeta?.label}</p>
              <div className="flex flex-wrap gap-3 text-xs">
                <span>Fitness <span className="text-green-400 font-mono font-bold">{cur.fitness.toFixed(3)}</span></span>
                <span>Return <span className={`font-mono font-bold ${cur.return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>{cur.return_pct >= 0 ? '+' : ''}{cur.return_pct.toFixed(1)}%</span></span>
                <span>Sharpe <span className="text-white font-mono">{cur.sharpe.toFixed(2)}</span></span>
                <span>Win <span className="text-white font-mono">{cur.win_rate.toFixed(0)}%</span></span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── RUNNING VIEW ── */}
      {open && view === 'running' && (
        <div className="px-4 pb-4 space-y-3">
          {/* Header + progress bar */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-amber-400 font-semibold">{goalMeta?.icon} {goalMeta?.label} Evolution</span>
              {elapsedStr && <span className="text-slate-500">{elapsedStr}</span>}
            </div>
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>Generation {currentGen} / {totalGens}</span>
              <span>{currentGen > 0 ? `${Math.round(pct)}%` : 'Starting…'}</span>
            </div>
            <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
              <div
                className="h-full bg-amber-500 rounded-full transition-all duration-1000"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>

          {/* Live fitness chart */}
          {genHistory.length > 0 && (
            <div>
              <p className="text-xs text-slate-500 uppercase tracking-wide mb-1.5">Fitness per Generation</p>
              <ResponsiveContainer width="100%" height={120}>
                <LineChart data={genHistory} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                  <XAxis dataKey="gen" tick={{ fontSize: 10, fill: '#64748b' }} tickCount={totalGens} />
                  <YAxis hide domain={['auto', 'auto']} />
                  <Tooltip
                    contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#fff' }}
                    formatter={(v: number, name: string) => [
                      v.toFixed(4),
                      name === 'bestFitness' ? 'Best (all time)' : 'This generation'
                    ]}
                  />
                  <Line type="monotone" dataKey="bestFitness" stroke="#22c55e" strokeWidth={2} dot={{ r: 3, fill: '#22c55e' }} isAnimationActive={false} />
                  <Line type="monotone" dataKey="genFitness" stroke="#94a3b8" strokeWidth={1.5} dot={{ r: 2, fill: '#94a3b8' }} strokeDasharray="4 2" isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
              <div className="flex gap-4 text-xs text-slate-500 mt-1 px-1">
                <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-green-500 rounded" />Best ever</span>
                <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-slate-400 rounded" style={{ borderTop: '1px dashed' }} />This gen</span>
              </div>
            </div>
          )}

          {/* Current best stats */}
          {progress?.best_fitness != null && (
            <div className="grid grid-cols-3 gap-2 text-center">
              {[
                { label: 'Fitness', value: progress.best_fitness.toFixed(3), color: 'text-amber-400' },
                { label: 'Return',  value: progress.best_return_pct != null ? `${progress.best_return_pct >= 0 ? '+' : ''}${progress.best_return_pct.toFixed(1)}%` : '—', color: (progress.best_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400' },
                { label: 'Sharpe',  value: progress.best_sharpe?.toFixed(2) ?? '—', color: 'text-white' },
              ].map(({ label, value, color }) => (
                <div key={label} className="bg-navy rounded-xl py-2">
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className={`text-sm font-bold mt-0.5 ${color}`}>{value}</p>
                </div>
              ))}
            </div>
          )}

          <p className="text-xs text-slate-600 text-center">Results will appear automatically when done</p>
        </div>
      )}

      {/* ── RESULTS VIEW ── */}
      {open && view === 'results' && (
        <div className="px-4 pb-4 space-y-3">

          {/* Banner */}
          <div className="bg-amber-900/30 border border-amber-700/50 rounded-xl px-3 py-2.5 flex items-center gap-3">
            <span className="text-2xl">🏆</span>
            <div>
              <p className="text-sm font-bold text-amber-300">Evolution Complete</p>
              <p className="text-xs text-slate-400">{goalMeta?.label} · {leaderboard?.total_evaluated ?? 0} genomes evaluated</p>
            </div>
          </div>

          {/* Winner card */}
          {winner && (
            <div className="bg-navy rounded-xl px-3 py-3 space-y-2">
              <p className="text-xs text-slate-400 uppercase tracking-wide font-medium">Winner Genome</p>
              <div className="grid grid-cols-3 gap-2 text-center">
                {[
                  { label: 'Fitness',   value: winner.fitness.toFixed(3),                                      color: 'text-amber-400' },
                  { label: 'Return',    value: `${winner.return_pct >= 0 ? '+' : ''}${winner.return_pct.toFixed(1)}%`, color: winner.return_pct >= 0 ? 'text-green-400' : 'text-red-400' },
                  { label: 'Sharpe',   value: winner.sharpe.toFixed(2),                                        color: 'text-white'     },
                  { label: 'Win Rate', value: `${winner.win_rate.toFixed(0)}%`,                                color: 'text-white'     },
                  { label: 'Max DD',   value: `-${winner.drawdown.toFixed(1)}%`,                               color: 'text-red-400'   },
                  { label: 'Trades',   value: String(winner.num_cycles),                                       color: 'text-white'     },
                ].map(({ label, value, color }) => (
                  <div key={label} className="bg-slate-900/60 rounded-lg py-2">
                    <p className="text-xs text-slate-500">{label}</p>
                    <p className={`text-xs font-bold mt-0.5 ${color}`}>{value}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Population scatter */}
          {(leaderboard?.top_genomes?.length ?? 0) > 1 && (
            <div>
              <p className="text-xs text-slate-400 uppercase tracking-wide font-medium mb-1.5">Population — top {leaderboard!.top_genomes.length} genomes</p>
              <ResponsiveContainer width="100%" height={160}>
                <ScatterChart margin={{ top: 4, right: 8, left: -16, bottom: 4 }}>
                  <XAxis dataKey="return_pct" name="Return %" type="number" tick={{ fontSize: 10, fill: '#64748b' }} label={{ value: 'Return %', position: 'insideBottomRight', offset: 0, fontSize: 10, fill: '#64748b' }} />
                  <YAxis dataKey="fitness" name="Fitness" type="number" tick={{ fontSize: 10, fill: '#64748b' }} />
                  <Tooltip
                    cursor={{ strokeDasharray: '3 3', stroke: '#334155' }}
                    contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#fff' }}
                    formatter={(v: number, name: string) => [
                      name === 'return_pct' ? `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` : v.toFixed(3),
                      name === 'return_pct' ? 'Return' : 'Fitness',
                    ]}
                  />
                  {/* Winner highlighted in amber */}
                  <Scatter data={leaderboard!.top_genomes.slice(0, 1)} fill="#f59e0b" r={7} name="Winner" />
                  {/* Rest in green, semi-transparent */}
                  <Scatter data={leaderboard!.top_genomes.slice(1)} fill="#22c55e" fillOpacity={0.45} r={4} name="Others" />
                </ScatterChart>
              </ResponsiveContainer>
              <div className="flex gap-4 text-xs text-slate-500 mt-1 px-1">
                <span className="flex items-center gap-1.5"><span className="inline-block w-2.5 h-2.5 rounded-full bg-amber-400" />winner</span>
                <span className="flex items-center gap-1.5"><span className="inline-block w-2 h-2 rounded-full bg-green-500 opacity-60" />others</span>
              </div>
            </div>
          )}

          {/* Why it won */}
          {winner && (
            <div className="bg-navy rounded-xl px-3 py-2.5">
              <p className="text-xs text-slate-400 uppercase tracking-wide font-medium mb-1">Why This Genome Won</p>
              <p className="text-xs text-slate-300 leading-relaxed">{goalExplainer(goal, winner)}</p>
            </div>
          )}

          {/* Save */}
          <div className="space-y-2">
            <p className="text-xs text-slate-400 font-medium">Save as Config</p>
            <input
              type="text" value={saveName} onChange={e => setSaveName(e.target.value)}
              placeholder="Config name"
              className="w-full bg-navy border border-border rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-green-500"
            />
            <input
              type="text" value={saveNotes} onChange={e => setSaveNotes(e.target.value)}
              placeholder="Notes (optional)"
              className="w-full bg-navy border border-border rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-green-500"
            />
            {saveMsg && (
              <p className={`text-xs px-3 py-2 rounded-lg border ${saveMsg.startsWith('✅') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-red-950 border-red-800 text-red-300'}`}>{saveMsg}</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={handleSaveConfig} disabled={saving || !saveName.trim()}
                className="flex-1 py-2.5 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-semibold"
              >
                {saving ? 'Saving…' : 'Save Config'}
              </button>
              <button
                onClick={() => { setView('setup'); setGenHistory([]); setProgress(null); setLaunchErr('') }}
                className="px-4 py-2.5 rounded-xl bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm"
              >
                Run Again
              </button>
            </div>
          </div>

        </div>
      )}
    </div>
  )
}

// ── Step 2 helpers ────────────────────────────────────────────────────────────

function WFResultsPanel({ r }: { r: WalkForwardResults }) {
  const rob = r.robustness_score ?? 0
  const isStrong     = rob >= 0.8
  const isAcceptable = rob >= 0.5
  const robColor    = isStrong ? 'text-green-400'  : isAcceptable ? 'text-amber-400'  : 'text-red-400'
  const robBarColor = isStrong ? 'bg-green-500'    : isAcceptable ? 'bg-amber-500'    : 'bg-red-500'
  const verdictBg   = isStrong ? 'bg-green-900/40 text-green-300' : isAcceptable ? 'bg-amber-900/40 text-amber-300' : 'bg-red-900/40 text-red-300'
  const IS = r.in_sample
  const OOS = r.out_of_sample
  const fmtRet = (n?: number) => n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`
  const fmtN   = (n?: number, d = 2) => n == null ? '—' : n.toFixed(d)
  return (
    <div className="space-y-2.5 pt-2">
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="text-slate-400">Robustness score</span>
          <span className={robColor}>{(rob * 100).toFixed(0)}%</span>
        </div>
        <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
          <div className={`h-full rounded-full ${robBarColor}`} style={{ width: `${Math.min(rob * 100, 100)}%` }} />
        </div>
        <div className="flex justify-between text-xs text-slate-600 mt-0.5">
          <span>0%</span><span>50%</span><span>80%</span><span>100%</span>
        </div>
      </div>
      {IS && OOS && (
        <div className="rounded-xl overflow-hidden border border-slate-700/50">
          <div className="grid grid-cols-3 text-xs text-slate-500 bg-slate-800/60 px-3 py-1.5">
            <span></span>
            <span className="text-center text-slate-300">In-Sample</span>
            <span className="text-center text-amber-400">Out-of-Sample</span>
          </div>
          {([
            ['Sharpe',   fmtN(IS.sharpe),          fmtN(OOS.sharpe)         ],
            ['Return',   fmtRet(IS.return_pct),    fmtRet(OOS.return_pct)   ],
            ['Win Rate', `${fmtN(IS.win_rate, 0)}%`, `${fmtN(OOS.win_rate, 0)}%`],
            ['Drawdown', `${fmtN(IS.max_drawdown, 1)}%`, `${fmtN(OOS.max_drawdown, 1)}%`],
            ['Trades',   String(IS.num_cycles ?? '—'), String(OOS.num_cycles ?? '—')],
          ] as [string, string, string][]).map(([label, isV, oosV]) => (
            <div key={label} className="grid grid-cols-3 text-xs px-3 py-1.5 border-t border-slate-700/30">
              <span className="text-slate-400">{label}</span>
              <span className="text-center text-white">{isV}</span>
              <span className="text-center text-amber-300">{oosV}</span>
            </div>
          ))}
        </div>
      )}
      <div className={`text-xs rounded-xl px-3 py-2.5 ${verdictBg}`}>
        {isStrong ? '✅' : isAcceptable ? '⚠️' : '❌'} {r.verdict ?? (isStrong ? 'Strong' : isAcceptable ? 'Acceptable' : 'Over-fitted')}
      </div>
    </div>
  )
}

function MCResultsPanel({ r }: { r: MonteCarloResults }) {
  const prob = r.prob_profit_pct ?? 0
  const d    = r.distributions?.return_pct
  const sD   = r.distributions?.sharpe
  const medSharpe = sD?.p50 ?? 0
  const isRobust   = medSharpe >= 1.0
  const isMarginal = medSharpe >= 0.5
  const verdictBg  = isRobust ? 'bg-green-900/40 text-green-300' : isMarginal ? 'bg-amber-900/40 text-amber-300' : 'bg-red-900/40 text-red-300'
  const probColor  = prob >= 70 ? 'text-green-400' : prob >= 50 ? 'text-amber-400' : 'text-red-400'
  const sortedRuns = (r.runs ?? [])
    .sort((a, b) => a.return_pct - b.return_pct)
    .map((run, i) => ({ i, v: +run.return_pct.toFixed(2) }))
  const fmtRet = (n?: number) => n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`
  return (
    <div className="space-y-2.5 pt-2">
      <div className="text-center py-1">
        <div className={`text-3xl font-bold ${probColor}`}>{prob.toFixed(0)}%</div>
        <div className="text-xs text-slate-400 mt-0.5">
          profitable across {r.n_runs ?? 0} random {r.sim_months ?? 6}-month windows
        </div>
      </div>
      {sortedRuns.length > 0 && (
        <div>
          <p className="text-xs text-slate-500 mb-1">Return distribution (sorted, each bar = 1 window)</p>
          <div className="h-24">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={sortedRuns} barCategoryGap={0} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
                <ReferenceLine y={0} stroke="#475569" strokeWidth={1} />
                <Bar dataKey="v" isAnimationActive={false}>
                  {sortedRuns.map((entry, i) => (
                    <Cell key={i} fill={entry.v >= 0 ? '#22c55e' : '#ef4444'} opacity={0.75} />
                  ))}
                </Bar>
                <Tooltip
                  content={({ active, payload }) =>
                    active && payload?.[0] ? (
                      <div className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-white">
                        {(payload[0].value as number) >= 0 ? '+' : ''}
                        {(payload[0].value as number).toFixed(1)}%
                      </div>
                    ) : null
                  }
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      {d && (
        <div className="grid grid-cols-3 gap-1.5">
          {([
            ['Worst', 'p5',     d.p5,  d.p5 < 0 ? 'text-red-400' : 'text-green-400'],
            ['Median', 'p50',   d.p50, d.p50 >= 0 ? 'text-green-400' : 'text-red-400'],
            ['Best',   'p95',   d.p95, 'text-green-400'],
          ] as [string, string, number, string][]).map(([label, pct, val, color]) => (
            <div key={label} className="bg-navy rounded-xl px-2 py-2 text-center">
              <div className={`text-sm font-semibold ${color}`}>{fmtRet(val)}</div>
              <div className="text-xs text-slate-500 leading-tight mt-0.5">{label}<br/>{pct}</div>
            </div>
          ))}
        </div>
      )}
      {sD && (
        <div className="grid grid-cols-2 gap-1.5 text-xs">
          <div className="bg-navy rounded-xl px-3 py-2">
            <div className="text-slate-400">Median Sharpe</div>
            <div className="text-white font-semibold mt-0.5">{medSharpe.toFixed(2)}</div>
          </div>
          <div className="bg-navy rounded-xl px-3 py-2">
            <div className="text-slate-400">Worst Sharpe (p5)</div>
            <div className={`font-semibold mt-0.5 ${sD.p5 >= 0 ? 'text-white' : 'text-red-400'}`}>{sD.p5.toFixed(2)}</div>
          </div>
        </div>
      )}
      <div className={`text-xs rounded-xl px-3 py-2.5 ${verdictBg}`}>
        {isRobust ? '✅ Robust' : isMarginal ? '⚠️ Marginal' : '❌ Fails under stress'} — median Sharpe {medSharpe.toFixed(2)}
      </div>
    </div>
  )
}

function ValidateTestCard({
  title, icon, description, state, elapsed, onRun, children,
}: {
  title: string; icon: string; description: string
  state: 'idle' | 'running' | 'done' | 'error'
  elapsed: number; onRun: () => void; children?: React.ReactNode
}) {
  const [showResults, setShowResults] = useState(false)
  useEffect(() => { if (state === 'done') setShowResults(true) }, [state])
  const fmt = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`
  return (
    <div className={`rounded-xl border overflow-hidden ${
      state === 'done'  ? 'border-green-800/60'
      : state === 'error' ? 'border-red-800/60'
      : state === 'running' ? 'border-amber-700/60'
      : 'border-slate-700'
    }`}>
      <div className="flex items-center gap-3 px-3 py-2.5 bg-navy">
        <span className="text-base">{icon}</span>
        <div className="flex-1 min-w-0">
          <span className="text-sm font-semibold text-white">{title}</span>
          {state === 'running' && <span className="text-xs text-amber-400 ml-2 animate-pulse">running · {fmt(elapsed)}</span>}
          {state === 'done'    && <span className="text-xs text-green-400 ml-2">✓ done</span>}
          {state === 'error'   && <span className="text-xs text-red-400 ml-2">✗ failed</span>}
        </div>
        {state === 'done' ? (
          <button onClick={() => setShowResults(v => !v)}
            className="text-xs px-2.5 py-1 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300">
            {showResults ? 'Hide' : 'Show'}
          </button>
        ) : (
          <button onClick={onRun} disabled={state === 'running'}
            className="text-xs px-2.5 py-1 rounded-lg bg-blue-700 hover:bg-blue-600 disabled:opacity-40 text-blue-200">
            {state === 'running' ? '…' : 'Run'}
          </button>
        )}
      </div>
      <div className="px-3 py-2 text-xs text-slate-400 leading-relaxed border-t border-slate-800">
        {description}
      </div>
      {state === 'running' && (
        <div className="px-3 py-3 flex flex-col items-center gap-2 bg-slate-900/40 border-t border-slate-800">
          <div className="flex gap-1">
            {[0,1,2,3].map(i => (
              <div key={i} className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-bounce"
                   style={{ animationDelay: `${i * 0.15}s` }} />
            ))}
          </div>
          <span className="text-xs text-slate-400">elapsed {fmt(elapsed)}</span>
        </div>
      )}
      {state === 'done' && showResults && children && (
        <div className="px-3 pb-3 border-t border-slate-800 bg-slate-900/20">
          {children}
        </div>
      )}
    </div>
  )
}

// ── Step 2 — Validate ─────────────────────────────────────────────────────────

function StepValidate({
  open, onToggle, configs,
}: {
  open: boolean
  onToggle: () => void
  configs: NamedConfig[]
}) {
  type TestState = 'idle' | 'running' | 'done' | 'error'
  const [selectedConfig, setSelectedConfig] = useState<string | null>(null)
  const [wfState, setWfState] = useState<TestState>('idle')
  const [mcState, setMcState] = useState<TestState>('idle')
  const [wfResults, setWfResults] = useState<WalkForwardResults | null>(null)
  const [mcResults, setMcResults] = useState<MonteCarloResults | null>(null)
  const [wfElapsed, setWfElapsed] = useState(0)
  const [mcElapsed, setMcElapsed] = useState(0)
  const [runAllActive, setRunAllActive] = useState(false)

  // Load any existing results when panel opens
  useEffect(() => {
    if (!open) return
    getWalkForwardResults().then(r => {
      if (r.available !== false && r.robustness_score != null) { setWfResults(r); setWfState('done') }
    }).catch(() => {})
    getMonteCarloResults().then(r => {
      if (r.available !== false && r.n_runs != null) { setMcResults(r); setMcState('done') }
    }).catch(() => {})
  }, [open])

  // Run-All chaining: once WF completes, kick off MC automatically
  useEffect(() => {
    if (!runAllActive) return
    if (wfState === 'done' && mcState === 'idle') startMC()
    if (wfState === 'done' && mcState === 'done') setRunAllActive(false)
  }, [runAllActive, wfState, mcState])

  // Polling while WF runs
  useEffect(() => {
    if (wfState !== 'running') return
    let cancelled = false
    const timer = setInterval(() => setWfElapsed(e => e + 1), 1000)
    async function poll() {
      try {
        const { running } = await getOptimizerRunning()
        if (!running && !cancelled) {
          await new Promise(res => setTimeout(res, 800))
          const r = await getWalkForwardResults()
          if (!cancelled) {
            if (r.available !== false && r.robustness_score != null) { setWfResults(r); setWfState('done') }
            else setWfState('error')
          }
        }
      } catch { /* keep polling */ }
    }
    poll()
    const id = setInterval(poll, 3000)
    return () => { cancelled = true; clearInterval(id); clearInterval(timer) }
  }, [wfState])

  // Polling while MC runs
  useEffect(() => {
    if (mcState !== 'running') return
    let cancelled = false
    const timer = setInterval(() => setMcElapsed(e => e + 1), 1000)
    async function poll() {
      try {
        const { running } = await getOptimizerRunning()
        if (!running && !cancelled) {
          await new Promise(res => setTimeout(res, 800))
          const r = await getMonteCarloResults()
          if (!cancelled) {
            if (r.available !== false && r.n_runs != null) { setMcResults(r); setMcState('done') }
            else setMcState('error')
          }
        }
      } catch { /* keep polling */ }
    }
    poll()
    const id = setInterval(poll, 3000)
    return () => { cancelled = true; clearInterval(id); clearInterval(timer) }
  }, [mcState])

  async function startWF() {
    setWfState('running'); setWfElapsed(0)
    try { await runOptimizer('walk_forward', undefined, undefined, selectedConfig ?? undefined) } catch { setWfState('error') }
  }

  async function startMC() {
    setMcState('running'); setMcElapsed(0)
    try { await runOptimizer('monte_carlo', undefined, undefined, selectedConfig ?? undefined) } catch { setMcState('error') }
  }

  const doneCount   = (wfState === 'done' ? 1 : 0) + (mcState === 'done' ? 1 : 0)
  const anyRunning  = wfState === 'running' || mcState === 'running'
  const status: StepStatus = doneCount === 2 ? 'complete' : doneCount > 0 ? 'in_progress' : 'not_started'

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      status === 'complete' ? 'border-green-800' : status === 'in_progress' ? 'border-amber-800' : 'border-border'
    }`}>
      <button className="w-full flex items-center gap-3 px-4 py-3 text-left" onClick={onToggle}>
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-bold text-white uppercase tracking-wide">Step 2 · Validate</span>
          <p className="text-xs text-slate-400 mt-0.5">
            {status === 'complete'
              ? `Both tests passed${selectedConfig ? ` · ${selectedConfig}` : ''}`
              : anyRunning
              ? `Running validation…${selectedConfig ? ` · ${selectedConfig}` : ''}`
              : selectedConfig
              ? selectedConfig
              : 'Walk-Forward · Monte Carlo'}
          </p>
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          <ConfigSelector
            value={selectedConfig}
            onChange={setSelectedConfig}
            label="Config to validate"
            showStats
          />
          <p className="text-xs text-slate-500 -mt-1">
            {selectedConfig
              ? `Tests will run against the params saved in "${selectedConfig}".`
              : 'Select a config above, or leave blank to test the current best genome.'}
          </p>

          <ValidateTestCard
            title="Walk-Forward"
            icon="🔬"
            description="Splits your history into a training half and an unseen test half. The robustness score is how much performance survives the handoff — ≥ 80% is strong, ≥ 50% is acceptable, below that suggests over-fitting."
            state={wfState}
            elapsed={wfElapsed}
            onRun={startWF}
          >
            {wfResults && <WFResultsPanel r={wfResults} />}
          </ValidateTestCard>

          <ValidateTestCard
            title="Monte Carlo"
            icon="🎲"
            description="Runs 100 backtests on random 6-month windows drawn from price history. If the strategy stays profitable across most windows, the edge is real — not just a lucky backtest period."
            state={mcState}
            elapsed={mcElapsed}
            onRun={startMC}
          >
            {mcResults && <MCResultsPanel r={mcResults} />}
          </ValidateTestCard>

          <button
            onClick={() => { setRunAllActive(true); startWF() }}
            disabled={anyRunning || runAllActive}
            className="w-full py-3 rounded-xl bg-green-800 hover:bg-green-700 disabled:opacity-40 text-green-200 text-sm font-semibold"
          >
            {runAllActive && anyRunning ? 'Running…' : runAllActive ? 'Starting next…' : 'Run Both Tests'}
          </button>
        </div>
      )}
    </div>
  )
}

// ── Farm bot card (inline in Step 3) ─────────────────────────────────────────

const CHECK_LABELS: Record<string, string> = {
  min_trades:     'Min Trades',
  min_days:       'Min Days',
  sharpe:         'Sharpe ≥ 0.8',
  drawdown:       'Drawdown < 15%',
  win_rate:       'Win Rate ≥ 55%',
  walk_forward:   'Walk-Forward',
  reconcile:      'Reconcile',
  no_kill_switch: 'No Kill Switch',
}

function FarmBotCard({ bot, onRefresh }: { bot: BotFarmEntry; onRefresh: () => void }) {
  const [expanded, setExpanded]         = useState(false)
  const [assignConfig, setAssignConfig] = useState<string | null>(null)
  const [assigning, setAssigning]       = useState(false)
  const [assignMsg, setAssignMsg]       = useState('')
  const [confirmPromote, setConfirmPromote] = useState(false)
  const [promoting, setPromoting]           = useState(false)
  const [promoteMsg, setPromoteMsg]         = useState('')
  const [startingEquity, setStartingEquity] = useState('')

  const m = bot.metrics
  const r = bot.readiness
  const configName = bot.config_name ?? 'Unassigned'
  const statusDot =
    bot.status === 'running' ? 'bg-green-400 shadow-[0_0_6px_#22c55e]' :
    bot.status === 'error'   ? 'bg-red-500' : 'bg-yellow-400'

  async function handleAssign() {
    if (!assignConfig) return
    setAssigning(true)
    setAssignMsg('')
    try {
      await assignBotConfig(bot.id, assignConfig)
      setAssignMsg(`✅ Assigned '${assignConfig}'`)
      onRefresh()
    } catch (e) {
      setAssignMsg(`❌ ${String(e)}`)
    } finally {
      setAssigning(false)
    }
  }

  async function handlePromote() {
    if (!assignConfig) return
    const equity = parseFloat(startingEquity)
    if (!equity || equity <= 0) return
    setPromoting(true)
    try {
      const res = await promoteConfig(assignConfig, equity)
      setPromoteMsg(`✅ ${res.message ?? 'Promoted — live bot will restart'} | Starting equity: $${equity.toLocaleString()}`)
      setConfirmPromote(false)
      setStartingEquity('')
      onRefresh()
    } catch (e) {
      setPromoteMsg(`❌ ${String(e)}`)
    } finally {
      setPromoting(false)
    }
  }

  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      <button className="w-full flex items-center justify-between px-4 py-3 text-left" onClick={() => setExpanded(e => !e)}>
        <div className="flex items-center gap-2.5 min-w-0">
          <span className={`inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusDot}`} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="font-semibold text-white text-sm truncate">{bot.name}</p>
              <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium flex-shrink-0 ${
                configName === 'Unassigned'
                  ? 'bg-slate-800 text-slate-500 border-slate-600'
                  : 'bg-amber-900 text-amber-300 border-amber-700'
              }`}>
                {configName}
              </span>
            </div>
            <p className="text-xs text-slate-500 truncate">{bot.description}</p>
          </div>
        </div>
        <span className="text-slate-500 text-xs ml-2">{expanded ? '▲' : '▼'}</span>
      </button>

      {/* Readiness bar — always visible */}
      <div className="px-4 pb-2">
        <ReadinessProgressBar score={r.score} total={r.total} />
      </div>

      {/* Metrics strip */}
      <div className="grid grid-cols-5 border-t border-border/40 text-center">
        {[
          { label: 'Trades',  value: String(m.num_trades ?? 0) },
          { label: 'Win',     value: m.win_rate != null ? `${(m.win_rate * 100).toFixed(0)}%` : '—' },
          { label: 'Sharpe',  value: m.sharpe != null ? m.sharpe.toFixed(2) : '—' },
          { label: 'DD',      value: m.max_drawdown != null ? `-${(m.max_drawdown * 100).toFixed(0)}%` : '—' },
          { label: 'Return',  value: m.total_return_pct != null ? `${m.total_return_pct >= 0 ? '+' : ''}${m.total_return_pct.toFixed(1)}%` : '—' },
        ].map(({ label, value }) => (
          <div key={label} className="py-2 border-r border-border/30 last:border-r-0">
            <p className="text-xs text-slate-500">{label}</p>
            <p className="text-xs font-medium text-white">{value}</p>
          </div>
        ))}
      </div>

      {/* Expandable section: checklist + assign */}
      {expanded && (
        <div className="border-t border-border/40 px-4 py-3 space-y-3">
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">Readiness Checklist</p>
          <div className="space-y-1">
            {Object.entries(r.checks).map(([key, passed]) => (
              <div key={key} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm">{passed ? '✅' : '❌'}</span>
                  <span className={`text-xs ${passed ? 'text-slate-300' : 'text-slate-500'}`}>{CHECK_LABELS[key] ?? key}</span>
                </div>
              </div>
            ))}
          </div>

          {/* Assign config */}
          <div className="pt-2 border-t border-border/30 space-y-2">
            <p className="text-xs text-slate-500 uppercase tracking-wide">Assign Config</p>
            <ConfigSelector value={assignConfig} onChange={setAssignConfig} label="Config to assign" showStats={false} />
            {assignMsg && (
              <p className={`text-xs px-3 py-2 rounded-lg border ${assignMsg.startsWith('✅') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-red-950 border-red-800 text-red-300'}`}>{assignMsg}</p>
            )}
            <button
              onClick={handleAssign}
              disabled={assigning || !assignConfig}
              className="w-full py-2 rounded-xl bg-amber-800 hover:bg-amber-700 disabled:opacity-40 text-amber-200 text-xs font-semibold"
            >
              {assigning ? 'Assigning…' : 'Assign Config'}
            </button>
          </div>

          {/* Promote — only if 8/8 ready */}
          {r.ready && (
            <div className="pt-2 border-t border-green-900/50 space-y-2">
              {promoteMsg && (
                <p className={`text-xs px-3 py-2 rounded-lg border ${promoteMsg.startsWith('✅') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-red-950 border-red-800 text-red-300'}`}>{promoteMsg}</p>
              )}
              <button
                onClick={() => setConfirmPromote(true)}
                disabled={!assignConfig}
                className="w-full py-3 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-bold"
              >
                Promote to Live
              </button>
              {!assignConfig && <p className="text-xs text-slate-500 text-center">Select a config above to promote</p>}
            </div>
          )}
        </div>
      )}

      {/* Promote confirm dialog */}
      {confirmPromote && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-green-700 rounded-2xl p-6 w-full max-w-sm space-y-4">
            <div className="text-3xl">⬆️</div>
            <h3 className="font-bold text-white text-lg">Promote to Live?</h3>
            <p className="text-slate-400 text-sm">
              You're about to promote <span className="text-green-400 font-medium">{assignConfig}</span> to the live bot.
            </p>
            <div className="space-y-1.5">
              <label className="text-xs text-slate-400 font-medium">Actual deposit amount (USD)</label>
              <div className="flex items-center gap-2 bg-slate-900 border border-border rounded-xl px-3 py-2.5">
                <span className="text-slate-400 text-sm">$</span>
                <input
                  type="number"
                  min="1"
                  step="any"
                  value={startingEquity}
                  onChange={e => setStartingEquity(e.target.value)}
                  placeholder="e.g. 5000"
                  className="flex-1 bg-transparent text-white text-sm focus:outline-none"
                />
              </div>
            </div>
            <div className="bg-red-950 border border-red-800 rounded-xl px-3 py-2.5 space-y-1">
              <p className="text-red-300 text-xs font-semibold">⚠️ The live bot will switch to MAINNET. Real money will be traded.</p>
              <p className="text-red-300 text-xs">⚠️ The bot will restart with the new configuration.</p>
            </div>
            <div className="flex gap-3">
              <button onClick={() => { setConfirmPromote(false); setStartingEquity('') }} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">Cancel</button>
              <button
                onClick={handlePromote}
                disabled={promoting || !startingEquity || parseFloat(startingEquity) <= 0}
                className="flex-1 py-3 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-bold"
              >
                {promoting ? 'Promoting…' : 'Promote to Live'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Paper Config Card (Step 3) ────────────────────────────────────────────────

function PaperConfigCard({
  config, bot, onRefresh,
}: {
  config: NamedConfig
  bot: BotFarmEntry | null
  onRefresh: () => void
}) {
  const [busy, setBusy]                     = useState(false)
  const [msg, setMsg]                       = useState('')
  const [showPromoteConfirm, setShowPromoteConfirm] = useState(false)
  const [startingEquity, setStartingEquity] = useState('')
  const [promoting, setPromoting]           = useState(false)
  const [showStopConfirm, setShowStopConfirm] = useState(false)

  const r = bot?.readiness
  const m = bot?.metrics

  async function handleStop() {
    setBusy(true)
    try {
      await stopPaperTesting(config.name)
      setShowStopConfirm(false)
      setMsg('Stopped — farm will remove bot shortly')
      onRefresh()
    } catch (e) { setMsg(String(e)) } finally { setBusy(false) }
  }

  async function handlePromote() {
    const equity = parseFloat(startingEquity)
    if (!equity || equity <= 0) return
    setPromoting(true)
    try {
      const res = await promoteConfig(config.name, equity)
      setMsg(`Promoted: ${res.message ?? 'live bot will restart'}`)
      setShowPromoteConfirm(false)
      setStartingEquity('')
      onRefresh()
    } catch (e) { setMsg(String(e)) } finally { setPromoting(false) }
  }

  const isReady = r?.ready ?? false
  const statusDot = bot?.status === 'running'
    ? 'bg-green-400 shadow-[0_0_6px_#22c55e]'
    : bot?.status === 'error' ? 'bg-red-500' : 'bg-amber-400'

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${isReady ? 'border-green-700' : 'border-amber-700/50'}`}>
      <div className="px-4 pt-3 pb-2">
        <div className="flex items-center gap-2 mb-2">
          <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${bot ? statusDot : 'bg-slate-600'}`} />
          <p className="font-semibold text-white text-sm flex-1">{config.name}</p>
          <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium ${
            config.status === 'ready' ? 'bg-green-900 text-green-300 border-green-700' : 'bg-amber-900 text-amber-300 border-amber-700'
          }`}>{config.status === 'ready' ? 'Ready' : 'Paper'}</span>
        </div>

        {/* Readiness bar */}
        {r && <ReadinessProgressBar score={r.score} total={r.total} />}

        {/* Metrics strip */}
        {m && (
          <div className="grid grid-cols-5 border-t border-border/40 text-center mt-2">
            {[
              { label: 'Trades', value: String(m.num_trades ?? 0) },
              { label: 'Win',    value: m.win_rate != null ? `${(m.win_rate * 100).toFixed(0)}%` : '—' },
              { label: 'Sharpe', value: m.sharpe != null ? m.sharpe.toFixed(2) : '—' },
              { label: 'DD',     value: m.max_drawdown != null ? `-${(m.max_drawdown * 100).toFixed(0)}%` : '—' },
              { label: 'Return', value: m.total_return_pct != null ? `${m.total_return_pct >= 0 ? '+' : ''}${m.total_return_pct.toFixed(1)}%` : '—' },
            ].map(({ label, value }) => (
              <div key={label} className="py-2 border-r border-border/30 last:border-r-0">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="text-xs font-medium text-white">{value}</p>
              </div>
            ))}
          </div>
        )}

        {msg && (
          <p className={`text-xs px-2 py-1.5 rounded-lg border mt-2 ${msg.includes('error') || msg.startsWith('Error') ? 'bg-red-950 border-red-800 text-red-300' : 'bg-green-950 border-green-800 text-green-300'}`}>{msg}</p>
        )}
      </div>

      <div className="px-4 pb-3 flex gap-2">
        {isReady && (
          <button
            onClick={() => setShowPromoteConfirm(true)}
            disabled={busy}
            className="flex-1 py-2 rounded-xl bg-green-700 hover:bg-green-600 text-white text-xs font-bold disabled:opacity-40"
          >
            Promote to Live
          </button>
        )}
        <button
          onClick={() => setShowStopConfirm(true)}
          disabled={busy}
          className="flex-1 py-2 rounded-xl bg-orange-900 hover:bg-orange-800 text-orange-300 text-xs font-medium disabled:opacity-40"
        >
          Stop Testing
        </button>
      </div>

      {/* Stop confirm */}
      {showStopConfirm && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-border rounded-2xl p-6 w-full max-w-sm space-y-4">
            <h3 className="font-bold text-white text-lg">Stop Paper Testing?</h3>
            <p className="text-slate-400 text-sm">Stop '{config.name}'? The farm bot will be stopped.</p>
            <div className="flex gap-3">
              <button onClick={() => setShowStopConfirm(false)} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">Cancel</button>
              <button onClick={handleStop} disabled={busy} className="flex-1 py-3 rounded-xl bg-red-700 text-white text-sm font-semibold disabled:opacity-40">Stop</button>
            </div>
          </div>
        </div>
      )}

      {/* Promote confirm */}
      {showPromoteConfirm && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-green-700 rounded-2xl p-6 w-full max-w-sm space-y-4">
            <div className="text-3xl">⬆️</div>
            <h3 className="font-bold text-white text-lg">Promote to Live?</h3>
            <p className="text-slate-400 text-sm">
              Promoting <span className="text-green-400 font-medium">{config.name}</span> to mainnet. Real money will be traded.
            </p>
            <div className="flex items-center gap-2 bg-slate-900 border border-border rounded-xl px-3 py-2.5">
              <span className="text-slate-400 text-sm">$</span>
              <input
                type="number" min="1" step="any"
                value={startingEquity}
                onChange={e => setStartingEquity(e.target.value)}
                placeholder="Deposit amount (USD)"
                className="flex-1 bg-transparent text-white text-sm focus:outline-none"
                autoFocus
              />
            </div>
            <div className="flex gap-3">
              <button onClick={() => { setShowPromoteConfirm(false); setStartingEquity('') }} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">Cancel</button>
              <button
                onClick={handlePromote}
                disabled={promoting || !startingEquity || parseFloat(startingEquity) <= 0}
                className="flex-1 py-3 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-bold"
              >
                {promoting ? 'Promoting…' : 'Promote'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Step 3 — Paper Trade ──────────────────────────────────────────────────────

function StepPaperTrade({
  open, onToggle, configs, bots, farmRunning, onRefresh,
}: {
  open: boolean
  onToggle: () => void
  configs: NamedConfig[]
  bots: BotFarmEntry[]
  farmRunning: boolean
  onRefresh: () => void
}) {
  const [farmBusy, setFarmBusy]         = useState(false)
  const [farmMsg, setFarmMsg]           = useState('')
  const [selectedStart, setSelectedStart] = useState<string | null>(null)
  const [startingBusy, setStartingBusy] = useState(false)
  const [startMsg, setStartMsg]         = useState('')

  // Paper + ready configs — these are what the farm runs
  const paperConfigs = configs.filter(c => c.status === 'paper' || c.status === 'ready')

  // Configs eligible to start paper testing (draft or validated, not already paper)
  const eligibleConfigs = configs.filter(c => c.status === 'draft' || c.status === 'validated')

  const readyCount = paperConfigs.filter(c => c.status === 'ready').length
  const bestReadiness = bots.length > 0 ? Math.max(...bots.map(b => b.readiness.score)) : 0
  const status: StepStatus = readyCount > 0 ? 'complete'
    : paperConfigs.length > 0 ? 'in_progress'
    : 'not_started'

  async function handleStartFarm() {
    setFarmBusy(true)
    setFarmMsg('')
    try {
      const r = await startFarm()
      setFarmMsg(`Farm started (PID ${r.pid})`)
      setTimeout(() => { setFarmMsg(''); onRefresh() }, 1500)
    } catch (e) { setFarmMsg(String(e)) } finally { setFarmBusy(false) }
  }

  async function handleStopFarm() {
    setFarmBusy(true)
    setFarmMsg('')
    try {
      await stopFarm()
      setFarmMsg('Farm stopped')
      setTimeout(() => { setFarmMsg(''); onRefresh() }, 1500)
    } catch (e) { setFarmMsg(String(e)) } finally { setFarmBusy(false) }
  }

  async function handleStartPaper() {
    if (!selectedStart) return
    setStartingBusy(true)
    setStartMsg('')
    try {
      await startPaperTesting(selectedStart)
      setStartMsg(`Started paper testing for '${selectedStart}'`)
      setSelectedStart(null)
      setTimeout(() => { setStartMsg(''); onRefresh() }, 1500)
    } catch (e) { setStartMsg(String(e)) } finally { setStartingBusy(false) }
  }

  // Map bot entries by config name for lookup
  const botByName: Record<string, BotFarmEntry> = {}
  for (const bot of bots) {
    if (bot.config_name) botByName[bot.config_name] = bot
  }

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      status === 'complete' ? 'border-green-800' : status === 'in_progress' ? 'border-amber-800' : 'border-border'
    }`}>
      <button className="w-full flex items-center gap-3 px-4 py-3 text-left" onClick={onToggle}>
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-bold text-white uppercase tracking-wide">Step 3 · Paper Trade</span>
          <p className="text-xs text-slate-400 mt-0.5">
            {readyCount > 0
              ? `${readyCount} config(s) ready for live`
              : paperConfigs.length > 0
              ? `${paperConfigs.length} config(s) running · best ${bestReadiness}/8 checks`
              : 'Select configs to paper test'}
          </p>
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          {/* Farm control */}
          <div className="flex items-center justify-between py-2 border-b border-border/40">
            <div className="flex items-center gap-2">
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${farmRunning ? 'bg-green-400 shadow-[0_0_6px_#22c55e]' : 'bg-slate-600'}`} />
              <span className="text-sm text-white">
                {farmRunning
                  ? `Farm running · ${bots.filter(b => b.status === 'running').length} bots active`
                  : 'Farm stopped'}
              </span>
            </div>
            <div className="flex gap-2">
              <button onClick={handleStartFarm} disabled={farmRunning || farmBusy}
                className="px-3 py-1.5 rounded-xl bg-green-800 hover:bg-green-700 text-green-200 text-xs font-medium disabled:opacity-40">
                Start
              </button>
              <button onClick={handleStopFarm} disabled={!farmRunning || farmBusy}
                className="px-3 py-1.5 rounded-xl bg-red-900 hover:bg-red-800 text-red-200 text-xs font-medium disabled:opacity-40">
                Stop
              </button>
            </div>
          </div>

          {farmMsg && (
            <p className={`text-xs px-3 py-2 rounded-lg border ${farmMsg.startsWith('Farm started') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-slate-800 border-border text-slate-300'}`}>{farmMsg}</p>
          )}

          {/* Start paper testing for a config */}
          {eligibleConfigs.length > 0 && (
            <div className="bg-navy rounded-xl px-3 py-3 space-y-2">
              <p className="text-xs text-slate-400 font-medium">Start paper testing</p>
              <ConfigSelector value={selectedStart} onChange={setSelectedStart} label="Config to test" showStats />
              {startMsg && (
                <p className={`text-xs px-2 py-1.5 rounded-lg border ${startMsg.startsWith('Started') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-red-950 border-red-800 text-red-300'}`}>{startMsg}</p>
              )}
              <button
                onClick={handleStartPaper}
                disabled={!selectedStart || startingBusy}
                className="w-full py-2 rounded-xl bg-amber-800 hover:bg-amber-700 text-amber-200 text-xs font-semibold disabled:opacity-40"
              >
                {startingBusy ? 'Starting…' : 'Start Paper Testing'}
              </button>
            </div>
          )}

          {/* Paper/ready config cards */}
          {paperConfigs.length === 0 ? (
            <div className="py-4 text-center">
              <p className="text-slate-400 text-sm">No configs in paper testing yet.</p>
              <p className="text-slate-600 text-xs mt-1">
                Save a config in Step 1, validate it in Step 2, then start paper testing above.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {paperConfigs.map(cfg => (
                <PaperConfigCard
                  key={cfg.name}
                  config={cfg}
                  bot={botByName[cfg.name] ?? null}
                  onRefresh={onRefresh}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Step 4 — Sweep ────────────────────────────────────────────────────────────

function StepSweep({
  open, onToggle, configs,
}: {
  open: boolean
  onToggle: () => void
  configs: NamedConfig[]
}) {
  const [selectedConfig, setSelectedConfig] = useState<string | null>(null)
  const [sweepResults, setSweepResults]     = useState<SweepResults | null>(null)
  const [sweepLoading, setSweepLoading]     = useState(false)
  const [runningParam, setRunningParam]     = useState<string | null>(null)
  const [runMsg, setRunMsg]                 = useState('')
  const [applying, setApplying]             = useState(false)
  const [applyMsg, setApplyMsg]             = useState('')
  const [lastUpdated, setLastUpdated]       = useState<Date | null>(null)
  const [changedParams, setChangedParams]   = useState<Set<string>>(new Set())
  // Save-as-new-config flow
  const [showSaveNew, setShowSaveNew]       = useState(false)
  const [newConfigName, setNewConfigName]   = useState('')
  const [savingNew, setSavingNew]           = useState(false)
  const [saveNewMsg, setSaveNewMsg]         = useState('')

  // Tracks previous best values so we can highlight what changed
  const prevBestRef = useRef<Record<string, number>>({})

  const hasBest    = (sweepResults?.best_per_param != null) && Object.keys(sweepResults.best_per_param).length > 0
  const status: StepStatus = hasBest ? 'in_progress' : 'not_started'

  const tuneConfigs  = configs.filter(c => c.status === 'paper' || c.status === 'ready')
  const otherConfigs = configs.filter(c => c.status !== 'paper' && c.status !== 'ready')

  function formatUpdatedAgo(d: Date) {
    const secs = Math.round((Date.now() - d.getTime()) / 1000)
    if (secs < 60)  return `${secs}s ago`
    if (secs < 3600) return `${Math.round(secs / 60)}m ago`
    return `${Math.round(secs / 3600)}h ago`
  }

  async function loadSweepResults() {
    try {
      const r = await getSweepResults()
      // Detect which params changed compared to the previous load
      const changed = new Set<string>()
      if (r.best_per_param) {
        for (const [param, best] of Object.entries(r.best_per_param)) {
          const prev = prevBestRef.current[param]
          if (prev === undefined || Math.abs(prev - best.value) > 1e-9) {
            changed.add(param)
          }
          prevBestRef.current[param] = best.value
        }
      }
      setSweepResults(r)
      if (changed.size > 0) {
        setChangedParams(changed)
        setLastUpdated(new Date())
      }
    } catch { /* no sweep data yet */ }
  }

  useEffect(() => { if (open) loadSweepResults() }, [open])

  async function handleBroadSweep() {
    setSweepLoading(true)
    setRunMsg('')
    try {
      const r = await runOptimizer('sweep', undefined, undefined, selectedConfig ?? undefined)
      setRunMsg(`Broad sweep started (PID ${r.pid}) — results will load automatically`)
      setTimeout(loadSweepResults, 12_000)
    } catch (e) {
      setRunMsg(String(e))
    } finally {
      setSweepLoading(false)
    }
  }

  async function handleFineSweep(param: string) {
    setRunningParam(param)
    setRunMsg('')
    try {
      const r = await runOptimizer('sweep', param, undefined, selectedConfig ?? undefined)
      setRunMsg(`Fine sweep started for '${SWEEP_PARAMS.find(p => p.key === param)?.label ?? param}' (PID ${r.pid})`)
      setTimeout(loadSweepResults, 8_000)
    } catch (e) {
      setRunMsg(String(e))
    } finally {
      setRunningParam(null)
    }
  }

  function buildBestParams() {
    const bestParams: Record<string, unknown> = {}
    for (const [param, best] of Object.entries(sweepResults!.best_per_param)) {
      bestParams[param] = best.value
    }
    return bestParams
  }

  async function handleApplyBest() {
    if (!selectedConfig || !hasBest) return
    setApplying(true)
    setApplyMsg('')
    try {
      const bestParams = buildBestParams()
      await updateConfigParams(selectedConfig, bestParams)
      setApplyMsg(`✅ Applied ${Object.keys(bestParams).length} params to '${selectedConfig}'`)
      setShowSaveNew(true)
      setNewConfigName(`${selectedConfig}_tuned`)
      setSaveNewMsg('')
    } catch (e) {
      setApplyMsg(String(e))
    } finally {
      setApplying(false)
    }
  }

  async function handleSaveNew() {
    if (!newConfigName.trim() || !hasBest) return
    setSavingNew(true)
    setSaveNewMsg('')
    const baseConfig = configs.find(c => c.name === selectedConfig)
    try {
      const mergedParams = { ...(baseConfig?.params ?? {}), ...buildBestParams() }
      await apiSaveConfig({
        name: newConfigName.trim(),
        source: 'evolved',
        notes: `Sweep-tuned from '${selectedConfig ?? 'unknown'}'`,
        fitness: sweepResults ? Math.max(...Object.values(sweepResults.best_per_param).map(b => b.fitness)) : undefined,
        total_return_pct: baseConfig?.total_return_pct ?? null,
        sharpe: baseConfig?.sharpe ?? null,
        params: mergedParams,
      })
      setSaveNewMsg(`✅ Saved as '${newConfigName.trim()}' — select it in Step 3 to paper test`)
      setShowSaveNew(false)
    } catch (e) {
      setSaveNewMsg(String(e))
    } finally {
      setSavingNew(false)
    }
  }

  function fmtVal(v: number) { return v < 1 ? v.toFixed(4) : v.toFixed(2) }

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      hasBest ? 'border-amber-800' : 'border-border'
    }`}>
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
        onClick={onToggle}
      >
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-white uppercase tracking-wide">Step 4 · Sweep</span>
            {changedParams.size > 0 && lastUpdated && (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-900 border border-amber-700 text-amber-300 flex-shrink-0">
                ↑ updated {formatUpdatedAgo(lastUpdated)}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            {hasBest
              ? `${Object.keys(sweepResults!.best_per_param).length} params optimised — apply to config`
              : 'Tune parameters with broad + fine sweeps'}
          </p>
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">

          {/* Config selector */}
          <div className="space-y-1.5">
            <p className="text-xs text-slate-400 font-medium">Config to tune</p>
            <select
              value={selectedConfig ?? ''}
              onChange={e => setSelectedConfig(e.target.value || null)}
              className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-amber-500"
            >
              <option value="">Select a config…</option>
              {tuneConfigs.length > 0 && (
                <optgroup label="Paper / Ready">
                  {tuneConfigs.map(c => (
                    <option key={c.name} value={c.name}>{c.name} ({c.status})</option>
                  ))}
                </optgroup>
              )}
              {otherConfigs.length > 0 && (
                <optgroup label="Other">
                  {otherConfigs.map(c => (
                    <option key={c.name} value={c.name}>{c.name} ({c.status})</option>
                  ))}
                </optgroup>
              )}
            </select>
          </div>

          {/* 1. Broad sweep */}
          <div className="bg-navy rounded-xl px-3 py-3 space-y-2">
            <p className="text-xs text-white font-semibold">1 · Broad Sweep</p>
            <p className="text-xs text-slate-500">Tests all key params over a wide range to find the best starting point.</p>
            <button
              onClick={handleBroadSweep}
              disabled={sweepLoading}
              className="w-full py-2.5 rounded-xl bg-amber-800 hover:bg-amber-700 disabled:opacity-40 text-amber-200 text-xs font-semibold"
            >
              {sweepLoading ? 'Sweeping…' : 'Sweep All Params'}
            </button>
          </div>

          {/* 2. Fine sweep per param */}
          <div className="bg-navy rounded-xl px-3 py-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-xs text-white font-semibold">2 · Fine Sweep</p>
              {lastUpdated && (
                <span className="text-xs text-slate-500">Last run {formatUpdatedAgo(lastUpdated)}</span>
              )}
            </div>
            <p className="text-xs text-slate-500">Zoom in on the best value for a single parameter.</p>
            <div className="space-y-2 pt-1">
              {SWEEP_PARAMS.map(({ key, label }) => {
                const best    = sweepResults?.best_per_param?.[key]
                const changed = changedParams.has(key)
                return (
                  <div key={key} className={`flex items-center gap-2 rounded-lg px-2 py-1 transition-colors ${
                    changed ? 'bg-amber-950/50 border border-amber-800/60' : ''
                  }`}>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className="text-xs text-slate-300">{label}</p>
                        {changed && (
                          <span className="text-xs text-amber-400 font-bold">↑</span>
                        )}
                      </div>
                      {best ? (
                        <p className={`text-xs font-mono ${changed ? 'text-amber-300' : 'text-slate-500'}`}>
                          best: {fmtVal(best.value)}
                          {' '}· fit {best.fitness.toFixed(3)}
                        </p>
                      ) : (
                        <p className="text-xs text-slate-600">no data yet</p>
                      )}
                    </div>
                    <button
                      onClick={() => handleFineSweep(key)}
                      disabled={runningParam != null}
                      className="flex-shrink-0 text-xs px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200"
                    >
                      {runningParam === key ? '…' : 'Sweep'}
                    </button>
                  </div>
                )
              })}
            </div>
          </div>

          {runMsg && (
            <p className={`text-xs px-3 py-2 rounded-lg border ${
              runMsg.startsWith('Broad sweep') || runMsg.startsWith('Fine sweep')
                ? 'bg-green-950 border-green-800 text-green-300'
                : 'bg-red-950 border-red-800 text-red-300'
            }`}>{runMsg}</p>
          )}

          {/* 3. Apply + Save */}
          {hasBest && (
            <div className="space-y-2">
              <p className="text-xs text-slate-400 font-semibold">3 · Apply Best Values</p>

              {/* Summary table */}
              <div className="bg-navy rounded-xl px-3 py-2.5 space-y-1.5">
                {Object.entries(sweepResults!.best_per_param).map(([param, best]) => {
                  const changed = changedParams.has(param)
                  return (
                    <div key={param} className="flex items-center justify-between text-xs">
                      <span className="text-slate-400 flex items-center gap-1">
                        {SWEEP_PARAMS.find(p => p.key === param)?.label ?? param}
                        {changed && <span className="text-amber-400 text-xs">↑</span>}
                      </span>
                      <span className={`font-mono ${changed ? 'text-amber-300' : 'text-slate-300'}`}>
                        {fmtVal(best.value)}
                      </span>
                    </div>
                  )
                })}
              </div>

              {applyMsg && (
                <p className={`text-xs px-3 py-2 rounded-lg border ${
                  applyMsg.startsWith('✅')
                    ? 'bg-green-950 border-green-800 text-green-300'
                    : 'bg-red-950 border-red-800 text-red-300'
                }`}>{applyMsg}</p>
              )}

              <button
                onClick={handleApplyBest}
                disabled={applying || !selectedConfig}
                className="w-full py-2.5 rounded-xl bg-amber-800 hover:bg-amber-700 disabled:opacity-40 text-amber-200 text-xs font-semibold"
              >
                {applying
                  ? 'Applying…'
                  : selectedConfig
                  ? `Apply to '${selectedConfig}'`
                  : 'Select a config above'}
              </button>

              {/* Save as new config */}
              {showSaveNew && (
                <div className="bg-navy rounded-xl px-3 py-3 space-y-2 border border-green-900">
                  <p className="text-xs text-white font-semibold">4 · Save as New Config</p>
                  <p className="text-xs text-slate-500">
                    Save the tuned params under a new name, then send it back through paper testing.
                  </p>
                  <input
                    type="text"
                    value={newConfigName}
                    onChange={e => setNewConfigName(e.target.value)}
                    placeholder="Config name…"
                    className="w-full bg-slate-900 border border-border rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-green-500"
                  />
                  {saveNewMsg && (
                    <p className={`text-xs px-3 py-2 rounded-lg border ${
                      saveNewMsg.startsWith('✅')
                        ? 'bg-green-950 border-green-800 text-green-300'
                        : 'bg-red-950 border-red-800 text-red-300'
                    }`}>{saveNewMsg}</p>
                  )}
                  <div className="flex gap-2">
                    <button
                      onClick={handleSaveNew}
                      disabled={savingNew || !newConfigName.trim()}
                      className="flex-1 py-2 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-xs font-semibold"
                    >
                      {savingNew ? 'Saving…' : 'Save New Config'}
                    </button>
                    <button
                      onClick={() => setShowSaveNew(false)}
                      className="px-3 py-2 rounded-xl bg-slate-700 text-slate-300 text-xs"
                    >
                      Skip
                    </button>
                  </div>
                </div>
              )}

            </div>
          )}

        </div>
      )}
    </div>
  )
}

// ── Step 5 — Reconcile ────────────────────────────────────────────────────────

function StepReconcile({
  open, onToggle, configs,
}: {
  open: boolean
  onToggle: () => void
  configs: NamedConfig[]
}) {
  const [selectedConfig, setSelectedConfig] = useState<string | null>(null)
  const [running, setRunning]               = useState(false)
  const [runMsg, setRunMsg]                 = useState('')
  const [summary, setSummary]               = useState<OptimizerSummary | null>(null)
  const [loadingResults, setLoadingResults] = useState(false)

  const reconciliation = summary?.reconciliation as Record<string, unknown> | null | undefined
  const passed         = reconciliation?.passed === true
  const status: StepStatus = passed ? 'complete' : reconciliation != null ? 'in_progress' : 'not_started'

  const paperConfigs = configs.filter(c => c.status === 'paper' || c.status === 'ready')

  async function loadSummary() {
    setLoadingResults(true)
    try {
      const s = await getOptimizerSummary()
      setSummary(s)
    } catch { /* ignore */ } finally {
      setLoadingResults(false)
    }
  }

  useEffect(() => { if (open) loadSummary() }, [open])

  async function handleRunReconcile() {
    setRunning(true)
    setRunMsg('')
    try {
      const r = await runOptimizer('reconcile', undefined, undefined, selectedConfig ?? undefined)
      setRunMsg(`Reconcile started (PID ${r.pid}) — loading results…`)
      setTimeout(loadSummary, 8_000)
    } catch (e) {
      setRunMsg(String(e))
    } finally {
      setRunning(false)
    }
  }

  // Build display rows, exclude the top-level 'passed' flag
  const reconFields = reconciliation
    ? Object.entries(reconciliation).filter(([k]) => k !== 'passed')
    : []

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      status === 'complete' ? 'border-green-800'
      : status === 'in_progress' ? 'border-amber-800'
      : 'border-border'
    }`}>
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
        onClick={onToggle}
      >
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-bold text-white uppercase tracking-wide">Step 5 · Reconcile</span>
          <p className="text-xs text-slate-400 mt-0.5">
            {status === 'complete'
              ? 'Paper P&L matches backtest — ready for live'
              : reconciliation
              ? 'Results available — review before going live'
              : 'Verify paper P&L matches backtest predictions'}
          </p>
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">

          {/* Config selector */}
          <div className="space-y-1.5">
            <p className="text-xs text-slate-400 font-medium">Config to reconcile</p>
            <select
              value={selectedConfig ?? ''}
              onChange={e => setSelectedConfig(e.target.value || null)}
              className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-green-500"
            >
              <option value="">Select a config…</option>
              {paperConfigs.map(c => (
                <option key={c.name} value={c.name}>{c.name} ({c.status})</option>
              ))}
              {configs.filter(c => c.status !== 'paper' && c.status !== 'ready').map(c => (
                <option key={c.name} value={c.name}>{c.name} ({c.status})</option>
              ))}
            </select>
          </div>

          <button
            onClick={handleRunReconcile}
            disabled={running}
            className="w-full py-3 rounded-xl bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-white text-sm font-semibold"
          >
            {running ? 'Running reconcile…' : 'Run Reconcile'}
          </button>

          {runMsg && (
            <p className={`text-xs px-3 py-2 rounded-lg border ${
              runMsg.startsWith('Reconcile started')
                ? 'bg-green-950 border-green-800 text-green-300'
                : 'bg-red-950 border-red-800 text-red-300'
            }`}>{runMsg}</p>
          )}

          {loadingResults && (
            <p className="text-xs text-slate-500 text-center">Loading results…</p>
          )}

          {reconciliation && !loadingResults && (
            <div className={`rounded-xl border px-4 py-3 space-y-2 ${
              passed ? 'bg-green-950 border-green-800' : 'bg-red-950 border-red-800'
            }`}>
              <div className="flex items-center gap-2">
                <span>{passed ? '✅' : '❌'}</span>
                <p className={`text-sm font-semibold ${passed ? 'text-green-300' : 'text-red-300'}`}>
                  {passed ? 'Reconcile Passed' : 'Reconcile Failed'}
                </p>
              </div>
              {reconFields.length > 0 && (
                <div className="space-y-1.5 pt-1.5 border-t border-white/10">
                  {reconFields.map(([key, val]) => (
                    <div key={key} className="flex items-center justify-between text-xs">
                      <span className="text-slate-400 capitalize">{key.replace(/_/g, ' ')}</span>
                      <span className="font-mono text-white">
                        {typeof val === 'number' ? val.toFixed(2) : String(val)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {!reconciliation && !loadingResults && summary !== null && (
            <p className="text-xs text-slate-500 text-center py-2">
              No reconcile results yet — run reconcile above.
            </p>
          )}

          {passed && (
            <div className="bg-green-950 border border-green-800 rounded-xl px-3 py-2">
              <p className="text-xs text-green-300">
                ✅ Config is verified — proceed to Step 6 to go live.
              </p>
            </div>
          )}

        </div>
      )}
    </div>
  )
}

// ── Step 6 — Go Live ──────────────────────────────────────────────────────────

function StepGoLive({
  open, onToggle, bots, configs,
}: {
  open: boolean
  onToggle: () => void
  bots: BotFarmEntry[]
  configs: NamedConfig[]
}) {
  const readyConfigs = configs.filter(c => c.status === 'ready')
  // Also accept bots with readiness.ready for backward compat
  const readyBots    = bots.filter(b => b.readiness.ready)

  const [selectedConfig, setSelectedConfig] = useState<string | null>(
    readyConfigs[0]?.name ?? readyBots[0]?.config_name ?? null
  )
  const [confirmOpen, setConfirmOpen]   = useState(false)
  const [promoting, setPromoting]       = useState(false)
  const [promoteMsg, setPromoteMsg]     = useState('')
  const [startingEquity, setStartingEquity] = useState('')

  const isLocked  = readyConfigs.length === 0 && readyBots.length === 0
  const bot       = selectedConfig ? (bots.find(b => b.config_name === selectedConfig) ?? null) : null
  const status: StepStatus = isLocked ? 'locked' : 'not_started'

  async function handlePromote() {
    if (!selectedConfig) return
    const equity = parseFloat(startingEquity)
    if (!equity || equity <= 0) return
    setPromoting(true)
    setPromoteMsg('')
    try {
      const r = await promoteConfig(selectedConfig, equity)
      setPromoteMsg(`✅ ${r.message ?? 'Promoted — live bot will restart'} | Starting equity: $${equity.toLocaleString()}`)
      setConfirmOpen(false)
      setStartingEquity('')
    } catch (e) {
      setPromoteMsg(`❌ ${String(e)}`)
    } finally {
      setPromoting(false)
    }
  }

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      isLocked ? 'border-border opacity-60' : 'border-green-700'
    }`}>
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
        onClick={isLocked ? undefined : onToggle}
        disabled={isLocked}
      >
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <span className={`text-sm font-bold uppercase tracking-wide ${isLocked ? 'text-slate-500' : 'text-white'}`}>
            Step 6 · Go Live
          </span>
          <p className="text-xs text-slate-500 mt-0.5">
            {isLocked
              ? `Locked — needs 8/8 readiness (best: ${Math.max(0, ...bots.map(b => b.readiness.score))}/8)`
              : `${readyConfigs.length + readyBots.length} ready · promote to live`}
          </p>
        </div>
        {!isLocked && <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>}
      </button>

      {open && !isLocked && (
        <div className="px-4 pb-4 space-y-3">
          {/* Config selector — prefer ready configs */}
          <div className="space-y-1.5">
            <span className="text-xs text-slate-400 font-medium">Ready config</span>
            <select
              value={selectedConfig ?? ''}
              onChange={e => setSelectedConfig(e.target.value)}
              className="w-full bg-navy border border-green-800 rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none"
            >
              <option value="">Select a config…</option>
              {readyConfigs.map(c => (
                <option key={c.name} value={c.name}>{c.name} — 8/8 ✅</option>
              ))}
              {readyBots.filter(b => b.config_name && !readyConfigs.find(c => c.name === b.config_name)).map(b => (
                <option key={b.id} value={b.config_name!}>{b.config_name} (via farm bot)</option>
              ))}
            </select>
          </div>

          {/* Bot stats */}
          {bot && (
            <div className="flex flex-wrap gap-3 text-xs text-slate-400 px-1">
              <span>Trades <span className="text-white font-mono">{bot.metrics.num_trades}</span></span>
              <span>Sharpe <span className="text-white font-mono">
                {bot.metrics.sharpe != null ? bot.metrics.sharpe.toFixed(2) : '—'}
              </span></span>
              <span>Return <span className={`font-mono ${(bot.metrics.total_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {(bot.metrics.total_return_pct ?? 0) >= 0 ? '+' : ''}{(bot.metrics.total_return_pct ?? 0).toFixed(1)}%
              </span></span>
            </div>
          )}

          {promoteMsg && (
            <p className={`text-xs px-3 py-2 rounded-lg border ${
              promoteMsg.startsWith('✅')
                ? 'bg-green-950 border-green-800 text-green-300'
                : 'bg-red-950 border-red-800 text-red-300'
            }`}>{promoteMsg}</p>
          )}

          <button
            onClick={() => setConfirmOpen(true)}
            disabled={!selectedConfig}
            className="w-full py-3 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-bold"
          >
            Promote to Live
          </button>

          <p className="text-xs text-slate-600 text-center">
            Requires 8/8 readiness checks
          </p>
        </div>
      )}

      {/* Confirm dialog */}
      {confirmOpen && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-green-700 rounded-2xl p-6 w-full max-w-sm space-y-4">
            <div className="text-3xl">⬆️</div>
            <h3 className="font-bold text-white text-lg">Promote to Live?</h3>
            <p className="text-slate-400 text-sm">
              You're about to promote{' '}
              <span className="text-green-400 font-medium">{selectedConfig}</span> to the live bot.
            </p>
            <div className="space-y-1.5">
              <label className="text-xs text-slate-400 font-medium">Actual deposit amount (USD)</label>
              <div className="flex items-center gap-2 bg-slate-900 border border-border rounded-xl px-3 py-2.5">
                <span className="text-slate-400 text-sm">$</span>
                <input
                  type="number"
                  min="1"
                  step="any"
                  value={startingEquity}
                  onChange={e => setStartingEquity(e.target.value)}
                  placeholder="e.g. 5000"
                  className="flex-1 bg-transparent text-white text-sm focus:outline-none"
                />
              </div>
            </div>
            <div className="bg-red-950 border border-red-800 rounded-xl px-3 py-2.5 space-y-1">
              <p className="text-red-300 text-xs font-semibold">⚠️ The live bot will switch to MAINNET. Real money will be traded.</p>
              <p className="text-red-300 text-xs">⚠️ The bot will restart with the new configuration.</p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => { setConfirmOpen(false); setStartingEquity('') }}
                className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handlePromote}
                disabled={promoting || !startingEquity || parseFloat(startingEquity) <= 0}
                className="flex-1 py-3 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-bold"
              >
                {promoting ? 'Promoting…' : 'Promote to Live'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main Pipeline component ───────────────────────────────────────────────────

export default function Pipeline() {
  const [openStep, setOpenStep]     = useState<number | null>(1)
  const [evolveAll, setEvolveAll]   = useState<EvolveAllResults | null>(null)
  const [farmStatus, setFarmStatus] = useState<FarmStatus | null>(null)
  const [configs, setConfigs]       = useState<NamedConfig[]>([])
  const [loading, setLoading]       = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [ev, farm, cfgs] = await Promise.allSettled([
        getEvolveResultsAll(),
        getFarmStatus(),
        listConfigs(),
      ])
      if (ev.status === 'fulfilled')   setEvolveAll(ev.value ?? null)
      if (farm.status === 'fulfilled') setFarmStatus(farm.value)
      if (cfgs.status === 'fulfilled') setConfigs(cfgs.value)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 15_000)
    return () => clearInterval(id)
  }, [refresh])

  const bots = farmStatus?.bots ?? []

  function toggle(step: number) {
    setOpenStep(s => s === step ? null : step)
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-slate-400">Loading…</div>
  }

  return (
    <div className="p-4 space-y-1 pb-6">
      <h1 className="text-lg font-bold text-white pt-2 mb-4">Pipeline</h1>

      <p className="text-xs text-slate-500 px-1 mb-3">
        Follow these steps in order: Evolve → Validate → Paper Trade → Sweep → Reconcile → Go Live
      </p>

      <StepEvolve
        open={openStep === 1}
        onToggle={() => toggle(1)}
        evolveAll={evolveAll}
        onSaved={name => {
          setConfigs(c => [...c.filter(x => x.name !== name), {
            name,
            status: 'draft' as const,
            source: 'evolved' as const,
            created_at: new Date().toISOString(),
            fitness: null,
            total_return_pct: null,
            sharpe: null,
            params: {},
          }])
          setOpenStep(2)
        }}
      />

      <StepConnector />

      <StepValidate
        open={openStep === 2}
        onToggle={() => toggle(2)}
        configs={configs}
      />

      <StepConnector />

      <StepPaperTrade
        open={openStep === 3}
        onToggle={() => toggle(3)}
        configs={configs}
        bots={bots}
        farmRunning={farmStatus?.farm_running ?? false}
        onRefresh={refresh}
      />

      <StepConnector />

      <StepSweep
        open={openStep === 4}
        onToggle={() => toggle(4)}
        configs={configs}
      />

      <StepConnector />

      <StepReconcile
        open={openStep === 5}
        onToggle={() => toggle(5)}
        configs={configs}
      />

      <StepConnector />

      <StepGoLive
        open={openStep === 6}
        onToggle={() => toggle(6)}
        bots={bots}
        configs={configs}
      />
    </div>
  )
}
