import { useState, useEffect, useRef, useCallback } from 'react'
import {
  getOptimizerSummary, getOptimizerRunning, runOptimizer,
  getSweepResults, getEvolveResultsAll, getOptimizerProgress,
  OptimizerSummary, SweepResults, EvolveAllResults, EvolveGoalResult,
  SweepEntry, EvolveGoal, EvolutionProgress,
  saveConfig as apiSaveConfig,
} from '../api'
import InfoModal from './InfoModal'
import ConfigSelector from './ConfigSelector'
import { GLOSSARY } from '../lib/glossary'

type OptMode = 'sweep' | 'evolve' | 'walk_forward' | 'monte_carlo' | 'reconcile'

const FITNESS_GOALS: { id: EvolveGoal; icon: string; label: string; desc: string; activeCls: string }[] = [
  { id: 'balanced',   icon: '🎯', label: 'Balanced',    desc: 'All-round (default)',                        activeCls: 'bg-green-900 border-green-600 text-white' },
  { id: 'max_yield',  icon: '🚀', label: 'Max Yield',   desc: 'Highest return. Aggressive.',                activeCls: 'bg-orange-900 border-orange-600 text-white' },
  { id: 'safest',     icon: '🛡', label: 'Safest',      desc: 'Lowest drawdown. Conservative.',             activeCls: 'bg-sky-900 border-sky-600 text-white' },
  { id: 'sharpe',     icon: '⚖️', label: 'Sharpe',      desc: 'Best risk-adjusted return.',                 activeCls: 'bg-purple-900 border-purple-600 text-white' },
  { id: 'capital_roi',icon: '📊', label: 'Capital ROI', desc: 'Best return per dollar of margin deployed.', activeCls: 'bg-amber-900 border-amber-600 text-white' },
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

// ── Per-goal evolution panel ──────────────────────────────────────────────────

function DeltaChip({ val, suffix = '' }: { val: number; suffix?: string }) {
  if (Math.abs(val) < 0.001) return <span className="text-xs text-slate-500">±0</span>
  const pos = val > 0
  return (
    <span className={`text-xs font-medium ${pos ? 'text-green-400' : 'text-red-400'}`}>
      {pos ? '↑' : '↓'}{pos ? '+' : ''}{val.toFixed(2)}{suffix}
    </span>
  )
}

function HistoryRow({ entry, isCurrent }: { entry: { version: number; timestamp: string; fitness: number; return_pct: number; sharpe: number }; isCurrent: boolean }) {
  const d = new Date(entry.timestamp)
  const dateStr = d.toLocaleDateString([], { month: 'short', day: 'numeric' })
  const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return (
    <div className={`flex items-center gap-2 px-2 py-1.5 rounded-lg ${isCurrent ? 'bg-green-950 border border-green-800' : 'bg-navy'}`}>
      <span className={`text-xs font-mono font-bold w-8 shrink-0 ${isCurrent ? 'text-green-400' : 'text-slate-500'}`}>
        v{entry.version}
      </span>
      <span className="text-xs text-slate-500 shrink-0">{dateStr} {timeStr}</span>
      <div className="flex-1" />
      <span className={`text-xs font-mono ${entry.return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
        {entry.return_pct >= 0 ? '+' : ''}{entry.return_pct.toFixed(1)}%
      </span>
      <span className="text-xs font-mono text-slate-400">
        S{entry.sharpe.toFixed(2)}
      </span>
      <span className={`text-xs font-mono font-bold ${isCurrent ? 'text-green-400' : 'text-slate-300'}`}>
        {entry.fitness.toFixed(3)}
      </span>
    </div>
  )
}

function EvolveGoalPanel({
  goalMeta, data, onInfo,
}: {
  goalMeta: typeof FITNESS_GOALS[number]
  data: EvolveGoalResult | undefined
  onInfo?: (e: InfoEntry) => void
}) {
  const [open, setOpen] = useState(false)
  const [histOpen, setHistOpen] = useState(false)

  type CurWithMargin = EvolveGoalResult['current'] & {
    premium_on_margin?: number
    min_viable_capital?: number
    annualised_margin_roi?: number
  }
  const cur  = data?.current as CurWithMargin | undefined
  const prev = data?.previous
  const delta = data?.delta
  const version = data?.version ?? 0
  const ts = data?.timestamp

  // Format date+time for timestamp
  const fmtDateTime = (iso: string | null | undefined) => {
    if (!iso) return null
    try {
      const d = new Date(iso)
      return {
        date: d.toLocaleDateString([], { month: 'short', day: 'numeric', year: '2-digit' }),
        time: d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }
    } catch { return null }
  }
  const tsFormatted = fmtDateTime(ts)

  const hasData = version > 0 && cur != null

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      hasData ? 'border-border' : 'border-border opacity-60'
    }`}>
      {/* Header — always visible, tap to expand */}
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-base leading-none">{goalMeta.icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-white">{goalMeta.label}</span>
            {version > 0 && (
              <span className="text-xs font-mono px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-400">
                v{version}
              </span>
            )}
            {delta && (
              <DeltaChip val={delta.fitness} />
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            {hasData ? (
              <>
                <span className={`text-xs font-medium ${
                  cur!.return_pct >= 0 ? 'text-green-400' : 'text-red-400'
                }`}>
                  {cur!.return_pct >= 0 ? '+' : ''}{cur!.return_pct.toFixed(1)}%
                </span>
                <span className="text-xs text-slate-500">Sharpe {cur!.sharpe.toFixed(2)}</span>
                <span className="text-xs text-slate-500">Win {cur!.win_rate.toFixed(0)}%</span>
                {tsFormatted && (
                  <span className="text-xs text-slate-600 ml-auto">{tsFormatted.date}</span>
                )}
              </>
            ) : (
              <span className="text-xs text-slate-600">Not yet run</span>
            )}
          </div>
        </div>
        <span className="text-slate-500 text-xs shrink-0">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          {!hasData ? (
            <p className="text-xs text-slate-500 text-center py-2">
              Run "{goalMeta.label}" evolution to see results here.
            </p>
          ) : (
            <>
              {/* Key metrics grid */}
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-navy rounded-xl px-3 py-2">
                  <p className="text-xs text-slate-400 mb-1 flex items-center gap-1">
                    Fitness
                    <InfoBtn onClick={() => onInfo?.(GLOSSARY.fitness_score)} />
                  </p>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-green-400">{cur!.fitness.toFixed(3)}</span>
                    {delta && <DeltaChip val={delta.fitness} />}
                  </div>
                </div>
                <div className="bg-navy rounded-xl px-3 py-2">
                  <p className="text-xs text-slate-400 mb-1 flex items-center gap-1">
                    Return
                    <InfoBtn onClick={() => onInfo?.(GLOSSARY.return_pct)} />
                  </p>
                  <div className="flex items-center gap-2">
                    <span className={`text-sm font-bold ${cur!.return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {cur!.return_pct >= 0 ? '+' : ''}{cur!.return_pct.toFixed(1)}%
                    </span>
                    {delta && <DeltaChip val={delta.return_pct} suffix="%" />}
                  </div>
                </div>
                <div className="bg-navy rounded-xl px-3 py-2">
                  <p className="text-xs text-slate-400 mb-1 flex items-center gap-1">
                    Sharpe
                    <InfoBtn onClick={() => onInfo?.(GLOSSARY.sharpe_ratio)} />
                  </p>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-white">{cur!.sharpe.toFixed(2)}</span>
                    {delta && <DeltaChip val={delta.sharpe} />}
                  </div>
                </div>
                <div className="bg-navy rounded-xl px-3 py-2">
                  <p className="text-xs text-slate-400 mb-1 flex items-center gap-1">
                    Win / MaxDD
                    <InfoBtn onClick={() => onInfo?.(GLOSSARY.win_rate)} />
                  </p>
                  <span className="text-sm font-bold text-white">
                    {cur!.win_rate.toFixed(0)}% / <span className="text-red-400">{cur!.drawdown.toFixed(1)}%</span>
                  </span>
                </div>
              </div>

              {/* Capital ROI extra metrics */}
              {goalMeta.id === 'capital_roi' && (
                <div className="grid grid-cols-2 gap-2">
                  <div className="bg-navy rounded-xl px-3 py-2 col-span-2">
                    <p className="text-xs text-slate-400 mb-1">Annualised Margin ROI</p>
                    <span className="text-sm font-bold text-amber-400">
                      {cur!.annualised_margin_roi != null
                        ? `${(cur!.annualised_margin_roi * 100).toFixed(0)}%/yr`
                        : '—'}
                    </span>
                  </div>
                  <div className="bg-navy rounded-xl px-3 py-2">
                    <p className="text-xs text-slate-400 mb-1">Premium on Margin</p>
                    <span className="text-sm font-bold text-white">
                      {cur!.premium_on_margin != null
                        ? `${cur!.premium_on_margin.toFixed(2)}×`
                        : '—'}
                    </span>
                  </div>
                  <div className="bg-navy rounded-xl px-3 py-2">
                    <p className="text-xs text-slate-400 mb-1">Min Viable Capital</p>
                    <span className="text-sm font-bold text-white">
                      {cur!.min_viable_capital != null && cur!.min_viable_capital > 0
                        ? `$${cur!.min_viable_capital.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
                        : '—'}
                    </span>
                  </div>
                </div>
              )}

              {/* Last run timestamp */}
              {tsFormatted && (
                <p className="text-xs text-slate-600 text-right">
                  Last run: {tsFormatted.date} {tsFormatted.time}
                </p>
              )}

              {/* Version history — collapsible */}
              {(data?.history?.length ?? 0) > 1 && (
                <div className="rounded-xl overflow-hidden bg-navy">
                  <button
                    className="w-full flex items-center justify-between px-3 py-2 text-left"
                    onClick={() => setHistOpen(h => !h)}
                  >
                    <span className="text-xs text-slate-400 font-medium">
                      Version history ({data!.history.length} runs)
                    </span>
                    <span className="text-slate-500 text-xs">{histOpen ? '▲' : '▼'}</span>
                  </button>
                  {histOpen && (
                    <div className="px-3 pb-3 space-y-1">
                      {[...data!.history].reverse().map(h => (
                        <HistoryRow
                          key={h.version}
                          entry={h}
                          isCurrent={h.version === version}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
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
  const [summary,     setSummary]     = useState<OptimizerSummary | null>(null)
  const [sweepData,   setSweepData]   = useState<SweepResults | null>(null)
  const [evolveAll,   setEvolveAll]   = useState<EvolveAllResults | null>(null)
  const [running,     setRunning]     = useState(false)
  const [completed,   setCompleted]   = useState(false)
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState('')
  const [mode,        setMode]        = useState<OptMode>('sweep')
  const [fitnessGoal, setFitnessGoal] = useState<EvolveGoal>('balanced')
  const [launching,   setLaunching]   = useState(false)
  const [launchMsg,   setLaunchMsg]   = useState('')
  const [info,        setInfo]        = useState<InfoEntry | null>(null)
  const [sweepOpen,   setSweepOpen]   = useState(false)
  const [wfOpen,      setWfOpen]      = useState(false)
  const [mcOpen,      setMcOpen]      = useState(false)
  const [progress,    setProgress]    = useState<EvolutionProgress | null>(null)
  const [testingConfig, setTestingConfig] = useState<string | null>(null)

  // Save-as-named-config dialog state
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [saveName,       setSaveName]       = useState('')
  const [saveNotes,      setSaveNotes]      = useState('')
  const [savingConfig,   setSavingConfig]   = useState(false)
  const [saveConfigMsg,  setSaveConfigMsg]  = useState('')

  const fastPollRef     = useRef<ReturnType<typeof setInterval> | null>(null)
  const slowPollRef     = useRef<ReturnType<typeof setInterval> | null>(null)
  const progressPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      // Core data — if any of these fail, show the error banner
      const [s, r, sw] = await Promise.all([
        getOptimizerSummary(),
        getOptimizerRunning(),
        getSweepResults(),
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
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }

    // Evolution history — isolated so a missing endpoint (server restart needed)
    // doesn't crash the rest of the optimizer tab
    try {
      const ev = await getEvolveResultsAll()
      setEvolveAll(ev ?? null)
    } catch {
      // Silently ignore — panels show "Not yet run" until server is restarted
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

  async function handleSaveConfig() {
    if (!saveName.trim()) return
    setSavingConfig(true)
    setSaveConfigMsg('')
    try {
      const cur = mode === 'evolve' && evolveAll?.[fitnessGoal]?.current
      await apiSaveConfig({
        name: saveName.trim(),
        source: 'evolved',
        notes: saveNotes.trim() || undefined,
        fitness: cur ? cur.fitness : summary?.best_fitness ?? undefined,
        total_return_pct: cur ? cur.return_pct : undefined,
        sharpe: cur ? cur.sharpe : undefined,
        params: {},
      })
      setSaveConfigMsg(`✅ Saved as '${saveName.trim()}' — go to Pipeline to validate it →`)
      setShowSaveDialog(false)
    } catch (e) {
      setSaveConfigMsg(String(e))
    } finally {
      setSavingConfig(false)
    }
  }

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

      {/* Config context — what is this testing? */}
      <div className="bg-card rounded-2xl p-4 border border-border">
        <ConfigSelector
          value={testingConfig}
          onChange={setTestingConfig}
          label="Testing config"
          showStats
        />
      </div>

      {/* Save-as-named-config message */}
      {saveConfigMsg && (
        <div className={`rounded-xl px-4 py-3 text-sm border ${
          saveConfigMsg.startsWith('✅')
            ? 'bg-green-950 border-green-800 text-green-300'
            : 'bg-red-950 border-red-800 text-red-300'
        }`}>
          {saveConfigMsg}
        </div>
      )}

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
              {FITNESS_GOALS.map((g, idx) => (
                <div
                  key={g.id}
                  onClick={() => setFitnessGoal(g.id)}
                  className={`rounded-xl p-3 text-left border transition-colors cursor-pointer ${
                    idx === FITNESS_GOALS.length - 1 && FITNESS_GOALS.length % 2 !== 0
                      ? 'col-span-2'
                      : ''
                  } ${
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

        {/* Save as named config — available after a completed run */}
        {(completed || summary?.best_fitness != null) && (
          <button
            onClick={() => {
              const autoName = mode === 'evolve'
                ? `${fitnessGoal}_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}`
                : `sweep_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}`
              setSaveName(autoName)
              setSaveNotes('')
              setSaveConfigMsg('')
              setShowSaveDialog(true)
            }}
            className="w-full py-2.5 rounded-xl border border-amber-700 text-amber-300 text-sm hover:bg-amber-900/30"
          >
            Save as named config…
          </button>
        )}

        {/* Save dialog */}
        {showSaveDialog && (
          <div className="space-y-2 pt-1 border-t border-border">
            <p className="text-xs text-slate-400 font-medium">Save config as</p>
            <input
              type="text"
              value={saveName}
              onChange={e => setSaveName(e.target.value)}
              placeholder="Config name"
              className="w-full bg-navy border border-amber-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-amber-400"
            />
            <input
              type="text"
              value={saveNotes}
              onChange={e => setSaveNotes(e.target.value)}
              placeholder="Notes (optional)"
              className="w-full bg-navy border border-border rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-amber-400"
            />
            <div className="flex gap-2">
              <button
                onClick={handleSaveConfig}
                disabled={savingConfig || !saveName.trim()}
                className="flex-1 py-2 rounded-xl bg-amber-700 hover:bg-amber-600 disabled:opacity-40 text-white text-sm font-semibold"
              >
                {savingConfig ? 'Saving…' : 'Save Config'}
              </button>
              <button
                onClick={() => setShowSaveDialog(false)}
                className="px-3 py-2 rounded-xl bg-slate-700 text-slate-300 text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
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

      {/* Evolution results — one panel per goal */}
      <div className="space-y-2">
        <p className="text-xs font-medium text-slate-400 px-1">Evolution Goals</p>
        {FITNESS_GOALS.map(g => (
          <EvolveGoalPanel
            key={g.id}
            goalMeta={g}
            data={evolveAll?.[g.id]}
            onInfo={setInfo}
          />
        ))}
      </div>

      {/* No results placeholder */}
      {!sweepData && !evolveAll && !summary?.best_genome && (
        <div className="bg-card rounded-2xl p-6 border border-border text-center text-slate-500 text-sm">
          No results yet — run Sweep or Evolve to see results here.
        </div>
      )}
    </div>
  )
}
