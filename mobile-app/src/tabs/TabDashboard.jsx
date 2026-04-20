import { useState, useEffect, useCallback } from 'react'
import { api } from '../api.js'
import { LineChart, Line, ResponsiveContainer, Tooltip, YAxis } from 'recharts'

const C = {
  bg:    '#0f172a',
  card:  '#1e293b',
  green: '#22c55e',
  red:   '#ef4444',
  amber: '#f59e0b',
  muted: '#94a3b8',
}

function Card({ children, className = '' }) {
  return (
    <div className={`rounded-xl p-4 ${className}`} style={{ background: C.card }}>
      {children}
    </div>
  )
}

function Badge({ label, color }) {
  return (
    <span className="rounded-full px-2 py-0.5 text-xs font-bold"
          style={{ background: color + '22', color }}>
      {label}
    </span>
  )
}

function ConfirmButton({ label, onConfirm, color = C.red, confirmLabel = 'Confirm?' }) {
  const [pending, setPending] = useState(false)
  const [busy,    setBusy]    = useState(false)

  async function handleClick() {
    if (!pending) { setPending(true); return }
    setBusy(true)
    try { await onConfirm() } finally { setPending(false); setBusy(false) }
  }

  return (
    <button
      className="flex-1 rounded-lg py-2.5 text-sm font-semibold text-white disabled:opacity-50"
      style={{ background: pending ? C.amber : color }}
      disabled={busy}
      onClick={handleClick}
    >
      {busy ? '…' : pending ? confirmLabel : label}
    </button>
  )
}

export default function TabDashboard() {
  const [status,   setStatus]   = useState(null)
  const [position, setPosition] = useState(null)
  const [equity,   setEquity]   = useState(null)
  const [error,    setError]    = useState('')
  const [lastRefresh, setLastRefresh] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const [s, p, e] = await Promise.all([api.status(), api.position(), api.equity()])
      setStatus(s)
      setPosition(p)
      setEquity(e)
      setError('')
      setLastRefresh(new Date())
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30_000)
    return () => clearInterval(id)
  }, [refresh])

  function formatUptime(secs) {
    if (!secs) return '—'
    const h = Math.floor(secs / 3600)
    const m = Math.floor((secs % 3600) / 60)
    return h > 0 ? `${h}h ${m}m` : `${m}m`
  }

  const sparkData = equity?.equity
    ? equity.equity.map((v, i) => ({ v, d: equity.dates[i] }))
    : []

  const pnlColor = equity?.total_return_pct >= 0 ? C.green : C.red

  return (
    <div className="p-4 flex flex-col gap-3" style={{ paddingTop: 'env(safe-area-inset-top,12px)' }}>
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-white">Dashboard</h1>
        <button onClick={refresh} className="text-xs" style={{ color: C.muted }}>
          {lastRefresh ? `↻ ${lastRefresh.toLocaleTimeString()}` : '↻ Refresh'}
        </button>
      </div>

      {error && (
        <div className="rounded-lg px-4 py-3 text-sm" style={{ background: '#ef444422', color: C.red }}>
          {error}
        </div>
      )}

      {/* Bot status card */}
      <Card>
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-white">Bot Status</span>
          {status && (
            <Badge
              label={status.bot_running ? 'RUNNING' : 'STOPPED'}
              color={status.bot_running ? C.green : C.red}
            />
          )}
        </div>
        <div className="flex gap-4 text-sm" style={{ color: C.muted }}>
          <span>Mode: <b style={{ color: 'white' }}>{status?.mode?.toUpperCase() ?? '—'}</b></span>
          <span>Uptime: <b style={{ color: 'white' }}>{formatUptime(status?.uptime_seconds)}</b></span>
        </div>
      </Card>

      {/* Position card */}
      <Card>
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-white">Position</span>
          {position?.open
            ? <Badge label={position.option_type?.toUpperCase() ?? 'OPEN'} color={C.amber} />
            : <Badge label="FLAT" color={C.muted} />
          }
        </div>
        {position?.open ? (
          <div className="flex flex-col gap-1 text-sm">
            <div className="font-mono text-xs" style={{ color: C.muted }}>{position.name}</div>
            <div className="flex gap-4">
              <span style={{ color: C.muted }}>Strike: <b style={{ color: 'white' }}>${position.strike?.toLocaleString()}</b></span>
              <span style={{ color: C.muted }}>DTE: <b style={{ color: 'white' }}>{position.dte}d</b></span>
              <span style={{ color: C.muted }}>Δ: <b style={{ color: 'white' }}>{position.delta?.toFixed(3)}</b></span>
            </div>
            <div className="flex gap-4">
              <span style={{ color: C.muted }}>Contracts: <b style={{ color: 'white' }}>{position.contracts}</b></span>
              <span style={{ color: position.unrealized_pnl_usd >= 0 ? C.green : C.red, fontWeight: 700 }}>
                {position.unrealized_pnl_usd >= 0 ? '+' : ''}${position.unrealized_pnl_usd?.toFixed(0)} unrealized
              </span>
            </div>
          </div>
        ) : (
          <p className="text-sm" style={{ color: C.muted }}>No open position.</p>
        )}
      </Card>

      {/* Equity sparkline */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <span className="font-semibold text-white">Equity</span>
          {equity?.total_return_pct != null && (
            <span className="text-sm font-bold" style={{ color: pnlColor }}>
              {equity.total_return_pct >= 0 ? '+' : ''}{equity.total_return_pct?.toFixed(1)}%
            </span>
          )}
        </div>
        {sparkData.length > 1 ? (
          <ResponsiveContainer width="100%" height={80}>
            <LineChart data={sparkData}>
              <Line type="monotone" dataKey="v" stroke={pnlColor} dot={false} strokeWidth={2} />
              <YAxis domain={['auto', 'auto']} hide />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: 'none', borderRadius: 8, fontSize: 12 }}
                formatter={v => [`$${v.toLocaleString()}`, 'Equity']}
                labelFormatter={(_,p) => p[0]?.payload?.d ?? ''}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-sm text-center py-4" style={{ color: C.muted }}>
            {equity?.current_equity ? `$${equity.current_equity.toLocaleString()}` : 'No equity data yet'}
          </p>
        )}
      </Card>

      {/* Action buttons */}
      <div className="flex gap-2">
        <ConfirmButton
          label="▶ Start"
          onConfirm={() => api.start()}
          color={C.green}
          confirmLabel="Start bot?"
        />
        <ConfirmButton
          label="⏹ Stop"
          onConfirm={() => api.stop()}
          color={C.red}
          confirmLabel="Stop bot?"
        />
        <ConfirmButton
          label="✕ Close Pos"
          onConfirm={() => api.closePosition()}
          color={C.amber}
          confirmLabel="Close now?"
        />
      </div>
    </div>
  )
}
