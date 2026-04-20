import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../api.js'

const C = {
  card:  '#1e293b',
  green: '#22c55e',
  red:   '#ef4444',
  amber: '#f59e0b',
  muted: '#94a3b8',
  blue:  '#38bdf8',
  bg:    '#0f172a',
}

const ACCURACY_COLOR = { good: C.green, moderate: C.amber, poor: C.red }
const VERDICT_COLOR  = {
  robust: C.green, marginal: C.amber,
  'likely overfit': C.red, 'fails under stress': C.red,
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function SummaryCard({ label, value, color, sub }) {
  return (
    <div className="rounded-xl p-4 flex flex-col gap-1" style={{ background: C.card }}>
      <div className="text-xs" style={{ color: C.muted }}>{label}</div>
      <div className="text-lg font-bold" style={{ color: color || 'white' }}>{value ?? '—'}</div>
      {sub && <div className="text-xs" style={{ color: C.muted }}>{sub}</div>}
    </div>
  )
}

function fmtTs(iso) {
  if (!iso) return null
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch { return null }
}

const OPT_MODES = [
  { value: 'sweep',        label: 'Sweep' },
  { value: 'evolve',       label: 'Evolve' },
  { value: 'walk_forward', label: 'Walk-Forward' },
  { value: 'monte_carlo',  label: 'Monte Carlo' },
  { value: 'reconcile',    label: 'Reconcile' },
]

// ── Sweep results display ─────────────────────────────────────────────────────

function sensitivityLabel(entries) {
  const fits = entries.map(e => e.fitness)
  const maxF = Math.max(...fits)
  const minF = Math.min(...fits)
  if (maxF <= 0) return { label: '—', color: C.muted }
  const range = (maxF - minF) / maxF
  if (range > 0.15) return { label: 'Sensitive ⚡', color: C.amber }
  if (range < 0.05) return { label: 'Flat —',       color: C.muted }
  return { label: 'Moderate',     color: C.blue }
}

function sensitivityScore(entries) {
  const fits = entries.map(e => e.fitness)
  const maxF = Math.max(...fits)
  const minF = Math.min(...fits)
  return maxF > 0 ? (maxF - minF) / maxF : 0
}

function ParamSweepRow({ param, entries, bestValue }) {
  const maxFit = Math.max(...entries.map(e => e.fitness), 0.001)
  const { label, color } = sensitivityLabel(entries)
  const [expanded, setExpanded] = useState(false)
  const displayName = param.replace(/_/g, ' ')

  return (
    <div className="rounded-lg overflow-hidden" style={{ background: '#0f172a' }}>
      {/* Header row */}
      <button
        className="w-full flex items-center justify-between px-3 py-2.5 text-left"
        onClick={() => setExpanded(e => !e)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium text-white truncate">{displayName}</span>
          <span className="text-xs px-1.5 py-0.5 rounded-full shrink-0"
                style={{ background: color + '22', color }}>
            {label}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0 ml-2">
          <span className="text-xs font-mono" style={{ color: C.green }}>
            best={typeof bestValue === 'number' ? bestValue.toFixed(3) : bestValue}
          </span>
          <span className="text-xs" style={{ color: C.muted }}>{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {/* Expanded bar chart */}
      {expanded && (
        <div className="px-3 pb-3 flex flex-col gap-1">
          {entries.map((e, i) => {
            const isBest = e.value === bestValue
            const pct = maxFit > 0 ? Math.max((e.fitness / maxFit) * 100, 2) : 2
            return (
              <div key={i} className="flex items-center gap-2">
                <span className="text-xs font-mono w-14 text-right shrink-0"
                      style={{ color: isBest ? C.green : C.muted }}>
                  {typeof e.value === 'number' ? e.value.toFixed(3) : e.value}
                </span>
                <div className="flex-1 rounded-sm overflow-hidden" style={{ background: '#1e293b', height: 14 }}>
                  <div
                    className="h-full rounded-sm transition-all"
                    style={{
                      width: `${pct}%`,
                      background: isBest ? C.green : C.blue + '88',
                    }}
                  />
                </div>
                <span className="text-xs font-mono w-10 shrink-0"
                      style={{ color: isBest ? C.green : C.muted }}>
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

function SweepResultsSection({ data }) {
  if (!data || !data.params?.length) return null

  // Sort params by sensitivity descending
  const sorted = [...data.params].sort((a, b) => {
    const ra = sensitivityScore(data.results[a] || [])
    const rb = sensitivityScore(data.results[b] || [])
    return rb - ra
  })

  // Find overall best genome point
  let overallBest = null
  for (const param of data.params) {
    for (const e of (data.results[param] || [])) {
      if (!overallBest || e.fitness > overallBest.fitness) {
        overallBest = { param, ...e }
      }
    }
  }

  const runTime = fmtTs(data.timestamp)

  return (
    <div className="rounded-xl p-4 flex flex-col gap-3" style={{ background: C.card }}>
      <div className="flex items-start justify-between gap-2">
        <div className="font-semibold text-white">Last Sweep Results</div>
        {runTime && <span className="text-xs shrink-0" style={{ color: C.muted }}>{runTime}</span>}
      </div>

      {/* Summary line */}
      {overallBest && (
        <div className="rounded-lg px-3 py-2 text-xs" style={{ background: '#0f172a' }}>
          <span style={{ color: C.muted }}>Swept </span>
          <span className="font-bold text-white">{data.params.length}</span>
          <span style={{ color: C.muted }}> params · Best: </span>
          <span className="font-bold" style={{ color: C.green }}>
            {overallBest.param.replace(/_/g, ' ')}={typeof overallBest.value === 'number'
              ? overallBest.value.toFixed(3) : overallBest.value}
          </span>
          <span style={{ color: C.muted }}> (fitness </span>
          <span className="font-bold" style={{ color: C.green }}>{overallBest.fitness.toFixed(2)}</span>
          <span style={{ color: C.muted }}>)</span>
        </div>
      )}

      {/* Per-param rows */}
      <div className="flex flex-col gap-1.5">
        {sorted.map(param => (
          <ParamSweepRow
            key={param}
            param={param}
            entries={data.results[param] || []}
            bestValue={data.best_per_param[param]?.value}
          />
        ))}
      </div>
    </div>
  )
}

// ── Evolve results display ────────────────────────────────────────────────────

function EvolveResultsSection({ data }) {
  if (!data || !data.top_genomes?.length) return null

  const runTime = fmtTs(data.timestamp)

  return (
    <div className="rounded-xl p-4 flex flex-col gap-3" style={{ background: C.card }}>
      <div className="flex items-center justify-between">
        <div className="font-semibold text-white">Evolution Results</div>
        <div className="flex items-center gap-2">
          {data.total_evaluated > 0 && (
            <span className="text-xs" style={{ color: C.muted }}>
              {data.total_evaluated} evaluated
            </span>
          )}
          {runTime && <span className="text-xs" style={{ color: C.muted }}>{runTime}</span>}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        {data.top_genomes.slice(0, 5).map((g, i) => {
          const isBest = i === 0
          return (
            <div
              key={i}
              className="rounded-lg p-3"
              style={{
                background: '#0f172a',
                border: isBest ? `1px solid ${C.green}44` : '1px solid transparent',
              }}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold" style={{ color: isBest ? C.green : C.muted }}>
                  {isBest ? '★ #1 Best' : `#${i + 1}`}
                </span>
                <span className="text-sm font-bold" style={{ color: isBest ? C.green : 'white' }}>
                  {g.fitness.toFixed(3)}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <span style={{ color: C.muted }}>Return:
                  <b style={{ color: g.return_pct >= 0 ? C.green : C.red }}>
                    {' '}{g.return_pct >= 0 ? '+' : ''}{g.return_pct.toFixed(1)}%
                  </b>
                </span>
                <span style={{ color: C.muted }}>Sharpe:
                  <b style={{ color: 'white' }}> {g.sharpe.toFixed(2)}</b>
                </span>
                <span style={{ color: C.muted }}>Win Rate:
                  <b style={{ color: 'white' }}> {g.win_rate.toFixed(0)}%</b>
                </span>
                <span style={{ color: C.muted }}>Max DD:
                  <b style={{ color: C.red }}> {g.drawdown.toFixed(1)}%</b>
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Main tab ──────────────────────────────────────────────────────────────────

export default function TabOptimizer() {
  const [summary,      setSummary]      = useState(null)
  const [sweepData,    setSweepData]    = useState(null)
  const [evolveData,   setEvolveData]   = useState(null)
  const [running,      setRunning]      = useState(false)
  const [wasRunning,   setWasRunning]   = useState(false)
  const [completed,    setCompleted]    = useState(false)
  const [mode,         setMode]         = useState('sweep')
  const [busy,         setBusy]         = useState(false)
  const [msg,          setMsg]          = useState('')
  const [error,        setError]        = useState('')

  const fastPollRef = useRef(null)  // 5s poll while running
  const slowPollRef = useRef(null)  // 15s poll at rest

  const loadAll = useCallback(async () => {
    try {
      const [s, r, sw, ev] = await Promise.all([
        api.optimizerSummary(),
        api.optimizerRunning(),
        api.sweepResults(),
        api.evolveResults(),
      ])
      setSummary(s)
      const nowRunning = r.running

      // Detect running → stopped transition
      setRunning(prev => {
        if (prev && !nowRunning) {
          setCompleted(true)
          setTimeout(() => setCompleted(false), 8000)
        }
        return nowRunning
      })

      setSweepData(sw?.params?.length ? sw : null)
      setEvolveData(ev?.top_genomes?.length ? ev : null)
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [])

  // Start fast poll when running, slow poll otherwise
  useEffect(() => {
    loadAll()

    slowPollRef.current = setInterval(loadAll, 15_000)
    return () => {
      clearInterval(slowPollRef.current)
      clearInterval(fastPollRef.current)
    }
  }, [loadAll])

  // Upgrade to 5s polling when the optimizer is running
  useEffect(() => {
    clearInterval(fastPollRef.current)
    if (running) {
      fastPollRef.current = setInterval(loadAll, 5_000)
    }
    return () => clearInterval(fastPollRef.current)
  }, [running, loadAll])

  async function runOptimizer() {
    setBusy(true)
    setMsg('')
    setCompleted(false)
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

      {/* Completed banner */}
      {completed && (
        <div className="rounded-lg px-4 py-3 text-sm font-semibold"
             style={{ background: '#22c55e22', color: C.green, border: `1px solid ${C.green}44` }}>
          ✓ Optimizer run completed — results updated
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-2">
        <SummaryCard
          label="Best Fitness"
          value={summary?.best_fitness?.toFixed(3) ?? '—'}
          color={C.blue}
          sub={summary?.sweep_params_count > 0
            ? `${summary.sweep_params_count} params swept` : undefined}
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
            style={{ background: C.bg, border: '1px solid #334155' }}
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
          style={{ background: running ? C.amber : busy ? '#475569' : C.green }}
          disabled={busy}
          onClick={runOptimizer}
        >
          {running ? '⏳ Running…' : busy ? 'Starting…' : '▶ Run'}
        </button>

        {running && (
          <p className="text-xs text-center animate-pulse" style={{ color: C.amber }}>
            Optimizer running · refreshing every 5s
          </p>
        )}

        {msg && !running && (
          <p className="text-xs text-center" style={{ color: msg.startsWith('Error') ? C.red : C.green }}>
            {msg}
          </p>
        )}
      </div>

      {/* Sweep Results */}
      <SweepResultsSection data={sweepData} />

      {/* Evolve Results */}
      <EvolveResultsSection data={evolveData} />

      {/* Best genome (from walk-forward / evolve) */}
      {summary?.best_genome && (
        <div className="rounded-xl p-4" style={{ background: C.card }}>
          <div className="font-semibold text-white mb-3">Best Genome (Applied)</div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            {Object.entries(summary.best_genome)
              .filter(([k]) => !['starting_equity', 'use_regime_filter', 'regime_ma_days'].includes(k))
              .map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span style={{ color: C.muted }}>{k.replace(/_/g, ' ')}</span>
                  <span className="font-mono font-bold text-white">
                    {typeof v === 'number' ? v.toFixed(3) : v}
                  </span>
                </div>
              ))
            }
          </div>
        </div>
      )}

      {!sweepData && !evolveData && !summary?.best_genome && (
        <p className="text-center py-6 text-sm" style={{ color: C.muted }}>
          No results yet — run Sweep or Evolve to see results here.
        </p>
      )}
    </div>
  )
}
