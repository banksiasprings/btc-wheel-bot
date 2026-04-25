import { useState, useEffect, useCallback } from 'react'
import {
  ComposedChart, Area, Line, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ReferenceArea, Cell,
} from 'recharts'
import {
  getChartData, ChartData, getFarmStatus, FarmStatus, BotFarmEntry,
} from '../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface CandleWithMarker {
  time: number
  open: number
  high: number
  low: number
  close: number
  tradeWon?: boolean
  tradePnl?: number
  tradeStrike?: number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPrice(n: number): string {
  return n >= 1000 ? `$${(n / 1000).toFixed(1)}k` : `$${n.toFixed(0)}`
}

function fmtTs(ts: number, resolution: string): string {
  const d = new Date(ts * 1000)
  if (resolution === '360') {
    return d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
  }
  return d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric' })
}

// Custom dot renderer: shows trade markers on the price line
function TradeDot(props: any) {
  const { cx, cy, payload } = props
  if (payload?.tradeWon === undefined) return null
  const fill = payload.tradeWon ? '#22c55e' : '#ef4444'
  const r = Math.min(10, Math.max(5, Math.abs(payload.tradePnl ?? 0) / 20))
  return (
    <g>
      <circle cx={cx} cy={cy} r={r + 3} fill={fill} opacity={0.2} />
      <circle cx={cx} cy={cy} r={r} fill={fill} stroke="white" strokeWidth={1.5} />
    </g>
  )
}

// Custom tooltip
function ChartTooltip({ active, payload, label, resolution }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload as CandleWithMarker
  if (!d) return null
  return (
    <div className="bg-slate-800 border border-slate-600 rounded-xl px-3 py-2 text-xs shadow-lg">
      <p className="text-slate-400 mb-1">{fmtTs(d.time, resolution)}</p>
      <p className="text-white font-semibold">{fmtPrice(d.close)}</p>
      {d.tradeWon !== undefined && (
        <p className={`mt-1 font-medium ${d.tradeWon ? 'text-green-400' : 'text-red-400'}`}>
          {d.tradeWon ? '✅ Win' : '❌ Loss'} · {d.tradePnl !== undefined ? `$${d.tradePnl.toFixed(0)}` : ''}
          {d.tradeStrike ? ` · Strike ${fmtPrice(d.tradeStrike)}` : ''}
        </p>
      )}
    </div>
  )
}

// ── Position card ─────────────────────────────────────────────────────────────

function PositionCard({ data }: { data: ChartData }) {
  const o = data.overlays
  if (!o.active_strike) return null
  const currentPrice = data.current_price ?? 0
  const distPct = currentPrice > 0 && o.active_strike
    ? ((currentPrice - o.active_strike) / currentPrice * 100).toFixed(1)
    : null
  const beDistPct = currentPrice > 0 && o.breakeven
    ? ((currentPrice - o.breakeven) / currentPrice * 100).toFixed(1)
    : null
  return (
    <div className="bg-card rounded-2xl border border-amber-800/60 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-white">Open Position</span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-amber-900/60 text-amber-300 border border-amber-700/60">
          PUT active
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-navy rounded-xl px-3 py-2 text-center">
          <p className="text-xs text-slate-500">Strike</p>
          <p className="text-sm font-semibold text-orange-400 mt-0.5">{fmtPrice(o.active_strike)}</p>
          {distPct && <p className="text-xs text-slate-500">↑ {distPct}% buffer</p>}
        </div>
        {o.breakeven && (
          <div className="bg-navy rounded-xl px-3 py-2 text-center">
            <p className="text-xs text-slate-500">Breakeven</p>
            <p className="text-sm font-semibold text-green-400 mt-0.5">{fmtPrice(o.breakeven)}</p>
            {beDistPct && <p className="text-xs text-slate-500">↑ {beDistPct}%</p>}
          </div>
        )}
        {o.zone_center && (
          <div className="bg-navy rounded-xl px-3 py-2 text-center">
            <p className="text-xs text-slate-500">Entry Zone</p>
            <p className="text-sm font-semibold text-blue-400 mt-0.5">{fmtPrice(o.zone_center)}</p>
            <p className="text-xs text-slate-500">center</p>
          </div>
        )}
      </div>
      {o.expiry_ts && (
        <p className="text-xs text-slate-400">
          Expires: {new Date(o.expiry_ts * 1000).toLocaleDateString('en-AU', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}
        </p>
      )}
    </div>
  )
}

// ── Main TradingView ──────────────────────────────────────────────────────────

export default function TradingView() {
  const [days, setDays]             = useState(30)
  const [botId, setBotId]           = useState<string | undefined>(undefined)
  const [chartData, setChartData]   = useState<ChartData | null>(null)
  const [farmStatus, setFarmStatus] = useState<FarmStatus | null>(null)
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState<string | null>(null)

  // Load farm status once; auto-select first bot
  useEffect(() => {
    getFarmStatus().then(fs => {
      setFarmStatus(fs)
      if (!botId && fs.bots.length > 0) {
        setBotId(fs.bots[0].id)
      }
    }).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getChartData(days, botId)
      setChartData(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [days, botId])

  useEffect(() => { load() }, [load])

  // Auto-refresh every 30s
  useEffect(() => {
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  // Merge trade markers into candle data (place marker on nearest candle)
  const candlesWithMarkers: CandleWithMarker[] = (() => {
    if (!chartData) return []
    const { candles, trade_markers } = chartData
    return candles.map(c => {
      // Find a trade marker whose exit_time lands on or near this candle
      // Resolution: 360min = 21600s, 1D = 86400s
      const resolution = chartData.resolution === '360' ? 21600 : 86400
      const marker = trade_markers.find(m => {
        const mt = m.exit_time ?? m.entry_time
        return mt >= c.time - resolution / 2 && mt < c.time + resolution / 2
      })
      if (!marker) return c
      return {
        ...c,
        tradeWon: marker.won,
        tradePnl: marker.pnl_usd,
        tradeStrike: marker.strike ?? undefined,
      }
    })
  })()

  // Compute Y axis domain with some padding for reference lines
  const yDomain = (() => {
    if (!candlesWithMarkers.length) return ['auto', 'auto'] as [string, string]
    const prices = candlesWithMarkers.map(c => c.close)
    const o = chartData?.overlays
    const allValues = [
      ...prices,
      o?.active_strike,
      o?.breakeven,
      o?.zone_upper,
      o?.zone_lower,
    ].filter((v): v is number => v != null && v > 0)
    const min = Math.min(...allValues)
    const max = Math.max(...allValues)
    const pad = (max - min) * 0.05
    return [Math.floor(min - pad), Math.ceil(max + pad)] as [number, number]
  })()

  const bots: BotFarmEntry[] = farmStatus?.bots ?? []
  const selectedBot = bots.find(b => b.id === botId)

  const o = chartData?.overlays
  const tradeCount = chartData?.trade_markers.length ?? 0
  const wins = chartData?.trade_markers.filter(m => m.won).length ?? 0
  const losses = tradeCount - wins

  return (
    <div className="flex flex-col min-h-screen bg-navy pb-24">
      {/* Header */}
      <div className="sticky top-0 bg-navy/95 backdrop-blur border-b border-slate-800 px-4 py-3 z-10">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-base font-bold text-white">Trading</h1>
          {chartData?.current_price && (
            <div className="text-right">
              <span className="text-lg font-bold text-white">
                ${chartData.current_price.toLocaleString('en-AU', { maximumFractionDigits: 0 })}
              </span>
              <span className="text-xs text-slate-400 ml-1">BTC</span>
            </div>
          )}
        </div>

        {/* Bot selector dropdown */}
        {bots.length > 0 && (
          <div className="relative mb-2">
            <select
              value={botId ?? ''}
              onChange={e => setBotId(e.target.value || undefined)}
              className="w-full appearance-none bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 pr-8 text-sm text-white focus:outline-none focus:border-green-600"
            >
              {bots.map(b => (
                <option key={b.id} value={b.id}>
                  {b.status === 'running' ? '🟢' : '🟡'} {b.name}
                  {b.config_name ? ` — ${b.config_name}` : ''}
                </option>
              ))}
            </select>
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs">▼</span>
          </div>
        )}

        {/* Period selector */}
        <div className="flex gap-2">
          {([7, 30, 90] as const).map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`flex-1 text-xs py-1.5 rounded-lg font-medium transition-colors ${
                days === d
                  ? 'bg-green-800 text-green-200'
                  : 'bg-slate-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      <div className="px-4 pt-4 space-y-4">

        {/* Trade summary pills */}
        {tradeCount > 0 && (
          <div className="flex gap-2">
            <div className="flex items-center gap-1.5 bg-slate-800 rounded-full px-3 py-1.5 text-xs">
              <span className="w-2 h-2 rounded-full bg-green-400" />
              <span className="text-slate-300">{wins} win{wins !== 1 ? 's' : ''}</span>
            </div>
            <div className="flex items-center gap-1.5 bg-slate-800 rounded-full px-3 py-1.5 text-xs">
              <span className="w-2 h-2 rounded-full bg-red-400" />
              <span className="text-slate-300">{losses} loss{losses !== 1 ? 'es' : ''}</span>
            </div>
            {tradeCount > 0 && (
              <div className="flex items-center gap-1.5 bg-slate-800 rounded-full px-3 py-1.5 text-xs">
                <span className="text-slate-400">WR</span>
                <span className={`font-semibold ${wins / tradeCount >= 0.6 ? 'text-green-400' : wins / tradeCount >= 0.5 ? 'text-amber-400' : 'text-red-400'}`}>
                  {(wins / tradeCount * 100).toFixed(0)}%
                </span>
              </div>
            )}
          </div>
        )}

        {/* Chart */}
        {loading && !chartData && (
          <div className="h-64 flex items-center justify-center">
            <div className="flex gap-1">
              {[0,1,2].map(i => (
                <div key={i} className="w-2 h-2 rounded-full bg-green-500 animate-bounce"
                     style={{ animationDelay: `${i * 0.15}s` }} />
              ))}
            </div>
          </div>
        )}

        {error && (
          <div className="h-32 flex items-center justify-center text-sm text-red-400">
            {error}
          </div>
        )}

        {chartData && candlesWithMarkers.length > 0 && (
          <div className="bg-card rounded-2xl border border-border p-2 pb-1 overflow-hidden">
            {/* Legend */}
            <div className="flex flex-wrap gap-3 px-2 pt-1 pb-2 text-xs text-slate-400">
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-3 h-0.5 bg-green-500" />BTC Price
              </span>
              {o?.active_strike && (
                <span className="flex items-center gap-1.5">
                  <span className="inline-block w-3 border-t border-dashed border-orange-400" />Strike
                </span>
              )}
              {o?.breakeven && (
                <span className="flex items-center gap-1.5">
                  <span className="inline-block w-3 border-t border-dashed border-green-500" />Breakeven
                </span>
              )}
              {(o?.zone_lower && o?.zone_upper) && (
                <span className="flex items-center gap-1.5">
                  <span className="inline-block w-3 h-2 bg-blue-500 opacity-30 rounded-sm" />Entry Zone
                </span>
              )}
              {tradeCount > 0 && (
                <span className="flex items-center gap-1.5">
                  <span className="inline-block w-2.5 h-2.5 rounded-full bg-green-500 border border-white" />Trade
                </span>
              )}
            </div>

            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={candlesWithMarkers} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#22c55e" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#22c55e" stopOpacity={0.02} />
                  </linearGradient>
                </defs>

                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />

                <XAxis
                  dataKey="time"
                  type="number"
                  scale="time"
                  domain={['dataMin', 'dataMax']}
                  tickFormatter={ts => {
                    const d = new Date(ts * 1000)
                    return chartData.resolution === '360'
                      ? d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric' })
                      : d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric' })
                  }}
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  tickCount={5}
                />

                <YAxis
                  domain={yDomain}
                  tickFormatter={v => fmtPrice(v)}
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  width={52}
                  tickCount={5}
                />

                <Tooltip content={<ChartTooltip resolution={chartData.resolution} />} />

                {/* Entry zone band */}
                {o?.zone_lower != null && o?.zone_upper != null && (
                  <ReferenceArea
                    y1={o.zone_lower} y2={o.zone_upper}
                    fill="#3b82f6" fillOpacity={0.08}
                    stroke="#3b82f6" strokeOpacity={0.2} strokeDasharray="4 4"
                    strokeWidth={1}
                  />
                )}

                {/* BTC price area */}
                <Area
                  type="monotone"
                  dataKey="close"
                  stroke="#22c55e"
                  strokeWidth={2}
                  fill="url(#priceGradient)"
                  dot={<TradeDot />}
                  activeDot={{ r: 4, fill: '#22c55e', stroke: 'white', strokeWidth: 2 }}
                  connectNulls
                />

                {/* Active strike */}
                {o?.active_strike != null && (
                  <ReferenceLine
                    y={o.active_strike}
                    stroke="#f97316"
                    strokeDasharray="6 3"
                    strokeWidth={1.5}
                    label={{ value: `Strike ${fmtPrice(o.active_strike)}`, position: 'insideTopRight', fill: '#f97316', fontSize: 10 }}
                  />
                )}

                {/* Breakeven */}
                {o?.breakeven != null && (
                  <ReferenceLine
                    y={o.breakeven}
                    stroke="#22c55e"
                    strokeDasharray="4 4"
                    strokeWidth={1.5}
                    label={{ value: `BE ${fmtPrice(o.breakeven)}`, position: 'insideBottomRight', fill: '#22c55e', fontSize: 10 }}
                  />
                )}

                {/* Expiry vertical line */}
                {o?.expiry_ts != null && (
                  <ReferenceLine
                    x={o.expiry_ts}
                    stroke="#94a3b8"
                    strokeDasharray="4 4"
                    strokeWidth={1}
                    label={{ value: 'Expiry', position: 'top', fill: '#94a3b8', fontSize: 10 }}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Open position card */}
        {chartData && <PositionCard data={chartData} />}

        {/* Config info strip */}
        {chartData?.config && (
          <div className="bg-card rounded-2xl border border-border p-4">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
              {selectedBot ? `${selectedBot.name} — Config` : 'Config'}
            </p>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              {[
                ['IV Threshold', chartData.config.iv_rank_threshold != null ? `${(chartData.config.iv_rank_threshold * 100).toFixed(0)}%` : '—'],
                ['Delta Range', `${chartData.config.target_delta_min}–${chartData.config.target_delta_max}`],
                ['DTE Range', `${chartData.config.min_dte}–${chartData.config.max_dte}d`],
                ['OTM Offset', `${(chartData.config.otm_offset * 100).toFixed(1)}%`],
                ['Max Eq / Leg', `${(chartData.config.max_equity_per_leg * 100).toFixed(0)}%`],
                ['Premium Min', `${(chartData.config.premium_fraction * 100).toFixed(2)}%`],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between">
                  <span className="text-slate-500">{label}</span>
                  <span className="text-slate-200 font-medium">{value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* No trades message */}
        {chartData && tradeCount === 0 && (
          <div className="text-center py-4 text-xs text-slate-500">
            No trades recorded in this period
          </div>
        )}

      </div>
    </div>
  )
}
