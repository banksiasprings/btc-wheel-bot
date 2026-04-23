import { useState, useEffect, useCallback } from 'react'
import {
  getEvolveResultsAll, EvolveAllResults, EvolveGoal,
  getFarmStatus, FarmStatus, BotFarmEntry,
  listConfigs, NamedConfig,
  runOptimizer, saveConfig as apiSaveConfig,
  assignBotConfig, promoteConfig,
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

// ── Step 3 — Paper Trade ──────────────────────────────────────────────────────

function StepPaperTrade({
  open, onToggle, bots, onAssigned,
}: {
  open: boolean
  onToggle: () => void
  bots: BotFarmEntry[]
  onAssigned: () => void
}) {
  const [selectedBot, setSelectedBot]       = useState<string | null>(bots[0]?.id ?? null)
  const [selectedConfig, setSelectedConfig] = useState<string | null>(null)
  const [assigning, setAssigning]           = useState(false)
  const [assignMsg, setAssignMsg]           = useState('')

  const bot = bots.find(b => b.id === selectedBot) ?? null
  const readinessScore  = bot?.readiness.score  ?? 0
  const readinessTotal  = bot?.readiness.total  ?? 8
  const configName      = bot?.config_name ?? null

  const status: StepStatus = bots.length === 0 ? 'not_started'
    : bots.some(b => b.readiness.ready) ? 'complete'
    : bots.some(b => b.readiness.score > 0) ? 'in_progress'
    : 'not_started'

  async function handleAssign() {
    if (!selectedBot || !selectedConfig) return
    setAssigning(true)
    setAssignMsg('')
    try {
      await assignBotConfig(selectedBot, selectedConfig)
      setAssignMsg(`✅ Assigned '${selectedConfig}' to ${bot?.name ?? selectedBot}`)
      onAssigned()
    } catch (e) {
      setAssignMsg(`❌ ${String(e)}`)
    } finally {
      setAssigning(false)
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
          <span className="text-sm font-bold text-white uppercase tracking-wide">Step 3 · Paper Trade</span>
          <p className="text-xs text-slate-400 mt-0.5">
            {status === 'complete'
              ? `${bots.filter(b => b.readiness.ready).length} bot(s) ready for live`
              : bots.length > 0
              ? `Best: ${Math.max(...bots.map(b => b.readiness.score))}/${readinessTotal} checks`
              : 'Run in bot farm to verify live readiness'}
          </p>
        </div>
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          {bots.length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-2">
              No bots running. Start the farm first.
            </p>
          ) : (
            <>
              {/* Bot selector */}
              <div className="space-y-1.5">
                <span className="text-xs text-slate-400 font-medium">Assign to bot</span>
                <select
                  value={selectedBot ?? ''}
                  onChange={e => setSelectedBot(e.target.value)}
                  className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-amber-500"
                >
                  {bots.map(b => (
                    <option key={b.id} value={b.id}>
                      {b.name} · {b.readiness.score}/{b.readiness.total} checks
                    </option>
                  ))}
                </select>
              </div>

              {/* Config selector */}
              <ConfigSelector
                value={selectedConfig}
                onChange={setSelectedConfig}
                label="Config to assign"
                showStats
              />

              {/* Current config badge */}
              {configName && (
                <p className="text-xs text-slate-500">
                  Currently running: <span className="text-amber-400 font-medium">{configName}</span>
                </p>
              )}

              {/* Readiness bar for selected bot */}
              {bot && (
                <ReadinessProgressBar
                  score={bot.readiness.score}
                  total={bot.readiness.total}
                />
              )}

              {/* Bot quick stats */}
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

              {assignMsg && (
                <p className={`text-xs px-3 py-2 rounded-lg border ${
                  assignMsg.startsWith('✅')
                    ? 'bg-green-950 border-green-800 text-green-300'
                    : 'bg-red-950 border-red-800 text-red-300'
                }`}>{assignMsg}</p>
              )}

              <button
                onClick={handleAssign}
                disabled={assigning || !selectedBot || !selectedConfig}
                className="w-full py-3 rounded-xl bg-amber-800 hover:bg-amber-700 disabled:opacity-40 text-amber-200 text-sm font-semibold"
              >
                {assigning ? 'Assigning…' : 'Assign Config'}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Step 4 — Go Live ──────────────────────────────────────────────────────────

function StepGoLive({
  open, onToggle, bots,
}: {
  open: boolean
  onToggle: () => void
  bots: BotFarmEntry[]
}) {
  const [selectedBot, setSelectedBot]   = useState<string | null>(bots.find(b => b.readiness.ready)?.id ?? null)
  const [selectedConfig, setSelectedConfig] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen]   = useState(false)
  const [promoting, setPromoting]       = useState(false)
  const [promoteMsg, setPromoteMsg]     = useState('')

  const readyBots = bots.filter(b => b.readiness.ready)
  const isLocked  = readyBots.length === 0
  const bot       = bots.find(b => b.id === selectedBot) ?? null
  const status: StepStatus = isLocked ? 'locked' : 'not_started'

  async function handlePromote() {
    if (!selectedConfig) return
    setPromoting(true)
    setPromoteMsg('')
    try {
      const r = await promoteConfig(selectedConfig)
      setPromoteMsg(`✅ ${r.message ?? 'Promoted — live bot will restart'}`)
      setConfirmOpen(false)
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
            Step 4 · Go Live
          </span>
          <p className="text-xs text-slate-500 mt-0.5">
            {isLocked
              ? `Locked — needs 8/8 readiness (best: ${Math.max(0, ...bots.map(b => b.readiness.score))}/8)`
              : `${readyBots.length} bot(s) ready · promote to live`}
          </p>
        </div>
        {!isLocked && <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>}
      </button>

      {open && !isLocked && (
        <div className="px-4 pb-4 space-y-3">
          {/* Bot selector (only ready bots) */}
          <div className="space-y-1.5">
            <span className="text-xs text-slate-400 font-medium">Ready bot</span>
            <select
              value={selectedBot ?? ''}
              onChange={e => setSelectedBot(e.target.value)}
              className="w-full bg-navy border border-green-800 rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none"
            >
              {readyBots.map(b => (
                <option key={b.id} value={b.id}>{b.name} — 8/8 ✅</option>
              ))}
            </select>
          </div>

          {/* Config selector */}
          <ConfigSelector
            value={selectedConfig}
            onChange={setSelectedConfig}
            label="Config to promote"
            showStats
          />

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
              This will overwrite the live config with{' '}
              <span className="text-green-400 font-medium">{selectedConfig}</span>.{' '}
              The live bot will restart. Are you sure?
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setConfirmOpen(false)}
                className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handlePromote}
                disabled={promoting}
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
        Follow these steps in order: Evolve → Validate → Paper Trade → Go Live
      </p>

      <StepEvolve
        open={openStep === 1}
        onToggle={() => toggle(1)}
        evolveAll={evolveAll}
        onSaved={name => {
          setConfigs(c => [...c.filter(x => x.name !== name), {
            name,
            source: 'evolved',
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
        bots={bots}
        onAssigned={refresh}
      />

      <StepConnector />

      <StepGoLive
        open={openStep === 4}
        onToggle={() => toggle(4)}
        bots={bots}
      />
    </div>
  )
}
