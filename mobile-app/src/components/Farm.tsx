import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { getFarmStatus, startFarm, stopFarm, closeFarmBotPosition, getBotLiveState, getBotWhyNotTrading, getBtcPrice, pauseFarmBot, resumeFarmBot, FarmStatus, BotFarmEntry, BotLiveState, WhyNotTrading } from '../api'
import { loadBotOrder, saveBotOrder, applyBotOrder, sortBotsByMetric } from '../lib/botOrder'

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

function BotCard({ bot, onRefresh: _onRefresh, isDragging, onExpandAttempt, onClosePosition, closeMsgText, onTogglePause, pauseBusy, pauseMsgText }: {
  bot: BotFarmEntry
  onRefresh: () => void
  isDragging?: boolean
  onExpandAttempt?: () => boolean
  onClosePosition?: (bot: BotFarmEntry) => void
  closeMsgText?: string
  onTogglePause?: (bot: BotFarmEntry) => void
  pauseBusy?: boolean
  pauseMsgText?: string
}) {
  const [expanded, setExpanded]   = useState(false)
  const [promoteMsg, setPromoteMsg] = useState('')
  const [liveState, setLiveState]   = useState<BotLiveState | null>(null)
  const [liveLoading, setLiveLoading] = useState(false)
  const [whyNot, setWhyNot]           = useState<WhyNotTrading | null>(null)
  const [whyDetailOpen, setWhyDetailOpen] = useState(false)

  const m = bot.metrics
  const r = bot.readiness
  const daysToReady = r.ready ? 0 : Math.max(0, 30 - (m.days_running ?? 0))

  // "Why isn't this bot trading?" — fetched whenever the bot has 0 trades and
  // no open position. Shown as an amber chip in the header; tap-to-expand
  // shows the per-check breakdown inside the card.
  const isIdle = (m.num_trades ?? 0) === 0 && !bot.has_open_position
  useEffect(() => {
    if (!isIdle) {
      setWhyNot(null)
      return
    }
    let cancelled = false
    getBotWhyNotTrading(bot.id)
      .then(d => { if (!cancelled) setWhyNot(d) })
      .catch(() => { if (!cancelled) setWhyNot(null) })
    return () => { cancelled = true }
  }, [isIdle, bot.id])

  // Short label for the amber chip — derive from the long reason
  const whyChipLabel = (() => {
    if (!whyNot) return null
    const r = whyNot.reason.toLowerCase()
    if (r.startsWith('insufficient equity')) return 'Insufficient equity'
    if (r.startsWith('low iv'))               return 'Low IV'
    if (r.startsWith('kill switch'))          return 'Kill-switch'
    if (r.startsWith('heartbeat stale'))      return 'Stale heartbeat'
    if (r.startsWith('bot is not running'))   return 'Stopped'
    if (r.startsWith('dte range'))            return 'Bad DTE config'
    if (r.startsWith('eligible'))             return 'Awaiting signal'
    return whyNot.reason.slice(0, 40)
  })()

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
        onClick={() => {
          // Suppress expand if the tap was really the end of a drag
          if (onExpandAttempt && !onExpandAttempt()) return
          setExpanded(e => !e)
        }}
      >
        <div className="flex items-center gap-2.5 min-w-0">
          {/* Drag handle — visible always, glows amber when actively dragging */}
          <span className={`text-sm select-none flex-shrink-0 transition-colors ${isDragging ? 'text-amber-400' : 'text-slate-700'}`}>⠿</span>
          <StatusDot status={bot.status} />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="font-semibold text-white text-sm truncate">{bot.name}</p>
              <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium flex-shrink-0 ${
                configName === 'Unassigned'
                  ? 'bg-slate-800 text-slate-500 border-slate-600'
                  : 'bg-amber-900 text-amber-300 border-amber-700'
              }`}>
                {configName}
              </span>
              {bot.has_open_position && bot.open_position && (() => {
                const risk = bot.position_risk ?? 'ok'
                const riskStyle = risk === 'danger'
                  ? 'bg-red-900/80 text-red-200 border-red-600 animate-pulse'
                  : risk === 'caution'
                    ? 'bg-amber-900/80 text-amber-200 border-amber-600'
                    : 'bg-green-900/60 text-green-300 border-green-700'
                const riskIcon = risk === 'danger' ? '🚨' : risk === 'caution' ? '⚠️' : '📋'
                return (
                  <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium flex-shrink-0 ${riskStyle}`}>
                    {riskIcon} {bot.open_position.type?.replace('short_', '').toUpperCase() ?? 'PUT'} open
                    {bot.open_position.strike ? ` $${(bot.open_position.strike/1000).toFixed(0)}k` : ''}
                    {bot.open_position.dte != null ? ` · ${bot.open_position.dte}d` : ''}
                  </span>
                )
              })()}
              {/* "Why not trading" amber chip — visible on idle bots only */}
              {isIdle && whyChipLabel && !whyNot?.ready && (
                <span
                  className="text-xs px-1.5 py-0.5 rounded-full border font-medium flex-shrink-0 bg-amber-950/80 text-amber-200 border-amber-700"
                  title={whyNot?.reason ?? whyChipLabel}
                >
                  ⓘ {whyChipLabel}
                </span>
              )}
              {bot.paused && (
                <span className="text-xs px-1.5 py-0.5 rounded-full border font-medium flex-shrink-0 bg-amber-900/80 text-amber-200 border-amber-600">
                  ⏸ Paused
                </span>
              )}
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

          {/* ── Why not trading? (idle bots only) ── */}
          {isIdle && whyNot && !whyNot.ready && (
            <div className="bg-amber-950/40 border border-amber-800/60 rounded-xl px-3 py-2.5 mb-3">
              <button
                onClick={() => setWhyDetailOpen(o => !o)}
                className="w-full flex items-start justify-between text-left gap-2"
              >
                <div className="min-w-0">
                  <p className="text-amber-300 text-xs font-bold">ⓘ Why not trading?</p>
                  <p className="text-amber-200/90 text-xs mt-0.5">{whyNot.reason}</p>
                </div>
                <span className="text-amber-400 text-xs flex-shrink-0">{whyDetailOpen ? '▲' : '▼'}</span>
              </button>
              {whyDetailOpen && (
                <div className="mt-2 pt-2 border-t border-amber-900/60 space-y-1 text-xs">
                  {(() => {
                    const c = whyNot.checks
                    const rows: Array<{ ok: boolean; label: string; hint?: string }> = [
                      { ok: !c.kill_switch.active, label: 'Kill switch clear',
                        hint: c.kill_switch.active
                          ? (c.kill_switch.global ? 'global' : 'per-bot')
                          : undefined },
                      { ok: c.heartbeat.fresh && c.heartbeat.running,
                        label: 'Heartbeat fresh',
                        hint: c.heartbeat.age_seconds != null
                          ? `${Math.round(c.heartbeat.age_seconds)}s ago`
                          : 'no heartbeat' },
                      { ok: c.sizing.sufficient,
                        label: 'Equity can size 0.1 BTC',
                        hint: c.sizing.equity_needed_usd
                          ? `eq $${Math.round(c.sizing.equity_usd ?? 0).toLocaleString()} · need $${Math.round(c.sizing.equity_needed_usd).toLocaleString()}`
                          : undefined },
                      { ok: c.iv_rank.above_threshold,
                        label: `IV rank ≥ ${(c.iv_rank.threshold * 100).toFixed(1)}%`,
                        hint: c.iv_rank.current != null
                          ? `now ${(c.iv_rank.current * 100).toFixed(2)}%`
                          : 'unknown' },
                      { ok: c.dte_range.configured,
                        label: 'DTE range valid',
                        hint: `${c.dte_range.min_dte}–${c.dte_range.max_dte}d` },
                    ]
                    return rows.map((row, i) => (
                      <div key={i} className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span>{row.ok ? '✅' : '❌'}</span>
                          <span className={row.ok ? 'text-slate-300' : 'text-amber-200'}>{row.label}</span>
                        </div>
                        {row.hint && <span className="text-slate-500 font-mono">{row.hint}</span>}
                      </div>
                    ))
                  })()}
                </div>
              )}
            </div>
          )}

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

              {/* Emergency close button — shown when there's a live open position */}
              {bot.has_open_position && onClosePosition && (
                <div className="space-y-1.5">
                  {closeMsgText ? (
                    <div className="text-xs text-center text-amber-400 py-1">{closeMsgText}</div>
                  ) : (
                    <button
                      onClick={() => onClosePosition(bot)}
                      className={`w-full py-2 rounded-xl text-xs font-bold transition-colors border ${
                        bot.position_risk === 'danger'
                          ? 'bg-red-800 hover:bg-red-700 text-red-100 border-red-600'
                          : bot.position_risk === 'caution'
                            ? 'bg-amber-900 hover:bg-amber-800 text-amber-100 border-amber-700'
                            : 'bg-slate-700 hover:bg-slate-600 text-slate-200 border-slate-500'
                      }`}
                    >
                      🆘 Emergency Close Position
                    </button>
                  )}
                </div>
              )}

              {/* Per-bot pause toggle — blocks new entries; existing position is unaffected */}
              {onTogglePause && (
                <div className="space-y-1.5">
                  <p className={`text-xs ${
                    bot.has_open_position
                      ? 'text-amber-400'
                      : 'text-slate-500'
                  }`}>
                    {bot.has_open_position
                      ? '⚠️ Has open position — pausing won\'t close the existing trade'
                      : '✓ No open position — safe to pause'}
                  </p>
                  {pauseMsgText ? (
                    <div className="text-xs text-center text-amber-400 py-1">{pauseMsgText}</div>
                  ) : (
                    <button
                      onClick={() => onTogglePause(bot)}
                      disabled={pauseBusy}
                      className={`w-full py-2 rounded-xl text-xs font-bold transition-colors border disabled:opacity-50 ${
                        bot.paused
                          ? 'bg-green-800 hover:bg-green-700 text-green-100 border-green-600'
                          : 'bg-amber-900 hover:bg-amber-800 text-amber-100 border-amber-700'
                      }`}
                    >
                      {pauseBusy
                        ? '…'
                        : bot.paused
                          ? '▶ Resume New Entries'
                          : '⏸ Pause New Entries'}
                    </button>
                  )}
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
  // Sort by return descending; custom drag order breaks ties
  const sorted = sortBotsByMetric(bots, b => b.metrics.total_return_pct)

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
  const [confirm, setConfirm]       = useState<{ type: 'start' | 'stop'; liveBots: number; totalBots: number } | null>(null)
  const [closeConfirm, setCloseConfirm] = useState<{ botId: string; botName: string; pos: BotFarmEntry['open_position'] } | null>(null)
  const [closeMsg, setCloseMsg]     = useState<Record<string, string>>({})
  const [pauseBusy, setPauseBusy]   = useState<Record<string, boolean>>({})
  const [pauseMsg, setPauseMsg]     = useState<Record<string, string>>({})

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

  function handleStartFarm() {
    const allBots   = farmStatus?.bots ?? []
    const liveBots  = allBots.filter(b => b.readiness?.ready).length
    setConfirm({ type: 'start', liveBots, totalBots: allBots.length })
  }

  function handleStopFarm() {
    const allBots  = farmStatus?.bots ?? []
    const liveBots = allBots.filter(b => b.readiness?.ready).length
    setConfirm({ type: 'stop', liveBots, totalBots: allBots.length })
  }

  async function executeConfirmed() {
    if (!confirm) return
    setConfirm(null)
    setBusy(true)
    try {
      if (confirm.type === 'start') {
        const r = await startFarm()
        setActionMsg(`Farm started (PID ${r.pid})`)
      } else {
        await stopFarm()
        setActionMsg('Farm stopped')
      }
      setTimeout(() => setActionMsg(''), 4000)
      setTimeout(fetchStatus, 1000)
    } catch (e) {
      setActionMsg(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleTogglePause(bot: BotFarmEntry) {
    const botId = bot.id
    setPauseBusy(prev => ({ ...prev, [botId]: true }))
    try {
      if (bot.paused) {
        await resumeFarmBot(botId)
        setPauseMsg(prev => ({ ...prev, [botId]: '✅ Resumed — new entries enabled' }))
      } else {
        await pauseFarmBot(botId)
        setPauseMsg(prev => ({ ...prev, [botId]: '✅ Paused — new entries blocked' }))
      }
      setTimeout(() => {
        setPauseMsg(prev => { const n = { ...prev }; delete n[botId]; return n })
        setTimeout(fetchStatus, 500)
      }, 2500)
    } catch (e) {
      setPauseMsg(prev => ({ ...prev, [botId]: `Error: ${String(e)}` }))
    } finally {
      setPauseBusy(prev => ({ ...prev, [botId]: false }))
    }
  }

  async function executeClose() {
    if (!closeConfirm) return
    const { botId, botName } = closeConfirm
    setCloseConfirm(null)
    setCloseMsg(prev => ({ ...prev, [botId]: 'Sending close command…' }))
    try {
      await closeFarmBotPosition(botId)
      setCloseMsg(prev => ({ ...prev, [botId]: '✅ Close command sent — bot will execute on next cycle' }))
      setTimeout(() => {
        setCloseMsg(prev => { const n = { ...prev }; delete n[botId]; return n })
        setTimeout(fetchStatus, 2000)
      }, 4000)
    } catch (e) {
      setCloseMsg(prev => ({ ...prev, [botId]: `Error: ${String(e)}` }))
    }
  }

  const farmRunning = farmStatus?.farm_running ?? false
  const bots        = farmStatus?.bots ?? []
  const runningBots = bots.filter(b => b.status === 'running').length
  const readyBots   = bots.filter(b => b.readiness.ready).length

  // ── Drag-to-reorder ──────────────────────────────────────────────────────────
  const [botOrder, setBotOrder] = useState<string[]>(() => loadBotOrder())
  const [draggingId, setDraggingId]   = useState<string | null>(null)
  const cardRefs      = useRef<Record<string, HTMLDivElement | null>>({})
  const longPressRef  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const liveOrderRef  = useRef<string[]>([])   // tracks order during active drag
  const lastSwapRef   = useRef<number>(0)       // throttle rapid swaps
  const didDragRef    = useRef(false)           // suppress expand-click after drag

  const sortedBots = useMemo(() => applyBotOrder(bots, botOrder), [bots, botOrder])

  // Lock page scroll while dragging — React synthetic handlers are passive and
  // can't preventDefault, so we attach a native listener with passive:false.
  useEffect(() => {
    if (!draggingId) return
    const block = (e: TouchEvent) => e.preventDefault()
    document.addEventListener('touchmove', block, { passive: false })
    return () => document.removeEventListener('touchmove', block)
  }, [draggingId])

  function startLongPress(botId: string) {
    longPressRef.current = setTimeout(() => {
      liveOrderRef.current = sortedBots.map(b => b.id)
      setDraggingId(botId)
      if (navigator.vibrate) navigator.vibrate(40)
    }, 500)
  }

  function handleCardTouchMove(e: React.TouchEvent, botId: string) {
    // Cancel long-press if finger moves before the timer fires
    if (!draggingId) {
      if (longPressRef.current) { clearTimeout(longPressRef.current); longPressRef.current = null }
      return
    }
    if (draggingId !== botId) return
    e.preventDefault()

    // Throttle — also gives CSS transition (200ms) time to settle before next swap
    const now = Date.now()
    if (now - lastSwapRef.current < 200) return

    const touch = e.touches[0]
    const order = liveOrderRef.current
    const fromIdx = order.indexOf(botId)
    if (fromIdx === -1) return

    // Only ever look at the immediately adjacent card (no jumping multiple slots).
    // Use directional thresholds: to go UP the finger must clear the midpoint of the
    // card above; to go DOWN it must clear the midpoint of the card below.
    // This prevents the ping-pong where "closest centre" keeps toggling when the
    // finger sits between two positions.
    const above = fromIdx > 0 ? order[fromIdx - 1] : null
    const below = fromIdx < order.length - 1 ? order[fromIdx + 1] : null

    let swapTargetIdx = -1

    if (above) {
      const ref = cardRefs.current[above]
      if (ref) {
        const rect = ref.getBoundingClientRect()
        const mid  = rect.top + rect.height / 2
        if (touch.clientY < mid) swapTargetIdx = fromIdx - 1
      }
    }

    if (swapTargetIdx === -1 && below) {
      const ref = cardRefs.current[below]
      if (ref) {
        const rect = ref.getBoundingClientRect()
        const mid  = rect.top + rect.height / 2
        if (touch.clientY > mid) swapTargetIdx = fromIdx + 1
      }
    }

    if (swapTargetIdx !== -1) {
      const newOrder = [...order]
      newOrder.splice(fromIdx, 1)
      newOrder.splice(swapTargetIdx, 0, botId)
      liveOrderRef.current = newOrder
      lastSwapRef.current  = now
      setBotOrder(newOrder)
    }
  }

  function endDrag(e?: React.TouchEvent) {
    if (longPressRef.current) { clearTimeout(longPressRef.current); longPressRef.current = null }
    if (draggingId) {
      if (e) e.preventDefault()  // prevent the synthesised click from toggling expand
      didDragRef.current = true
      setTimeout(() => { didDragRef.current = false }, 200)
      saveBotOrder(liveOrderRef.current)
    }
    setDraggingId(null)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400">
        Loading…
      </div>
    )
  }

  return (
    <div className="p-4 space-y-4 pb-6">

      {/* ── Confirm modal ─────────────────────────────────────────────────── */}
      {confirm && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-sm px-4 pb-8"
             onClick={() => setConfirm(null)}>
          <div className="w-full max-w-sm bg-card border rounded-2xl p-5 space-y-4"
               style={{ borderColor: confirm.liveBots > 0 ? '#ef4444' : '#334155' }}
               onClick={e => e.stopPropagation()}>

            {/* Icon + title */}
            <div className="flex items-center gap-3">
              <span className="text-2xl">{confirm.type === 'start' ? '🚀' : '🛑'}</span>
              <div>
                <p className="font-bold text-white text-base">
                  {confirm.type === 'start' ? 'Start the Farm?' : 'Stop the Farm?'}
                </p>
                <p className="text-xs text-slate-400 mt-0.5">
                  {confirm.totalBots} bot{confirm.totalBots !== 1 ? 's' : ''} will be affected
                </p>
              </div>
            </div>

            {/* Live warning */}
            {confirm.liveBots > 0 && (
              <div className="bg-red-950 border border-red-800 rounded-xl px-3 py-2.5">
                <p className="text-red-300 text-xs font-semibold">⚠️ Live bots detected</p>
                <p className="text-red-400 text-xs mt-1">
                  {confirm.liveBots} bot{confirm.liveBots !== 1 ? 's are' : ' is'} marked ready for live trading.
                  {confirm.type === 'start'
                    ? ' Starting the farm will execute real trades with real money.'
                    : ' Stopping the farm may interrupt active positions.'}
                </p>
              </div>
            )}

            {/* Description */}
            <p className="text-slate-300 text-sm">
              {confirm.type === 'start'
                ? 'This will start all configured bots. They will begin monitoring the market and placing trades according to their configs.'
                : 'This will stop all running bots. Any open positions will remain open but no new trades will be placed.'}
            </p>

            {/* Buttons */}
            <div className="flex gap-3">
              <button
                onClick={() => setConfirm(null)}
                className="flex-1 py-2.5 rounded-xl bg-slate-700 hover:bg-slate-600 text-white text-sm font-medium transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={executeConfirmed}
                className={`flex-1 py-2.5 rounded-xl text-sm font-bold transition-colors ${
                  confirm.type === 'start'
                    ? confirm.liveBots > 0
                      ? 'bg-red-700 hover:bg-red-600 text-white'
                      : 'bg-green-700 hover:bg-green-600 text-white'
                    : 'bg-red-900 hover:bg-red-800 text-red-100'
                }`}
              >
                {confirm.type === 'start' ? 'Yes, Start Farm' : 'Yes, Stop Farm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Emergency close confirmation modal ───────────────────────────── */}
      {closeConfirm && (() => {
        const pos = closeConfirm.pos
        const pnl = pos?.pnl_usd
        const spot = pos?.current_spot
        const strike = pos?.strike
        const delta = pos?.current_delta
        const isItm = spot != null && strike != null && (
          (pos?.type?.includes('put') && spot < strike) ||
          (pos?.type?.includes('call') && spot > strike)
        )
        return (
          <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-sm px-4 pb-8"
               onClick={() => setCloseConfirm(null)}>
            <div className="w-full max-w-sm bg-card border border-red-800 rounded-2xl p-5 space-y-4"
                 onClick={e => e.stopPropagation()}>
              <div className="flex items-center gap-3">
                <span className="text-2xl">🆘</span>
                <div>
                  <p className="font-bold text-white text-base">Emergency Close Position?</p>
                  <p className="text-xs text-slate-400 mt-0.5">{closeConfirm.botName}</p>
                </div>
              </div>

              {pos && (
                <div className="bg-slate-800 rounded-xl px-3 py-2.5 space-y-1">
                  <p className="text-xs text-slate-300">
                    <span className="text-slate-500">Position: </span>
                    {(pos.type ?? 'Option').replace('short_', 'Short ').toUpperCase()} @ ${(strike ?? 0).toLocaleString()}
                  </p>
                  {spot != null && <p className="text-xs text-slate-300"><span className="text-slate-500">BTC Spot: </span>${spot.toLocaleString()}</p>}
                  {delta != null && <p className="text-xs text-slate-300"><span className="text-slate-500">Delta: </span>{delta.toFixed(3)}</p>}
                  {pnl != null && (
                    <p className={`text-xs font-semibold ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      Unrealised P&L: {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                    </p>
                  )}
                  {isItm && <p className="text-xs text-red-400 font-semibold">⚠️ Option is currently in the money</p>}
                </div>
              )}

              <p className="text-slate-300 text-sm">
                This sends a buy-back command to the bot. It will execute a market order to close the short option on its next cycle (within seconds if running).
              </p>

              <div className="flex gap-3">
                <button onClick={() => setCloseConfirm(null)}
                  className="flex-1 py-2.5 rounded-xl bg-slate-700 hover:bg-slate-600 text-white text-sm font-medium transition-colors">
                  Cancel
                </button>
                <button onClick={executeClose}
                  className="flex-1 py-2.5 rounded-xl bg-red-700 hover:bg-red-600 text-white text-sm font-bold transition-colors">
                  Close Position
                </button>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── Danger banner ────────────────────────────────────────────────── */}
      {bots.some(b => b.position_risk === 'danger') && (
        <div className="bg-red-950 border border-red-700 rounded-2xl px-4 py-3 flex items-start gap-3">
          <span className="text-xl flex-shrink-0 mt-0.5">🚨</span>
          <div>
            <p className="text-red-300 font-semibold text-sm">Position in danger zone</p>
            <p className="text-red-400 text-xs mt-0.5">
              {bots.filter(b => b.position_risk === 'danger').map(b => b.name).join(', ')} — option is ITM or approaching it.
              Check below and consider emergency close.
            </p>
          </div>
        </div>
      )}

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

      {/* Bot cards — long-press any card to drag-reorder */}
      {bots.length > 0 && (
        <div className="space-y-3">
          {draggingId && (
            <p className="text-xs text-amber-400 text-center animate-pulse">Drag to reorder · release to drop</p>
          )}
          {sortedBots.map(bot => (
            <div
              key={bot.id}
              ref={el => { cardRefs.current[bot.id] = el }}
              onTouchStart={() => startLongPress(bot.id)}
              onTouchMove={e => handleCardTouchMove(e, bot.id)}
              onTouchEnd={e => endDrag(e)}
              onTouchCancel={() => endDrag()}
              className={`transition-all duration-200 ${
                draggingId === bot.id
                  ? 'opacity-70 scale-[0.97] shadow-2xl relative z-10'
                  : draggingId
                  ? 'opacity-90'
                  : ''
              }`}
              style={{ touchAction: draggingId === bot.id ? 'none' : 'pan-y' }}
            >
              <BotCard
                bot={bot}
                onRefresh={fetchStatus}
                isDragging={draggingId === bot.id}
                onExpandAttempt={() => !didDragRef.current}
                onClosePosition={b => setCloseConfirm({ botId: b.id, botName: b.name, pos: b.open_position ?? null })}
                closeMsgText={closeMsg[bot.id]}
                onTogglePause={handleTogglePause}
                pauseBusy={pauseBusy[bot.id]}
                pauseMsgText={pauseMsg[bot.id]}
              />
            </div>
          ))}
          {bots.length > 1 && !draggingId && (
            <p className="text-xs text-slate-700 text-center">Hold a card to reorder</p>
          )}
        </div>
      )}
    </div>
  )
}
