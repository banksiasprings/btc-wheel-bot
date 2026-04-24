import { useState, useEffect, useCallback } from 'react'
import InfoModal from './InfoModal'
import { GLOSSARY } from '../lib/glossary'
import {
  getStatus,
  getPosition,
  getEquity,
  getBtcPrice,
  getFarmStatus,
  listConfigs,
  startBot,
  stopBot,
  closePosition,
  StatusData,
  PositionData,
  EquityData,
  FarmStatus,
  NamedConfig,
  PresetParams,
} from '../api'

// ── Formatting helpers ─────────────────────────────────────────────────────────

function fmt$(n: number | undefined | null) {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function fmtPct(n: number | undefined | null) {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function fmtUptime(s: number | null | undefined) {
  if (s == null || isNaN(s)) return null
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}h ${m}m`
}

type ConfirmAction = 'stop' | 'close' | null

interface Props {
  onNavigateTo?: (tab: string) => void
}

// ── Stat chip ─────────────────────────────────────────────────────────────────

function Stat({ label, value, accent, onInfo }: { label: string; value: string; accent?: boolean; onInfo?: () => void }) {
  return (
    <div className={`rounded-xl px-3 py-2 ${accent ? 'bg-amber-950/40 border border-amber-900/50' : 'bg-navy'}`}>
      <div className="flex items-center gap-1">
        <p className={`text-xs ${accent ? 'text-amber-500/80' : 'text-slate-500'}`}>{label}</p>
        {onInfo && (
          <button onClick={onInfo} className="text-slate-600 hover:text-slate-400 text-xs leading-none flex-shrink-0">ⓘ</button>
        )}
      </div>
      <p className="text-sm font-medium text-white truncate">{value}</p>
    </div>
  )
}

// ── Action button ─────────────────────────────────────────────────────────────

function ActionBtn({ label, color, onClick, disabled }: { label: string; color: 'green' | 'red' | 'amber'; onClick: () => void; disabled?: boolean }) {
  const colors = {
    green: 'bg-green-800 hover:bg-green-700 text-green-200 disabled:opacity-40',
    red: 'bg-red-900 hover:bg-red-800 text-red-200 disabled:opacity-40',
    amber: 'bg-amber-900 hover:bg-amber-800 text-amber-200 disabled:opacity-40',
  }
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`${colors[color]} disabled:cursor-not-allowed rounded-xl py-3 px-2 text-xs font-medium transition-colors text-center leading-tight`}
    >
      {label}
    </button>
  )
}

// ── Source badge for named config ──────────────────────────────────────────────

function ConfigSourceBadge({ source }: { source: string }) {
  const map: Record<string, string> = {
    evolved:  'bg-green-900 text-green-300 border-green-700',
    manual:   'bg-slate-800 text-slate-400 border-slate-600',
    promoted: 'bg-amber-900 text-amber-300 border-amber-700',
  }
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-bold border ${map[source] ?? map.manual}`}>
      {source.charAt(0).toUpperCase() + source.slice(1)}
    </span>
  )
}

// ── Black Swan Calculator (unchanged logic, slimmed) ──────────────────────────

function BlackSwanCard({
  position,
  equityUsd,
  btcPrice,
  configParams,
}: {
  position: PositionData | null
  equityUsd: number
  btcPrice: number
  configParams: PresetParams
}) {
  const [open, setOpen] = useState(false)

  const isHypothetical = !position
  const isPut = true
  const effectiveEquity = equityUsd > 0 ? equityUsd : (configParams.starting_equity ?? 0)

  let strike: number
  let contracts: number
  let premiumUsd: number

  if (position) {
    strike = position.strike ?? 0
    contracts = position.contracts ?? 0
    premiumUsd = position.premium_collected ?? 0
  } else {
    const otmOffset = configParams.approx_otm_offset ?? 0.05
    const legFrac = configParams.max_equity_per_leg ?? 0.10
    const premFrac = configParams.premium_fraction_of_spot ?? 0.02
    strike = Math.round(btcPrice * (1 - otmOffset) / 1000) * 1000
    const maxNotional = effectiveEquity * legFrac
    contracts = strike > 0 ? maxNotional / strike : 0
    premiumUsd = btcPrice * premFrac * contracts
  }

  const maxLossUsd = isPut ? strike * contracts : Math.max(0, btcPrice * 10 - strike) * contracts
  const marginSafety = maxLossUsd > 0 ? effectiveEquity / maxLossUsd : Infinity
  const marginDisplay = marginSafety === Infinity ? '∞' : `${marginSafety.toFixed(1)}×`
  const marginColor = marginSafety >= 2 ? 'text-green-400' : marginSafety >= 1.2 ? 'text-amber-400' : 'text-red-400'
  const marginBorder = marginSafety >= 2 ? 'border-green-900/50' : marginSafety >= 1.2 ? 'border-amber-900/50' : 'border-red-900/50'

  const optionDelta = position?.current_delta
    ?? (((configParams.target_delta_min ?? 0.15) + (configParams.target_delta_max ?? 0.35)) / 2)
  const perpBtc = position?.hedge?.perp_position_btc ?? -(optionDelta * contracts)

  type Scenario = { label: string; move: number; zone: 'crash' | 'flat' | 'rise' }
  const scenarios: Scenario[] = [
    { label: '+30%', move: +0.30, zone: 'rise' },
    { label: '+10%', move: +0.10, zone: 'rise' },
    { label: 'Flat', move: 0.00, zone: 'flat' },
    { label: '-10%', move: -0.10, zone: 'crash' },
    { label: '-20%', move: -0.20, zone: 'crash' },
    { label: '-30%', move: -0.30, zone: 'crash' },
    { label: '-50%', move: -0.50, zone: 'crash' },
    { label: '-70%', move: -0.70, zone: 'crash' },
    { label: '→ $0', move: -1.00, zone: 'crash' },
  ]

  const rows = scenarios.map(({ label, move, zone }) => {
    const sPrice = Math.max(1, btcPrice * (1 + move))
    const intrinsic = Math.max(0, strike - sPrice) * contracts
    const putPnl = premiumUsd - intrinsic
    const hedgePnl = perpBtc * (sPrice - btcPrice)
    const netPnl = putPnl + hedgePnl
    const eqAfter = effectiveEquity + netPnl
    const lossPct = effectiveEquity > 0 ? Math.max(0, -netPnl / effectiveEquity * 100) : 0
    const status = eqAfter <= 0 ? '❌ Liq.' : lossPct > 30 ? '🔴 Critical' : lossPct > 10 ? '🟡 Warning' : zone === 'flat' || netPnl > 0 ? '🟢 Win' : '🟢 Safe'
    return { label, zone, sPrice, putPnl, hedgePnl, netPnl, eqAfter, lossPct, status }
  })

  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-amber-400 text-base">⚡</span>
          <div className="text-left">
            <span className="text-sm font-medium text-white">Black Swan Calculator</span>
            {isHypothetical && <span className="ml-2 text-xs text-slate-500">· hypothetical</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {!open && <span className={`text-sm font-bold ${marginColor}`}>{marginDisplay} cover</span>}
          <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-border/40">
          {isHypothetical && (
            <div className="mt-3 rounded-xl px-3 py-2 bg-slate-800/60 border border-slate-700/50">
              <p className="text-xs text-slate-400">
                No open position — estimated exposure if the bot opens a put now.
                Equity {fmt$(effectiveEquity)} · Strike ≈ {fmt$(strike)} · Contracts ≈ {contracts.toFixed(1)}
              </p>
            </div>
          )}
          <div className={`${isHypothetical ? '' : 'mt-3'} rounded-xl px-4 py-3 bg-navy border ${marginBorder} flex items-baseline gap-3 flex-wrap`}>
            <span className="text-xs text-slate-500 whitespace-nowrap">Margin Safety</span>
            <span className={`text-2xl font-bold ${marginColor}`}>{marginDisplay}</span>
            <span className="text-xs text-slate-500 leading-snug">Max loss (BTC → $0): {fmt$(maxLossUsd)}</span>
          </div>
          <div className="space-y-1">
            {rows.map(({ label, zone, sPrice, hedgePnl, netPnl, eqAfter, lossPct, status }, idx) => {
              const prevZone = idx > 0 ? rows[idx - 1].zone : zone
              const showDivider = zone !== prevZone
              const rowBg = eqAfter <= 0 ? 'bg-red-950/60 border-red-900/60' : lossPct > 30 ? 'bg-red-950/30 border-red-900/30' : lossPct > 10 ? 'bg-amber-950/30 border-amber-900/30' : zone === 'flat' ? 'bg-green-950/40 border-green-900/40' : zone === 'rise' ? 'bg-sky-950/20 border-sky-900/20' : 'bg-navy border-transparent'
              const zoneLabel: Record<string, string> = { flat: '── Flat · your ideal outcome ──', crash: '── BTC falls · crash risk ──' }
              return (
                <div key={label}>
                  {showDivider && zone in zoneLabel && (
                    <p className="text-center text-xs text-slate-600 py-1">{zoneLabel[zone]}</p>
                  )}
                  <div className={`rounded-xl border px-3 py-2 ${rowBg}`}>
                    <div className="flex items-center justify-between gap-1">
                      <span className="text-sm font-bold font-mono text-white w-14 flex-shrink-0">{label}</span>
                      <span className="text-xs text-slate-400 flex-1">{fmt$(Math.round(sPrice))}</span>
                      <span className={`text-xs font-bold flex-shrink-0 ${netPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {netPnl >= 0 ? '+' : ''}{fmt$(netPnl)}
                      </span>
                      <span className="text-xs flex-shrink-0 ml-1">{status}</span>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5 pl-14 flex-wrap">
                      <span className="text-xs text-slate-500">Eq. {fmt$(eqAfter)}</span>
                      {Math.abs(hedgePnl) > 1 && (
                        <span className={`text-xs ${hedgePnl > 0 ? 'text-sky-600' : 'text-slate-600'}`}>
                          hedge {hedgePnl >= 0 ? '+' : ''}{fmt$(hedgePnl)}
                        </span>
                      )}
                      {lossPct > 0 && <span className="text-xs text-slate-500">loss {lossPct.toFixed(1)}%</span>}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
          <p className="text-xs text-slate-600 leading-snug">
            Net = put P&L + delta hedge P&L. Hedge estimate assumes static position — real daily rebalancing reduces crash losses further.
          </p>
        </div>
      )}
    </div>
  )
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function Dashboard({ onNavigateTo }: Props) {
  const [status, setStatus]           = useState<StatusData | null>(null)
  const [position, setPosition]       = useState<PositionData | null>(null)
  const [equity, setEquity]           = useState<EquityData | null>(null)
  const [farmStatus, setFarmStatus]   = useState<FarmStatus | null>(null)
  const [configs, setConfigs]         = useState<NamedConfig[]>([])
  const [btcPrice, setBtcPrice]       = useState<number | null>(null)
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState('')
  const [confirm, setConfirm]         = useState<ConfirmAction>(null)
  const [actionMsg, setActionMsg]     = useState('')
  const [info, setInfo]               = useState<{ title: string; body: string } | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const [s, p, e, farm, btc, cfgs] = await Promise.allSettled([
        getStatus(),
        getPosition(),
        getEquity(),
        getFarmStatus(),
        getBtcPrice(),
        listConfigs(),
      ])
      if (s.status === 'fulfilled')    setStatus(s.value)
      if (p.status === 'fulfilled')    setPosition(p.value)
      if (e.status === 'fulfilled')    setEquity(e.value)
      if (farm.status === 'fulfilled') setFarmStatus(farm.value)
      if (btc.status === 'fulfilled')  setBtcPrice(btc.value.price)
      if (cfgs.status === 'fulfilled') setConfigs(cfgs.value)
      // Only set error if the core status call failed
      if (s.status === 'rejected') setError(String(s.reason))
      else setError('')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 5_000)
    return () => clearInterval(id)
  }, [fetchAll])

  async function handleStart() {
    try {
      const r = await startBot()
      setActionMsg(r.message)
      setTimeout(() => setActionMsg(''), 3000)
      fetchAll()
    } catch (e) { setActionMsg(String(e)) }
  }

  async function handleStop() {
    setConfirm(null)
    try {
      const r = await stopBot()
      setActionMsg(r.message)
      setTimeout(() => setActionMsg(''), 3000)
      fetchAll()
    } catch (e) { setActionMsg(String(e)) }
  }

  async function handleClose() {
    setConfirm(null)
    try {
      const r = await closePosition()
      setActionMsg(r.message)
      setTimeout(() => setActionMsg(''), 3000)
      fetchAll()
    } catch (e) { setActionMsg(String(e)) }
  }

  // ── Derive active config name from named configs + status ─────────────────
  // Try to find a promoted config — that's the live config
  const promotedConfig = configs.find(c => c.source === 'promoted')
  const activeConfigName = promotedConfig?.name ?? 'Master Config'

  // ── Farm overview strip data ──────────────────────────────────────────────
  const bots = farmStatus?.bots ?? []
  const runningBots = bots.filter(b => b.status === 'running').length
  const readyBots   = bots.filter(b => b.readiness.ready).length
  const bestBot     = bots.length > 0
    ? bots.reduce((a, b) => ((a.metrics.sharpe ?? 0) > (b.metrics.sharpe ?? 0) ? a : b))
    : null

  // ── Today's stats ─────────────────────────────────────────────────────────
  const pnlPositive = (position?.unrealized_pnl_usd ?? 0) >= 0

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-slate-400">Loading…</div>
  }

  return (
    <div className="p-4 space-y-4 pb-4">
      {/* Header */}
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-lg font-bold text-white">Dashboard</h1>
        {btcPrice != null && (
          <span className="text-sm font-mono text-slate-300">
            ₿ {btcPrice.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
          </span>
        )}
      </div>

      {info && <InfoModal title={info.title} body={info.body} onClose={() => setInfo(null)} />}

      {error && (
        <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">{error}</div>
      )}
      {actionMsg && (
        <div className="bg-green-950 border border-green-800 rounded-xl px-4 py-3 text-green-300 text-sm">{actionMsg}</div>
      )}

      {/* ── 1. Live Bot Header ────────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl p-4 border border-border">
        <div className="flex items-center justify-between mb-3">
          {/* Status indicator */}
          <div className="flex items-center gap-3">
            <span className={`w-3 h-3 rounded-full flex-shrink-0 ${
              !status?.bot_running
                ? 'bg-red-500'
                : status.paused
                ? 'bg-yellow-400 shadow-[0_0_8px_#facc15]'
                : 'bg-green-400 shadow-[0_0_8px_#22c55e]'
            }`} />
            <div>
              <div className="flex items-center gap-1.5">
                <p className="font-semibold text-white">
                  {!status?.bot_running ? 'Stopped' : status.paused ? 'Paused' : 'Running'}
                </p>
                <button onClick={() => setInfo(GLOSSARY.bot_status)} className="text-slate-500 hover:text-slate-300 text-xs leading-none">ⓘ</button>
              </div>
              {status?.bot_running && (
                <p className="text-xs text-slate-400">
                  {status.paused
                    ? 'Kill switch active'
                    : fmtUptime(status.uptime_seconds)
                    ? `Up ${fmtUptime(status.uptime_seconds)}`
                    : 'Running'}
                </p>
              )}
            </div>
          </div>

          {/* Mode + config badge */}
          <div className="flex flex-col items-end gap-1">
            <span className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wide ${
              status?.mode === 'live'
                ? 'bg-red-900 text-red-300 border border-red-700'
                : 'bg-amber-900 text-amber-300 border border-amber-700'
            }`}>
              {status?.mode ?? '—'}
            </span>
            <span className="text-xs text-slate-500 font-medium">{activeConfigName}</span>
          </div>
        </div>

        {status?.last_heartbeat && (
          <div className="flex items-center gap-1 border-t border-border/40 pt-2">
            <p className="text-xs text-slate-500">
              Heartbeat {new Date(status.last_heartbeat).toLocaleTimeString()}
            </p>
            <button onClick={() => setInfo(GLOSSARY.heartbeat)} className="text-slate-600 hover:text-slate-400 text-xs leading-none">ⓘ</button>
            <button onClick={fetchAll} title="Refresh" className="text-slate-600 hover:text-slate-400 text-xs leading-none ml-1">↻</button>
          </div>
        )}
      </div>

      {/* ── 2. Active Position Card ───────────────────────────────────────── */}
      {position?.open && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-3">Active Position</p>
          <div className="flex items-center justify-between">
            <div>
              <p className="font-semibold text-white">
                {position.type?.replace('_', ' ').toUpperCase()}
              </p>
              <p className="text-slate-400 text-sm">
                Strike {position.strike?.toLocaleString()} · {position.days_to_expiry}d DTE
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                {position.entry_date ? `Opened ${new Date(position.entry_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}
              </p>
            </div>
            <div className="text-right">
              <p className={`font-bold text-lg ${pnlPositive ? 'text-green-400' : 'text-red-400'}`}>
                {fmt$(position.unrealized_pnl_usd)}
              </p>
              <p className={`text-sm ${pnlPositive ? 'text-green-500' : 'text-red-500'}`}>
                {fmtPct(position.unrealized_pnl_pct)}
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 mt-3">
            <Stat label="Option Delta" value={position.current_delta != null ? position.current_delta.toFixed(3) : '—'} />
            <Stat label="Premium" value={fmt$(position.premium_collected)} />
            {position.hedge && (
              <>
                <Stat
                  label="Net Delta"
                  value={position.net_delta != null ? `${position.net_delta >= 0 ? '+' : ''}${position.net_delta.toFixed(3)} BTC` : '—'}
                  accent={position.net_delta != null && Math.abs(position.net_delta) > 0.05}
                />
                <Stat
                  label="Hedge P&L"
                  value={position.hedge.unrealised_pnl_usd != null ? fmt$(position.hedge.unrealised_pnl_usd) : '—'}
                />
              </>
            )}
          </div>
        </div>
      )}

      {/* ── 3. Quick Actions Row ──────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl p-4 border border-border">
        <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-3">Quick Actions</p>
        <div className="grid grid-cols-3 gap-2">
          <ActionBtn label="Start Bot" color="green" onClick={handleStart} disabled={status?.bot_running} />
          <ActionBtn label="Stop Bot" color="red" onClick={() => setConfirm('stop')} disabled={!status?.bot_running} />
          <ActionBtn label="Close Position" color="amber" onClick={() => setConfirm('close')} disabled={!position?.open} />
        </div>
      </div>

      {/* ── 4. Farm Overview Strip ───────────────────────────────────────── */}
      <button
        onClick={() => onNavigateTo?.('pipeline')}
        className="w-full bg-card rounded-2xl p-4 border border-border text-left hover:border-slate-600 transition-colors"
      >
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-1">Bot Farm</p>
            {farmStatus ? (
              <p className="text-sm text-white">
                {runningBots}/{bots.length} running
                {bestBot && bestBot.metrics.sharpe != null && (
                  <span className="text-slate-400"> · Best: {bestBot.name} (Sharpe {bestBot.metrics.sharpe.toFixed(2)})</span>
                )}
                {readyBots > 0 && (
                  <span className="text-green-400"> · {readyBots} ready ✅</span>
                )}
              </p>
            ) : (
              <p className="text-sm text-slate-500">Farm not started — tap to set up</p>
            )}
          </div>
          <span className="text-slate-500 text-sm ml-2">→</span>
        </div>
      </button>

      {/* ── 5. Today's Stats Row ─────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-card rounded-2xl p-3 border border-border text-center">
          <p className="text-xs text-slate-400">Unrealised P&L</p>
          <p className={`font-bold text-sm mt-0.5 ${(position?.unrealized_pnl_usd ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {position?.open ? fmt$(position.unrealized_pnl_usd) : '—'}
          </p>
        </div>
        <div className="bg-card rounded-2xl p-3 border border-border text-center">
          <p className="text-xs text-slate-400">Position</p>
          <p className="font-bold text-sm mt-0.5 text-white">
            {position?.open ? 'Open' : 'Flat'}
          </p>
        </div>
        <div className="bg-card rounded-2xl p-3 border border-border text-center">
          <p className="text-xs text-slate-400">Days to Expiry</p>
          <p className="font-bold text-sm mt-0.5 text-white">
            {position?.open && position.days_to_expiry != null ? `${position.days_to_expiry}d` : '—'}
          </p>
        </div>
      </div>

      {/* ── Capital overview ──────────────────────────────────────────────── */}
      {equity && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <div className="flex items-center gap-1 mb-3">
            <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">Capital Overview</p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-xl bg-navy px-3 py-2.5">
              <p className="text-xs text-slate-500 mb-0.5">Total Equity</p>
              <p className="text-sm font-semibold text-white">{fmt$(equity.current_equity)}</p>
              <p className="text-xs text-slate-600 mt-0.5">started {fmt$(equity.starting_equity)}</p>
            </div>
            <div className="rounded-xl bg-navy px-3 py-2.5">
              <p className="text-xs text-slate-500 mb-0.5">Overall ROI</p>
              <p className={`text-sm font-semibold ${(equity.total_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {fmtPct(equity.total_return_pct)}
              </p>
              <p className="text-xs text-slate-600 mt-0.5">
                {fmt$(equity.current_equity - equity.starting_equity)} net
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── Named Config Manager ─────────────────────────────────────────── */}
      {configs.length > 0 && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">Named Configs</p>
            {onNavigateTo && (
              <button
                onClick={() => onNavigateTo('settings')}
                className="text-xs text-green-400 hover:text-green-300 transition-colors"
              >
                Manage →
              </button>
            )}
          </div>
          <div className="space-y-1.5">
            {configs.slice(0, 4).map(cfg => (
              <div key={cfg.name} className="flex items-center justify-between bg-navy rounded-xl px-3 py-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm text-white truncate">{cfg.name}</span>
                  <ConfigSourceBadge source={cfg.source} />
                </div>
                <div className="flex gap-2 flex-shrink-0 ml-2 text-xs text-slate-500">
                  {cfg.fitness != null && <span>fit {cfg.fitness.toFixed(2)}</span>}
                  {cfg.sharpe != null && <span>S{cfg.sharpe.toFixed(2)}</span>}
                </div>
              </div>
            ))}
            {configs.length > 4 && (
              <p className="text-xs text-slate-600 text-center pt-1">+{configs.length - 4} more in Settings</p>
            )}
          </div>
        </div>
      )}

      {/* ── Black Swan Calculator ─────────────────────────────────────────── */}
      {equity && (btcPrice ?? 0) > 0 && (
        <BlackSwanCard
          position={position?.open ? position : null}
          equityUsd={equity.current_equity}
          btcPrice={btcPrice ?? 0}
          configParams={{
            approx_otm_offset: 0.05,
            max_equity_per_leg: 0.10,
            premium_fraction_of_spot: 0.02,
            target_delta_min: 0.15,
            target_delta_max: 0.35,
            starting_equity: equity.starting_equity,
          }}
        />
      )}

      {/* ── Confirm dialog ────────────────────────────────────────────────── */}
      {confirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-border rounded-2xl p-6 w-full max-w-sm">
            <h3 className="font-bold text-white text-lg mb-2">
              {confirm === 'stop' ? 'Stop Bot?' : 'Close Position?'}
            </h3>
            <div className="bg-amber-950 border border-amber-700 rounded-xl px-3 py-3 mb-5 flex gap-2.5">
              <span className="text-amber-400 text-base leading-snug flex-shrink-0">⚠️</span>
              {confirm === 'stop' ? (
                <ul className="text-amber-200 text-xs space-y-1.5 leading-snug">
                  <li>The bot will stop scanning and halt automated trading.</li>
                  <li className="font-semibold">Any open position will NOT be closed — it stays open until restarted.</li>
                  <li>You can restart at any time from this screen.</li>
                </ul>
              ) : (
                <ul className="text-amber-200 text-xs space-y-1.5 leading-snug">
                  <li>Your open position will be force-closed at current market price.</li>
                  <li className="font-semibold">You'll receive whatever premium the market offers now — this cannot be undone.</li>
                </ul>
              )}
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setConfirm(null)}
                className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm font-medium"
              >
                Cancel
              </button>
              <button
                onClick={confirm === 'stop' ? handleStop : handleClose}
                className={`flex-1 py-3 rounded-xl text-white text-sm font-semibold ${confirm === 'stop' ? 'bg-red-600' : 'bg-amber-600'}`}
              >
                {confirm === 'stop' ? 'Stop' : 'Close'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
