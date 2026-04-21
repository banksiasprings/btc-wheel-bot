import { useState, useEffect, useCallback } from 'react'
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState<ConfirmAction>(null)
  const [actionMsg, setActionMsg] = useState('')

  const fetchAll = useCallback(async () => {
    try {
      const [s, p, e, pr] = await Promise.all([
        getStatus(),
        getPosition(),
        getEquity(),
        getPresets().catch(() => null),
      ])
      setStatus(s)
      setPosition(p)
      setEquity(e)
      setPresets(pr)
      setError('')
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 30_000)
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
      <h1 className="text-lg font-bold text-white pt-2">Dashboard</h1>

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
              className={`w-3 h-3 rounded-full ${
                status?.bot_running ? 'bg-green-400 shadow-[0_0_8px_#22c55e]' : 'bg-red-500'
              }`}
            />
            <div>
              <p className="font-semibold text-white">
                {status?.bot_running ? 'Running' : 'Stopped'}
              </p>
              {status?.bot_running && (
                <p className="text-xs text-slate-400">
                  {fmtUptime(status.uptime_seconds)
                    ? `Up ${fmtUptime(status.uptime_seconds)}`
                    : status.last_heartbeat
                      ? `Heartbeat ${new Date(status.last_heartbeat).toLocaleTimeString()}`
                      : 'Running'}
                </p>
              )}
            </div>
          </div>
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
        {status?.last_heartbeat && (
          <p className="text-xs text-slate-500 mt-2">
            Last heartbeat:{' '}
            {new Date(status.last_heartbeat).toLocaleTimeString()}
          </p>
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
          <div className="mb-3">
            <PresetBadge active={presets!.active} />
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
            <div className="grid grid-cols-3 gap-2 pt-1">
              <Stat label="Premium" value={fmt$(position.premium_collected)} />
              <Stat label="Spot" value={fmt$(position.current_spot)} />
              <Stat label="Contracts" value={String(position.contracts ?? '—')} />
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

      {/* Confirm dialog */}
      {confirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-border rounded-2xl p-6 w-full max-w-sm">
            <h3 className="font-bold text-white text-lg mb-2">
              {confirm === 'stop' ? 'Stop Bot?' : 'Close Position?'}
            </h3>
            <p className="text-slate-400 text-sm mb-6">
              {confirm === 'stop'
                ? 'This will create the KILL_SWITCH file and halt trading immediately.'
                : 'This will force-close your current open position at market.'}
            </p>
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

function PresetBadge({ active }: { active: 'sweep' | 'evolve' | 'custom' }) {
  const configs = {
    sweep:  { label: 'SWEEP BEST', cls: 'bg-amber-900 text-amber-300 border-amber-700' },
    evolve: { label: 'EVOLVED',    cls: 'bg-green-900 text-green-300 border-green-700' },
    custom: { label: 'CUSTOM',     cls: 'bg-slate-800 text-slate-400 border-slate-600' },
  }
  const { label, cls } = configs[active]
  return (
    <span className={`px-3 py-1 rounded-full text-xs font-bold border ${cls}`}>
      {label}
    </span>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-navy rounded-xl px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
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
