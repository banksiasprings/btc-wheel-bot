import { useState, useEffect } from 'react'
import { api } from '../api.js'

const C = { card: '#1e293b', green: '#22c55e', red: '#ef4444', amber: '#f59e0b', muted: '#94a3b8' }

const OUTCOME_COLOR = {
  expired_worthless: C.green,
  assigned:          C.red,
  closed_early:      C.amber,
}

function OutcomeBadge({ outcome }) {
  const color = OUTCOME_COLOR[outcome] ?? C.muted
  const label = {
    expired_worthless: 'Expired OTM',
    assigned:          'Assigned',
    closed_early:      'Closed Early',
  }[outcome] ?? outcome
  return (
    <span className="rounded-full px-2 py-0.5 text-xs font-bold"
          style={{ background: color + '22', color }}>
      {label}
    </span>
  )
}

export default function TabTrades() {
  const [trades, setTrades] = useState([])
  const [error,  setError]  = useState('')

  useEffect(() => {
    api.trades()
      .then(t => { setTrades(t); setError('') })
      .catch(e => setError(e.message))
  }, [])

  const wins   = trades.filter(t => t.pnl_usd > 0).length
  const losses = trades.filter(t => t.pnl_usd <= 0).length
  const totalPnl = trades.reduce((s, t) => s + (t.pnl_usd || 0), 0)

  return (
    <div className="p-4 flex flex-col gap-3" style={{ paddingTop: 'env(safe-area-inset-top,12px)' }}>
      <h1 className="text-lg font-bold text-white">Paper Trades</h1>

      {error && (
        <div className="rounded-lg px-4 py-3 text-sm" style={{ background: '#ef444422', color: C.red }}>
          {error}
        </div>
      )}

      {trades.length > 0 && (
        <div className="rounded-xl p-4 flex gap-4" style={{ background: C.card }}>
          <div className="flex-1 text-center">
            <div className="text-xs" style={{ color: C.muted }}>Trades</div>
            <div className="text-xl font-bold text-white">{trades.length}</div>
          </div>
          <div className="flex-1 text-center">
            <div className="text-xs" style={{ color: C.muted }}>Win Rate</div>
            <div className="text-xl font-bold" style={{ color: C.green }}>
              {trades.length ? Math.round(wins / trades.length * 100) : 0}%
            </div>
          </div>
          <div className="flex-1 text-center">
            <div className="text-xs" style={{ color: C.muted }}>Total P&L</div>
            <div className="text-xl font-bold" style={{ color: totalPnl >= 0 ? C.green : C.red }}>
              {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(0)}
            </div>
          </div>
        </div>
      )}

      {trades.length === 0 && !error && (
        <p className="text-center py-12 text-sm" style={{ color: C.muted }}>
          No paper trades yet. Run the bot in paper mode to start collecting trades.
        </p>
      )}

      <div className="flex flex-col gap-2">
        {trades.map((t, i) => (
          <div key={i} className="rounded-xl p-4 flex flex-col gap-2" style={{ background: C.card }}>
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold text-white">${t.strike?.toLocaleString()} put</div>
              <OutcomeBadge outcome={t.outcome} />
            </div>
            <div className="flex items-center justify-between text-xs" style={{ color: C.muted }}>
              <span>{t.entry_date?.slice(0, 10)} → {t.expiry_date?.slice(0, 10) ?? '—'}</span>
              <span className="font-bold text-sm" style={{ color: t.pnl_usd >= 0 ? C.green : C.red }}>
                {t.pnl_usd >= 0 ? '+' : ''}${t.pnl_usd?.toFixed(0)}
                <span className="font-normal ml-1">
                  ({t.pnl_pct >= 0 ? '+' : ''}{(t.pnl_pct * 100)?.toFixed(1)}%)
                </span>
              </span>
            </div>
            <div className="flex gap-3 text-xs" style={{ color: C.muted }}>
              <span>{t.contracts} contracts</span>
              <span>Entry ${t.spot_at_entry?.toLocaleString()}</span>
              {t.iv_at_entry > 0 && <span>IV {t.iv_at_entry?.toFixed(0)}%</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
