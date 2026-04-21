import { useState, useEffect } from 'react'
import { getTrades, Trade } from '../api'
import InfoModal from './InfoModal'
import { GLOSSARY } from '../lib/glossary'

type InfoEntry = { title: string; body: string }

function outcomeLabel(t: Trade): { text: string; color: string } {
  const r = t.reason ?? ''
  if (r.includes('expiry') || r.includes('otm')) {
    return { text: '✓ Expired OTM', color: 'text-green-400' }
  }
  if (r.includes('itm') || r.includes('assigned')) {
    return { text: '✗ Assigned ITM', color: 'text-red-400' }
  }
  return { text: '⟳ Closed Early', color: 'text-amber-400' }
}

function fmt$(n: number) {
  const sign = n >= 0 ? '+' : ''
  return `${sign}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return iso.slice(0, 10)
  }
}

export default function Trades() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [info, setInfo] = useState<InfoEntry | null>(null)

  useEffect(() => {
    getTrades()
      .then(setTrades)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const totalPnl = trades.reduce((s, t) => s + t.pnl_usd, 0)
  const wins = trades.filter((t) => t.pnl_usd > 0).length
  const winRate = trades.length > 0 ? (wins / trades.length) * 100 : 0

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-slate-400">Loading…</div>
  }

  return (
    <div className="p-4 space-y-4 pb-4">
      <h1 className="text-lg font-bold text-white pt-2">Trades</h1>

      {info && <InfoModal title={info.title} body={info.body} onClose={() => setInfo(null)} />}

      {error && (
        <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-3">
        <StatCard label="Total Trades" value={String(trades.length)} />
        <StatCard
          label="Win Rate"
          value={`${winRate.toFixed(0)}%`}
          color={winRate >= 70 ? 'text-green-400' : 'text-amber-400'}
          onInfo={() => setInfo(GLOSSARY.win_rate)}
        />
        <StatCard
          label="Total P&L"
          value={fmt$(totalPnl)}
          color={totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}
          onInfo={() => setInfo(GLOSSARY.trade_pnl)}
        />
      </div>

      {/* Trade list */}
      {trades.length === 0 ? (
        <div className="bg-card rounded-2xl p-6 border border-border text-center text-slate-400 text-sm">
          No trades yet. Start paper trading to see results here.
        </div>
      ) : (
        <>
          {/* Field legend */}
          <div className="flex gap-3 flex-wrap px-1">
            {([
              ['Instrument', 'trade_instrument'],
              ['DTE', 'trade_dte'],
              ['Outcome', 'trade_reason'],
              ['P&L', 'trade_pnl'],
            ] as [string, string][]).map(([label, key]) => (
              <button
                key={key}
                onClick={() => setInfo(GLOSSARY[key])}
                className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-0.5 transition-colors"
              >
                {label} <span className="opacity-70">ⓘ</span>
              </button>
            ))}
          </div>

          <div className="space-y-3">
            {trades.map((t, i) => {
              const outcome = outcomeLabel(t)
              const pnlColor = t.pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'
              return (
                <div key={i} className="bg-card rounded-2xl p-4 border border-border">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="font-medium text-white text-sm">
                        {t.instrument || `${t.option_type?.toUpperCase()} ${t.strike?.toLocaleString()}`}
                      </p>
                      <p className="text-xs text-slate-400 mt-0.5">
                        {fmtDate(t.timestamp)} · {t.dte_at_entry}d entry → {t.dte_at_close}d close
                      </p>
                      <p className={`text-xs mt-1 ${outcome.color}`}>{outcome.text}</p>
                    </div>
                    <div className="text-right">
                      <p className={`font-bold ${pnlColor}`}>{fmt$(t.pnl_usd)}</p>
                      <p className={`text-xs ${pnlColor}`}>
                        {t.pnl_btc >= 0 ? '+' : ''}{t.pnl_btc.toFixed(5)} BTC
                      </p>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

function StatCard({
  label,
  value,
  color = 'text-white',
  onInfo,
}: {
  label: string
  value: string
  color?: string
  onInfo?: () => void
}) {
  return (
    <div className="bg-card rounded-2xl p-3 border border-border text-center">
      <div className="flex items-center justify-center gap-1">
        <p className="text-xs text-slate-400">{label}</p>
        {onInfo && (
          <button onClick={onInfo} className="text-slate-500 hover:text-slate-300 text-xs leading-none">ⓘ</button>
        )}
      </div>
      <p className={`font-bold text-base mt-0.5 ${color}`}>{value}</p>
    </div>
  )
}
