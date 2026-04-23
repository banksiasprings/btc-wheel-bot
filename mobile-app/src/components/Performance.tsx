import { useState, useEffect, useCallback } from 'react'
import {
  AreaChart,
  Area,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend,
} from 'recharts'
import {
  getEquity,
  getTrades,
  getFarmStatus,
  getFarmBotTrades,
  EquityData,
  Trade,
  FarmStatus,
  BotFarmEntry,
} from '../api'
import InfoModal from './InfoModal'
import { GLOSSARY } from '../lib/glossary'

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

function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return iso.slice(0, 10)
  }
}

function outcomeLabel(t: Trade): { text: string; color: string } {
  const r = t.reason ?? ''
  if (r.includes('expiry') || r.includes('otm')) return { text: '✓ Expired OTM', color: 'text-green-400' }
  if (r.includes('itm') || r.includes('assigned')) return { text: '✗ Assigned ITM', color: 'text-red-400' }
  return { text: '⟳ Closed Early', color: 'text-amber-400' }
}

// ── Bot selector pill ─────────────────────────────────────────────────────────

type BotSelection = 'live' | 'all' | string  // string = farm bot id

interface BotTab {
  id: BotSelection
  label: string
  configName?: string
}

// ── Equity chart colours per bot ───────────────────────────────────────────────

const BOT_COLORS = ['#22c55e', '#38bdf8', '#f97316', '#a78bfa', '#fb7185']

// ── Trade list ────────────────────────────────────────────────────────────────

function TradeList({ trades, loading, empty }: { trades: Trade[]; loading: boolean; empty: string }) {
  if (loading) return <div className="text-slate-400 text-sm text-center py-6">Loading trades…</div>
  if (trades.length === 0) return (
    <div className="bg-card rounded-2xl p-6 border border-border text-center text-slate-400 text-sm">
      {empty}
    </div>
  )
  return (
    <div className="space-y-2">
      {trades.map((t, i) => {
        const outcome = outcomeLabel(t)
        const pnlColor = t.pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'
        return (
          <div key={i} className="bg-card rounded-2xl p-3 border border-border">
            <div className="flex items-start justify-between">
              <div>
                <p className="font-medium text-white text-sm">
                  {t.instrument || `${t.option_type?.toUpperCase()} ${t.strike?.toLocaleString()}`}
                </p>
                <p className="text-xs text-slate-400 mt-0.5">
                  {fmtDate(t.timestamp)} · {t.dte_at_entry}d → {t.dte_at_close}d
                </p>
                <p className={`text-xs mt-1 ${outcome.color}`}>{outcome.text}</p>
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

// ── Key metrics row ───────────────────────────────────────────────────────────

function MetricChip({ label, value, color = 'text-white', onInfo }: { label: string; value: string; color?: string; onInfo?: () => void }) {
  return (
    <div className="bg-navy rounded-xl px-3 py-2 text-center">
      <div className="flex items-center justify-center gap-1">
        <p className="text-xs text-slate-500">{label}</p>
        {onInfo && <button onClick={onInfo} className="text-slate-600 hover:text-slate-400 text-xs leading-none">ⓘ</button>}
      </div>
      <p className={`text-sm font-bold mt-0.5 ${color}`}>{value}</p>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function Performance() {
  const [equity, setEquity]         = useState<EquityData | null>(null)
  const [liveTrades, setLiveTrades] = useState<Trade[]>([])
  const [farmStatus, setFarmStatus] = useState<FarmStatus | null>(null)
  const [botTrades, setBotTrades]   = useState<Record<string, Trade[]>>({})
  const [selected, setSelected]     = useState<BotSelection>('live')
  const [loading, setLoading]       = useState(true)
  const [tradesLoading, setTradesLoading] = useState(false)
  const [error, setError]           = useState('')
  const [info, setInfo]             = useState<{ title: string; body: string } | null>(null)

  // ── Initial data load ─────────────────────────────────────────────────────
  const fetchAll = useCallback(async () => {
    try {
      const [eq, trades, farm] = await Promise.allSettled([
        getEquity(),
        getTrades(),
        getFarmStatus(),
      ])
      if (eq.status === 'fulfilled') setEquity(eq.value)
      if (trades.status === 'fulfilled') setLiveTrades(trades.value)
      if (farm.status === 'fulfilled') setFarmStatus(farm.value)
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 15_000)
    return () => clearInterval(id)
  }, [fetchAll])

  // ── Load farm bot trades when a bot tab is selected ───────────────────────
  useEffect(() => {
    if (selected === 'live' || selected === 'all') return
    if (botTrades[selected]) return  // already loaded
    setTradesLoading(true)
    getFarmBotTrades(selected)
      .then(t => setBotTrades(prev => ({ ...prev, [selected]: t })))
      .catch(() => setBotTrades(prev => ({ ...prev, [selected]: [] })))
      .finally(() => setTradesLoading(false))
  }, [selected, botTrades])

  // ── Build tab list ────────────────────────────────────────────────────────
  const bots = farmStatus?.bots ?? []
  const tabs: BotTab[] = [
    { id: 'live', label: 'Live Bot' },
    ...(bots.length > 0 ? [{ id: 'all' as BotSelection, label: 'All Bots' }] : []),
    ...bots.map(b => ({
      id: b.id,
      label: b.name,
      configName: b.config_name ?? undefined,
    })),
  ]

  // ── Equity chart data ─────────────────────────────────────────────────────

  // Live bot equity chart points
  const liveChartPoints = (() => {
    if (!equity || equity.dates.length === 0) return []
    return equity.dates.map((d, i) => ({
      date: d.slice(5),
      live: equity.equity[i],
    }))
  })()

  // Farm bot metrics for "All" overlay
  const farmChartData = (() => {
    if (selected !== 'all' || bots.length === 0) return null
    // Build a combined set of date keys from equity + farm bots
    // Farm bots don't have time-series data from API — we show their summary metrics
    return bots
  })()

  // Single-bot equity from live
  const singleBotChart = selected === 'live' ? liveChartPoints : []

  // ── Current trades to display ─────────────────────────────────────────────
  const currentTrades: Trade[] = (() => {
    if (selected === 'live') return liveTrades
    if (selected === 'all') return liveTrades
    return botTrades[selected] ?? []
  })()

  const currentTradesLoading = tradesLoading && selected !== 'live' && selected !== 'all'

  // ── Summary metrics for selected bot ─────────────────────────────────────
  const summaryMetrics = (() => {
    if (selected === 'live') {
      const wins = liveTrades.filter(t => t.pnl_usd > 0).length
      const total = liveTrades.length
      const totalReturn = equity?.total_return_pct ?? null
      const totalPnl = liveTrades.reduce((s, t) => s + t.pnl_usd, 0)
      return {
        totalReturn: totalReturn != null ? fmtPct(totalReturn) : '—',
        returnColor: (totalReturn ?? 0) >= 0 ? 'text-green-400' : 'text-red-400',
        sharpe: '—',
        winRate: total > 0 ? `${((wins / total) * 100).toFixed(0)}%` : '—',
        maxDd: '—',
        trades: String(total),
        totalPnl: fmt$(totalPnl || null),
        pnlColor: totalPnl >= 0 ? 'text-green-400' : 'text-red-400',
      }
    }
    if (selected === 'all') {
      const bestSharpe = bots.length > 0 ? Math.max(...bots.map(b => b.metrics.sharpe ?? 0)) : null
      const totalTrades = bots.reduce((s, b) => s + (b.metrics.num_trades ?? 0), 0)
      return {
        totalReturn: '—',
        returnColor: 'text-slate-400',
        sharpe: bestSharpe != null ? bestSharpe.toFixed(2) : '—',
        winRate: '—',
        maxDd: '—',
        trades: String(totalTrades),
        totalPnl: '—',
        pnlColor: 'text-slate-400',
      }
    }
    const bot = bots.find(b => b.id === selected)
    if (!bot) return null
    const m = bot.metrics
    const trades = botTrades[selected] ?? []
    const totalPnl = trades.reduce((s, t) => s + t.pnl_usd, 0)
    return {
      totalReturn: fmtPct(m.total_return_pct),
      returnColor: (m.total_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400',
      sharpe: m.sharpe != null ? m.sharpe.toFixed(2) : '—',
      winRate: m.win_rate != null ? `${(m.win_rate * 100).toFixed(0)}%` : '—',
      maxDd: m.max_drawdown != null ? `${(m.max_drawdown * 100).toFixed(1)}%` : '—',
      trades: String(m.num_trades ?? 0),
      totalPnl: fmt$(totalPnl || null),
      pnlColor: totalPnl >= 0 ? 'text-green-400' : 'text-red-400',
    }
  })()

  // ── "All" mode: per-bot summary cards ────────────────────────────────────
  const sortedBots = [...bots].sort(
    (a, b) => (b.metrics.sharpe ?? 0) - (a.metrics.sharpe ?? 0)
  )

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-slate-400">Loading…</div>
  }

  return (
    <div className="p-4 space-y-4 pb-6">
      <h1 className="text-lg font-bold text-white pt-2">Performance</h1>

      {info && <InfoModal title={info.title} body={info.body} onClose={() => setInfo(null)} />}

      {error && (
        <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* ── Bot selector tabs ──────────────────────────────────────────────── */}
      <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setSelected(tab.id)}
            className={`flex-shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-colors border ${
              selected === tab.id
                ? 'bg-green-800 border-green-600 text-green-200'
                : 'bg-card border-border text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
            {tab.configName && (
              <span className="ml-1 opacity-60">· {tab.configName}</span>
            )}
          </button>
        ))}
      </div>

      {/* ── Equity chart ─────────────────────────────────────────────────── */}
      {selected === 'live' && singleBotChart.length > 1 && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">
              Equity ({singleBotChart.length}d)
            </p>
            <div className="text-right">
              <p className="font-semibold text-white text-sm">{fmt$(equity?.current_equity)}</p>
              <p className={`text-xs ${(equity?.total_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {fmtPct(equity?.total_return_pct)}
              </p>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={120}>
            <AreaChart data={singleBotChart} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="perfGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" hide />
              <YAxis hide domain={['auto', 'auto']} />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, color: '#fff', fontSize: 12 }}
                formatter={(v: number) => [fmt$(v), 'Equity']}
              />
              <Area type="monotone" dataKey="live" stroke="#22c55e" strokeWidth={2} fill="url(#perfGrad)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── All bots: overlay chart ─────────────────────────────────────── */}
      {selected === 'all' && farmChartData && farmChartData.length > 0 && (
        <div className="bg-card rounded-2xl p-4 border border-border">
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-3">
            Bot Farm Performance
          </p>
          {/* No time-series data for farm bots — show bar chart of total returns */}
          <div className="space-y-2">
            {sortedBots.map((bot, idx) => {
              const ret = bot.metrics.total_return_pct ?? 0
              const maxRet = Math.max(...bots.map(b => Math.abs(b.metrics.total_return_pct ?? 0)), 1)
              const barW = Math.max((Math.abs(ret) / maxRet) * 100, 2)
              const color = BOT_COLORS[idx % BOT_COLORS.length]
              return (
                <div key={bot.id} className="flex items-center gap-3">
                  <span className="text-xs text-slate-400 w-28 truncate flex-shrink-0">{bot.name}</span>
                  <div className="flex-1 h-3 bg-slate-800 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${barW}%`, backgroundColor: color }}
                    />
                  </div>
                  <span className={`text-xs font-mono w-14 text-right flex-shrink-0 ${ret >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {ret >= 0 ? '+' : ''}{ret.toFixed(1)}%
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── All bots: per-bot summary cards ────────────────────────────── */}
      {selected === 'all' && sortedBots.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-slate-500 px-1 font-medium uppercase tracking-wide">Bots ranked by Sharpe</p>
          {sortedBots.map((bot, idx) => {
            const m = bot.metrics
            const r = bot.readiness
            const color = BOT_COLORS[idx % BOT_COLORS.length]
            return (
              <div key={bot.id} className="bg-card rounded-2xl border border-border overflow-hidden">
                <div className="px-4 py-3">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
                    <p className="font-semibold text-white text-sm">{bot.name}</p>
                    {bot.config_name && (
                      <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-900 text-amber-300 border border-amber-700">
                        {bot.config_name}
                      </span>
                    )}
                    {r.ready && <span className="text-xs text-green-400">✅ Ready</span>}
                  </div>
                  <div className="grid grid-cols-5 gap-1 text-center">
                    {[
                      { label: 'Return', value: fmtPct(m.total_return_pct), color: (m.total_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400' },
                      { label: 'Sharpe', value: m.sharpe != null ? m.sharpe.toFixed(2) : '—', color: 'text-white' },
                      { label: 'Win', value: m.win_rate != null ? `${(m.win_rate * 100).toFixed(0)}%` : '—', color: 'text-white' },
                      { label: 'MaxDD', value: m.max_drawdown != null ? `-${(m.max_drawdown * 100).toFixed(1)}%` : '—', color: 'text-red-400' },
                      { label: 'Trades', value: String(m.num_trades ?? 0), color: 'text-white' },
                    ].map(({ label, value, color: c }) => (
                      <div key={label}>
                        <p className="text-xs text-slate-500">{label}</p>
                        <p className={`text-xs font-medium ${c}`}>{value}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Key metrics row (single bot or live) ────────────────────────── */}
      {summaryMetrics && selected !== 'all' && (
        <div className="grid grid-cols-3 gap-2">
          <MetricChip
            label="Total Return"
            value={summaryMetrics.totalReturn}
            color={summaryMetrics.returnColor}
            onInfo={() => setInfo(GLOSSARY.return_pct)}
          />
          <MetricChip
            label="Sharpe"
            value={summaryMetrics.sharpe}
            onInfo={() => setInfo(GLOSSARY.sharpe_ratio)}
          />
          <MetricChip
            label="Win Rate"
            value={summaryMetrics.winRate}
            onInfo={() => setInfo(GLOSSARY.win_rate)}
          />
          {summaryMetrics.maxDd !== '—' && (
            <MetricChip label="Max DD" value={summaryMetrics.maxDd} color="text-red-400" />
          )}
          <MetricChip label="Trades" value={summaryMetrics.trades} />
          <MetricChip label="Total P&L" value={summaryMetrics.totalPnl} color={summaryMetrics.pnlColor} />
        </div>
      )}

      {/* ── Trade history ─────────────────────────────────────────────────── */}
      {selected !== 'all' && (
        <div className="space-y-2">
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide px-1">
            Trade History
            {selected !== 'live' && bots.find(b => b.id === selected)?.config_name && (
              <span className="ml-2 normal-case font-normal text-slate-600">
                · {bots.find(b => b.id === selected)?.config_name}
              </span>
            )}
          </p>
          <TradeList
            trades={currentTrades}
            loading={currentTradesLoading}
            empty={selected === 'live'
              ? 'No trades yet. Start paper trading to see results here.'
              : 'No trades for this bot yet.'}
          />
        </div>
      )}

      {/* ── Farm not started notice ───────────────────────────────────────── */}
      {selected !== 'live' && bots.length === 0 && (
        <div className="bg-card rounded-2xl p-6 border border-border text-center">
          <p className="text-slate-400 text-sm">Farm hasn't been started yet.</p>
          <p className="text-slate-600 text-xs mt-1">Start the farm from the Pipeline tab (Step 3) to see bot performance here.</p>
        </div>
      )}
    </div>
  )
}
