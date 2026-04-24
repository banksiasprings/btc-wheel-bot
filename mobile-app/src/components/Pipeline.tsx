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
} from '../api'
import ConfigSelector from './ConfigSelector'

// ── Types ─────────────────────────────────────────────────────────────────────

type StepStatus = 'not_started' | 'in_progress' | 'complete' | 'locked'

const EVOLVE_GOALS: { id: EvolveGoal; icon: string; label: string }[] = [
  { id: 'balanced',    icon: '🎯', label: 'Balanced'    },
  { id: 'max_yield',   icon: '🚀', label: 'Max Yield'   },
  { id: 'safest',      icon: '🛡', label: 'Safest'      },
  { id: 'sharpe',      icon: '⚖️', label: 'Sharpe'      },
  { id: 'capital_roi', icon: '📊', label: 'Capital ROI' },
]

const SWEEP_PARAMS: { key: string; label: string }[] = [
  { key: 'iv_rank_threshold',        label: 'IV Rank Threshold'  },
  { key: 'target_delta_min',         label: 'Min Delta'          },
  { key: 'target_delta_max',         label: 'Max Delta'          },
  { key: 'min_dte',                  label: 'Min DTE'            },
  { key: 'max_dte',                  label: 'Max DTE'            },
  { key: 'max_equity_per_leg',       label: 'Max Equity / Leg'   },
  { key: 'premium_fraction_of_spot', label: 'Premium Fraction'   },
]

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
  const [goal, setGoal]           = useState<EvolveGoal>('capital_roi')
  const [launching, setLaunching] = useState(false)
  const [launchMsg, setLaunchMsg] = useState('')
  const [showSave, setShowSave]   = useState(false)
  const [saveName, setSaveName]   = useState('')
  const [saveNotes, setSaveNotes] = useState('')
  const [saving, setSaving]       = useState(false)
  const [saveMsg, setSaveMsg]     = useState('')

  const goalData    = evolveAll?.[goal]
  const hasData     = (goalData?.version ?? 0) > 0 && goalData?.current != null
  const cur         = goalData?.current
  const lastRunName = hasData
    ? `${goal}_${new Date(goalData!.timestamp ?? '').toISOString().slice(0, 10).replace(/-/g, '')}`
    : null

  // Status
  const status: StepStatus = hasData ? 'complete' : 'not_started'

  async function handleRun() {
    setLaunching(true)
    setLaunchMsg('')
    try {
      const r = await runOptimizer('evolve', undefined, goal)
      setLaunchMsg(`Started (PID ${r.pid}) — results will appear when done`)
    } catch (e) {
      setLaunchMsg(String(e))
    } finally {
      setLaunching(false)
    }
  }

  async function handleSave() {
    if (!saveName.trim() || !cur) return
    setSaving(true)
    try {
      await apiSaveConfig({
        name: saveName.trim(),
        source: 'evolved',
        notes: saveNotes.trim() || undefined,
        fitness: cur.fitness,
        total_return_pct: cur.return_pct,
        sharpe: cur.sharpe,
        params: {},
      })
      setSaveMsg(`✅ Saved as '${saveName.trim()}' — select it in Step 2 to validate`)
      onSaved(saveName.trim())
      setShowSave(false)
    } catch (e) {
      setSaveMsg(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      status === 'complete' ? 'border-green-800' : 'border-border'
    }`}>
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
        onClick={onToggle}
      >
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-white uppercase tracking-wide">Step 1 · Evolve</span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            {hasData
              ? `Last: ${lastRunName} · Fitness ${cur!.fitness.toFixed(2)} · Return ${cur!.return_pct >= 0 ? '+' : ''}${cur!.return_pct.toFixed(1)}%`
              : 'Find the best config via genetic evolution'}
          </p>
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          {/* Goal selector */}
          <div className="space-y-1.5">
            <p className="text-xs text-slate-400 font-medium">Fitness Goal</p>
            <div className="grid grid-cols-2 gap-2">
              {EVOLVE_GOALS.map((g, idx) => (
                <button
                  key={g.id}
                  onClick={() => setGoal(g.id)}
                  className={`rounded-xl p-2.5 text-left border text-xs transition-colors ${
                    idx === EVOLVE_GOALS.length - 1 && EVOLVE_GOALS.length % 2 !== 0
                      ? 'col-span-2'
                      : ''
                  } ${
                    goal === g.id
                      ? 'bg-amber-900 border-amber-600 text-white'
                      : 'bg-navy border-border text-slate-400 hover:border-slate-500'
                  }`}
                >
                  {g.icon} {g.label}
                </button>
              ))}
            </div>
          </div>

          {launchMsg && (
            <p className={`text-xs px-3 py-2 rounded-lg border ${
              launchMsg.startsWith('Started')
                ? 'bg-green-950 border-green-800 text-green-300'
                : 'bg-red-950 border-red-800 text-red-300'
            }`}>{launchMsg}</p>
          )}

          <button
            onClick={handleRun}
            disabled={launching}
            className="w-full bg-amber-700 hover:bg-amber-600 disabled:opacity-40 text-white font-semibold py-3 rounded-xl text-sm"
          >
            {launching ? 'Launching…' : `Run ${EVOLVE_GOALS.find(g => g.id === goal)?.label ?? ''} Evolution`}
          </button>

          {/* Results summary + Save */}
          {hasData && cur && (
            <div className="bg-navy rounded-xl px-3 py-3 space-y-2">
              <div className="flex flex-wrap gap-3 text-xs">
                <span>Fitness <span className="text-green-400 font-mono font-bold">{cur.fitness.toFixed(3)}</span></span>
                <span>Return <span className={`font-mono font-bold ${cur.return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {cur.return_pct >= 0 ? '+' : ''}{cur.return_pct.toFixed(1)}%
                </span></span>
                <span>Sharpe <span className="text-white font-mono">{cur.sharpe.toFixed(2)}</span></span>
                <span>Win <span className="text-white font-mono">{cur.win_rate.toFixed(0)}%</span></span>
              </div>

              {!showSave ? (
                <button
                  onClick={() => { setShowSave(true); setSaveName(lastRunName ?? ''); setSaveMsg('') }}
                  className="w-full py-2 rounded-xl bg-green-800 hover:bg-green-700 text-green-200 text-xs font-semibold"
                >
                  Save as named config →
                </button>
              ) : (
                <div className="space-y-2">
                  <input
                    type="text"
                    value={saveName}
                    onChange={e => setSaveName(e.target.value)}
                    placeholder="Config name"
                    className="w-full bg-slate-900 border border-border rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-green-500"
                  />
                  <input
                    type="text"
                    value={saveNotes}
                    onChange={e => setSaveNotes(e.target.value)}
                    placeholder="Notes (optional)"
                    className="w-full bg-slate-900 border border-border rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-green-500"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={handleSave}
                      disabled={saving || !saveName.trim()}
                      className="flex-1 py-2 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-semibold"
                    >
                      {saving ? 'Saving…' : 'Save Config'}
                    </button>
                    <button
                      onClick={() => setShowSave(false)}
                      className="px-3 py-2 rounded-xl bg-slate-700 text-slate-300 text-sm"
                    >
                      Cancel
                    </button>
                  </div>
                  {saveMsg && (
                    <p className={`text-xs px-3 py-2 rounded-lg border ${
                      saveMsg.startsWith('✅')
                        ? 'bg-green-950 border-green-800 text-green-300'
                        : 'bg-red-950 border-red-800 text-red-300'
                    }`}>{saveMsg}</p>
                  )}
                </div>
              )}
            </div>
          )}
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
  const [selectedConfig, setSelectedConfig] = useState<string | null>(null)
  const [running, setRunning]               = useState<Record<string, boolean>>({})
  const [results, setResults]               = useState<Record<string, string>>({})

  const tests = [
    { key: 'walk_forward', label: 'Walk-Forward',  mode: 'walk_forward' },
    { key: 'monte_carlo',  label: 'Monte Carlo',   mode: 'monte_carlo'  },
    { key: 'reconcile',    label: 'Reconcile',     mode: 'reconcile'    },
  ] as const

  const doneCount = Object.values(results).filter(v => v.startsWith('✅')).length
  const status: StepStatus = doneCount === tests.length ? 'complete'
    : doneCount > 0 ? 'in_progress'
    : 'not_started'

  async function runTest(mode: string, key: string) {
    setRunning(r => ({ ...r, [key]: true }))
    setResults(r => ({ ...r, [key]: '🔄 Running…' }))
    try {
      await runOptimizer(mode, undefined, undefined, selectedConfig)
      setResults(r => ({ ...r, [key]: '✅ Done' }))
    } catch (e) {
      setResults(r => ({ ...r, [key]: `❌ ${String(e)}` }))
    } finally {
      setRunning(r => ({ ...r, [key]: false }))
    }
  }

  async function runAll() {
    for (const t of tests) {
      await runTest(t.mode, t.key)
    }
  }

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      status === 'complete' ? 'border-green-800' : status === 'in_progress' ? 'border-amber-800' : 'border-border'
    }`}>
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
        onClick={onToggle}
      >
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-bold text-white uppercase tracking-wide">Step 2 · Validate</span>
          <p className="text-xs text-slate-400 mt-0.5">
            {status === 'complete'
              ? `All ${tests.length} tests passed`
              : status === 'in_progress'
              ? `${doneCount}/${tests.length} tests done`
              : 'Backtest · Monte Carlo · Walk-Forward'}
          </p>
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          <ConfigSelector
            value={selectedConfig}
            onChange={setSelectedConfig}
            label="Testing config"
            showStats
          />

          {/* Test list */}
          <div className="space-y-2">
            {tests.map(t => (
              <div key={t.key} className="flex items-center gap-3 bg-navy rounded-xl px-3 py-2.5">
                <span className="text-sm w-5 text-center">
                  {results[t.key]?.startsWith('✅') ? '✅' :
                   results[t.key]?.startsWith('❌') ? '❌' :
                   results[t.key]?.startsWith('🔄') ? '🔄' : '⬜'}
                </span>
                <span className="flex-1 text-sm text-white">{t.label}</span>
                <button
                  onClick={() => runTest(t.mode, t.key)}
                  disabled={running[t.key]}
                  className="text-xs px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-200"
                >
                  {running[t.key] ? 'Running…' : 'Run'}
                </button>
              </div>
            ))}
          </div>

          <button
            onClick={runAll}
            disabled={Object.values(running).some(Boolean)}
            className="w-full py-3 rounded-xl bg-green-800 hover:bg-green-700 disabled:opacity-40 text-green-200 text-sm font-semibold"
          >
            Run All Tests
          </button>

          {configs.length === 0 && (
            <p className="text-xs text-slate-500 text-center">
              Save a config in Step 1 first, then select it here.
            </p>
          )}
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
