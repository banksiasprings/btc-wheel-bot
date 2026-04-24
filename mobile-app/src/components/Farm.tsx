import { useState, useEffect, useCallback } from 'react'
import { getFarmStatus, startFarm, stopFarm, getBotLiveState, getBtcPrice, FarmStatus, BotFarmEntry, BotLiveState } from '../api'

// ── Formatting helpers ─────────────────────────────────────────────────────────

function fmt$(n: number | undefined | null) {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function fmtPct(n: number | undefined | null, decimals = 1) {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(decimals)}%`
}

function fmtTime(iso: string | undefined | null) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString()
  } catch {
    return iso
  }
}

/**
 * Annualise a cumulative return using compound growth:
 * ((1 + r)^(365/days) − 1) × 100
 * Returns null for bots with < 7 days of data (too noisy to annualise).
 */
function annualisedReturn(totalReturnPct: number | undefined | null, daysRunning: number | undefined | null): number | null {
  if (totalReturnPct == null || daysRunning == null || daysRunning < 7) return null
  const r = totalReturnPct / 100
  return ((1 + r) ** (365 / daysRunning) - 1) * 100
}

// ── Readiness bar ─────────────────────────────────────────────────────────────

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

function ReadinessBar({ score, total }: { score: number; total: number }) {
  const pct = total > 0 ? score / total : 0

  let barColor = 'bg-yellow-500'
  let label    = 'Testing'
  let labelCls = 'text-yellow-400'

  if (score === total) {
    barColor = 'bg-green-500'
    label    = 'Ready for live'
    labelCls = 'text-green-400'
  } else if (score >= 5) {
    barColor = 'bg-orange-400'
    label    = 'Almost ready'
    labelCls = 'text-orange-400'
  }

  return (
    <div className="mt-2 space-y-1">
      <div className="flex items-center justify-between">
        <span className={`text-xs font-semibold ${labelCls}`}>
          {score === total ? '✅ ' : ''}{label}
        </span>
        <span className="text-xs font-mono font-bold text-white">{score}/{total} checks</span>
      </div>
      <div className="h-3 rounded-full bg-slate-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
    </div>
  )
}

// ── Status dot ────────────────────────────────────────────────────────────────

function StatusDot({ status }: { status: string }) {
  const dot =
    status === 'running' ? 'bg-green-400 shadow-[0_0_6px_#22c55e]' :
    status === 'error'   ? 'bg-red-500' :
    'bg-yellow-400'
  return <span className={`inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${dot}`} />
}

// ── Bot card ─────────────────────────────────────────────────────────────────

function BotCard({ bot, onRefresh: _onRefresh }: { bot: BotFarmEntry; onRefresh: () => void }) {
  const [expanded, setExpanded]   = useState(false)
  const [promoteMsg, setPromoteMsg] = useState('')
  const [liveState, setLiveState]   = useState<BotLiveState | null>(null)
  const [liveLoading, setLiveLoading] = useState(false)

  const m = bot.metrics
  const r = bot.readiness
  const daysToReady = r.ready ? 0 : Math.max(0, 30 - (m.days_running ?? 0))

  // Fetch live state when card is expanded
  useEffect(() => {
    if (!expanded) return
    setLiveLoading(true)
    getBotLiveState(bot.id)
      .then(setLiveState)
      .catch(() => setLiveState(null))
      .finally(() => setLiveLoading(false))
  }, [expanded, bot.id])

  const configName: string = bot.config_name ?? 'Unassigned'

  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      {/* Header row */}
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left"
        onClick={() => setExpanded(e => !e)}
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <StatusDot status={bot.status} />
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
        <div className="flex items-center gap-2 flex-shrink-0 ml-2">
          <span className="text-slate-500 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {/* Readiness bar — always visible */}
      <div className="px-4 pb-3">
        <ReadinessBar score={r.score} total={r.total} />
      </div>

      {/* Metrics strip */}
      <div className="grid grid-cols-5 border-t border-border/40 text-center">
        {[
          { label: 'Trades',      value: String(m.num_trades ?? 0) },
          { label: 'Win',         value: fmtPct((m.win_rate ?? 0) * 100, 0) },
          { label: 'Sharpe',      value: m.sharpe != null ? m.sharpe.toFixed(2) : '—' },
          { label: 'DD',          value: fmtPct(-(m.max_drawdown ?? 0) * 100, 0) },
          { label: 'Ann. Return', value: fmtPct(annualisedReturn(m.total_return_pct, m.days_running), 1) },
        ].map(({ label, value }) => (
          <div key={label} className="py-2 border-r border-border/30 last:border-r-0">
            <p className="text-xs text-slate-500">{label}</p>
            <p className="text-xs font-medium text-white">{value}</p>
          </div>
        ))}
      </div>

      {/* Days info */}
      <div className="px-4 py-2 border-t border-border/30 flex items-center justify-between text-xs text-slate-500">
        <span>Running {(m.days_running ?? 0).toFixed(0)}d</span>
        {!r.ready && daysToReady > 0 && (
          <span className="text-slate-600">~{daysToReady.toFixed(0)}d to min days</span>
        )}
        {r.ready && <span className="text-green-500">All checks passed</span>}
        {bot.pid && <span>PID {bot.pid}</span>}
      </div>

      {/* Expandable section */}
      {expanded && (
        <div className="border-t border-border/40 px-4 py-3 space-y-1.5">

          {/* ── Kill switch banner ── */}
          {liveState?.kill_switch_active && (
            <div className="bg-red-950 border border-red-700 rounded-xl px-3 py-2.5 mb-3">
              <p className="text-red-300 text-xs font-bold">🛑 TRADING HALTED — Kill switch active</p>
              <p className="text-red-400 text-xs mt-0.5">Delete the KILL_SWITCH file on the server to resume.</p>
            </div>
          )}

          {/* ── Live position ── */}
          {liveLoading ? (
            <p className="text-xs text-slate-500 py-2">Loading live state…</p>
          ) : liveState ? (
            <div className="space-y-2 mb-3">
              <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">Live Activity</p>

              {/* Heartbeat freshness */}
              {liveState.heartbeat_age_seconds != null && (
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                    liveState.heartbeat_age_seconds < 180 ? 'bg-green-400' :
                    liveState.heartbeat_age_seconds < 600 ? 'bg-yellow-400' : 'bg-red-500'
                  }`} />
                  <span className="text-xs text-slate-400">
                    Heartbeat {liveState.heartbeat_age_seconds < 60
                      ? `${Math.round(liveState.heartbeat_age_seconds)}s ago`
                      : `${Math.round(liveState.heartbeat_age_seconds / 60)}m ago`}
                  </span>
                </div>
              )}

              {/* Current position */}
              {liveState.position?.open ? (
                <div className="bg-navy rounded-xl px-3 py-2.5 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-amber-300">
                      📋 Open {liveState.position.type?.toUpperCase()} Position
                    </span>
                    {liveState.position.dte != null && (
                      <span className="text-xs text-slate-500">{liveState.position.dte}d DTE</span>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
                    {liveState.position.strike != null && (
                      <>
                        <span className="text-slate-500">Strike</span>
                        <span className="text-white font-mono">{fmt$(liveState.position.strike)}</span>
                      </>
                    )}
                    {liveState.position.delta != null && (
                      <>
                        <span className="text-slate-500">Delta</span>
                        <span className="text-white font-mono">{liveState.position.delta.toFixed(3)}</span>
                      </>
                    )}
                    {liveState.position.contracts != null && (
                      <>
                        <span className="text-slate-500">Contracts</span>
                        <span className="text-white font-mono">{liveState.position.contracts}</span>
                      </>
                    )}
                    {liveState.position.unrealized_pnl_usd != null && (
                      <>
                        <span className="text-slate-500">Unrealised P&L</span>
                        <span className={`font-mono font-semibold ${
                          liveState.position.unrealized_pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'
                        }`}>
                          {liveState.position.unrealized_pnl_usd >= 0 ? '+' : ''}
                          {fmt$(liveState.position.unrealized_pnl_usd)}
                        </span>
                      </>
                    )}
                    {liveState.position.expiry && (
                      <>
                        <span className="text-slate-500">Expiry</span>
                        <span className="text-white font-mono text-xs">{liveState.position.expiry}</span>
                      </>
                    )}
                  </div>
                </div>
              ) : (
                <div className="bg-navy rounded-xl px-3 py-2 text-xs text-slate-500">
                  No open position — waiting for signal
                </div>
              )}

              {/* Live state summary */}
              {liveState.state && (
                <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs px-1">
                  {liveState.state.iv_rank != null && (
                    <>
                      <span className="text-slate-500">IV Rank</span>
                      <span className="text-white font-mono">{(liveState.state.iv_rank * 100).toFixed(1)}%</span>
                    </>
                  )}
                  {liveState.state.total_pnl_usd != null && (
                    <>
                      <span className="text-slate-500">Total P&L</span>
                      <span className={`font-mono font-semibold ${
                        liveState.state.total_pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {liveState.state.total_pnl_usd >= 0 ? '+' : ''}{fmt$(liveState.state.total_pnl_usd)}
                      </span>
                    </>
                  )}
                  {liveState.state.equity_usd != null && (
                    <>
                      <span className="text-slate-500">Equity</span>
                      <span className="text-white font-mono">{fmt$(liveState.state.equity_usd)}</span>
                    </>
                  )}
                  {liveState.state.total_cycles != null && (
                    <>
                      <span className="text-slate-500">Cycles</span>
                      <span className="text-white font-mono">{liveState.state.total_cycles}</span>
                    </>
                  )}
                </div>
              )}

              {/* Recent trades */}
              {liveState.recent_trades.length > 0 && (
                <div className="space-y-1">
                  <p className="text-xs text-slate-500 uppercase tracking-wide pt-1">Recent Trades</p>
                  {liveState.recent_trades.slice(0, 3).map((t, i) => {
                    const pnl = typeof t.pnl_usd === 'number' ? t.pnl_usd : 0
                    const positive = pnl >= 0
                    return (
                      <div key={i} className="flex items-center justify-between bg-navy rounded-lg px-2.5 py-1.5">
                        <div className="min-w-0">
                          <p className="text-xs text-white font-mono truncate">
                            {t.instrument ?? t.option_type ?? '—'}
                          </p>
                          <p className="text-xs text-slate-500">{t.reason ?? '—'}</p>
                        </div>
                        <span className={`text-xs font-semibold ml-2 flex-shrink-0 ${positive ? 'text-green-400' : 'text-red-400'}`}>
                          {positive ? '+' : ''}{fmt$(pnl)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          ) : null}

          {/* ── Readiness checklist ── */}
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-2">
            Readiness Checklist
          </p>
          {Object.entries(r.checks).map(([key, passed]) => {
            const label = CHECK_LABELS[key] ?? key
            const metricHint: Record<string, string> = {
              min_trades:     `${m.num_trades ?? 0} trades`,
              min_days:       `${(m.days_running ?? 0).toFixed(1)}d`,
              sharpe:         m.sharpe != null ? m.sharpe.toFixed(2) : '—',
              drawdown:       fmtPct(-(m.max_drawdown ?? 0) * 100, 1),
              win_rate:       fmtPct((m.win_rate ?? 0) * 100, 1),
              walk_forward:   'see optimizer',
              reconcile:      'see optimizer',
              no_kill_switch: passed ? 'clear' : 'active',
            }
            return (
              <div key={key} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm">{passed ? '✅' : '❌'}</span>
                  <span className={`text-xs ${passed ? 'text-slate-300' : 'text-slate-500'}`}>
                    {label}
                  </span>
                </div>
                <span className="text-xs text-slate-600">{metricHint[key] ?? ''}</span>
              </div>
            )
          })}

          {/* Config params */}
          {bot.config_summary && Object.keys(bot.config_summary).length > 0 && (
            <div className="mt-3 pt-2 border-t border-border/30">
              <p className="text-xs text-slate-500 uppercase tracking-wide mb-1.5">Config Params</p>
              <div className="grid grid-cols-2 gap-1 text-xs">
                {Object.entries(bot.config_summary).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-1">
                    <span className="text-slate-600 truncate">{k.replace(/_/g, ' ')}</span>
                    <span className="text-slate-400 font-mono">
                      {typeof v === 'number' ? (v < 1 ? v.toFixed(3) : v.toLocaleString()) : String(v ?? '—')}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Promote to Live — only shown when 8/8 ready; manage via Pipeline */}
          {r.ready && (
            <div className="mt-3 pt-2 border-t border-green-900/50 space-y-2">
              {promoteMsg && (
                <p className={`text-xs px-3 py-2 rounded-lg border ${
                  promoteMsg.startsWith('✅')
                    ? 'bg-green-950 border-green-800 text-green-300'
                    : 'bg-red-950 border-red-800 text-red-300'
                }`}>{promoteMsg}</p>
              )}
              <p className="text-xs text-green-400 text-center font-medium">
                ✅ Ready for live — go to Pipeline → Step 4 to promote
              </p>
            </div>
          )}
        </div>
      )}

    </div>
  )
}

// ── Leaderboard table ─────────────────────────────────────────────────────────

function Leaderboard({ bots }: { bots: BotFarmEntry[] }) {
  const sorted = [...bots].sort(
    (a, b) => (b.metrics.total_return_pct ?? 0) - (a.metrics.total_return_pct ?? 0)
  )

  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      <p className="text-xs text-slate-400 font-medium uppercase tracking-wide px-4 pt-3 pb-2">
        Bot Leaderboard
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border/40">
              {['Bot', 'Ann. Return', 'Sharpe', 'Win%', 'DD%', 'Trades', 'Ready'].map(h => (
                <th key={h} className="px-3 py-2 text-left text-slate-500 font-medium whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((bot, idx) => {
              const m = bot.metrics
              const r = bot.readiness
              const returnPositive = (m.total_return_pct ?? 0) >= 0
              return (
                <tr key={bot.id} className={`border-b border-border/20 ${idx === 0 ? 'bg-green-950/20' : ''}`}>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      <StatusDot status={bot.status} />
                      <span className="text-white font-medium truncate max-w-[80px]">{bot.name}</span>
                    </div>
                  </td>
                  <td className={`px-3 py-2 font-medium ${returnPositive ? 'text-green-400' : 'text-red-400'}`}>
                    {fmtPct(annualisedReturn(m.total_return_pct, m.days_running), 1)}
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {m.sharpe != null ? m.sharpe.toFixed(2) : '—'}
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {fmtPct((m.win_rate ?? 0) * 100, 0)}
                  </td>
                  <td className="px-3 py-2 text-red-400">
                    {fmtPct(-(m.max_drawdown ?? 0) * 100, 1)}
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {m.num_trades ?? 0}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`font-bold ${r.ready ? 'text-green-400' : 'text-slate-500'}`}>
                      {r.score}/{r.total}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main Farm component ───────────────────────────────────────────────────────

export default function Farm() {
  const [farmStatus, setFarmStatus] = useState<FarmStatus | null>(null)
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState('')
  const [actionMsg, setActionMsg]   = useState('')
  const [busy, setBusy]             = useState(false)
  const [btcPrice, setBtcPrice]     = useState<number | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getFarmStatus()
      setFarmStatus(data)
      setError('')
    } catch (err: unknown) {
      const msg = String(err)
      // 404 = farm not started yet — not a real error, just show "not started"
      if (msg.includes('404')) {
        setFarmStatus(null)
        setError('')
      } else {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const id = setInterval(fetchStatus, 10_000)
    return () => clearInterval(id)
  }, [fetchStatus])

  // Fetch BTC price on mount and every 30s
  useEffect(() => {
    getBtcPrice().then(d => setBtcPrice(d.price)).catch(() => {})
    const id = setInterval(() => {
      getBtcPrice().then(d => setBtcPrice(d.price)).catch(() => {})
    }, 30_000)
    return () => clearInterval(id)
  }, [])

  async function handleStartFarm() {
    setBusy(true)
    try {
      const r = await startFarm()
      setActionMsg(`Farm started (PID ${r.pid})`)
      setTimeout(() => setActionMsg(''), 4000)
      setTimeout(fetchStatus, 1000)
    } catch (e) {
      setActionMsg(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleStopFarm() {
    setBusy(true)
    try {
      await stopFarm()
      setActionMsg('Farm stopped')
      setTimeout(() => setActionMsg(''), 4000)
      setTimeout(fetchStatus, 1000)
    } catch (e) {
      setActionMsg(String(e))
    } finally {
      setBusy(false)
    }
  }

  const farmRunning = farmStatus?.farm_running ?? false
  const bots        = farmStatus?.bots ?? []
  const runningBots = bots.filter(b => b.status === 'running').length
  const readyBots   = bots.filter(b => b.readiness.ready).length

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400">
        Loading…
      </div>
    )
  }

  return (
    <div className="p-4 space-y-4 pb-6">
      {/* Header */}
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-lg font-bold text-white">Bot Farm</h1>
        {farmStatus?.updated_at && (
          <span className="text-xs text-slate-500">
            Updated {fmtTime(farmStatus.updated_at)}
          </span>
        )}
      </div>

      {/* BTC price strip */}
      {btcPrice != null && (
        <div className="flex items-center gap-2 bg-card border border-border rounded-2xl px-4 py-2.5">
          <span className="text-amber-400 text-lg font-bold">₿</span>
          <span className="text-white font-mono font-semibold text-sm">
            {btcPrice.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
          </span>
          <span className="text-slate-500 text-xs ml-auto">BTC spot</span>
        </div>
      )}

      {error && (
        <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

      {actionMsg && (
        <div className="bg-green-950 border border-green-800 rounded-xl px-4 py-3 text-green-300 text-sm">
          {actionMsg}
        </div>
      )}

      {/* Farm control bar */}
      <div className="bg-card rounded-2xl p-4 border border-border">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span
              className={`w-3 h-3 rounded-full flex-shrink-0 ${
                farmRunning
                  ? 'bg-green-400 shadow-[0_0_8px_#22c55e]'
                  : 'bg-slate-600'
              }`}
            />
            <div>
              <p className="font-semibold text-white text-sm">
                {farmRunning
                  ? `Running ${runningBots} bot${runningBots !== 1 ? 's' : ''}`
                  : farmStatus ? 'Farm stopped' : 'Farm not started'}
              </p>
              {farmRunning && readyBots > 0 && (
                <p className="text-xs text-green-500">
                  {readyBots} bot{readyBots !== 1 ? 's' : ''} ready for live
                </p>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleStartFarm}
              disabled={farmRunning || busy}
              className="px-4 py-2 rounded-xl bg-green-800 hover:bg-green-700 text-green-200 text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Start Farm
            </button>
            <button
              onClick={handleStopFarm}
              disabled={!farmRunning || busy}
              className="px-4 py-2 rounded-xl bg-red-900 hover:bg-red-800 text-red-200 text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Stop Farm
            </button>
          </div>
        </div>

        {/* Summary stats */}
        {bots.length > 0 && (
          <div className="grid grid-cols-4 gap-2 pt-2 border-t border-border/40">
            {[
              { label: 'Total Bots',  value: String(bots.length) },
              { label: 'Running',     value: String(runningBots) },
              { label: 'Ready',       value: String(readyBots) },
              { label: 'Best Return', value: fmtPct(
                  Math.max(...bots.map(b => b.metrics.total_return_pct ?? 0)), 1
                ) },
            ].map(({ label, value }) => (
              <div key={label} className="rounded-xl bg-navy px-2 py-2 text-center">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="text-sm font-medium text-white">{value}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Leaderboard — directly below farm control */}
      {bots.length > 1 && <Leaderboard bots={bots} />}

      {/* No bots yet */}
      {bots.length === 0 && (
        <div className="bg-card rounded-2xl border border-border px-4 py-8 text-center">
          <p className="text-slate-400 text-sm">
            {farmStatus
              ? 'No bots running yet. Start the farm to launch bots.'
              : 'Start the farm to begin parallel paper trading.'}
          </p>
          <p className="text-slate-600 text-xs mt-2">
            4 bots will run simultaneously: Conservative, Balanced, Aggressive, and Capital ROI
          </p>
        </div>
      )}

      {/* Bot cards */}
      {bots.length > 0 && (
        <div className="space-y-3">
          {bots.map(bot => (
            <BotCard key={bot.id} bot={bot} onRefresh={fetchStatus} />
          ))}
        </div>
      )}
    </div>
  )
}
