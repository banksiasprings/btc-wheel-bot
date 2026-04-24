import { useState, useEffect, useCallback } from 'react'
import { AreaChart, Area, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  getFarmStatus,
  getFarmBotTrades,
  Trade,
  FarmStatus,
  BotFarmEntry,
} from '../api'

// ── Formatting helpers ─────────────────────────────────────────────────────────

function fmt$(n: number | undefined | null) {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function fmtPct(n: number | undefined | null, dec = 1) {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(dec)}%`
}

function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return iso.slice(0, 10)
  }
}

function outcomeLabel(t: Trade): { text: string; color: string } {
  const r = t.reason ?? ''
  if (r.includes('expiry') || r.includes('otm')) return { text: '✓ Expired OTM',   color: 'text-green-400' }
  if (r.includes('itm')    || r.includes('assigned')) return { text: '✗ Assigned ITM', color: 'text-red-400'   }
  return { text: '⟳ Closed Early', color: 'text-amber-400' }
}

/** Annualised return: ((1+r)^(365/days) - 1) × 100. Returns null if < 7 days. */
function annualisedReturn(totalReturnPct: number | null | undefined, daysRunning: number | null | undefined): number | null {
  if (totalReturnPct == null || daysRunning == null || daysRunning < 7) return null
  return ((1 + totalReturnPct / 100) ** (365 / daysRunning) - 1) * 100
}

/** Build equity time-series from a bot's trade list. */
function buildEquityCurve(trades: Trade[], startingEquity: number | undefined): { date: string; equity: number }[] {
  const sorted = [...trades].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  )
  const points: { date: string; equity: number }[] = []
  if (startingEquity != null && sorted.length > 0) {
    const d = new Date(sorted[0].timestamp)
    d.setDate(d.getDate() - 1)
    points.push({ date: d.toISOString().slice(5, 10), equity: startingEquity })
  }
  for (const t of sorted) {
    const eq = typeof t.equity_after === 'number' ? t.equity_after : parseFloat(String(t.equity_after))
    if (!isNaN(eq)) points.push({ date: new Date(t.timestamp).toISOString().slice(5, 10), equity: eq })
  }
  return points
}

// ── Trade list ─────────────────────────────────────────────────────────────────

function TradeList({ trades, loading }: { trades: Trade[]; loading: boolean }) {
  if (loading) return <p className="text-slate-400 text-xs text-center py-4">Loading trades…</p>
  if (trades.length === 0) return (
    <p className="text-slate-500 text-xs text-center py-4">No trades yet — waiting for first signal.</p>
  )
  return (
    <div className="space-y-2">
      {trades.map((t, i) => {
        const outcome = outcomeLabel(t)
        const pnlColor = t.pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'
        return (
          <div key={i} className="bg-navy rounded-xl p-3">
            <div className="flex items-start justify-between">
              <div>
                <p className="font-medium text-white text-xs">
                  {t.instrument || `${t.option_type?.toUpperCase()} ${t.strike?.toLocaleString()}`}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  {fmtDate(t.timestamp)} · {t.dte_at_entry}d → {t.dte_at_close}d
                </p>
                <p className={`text-xs mt-0.5 ${outcome.color}`}>{outcome.text}</p>
              </div>
              <div className="text-right">
                <p className={`font-bold text-sm ${pnlColor}`}>
                  {t.pnl_usd >= 0 ? '+' : ''}${Math.abs(t.pnl_usd).toFixed(2)}
                </p>
                <p className={`text-xs ${pnlColor}`}>
                  {t.pnl_btc >= 0 ? '+' : ''}{t.pnl_btc.toFixed(5)} BTC
                </p>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Bot performance card ───────────────────────────────────────────────────────

const BOT_COLORS = ['#22c55e', '#38bdf8', '#f97316', '#a78bfa', '#fb7185']

function BotPerfCard({
  bot, rank, onLoadTrades, trades, tradesLoading,
}: {
  bot: BotFarmEntry
  rank: number
  onLoadTrades: (id: string) => void
  trades: Trade[] | null
  tradesLoading: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const m = bot.metrics
  const color = BOT_COLORS[rank % BOT_COLORS.length]

  const annReturn = annualisedReturn(m.total_return_pct, m.days_running)
  const equityCurve = trades ? buildEquityCurve(trades, m.starting_equity) : []
  const curEq = equityCurve.length > 0 ? equityCurve[equityCurve.length - 1].equity : null

  function toggle() {
    setExpanded(e => !e)
    if (!expanded && trades == null) onLoadTrades(bot.id)
  }

  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      {/* Header row — always visible, tap to expand */}
      <button className="w-full flex items-center gap-3 px-4 py-3 text-left" onClick={toggle}>
        {/* Rank + colour dot */}
        <div className="flex flex-col items-center gap-0.5 flex-shrink-0 w-6">
          <span className="text-xs text-slate-500 font-mono">#{rank + 1}</span>
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-semibold text-white text-sm truncate">{bot.name}</p>
            {bot.config_name && (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-900 text-amber-300 border border-amber-700 flex-shrink-0">
                {bot.config_name}
              </span>
            )}
          </div>
          {/* Key stats inline */}
          <div className="flex gap-3 mt-0.5 flex-wrap">
            <span className="text-xs text-slate-500">
              Sharpe <span className="text-white font-mono">{m.sharpe != null ? m.sharpe.toFixed(2) : '—'}</span>
            </span>
            <span className="text-xs text-slate-500">
              Ann. <span className={`font-mono ${(annReturn ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {fmtPct(annReturn)}
              </span>
            </span>
            <span className="text-xs text-slate-500">
              Win <span className="text-white font-mono">{m.win_rate != null ? `${(m.win_rate * 100).toFixed(0)}%` : '—'}</span>
            </span>
            <span className="text-xs text-slate-500">
              {m.num_trades ?? 0} trades
            </span>
          </div>
        </div>

        <span className="text-slate-500 text-xs flex-shrink-0">{expanded ? '▲' : '▼'}</span>
      </button>

      {/* Expanded: equity chart + trade list */}
      {expanded && (
        <div className="border-t border-border/40 px-4 py-3 space-y-4">

          {/* Equity chart */}
          {equityCurve.length > 1 ? (
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs text-slate-400 uppercase tracking-wide font-medium">Equity Curve</p>
                <div className="text-right">
                  <p className="text-sm font-semibold text-white">{fmt$(curEq)}</p>
                  <p className={`text-xs ${(m.total_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {fmtPct(annReturn)} ann. · {fmtPct(m.total_return_pct)} total
                  </p>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={130}>
                <AreaChart data={equityCurve} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id={`grad-${bot.id}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={color} stopOpacity={0.35} />
                      <stop offset="95%" stopColor={color} stopOpacity={0}    />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="date" hide />
                  <YAxis hide domain={['auto', 'auto']} />
                  <Tooltip
                    contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, color: '#fff', fontSize: 12 }}
                    formatter={(v: number) => [fmt$(v), 'Equity']}
                  />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    stroke={color}
                    strokeWidth={2}
                    fill={`url(#grad-${bot.id})`}
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : tradesLoading ? (
            <p className="text-xs text-slate-500 text-center py-2">Loading chart…</p>
          ) : (
            <p className="text-xs text-slate-500 text-center py-2">No equity data yet — trades will appear here.</p>
          )}

          {/* Full metrics row */}
          <div className="grid grid-cols-4 gap-2 text-center">
            {[
              { label: 'Sharpe',   value: m.sharpe != null ? m.sharpe.toFixed(2) : '—',          color: 'text-white'      },
              { label: 'Win Rate', value: m.win_rate != null ? `${(m.win_rate*100).toFixed(0)}%` : '—', color: 'text-white' },
              { label: 'Max DD',   value: m.max_drawdown != null ? `-${(m.max_drawdown*100).toFixed(1)}%` : '—', color: 'text-red-400' },
              { label: 'Trades',   value: String(m.num_trades ?? 0),                              color: 'text-white'      },
            ].map(({ label, value, color: c }) => (
              <div key={label} className="bg-navy rounded-xl py-2">
                <p className="text-xs text-slate-500">{label}</p>
                <p className={`text-xs font-semibold mt-0.5 ${c}`}>{value}</p>
              </div>
            ))}
          </div>

          {/* Trade history */}
          <div>
            <p className="text-xs text-slate-400 uppercase tracking-wide font-medium mb-2">Trade History</p>
            <TradeList trades={trades ?? []} loading={tradesLoading && trades == null} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main Performance component ─────────────────────────────────────────────────

export default function Performance() {
  const [farmStatus, setFarmStatus] = useState<FarmStatus | null>(null)
  const [botTrades, setBotTrades]   = useState<Record<string, Trade[]>>({})
  const [tradesLoading, setTradesLoading] = useState<Record<string, boolean>>({})
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState('')

  const fetchFarm = useCallback(async () => {
    try {
      const farm = await getFarmStatus()
      setFarmStatus(farm)
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchFarm()
    const id = setInterval(fetchFarm, 15_000)
    return () => clearInterval(id)
  }, [fetchFarm])

  function loadTrades(botId: string) {
    if (botTrades[botId] != null || tradesLoading[botId]) return
    setTradesLoading(prev => ({ ...prev, [botId]: true }))
    getFarmBotTrades(botId)
      .then(t => setBotTrades(prev => ({ ...prev, [botId]: t })))
      .catch(() => setBotTrades(prev => ({ ...prev, [botId]: [] })))
      .finally(() => setTradesLoading(prev => ({ ...prev, [botId]: false })))
  }

  const bots = farmStatus?.bots ?? []
  // Sort by Sharpe descending (null last)
  const sorted = [...bots].sort((a, b) => (b.metrics.sharpe ?? -Infinity) - (a.metrics.sharpe ?? -Infinity))

  if (loading) return <div className="flex items-center justify-center h-64 text-slate-400">Loading…</div>

  return (
    <div className="p-4 space-y-3 pb-6">
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-lg font-bold text-white">Performance</h1>
        <span className="text-xs text-slate-500">Ranked by Sharpe</span>
      </div>

      {error && (
        <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">{error}</div>
      )}

      {sorted.length === 0 ? (
        <div className="bg-card rounded-2xl border border-border px-4 py-8 text-center">
          <p className="text-slate-400 text-sm">No bots running yet.</p>
          <p className="text-slate-600 text-xs mt-1">Start the farm from the Pipeline tab to see performance here.</p>
        </div>
      ) : (
        sorted.map((bot, rank) => (
          <BotPerfCard
            key={bot.id}
            bot={bot}
            rank={rank}
            onLoadTrades={loadTrades}
            trades={botTrades[bot.id] ?? null}
            tradesLoading={tradesLoading[bot.id] ?? false}
          />
        ))
      )}
    </div>
  )
}
