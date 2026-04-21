import { useState, useEffect, useCallback } from 'react'
import InfoModal from './InfoModal'
import { GLOSSARY } from '../lib/glossary'
import {
  AreaChart,
  Area,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from 'recharts'
import {
  getStatus,
  getPosition,
  getEquity,
  getPresets,
  getBtcPrice,
  startBot,
  stopBot,
  closePosition,
  StatusData,
  PositionData,
  EquityData,
  PresetsData,
} from '../api'

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

export default function Dashboard({ onNavigateTo }: Props) {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [position, setPosition] = useState<PositionData | null>(null)
  const [equity, setEquity] = useState<EquityData | null>(null)
  const [presets, setPresets] = useState<PresetsData | null>(null)
  const [btcPrice, setBtcPrice] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState<ConfirmAction>(null)
  const [actionMsg, setActionMsg] = useState('')
  const [info, setInfo] = useState<{ title: string; body: string } | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const [s, p, e, pr, btc] = await Promise.all([
        getStatus(),
        getPosition(),
        getEquity(),
        getPresets().catch(() => null),
        getBtcPrice().catch(() => null),
      ])
      setStatus(s)
      setPosition(p)
      setEquity(e)
      setPresets(pr)
      if (btc) setBtcPrice(btc.price)
      setError('')
    } catch (err) {
      setError(String(err))
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
    } catch (e) {
      setActionMsg(String(e))
    }
  }

  async function handleStop() {
    setConfirm(null)
    try {
      const r = await stopBot()
      setActionMsg(r.message)
      setTimeout(() => setActionMsg(''), 3000)
      fetchAll()
    } catch (e) {
      setActionMsg(String(e))
    }
  }

  async function handleClose() {
    setConfirm(null)
    try {
      const r = await closePosition()
      setActionMsg(r.message)
      setTimeout(() => setActionMsg(''), 3000)
      fetchAll()
    } catch (e) {
      setActionMsg(String(e))
    }
  }

  // Equity chart data (last 30 points)
  const chartData = (() => {
    if (!equity || equity.dates.length === 0) return []
    const n = Math.min(30, equity.dates.length)
    return equity.dates.slice(-n).map((d, i) => ({
      date: d.slice(5),
      equity: equity.equity[equity.equity.length - n + i],
    }))
  })()

  const pnlPositive = (position?.unrealized_pnl_usd ?? 0) >= 0

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400">
        Loading…
      </div>
    )
  }

  const cp = presets?.current?.params

  return (
    <div className="p-4 space-y-4 pb-4">
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-lg font-bold text-white">Dashboard</h1>
        {btcPrice != null && (
          <span className="text-sm font-mono text-slate-300">
            ₿ {btcPrice.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
          </span>
        )}
      </div>

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

      {/* Status card */}
      <div className="bg-card rounded-2xl p-4 border border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span
              className={`w-3 h-3 rounded-full flex-shrink-0 ${
                !status?.bot_running
                  ? 'bg-red-500'
                  : status.paused
                  ? 'bg-yellow-400 shadow-[0_0_8px_#facc15]'
                  : 'bg-green-400 shadow-[0_0_8px_#22c55e]'
              }`}
            />
            <div>
              <div className="flex items-center gap-1">
                <p className="font-semibold text-white">
                  {!status?.bot_running
                    ? 'Stopped'
                    : status.paused
                    ? 'Paused'
                    : 'Running'}
                </p>
                <button onClick={() => setInfo(GLOSSARY.bot_status)} className="text-slate-500 hover:text-slate-300 text-xs leading-none">ⓘ</button>
              </div>
              {status?.bot_running && (
                <p className="text-xs text-slate-400">
                  {status.paused
                    ? 'Kill switch active — press Start Bot to resume'
                    : fmtUptime(status.uptime_seconds)
                    ? `Up ${fmtUptime(status.uptime_seconds)}`
                    : status.last_heartbeat
                    ? `Heartbeat ${new Date(status.last_heartbeat).toLocaleTimeString()}`
                    : 'Running'}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <button onClick={() => setInfo(GLOSSARY.paper_mode)} className="text-slate-500 hover:text-slate-300 text-xs leading-none">ⓘ</button>
            <span
              className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wide ${
                status?.mode === 'live'
                  ? 'bg-red-900 text-red-300 border border-red-700'
                  : 'bg-amber-900 text-amber-300 border border-amber-700'
              }`}
            >
              {status?.mode ?? '—'}
            </span>
          </div>
        </div>
        {status?.last_heartbeat && (
          <div className="flex items-center gap-1 mt-2">
            <p className="text-xs text-slate-500">
              Last heartbeat: {new Date(status.last_heartbeat).toLocaleTimeString()}
            </p>
            <button onClick={() => setInfo(GLOSSARY.heartbeat)} className="text-slate-600 hover:text-slate-400 text-xs leading-none">ⓘ</button>
            <button
              onClick={fetchAll}
              title="Refresh status"
              className="text-slate-600 hover:text-slate-400 text-xs leading-none ml-1"
            >
              ↻
            </button>
          </div>
        )}
      </div>

      {/* Active Config card */}
      {cp && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">
              Active Config
            </p>
            {onNavigateTo && (
              <button
                onClick={() => onNavigateTo('settings')}
                className="text-xs text-green-400 hover:text-green-300 transition-colors"
              >
                Change →
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 mb-3">
            <PresetBadge active={presets!.active} />
            <button
              onClick={() => setInfo({
                title: "How Parameters Were Chosen",
                body: "EVOLVED — parameters were found by a genetic algorithm that tested hundreds of combinations and selected the best-performing set for a specific goal (Balanced, Max Yield, Safest, or Sharpe).\n\nSWEEP — parameters were found by a parameter sweep that tested each setting individually and picked the single best value for each.\n\nCUSTOM — parameters were set manually in the Trading Parameters section of Settings. No automated optimisation was used.",
              })}
              className="text-slate-500 hover:text-slate-300 text-xs leading-none"
            >ⓘ</button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Stat
              label="IV Threshold"
              value={cp.iv_rank_threshold != null
                ? `${(cp.iv_rank_threshold * 100).toFixed(1)}%`
                : '—'}
            />
            <Stat
              label="Delta Range"
              value={cp.target_delta_min != null && cp.target_delta_max != null
                ? `${cp.target_delta_min.toFixed(2)}–${cp.target_delta_max.toFixed(2)}`
                : '—'}
            />
            <Stat
              label="DTE Range"
              value={cp.min_dte != null && cp.max_dte != null
                ? `${cp.min_dte}–${cp.max_dte}d`
                : '—'}
            />
            <Stat
              label="Max Leg Size"
              value={cp.max_equity_per_leg != null
                ? `${(cp.max_equity_per_leg * 100).toFixed(1)}%`
                : '—'}
            />
          </div>
        </div>
      )}

      {/* Position card */}
      <div className="bg-card rounded-2xl p-4 border border-border">
        <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-3">
          Current Position
        </p>
        {position?.open ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div>
                <p className="font-semibold text-white">
                  {position.type?.replace('_', ' ').toUpperCase()}
                </p>
                <p className="text-slate-400 text-sm">
                  Strike {position.strike?.toLocaleString()} · {position.days_to_expiry}d to expiry
                </p>
              </div>
              <div className="text-right">
                <p
                  className={`font-bold text-lg ${
                    pnlPositive ? 'text-green-400' : 'text-red-400'
                  }`}
                >
                  {fmt$(position.unrealized_pnl_usd)}
                </p>
                <p
                  className={`text-sm ${
                    pnlPositive ? 'text-green-500' : 'text-red-500'
                  }`}
                >
                  {fmtPct(position.unrealized_pnl_pct)}
                </p>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 pt-1">
              <Stat label="Premium" value={fmt$(position.premium_collected)} />
              <Stat label="Spot" value={fmt$(position.current_spot)} />
              <Stat label="Contracts" value={String(position.contracts ?? '—')} />
              {(() => {
                const committed = (position.strike ?? 0) * (position.contracts ?? 0)
                const free = equity?.current_equity != null ? equity.current_equity - committed : null
                return (
                  <>
                    <Stat label="Capital Committed" value={fmt$(committed || null)} accent onInfo={() => setInfo(GLOSSARY.capital_committed)} />
                    <Stat label="Free Reserve" value={fmt$(free)} accent onInfo={() => setInfo(GLOSSARY.free_reserve)} />
                  </>
                )
              })()}
              {(() => {
                const { premium_collected, strike, contracts, days_to_expiry } = position
                if (
                  premium_collected != null && strike != null &&
                  contracts != null && days_to_expiry != null && days_to_expiry > 0
                ) {
                  const yield_pa = (premium_collected / (strike * contracts)) * (365 / days_to_expiry) * 100
                  return (
                    <div className="col-span-2 rounded-xl px-3 py-2 bg-green-950/40 border border-green-900/50">
                      <div className="flex items-center gap-1">
                        <p className="text-xs text-green-500/80">Est. Annual Yield</p>
                        <button onClick={() => setInfo(GLOSSARY.est_annual_yield)} className="text-green-700 hover:text-green-500 text-xs leading-none">ⓘ</button>
                      </div>
                      <p className="text-sm font-medium text-white">{yield_pa.toFixed(1)}% p.a.</p>
                      <p className="text-xs text-green-600/70 mt-0.5">If premium fully collected at expiry</p>
                    </div>
                  )
                }
                return null
              })()}
            </div>
          </div>
        ) : (
          <p className="text-slate-400 text-sm">No open position</p>
        )}
      </div>

      {/* Equity chart */}
      {chartData.length > 1 && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">
              Equity (last {chartData.length}d)
            </p>
            <div className="text-right">
              <p className="font-semibold text-white text-sm">
                {fmt$(equity?.current_equity)}
              </p>
              <p
                className={`text-xs ${
                  (equity?.total_return_pct ?? 0) >= 0
                    ? 'text-green-400'
                    : 'text-red-400'
                }`}
              >
                {fmtPct(equity?.total_return_pct)}
              </p>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={80}>
            <AreaChart data={chartData} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" hide />
              <Tooltip
                contentStyle={{
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: 8,
                  color: '#fff',
                  fontSize: 12,
                }}
                formatter={(v: number) => [fmt$(v), 'Equity']}
              />
              <Area
                type="monotone"
                dataKey="equity"
                stroke="#22c55e"
                strokeWidth={2}
                fill="url(#equityGrad)"
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Quick actions */}
      <div className="bg-card rounded-2xl p-4 border border-border">
        <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-3">
          Quick Actions
        </p>
        <div className="grid grid-cols-3 gap-2">
          <ActionBtn
            label="Start Bot"
            color="green"
            onClick={handleStart}
            disabled={status?.bot_running}
          />
          <ActionBtn
            label="Stop Bot"
            color="red"
            onClick={() => setConfirm('stop')}
            disabled={!status?.bot_running}
          />
          <ActionBtn
            label="Close Position"
            color="amber"
            onClick={() => setConfirm('close')}
            disabled={!position?.open}
          />
        </div>
      </div>

      {info && <InfoModal title={info.title} body={info.body} onClose={() => setInfo(null)} />}

      {/* Confirm dialog */}
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
                  <li>The bot will stop scanning for opportunities and halt all automated trading.</li>
                  <li className="font-semibold">Any open position will NOT be closed — it stays open and exposed until you manually close it or restart the bot.</li>
                  <li>You can restart at any time from this screen.</li>
                </ul>
              ) : (
                <ul className="text-amber-200 text-xs space-y-1.5 leading-snug">
                  <li>Your open position will be force-closed immediately at the current market price.</li>
                  <li className="font-semibold">You'll receive whatever premium the market offers now, which may be less than what you originally collected.</li>
                  <li>Use this to exit early or cut a loss — this cannot be undone.</li>
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
                className={`flex-1 py-3 rounded-xl text-white text-sm font-semibold ${
                  confirm === 'stop' ? 'bg-red-600' : 'bg-amber-600'
                }`}
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

function PresetBadge({ active }: { active: string }) {
  const configs: Record<string, { label: string; cls: string }> = {
    sweep:            { label: 'SWEEP BEST',   cls: 'bg-amber-900  text-amber-300  border-amber-700'  },
    evolve_balanced:  { label: '🎯 BALANCED',  cls: 'bg-green-900  text-green-300  border-green-700'  },
    evolve_max_yield: { label: '🚀 MAX YIELD', cls: 'bg-orange-900 text-orange-300 border-orange-700' },
    evolve_safest:    { label: '🛡 SAFEST',    cls: 'bg-sky-900    text-sky-300    border-sky-700'    },
    evolve_sharpe:    { label: '⚖️ SHARPE',    cls: 'bg-purple-900 text-purple-300 border-purple-700' },
    custom:           { label: 'CUSTOM',        cls: 'bg-slate-800  text-slate-400  border-slate-600'  },
  }
  const { label, cls } = configs[active] ?? configs.custom
  return (
    <span className={`px-3 py-1 rounded-full text-xs font-bold border ${cls}`}>
      {label}
    </span>
  )
}

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

function ActionBtn({
  label,
  color,
  onClick,
  disabled,
}: {
  label: string
  color: 'green' | 'red' | 'amber'
  onClick: () => void
  disabled?: boolean
}) {
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
