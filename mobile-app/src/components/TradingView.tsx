import { useState, useEffect, useCallback } from 'react'
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ReferenceArea,
} from 'recharts'
import {
  getChartData, ChartData, getFarmStatus, FarmStatus, BotFarmEntry,
} from '../api'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ChartPoint {
  time: number
  close?: number
  projected?: number
  isFuture?: boolean
  tradeWon?: boolean
  tradePnl?: number
  tradeStrike?: number
}

type InfoType = 'trade' | 'strike' | 'breakeven' | 'zone' | 'expiry' | 'projection'
interface InfoPanel { type: InfoType; payload?: ChartPoint }

// ── Helpers ───────────────────────────────────────────────────────────────────

const K = (n: number) => n >= 1000 ? `$${(n / 1000).toFixed(1)}k` : `$${n.toFixed(0)}`

function fmtDate(ts: number) {
  return new Date(ts * 1000).toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtShort(ts: number, res: string) {
  const d = new Date(ts * 1000)
  return res === '360'
    ? d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric' })
    : d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric' })
}

// ── Trade dot — triangles pointing up (win) or down (loss) ───────────────────

function TradeDot(props: any) {
  const { cx, cy, payload } = props
  if (payload?.tradeWon === undefined || payload?.isFuture) return null
  const won = payload.tradeWon as boolean
  const pnl = Math.abs(payload.tradePnl ?? 30)
  const sz  = Math.min(13, Math.max(8, pnl / 12))
  const fill = won ? '#22c55e' : '#ef4444'
  // up-triangle for win, down-triangle for loss
  const pts = won
    ? `${cx},${cy - sz} ${cx - sz * 0.9},${cy + sz * 0.55} ${cx + sz * 0.9},${cy + sz * 0.55}`
    : `${cx},${cy + sz} ${cx - sz * 0.9},${cy - sz * 0.55} ${cx + sz * 0.9},${cy - sz * 0.55}`
  return (
    <g style={{ cursor: 'pointer' }}>
      <circle cx={cx} cy={cy} r={sz * 1.8} fill={fill} opacity={0.12} />
      <polygon points={pts} fill={fill} stroke="white" strokeWidth={1.5} strokeLinejoin="round" />
    </g>
  )
}

// ── Hover tooltip ─────────────────────────────────────────────────────────────

function ChartTooltip({ active, payload, resolution }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload as ChartPoint
  if (!d) return null
  const price = d.isFuture ? d.projected : d.close
  if (!price) return null
  return (
    <div className="bg-slate-900 border border-slate-600 rounded-xl px-3 py-2 text-xs shadow-xl pointer-events-none">
      <p className="text-slate-400 mb-1">{fmtShort(d.time, resolution)}{d.isFuture ? ' (projected)' : ''}</p>
      <p className="text-white font-bold text-sm">{K(price)}</p>
      {d.tradeWon !== undefined && !d.isFuture && (
        <>
          <div className="border-t border-slate-700 mt-1.5 pt-1.5">
            <p className={`font-semibold ${d.tradeWon ? 'text-green-400' : 'text-red-400'}`}>
              {d.tradeWon ? '▲ WIN' : '▼ LOSS'} {d.tradePnl != null ? `$${d.tradePnl.toFixed(0)}` : ''}
            </p>
            {d.tradeStrike != null && <p className="text-slate-400 mt-0.5">Strike {K(d.tradeStrike)}</p>}
            <p className="text-slate-500 mt-0.5 text-xs">Tap for details</p>
          </div>
        </>
      )}
    </div>
  )
}

// ── Info panel content ────────────────────────────────────────────────────────

function InfoPanelContent({
  panel, overlays, config, currentPrice, onDismiss,
}: {
  panel: InfoPanel
  overlays: ChartData['overlays']
  config: ChartData['config']
  currentPrice: number | null
  onDismiss: () => void
}) {
  const o = overlays
  const cfg = config

  let icon = '💡'
  let title = ''
  let body: string[] = []
  let color = 'text-slate-300'

  switch (panel.type) {
    case 'trade': {
      const p = panel.payload!
      const won = p.tradeWon
      icon = won ? '▲' : '▼'
      color = won ? 'text-green-400' : 'text-red-400'
      title = won ? 'Winning Trade' : 'Losing Trade'
      body = [
        `The bot sold a cash-secured PUT option${p.tradeStrike ? ` at a strike of ${K(p.tradeStrike)}` : ''}.`,
        won
          ? `Bitcoin stayed above the strike at expiry. The option expired worthless — the full premium of ${p.tradePnl != null ? `$${p.tradePnl.toFixed(0)}` : 'an unknown amount'} was kept as profit.`
          : `Bitcoin dropped below the strike at expiry. The bot was assigned — forced to buy BTC at the strike price. The premium collected partially offset the loss. Net result: ${p.tradePnl != null ? `$${p.tradePnl.toFixed(0)}` : 'unknown'}.`,
        `Trade exit: ${fmtDate(p.time)}.`,
      ]
      break
    }
    case 'strike': {
      const strike = o.active_strike!
      const buf = currentPrice ? ((currentPrice - strike) / currentPrice * 100).toFixed(1) : null
      icon = '🎯'
      color = 'text-orange-400'
      title = `Active Strike — ${K(strike)}`
      body = [
        `The bot sold a PUT option at this strike price. BTC is currently ${buf ? `${buf}% above` : 'above'} this level — that's the safety buffer.`,
        `If BTC falls below ${K(strike)} at expiry${o.expiry_ts ? ` (${fmtDate(o.expiry_ts)})` : ''}, the option is exercised and the bot is forced to buy BTC at ${K(strike)} regardless of the market price.`,
        `The premium collected upfront is compensation for taking on this risk.`,
      ]
      break
    }
    case 'breakeven': {
      const be = o.breakeven!
      const strike = o.active_strike
      const premPerBtc = strike && be ? (strike - be).toFixed(0) : null
      icon = '📈'
      color = 'text-green-400'
      title = `Breakeven — ${K(be)}`
      body = [
        `The breakeven is the strike (${strike ? K(strike) : '?'}) minus the premium collected${premPerBtc ? ` ($${premPerBtc}/BTC)` : ''}.`,
        `Above ${K(be)}: the trade is profitable — premium more than covers any paper loss.`,
        `Below ${K(be)}: losses exceed the premium collected. The deeper BTC falls, the worse the loss.`,
        `The gap between the strike and breakeven is the "cushion" — how far BTC can fall before the trade genuinely costs money.`,
      ]
      break
    }
    case 'zone': {
      const upper = o.zone_upper
      const lower = o.zone_lower
      const center = o.zone_center
      const otm = cfg ? `${(cfg.otm_offset * 100).toFixed(1)}%` : null
      icon = '🔵'
      color = 'text-blue-400'
      title = 'Entry Zone'
      body = [
        `The blue band (${lower ? K(lower) : '?'} – ${upper ? K(upper) : '?'}) is where the bot would sell its next PUT right now, based on the ${otm ?? 'configured'} out-of-the-money offset.`,
        `Center of zone: ${center ? K(center) : '?'}.`,
        `The bot enters this zone when IV Rank exceeds ${cfg ? `${(cfg.iv_rank_threshold * 100).toFixed(0)}%` : 'the threshold'} — indicating options are expensive enough to sell profitably.`,
        `If BTC is in or above this zone at entry, the put has a good chance of expiring worthless.`,
      ]
      break
    }
    case 'expiry': {
      const expTs = o.expiry_ts!
      const strike = o.active_strike
      icon = '📅'
      color = 'text-slate-300'
      title = `Expiry — ${fmtDate(expTs)}`
      body = [
        `The active PUT option expires on this date.`,
        `If BTC is above ${strike ? K(strike) : 'the strike'} on this day: the option expires worthless. The bot keeps the full premium and starts a new cycle.`,
        `If BTC is below the strike: the option is exercised. The bot absorbs the loss (partially offset by premium) and may start a new cycle immediately or wait for better conditions.`,
      ]
      break
    }
    case 'projection': {
      icon = '〰️'
      color = 'text-slate-400'
      title = 'Trend Projection'
      body = [
        `The dashed line is a simple linear extrapolation of the last 7 days of price movement.`,
        `This is NOT a price prediction — just a visual guide to show the current momentum direction.`,
        `The bot does not use this projection for any decisions. It trades based on IV rank, delta, and DTE — not price forecasts.`,
      ]
      break
    }
  }

  return (
    <div className="bg-card rounded-2xl border border-slate-700 p-4 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className={`text-lg ${color}`}>{icon}</span>
          <span className={`text-sm font-bold ${color}`}>{title}</span>
        </div>
        <button onClick={onDismiss} className="text-slate-500 hover:text-slate-300 text-lg leading-none mt-0.5">×</button>
      </div>
      <div className="space-y-1.5">
        {body.map((line, i) => (
          <p key={i} className="text-xs text-slate-300 leading-relaxed">{line}</p>
        ))}
      </div>
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
  const [infoPanel, setInfoPanel]   = useState<InfoPanel | null>(null)

  useEffect(() => {
    getFarmStatus().then(fs => {
      setFarmStatus(fs)
      if (!botId && fs.bots.length > 0) setBotId(fs.bots[0].id)
    }).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setChartData(await getChartData(days, botId)) }
    catch (e) { setError(String(e)) }
    finally { setLoading(false) }
  }, [days, botId])

  useEffect(() => { load() }, [load])
  useEffect(() => { const id = setInterval(load, 30_000); return () => clearInterval(id) }, [load])

  // ── Build chart data ────────────────────────────────────────────────────────

  const allChartData: ChartPoint[] = (() => {
    if (!chartData) return []
    const { candles, trade_markers } = chartData
    const resSec = chartData.resolution === '360' ? 21600 : 86400

    // Historical candles with trade markers merged in
    const hist: ChartPoint[] = candles.map(c => {
      const marker = trade_markers.find(m => {
        const mt = m.exit_time ?? m.entry_time
        return mt >= c.time - resSec / 2 && mt < c.time + resSec / 2
      })
      return {
        time: c.time, close: c.close,
        projected: undefined, isFuture: false,
        tradeWon:   marker?.won,
        tradePnl:   marker?.pnl_usd,
        tradeStrike: marker?.strike ?? undefined,
      }
    })

    // Linear trend projection from last 7 candles
    const recent = hist.slice(-7)
    const n = recent.length
    if (n >= 3) {
      const xs = recent.map((_, i) => i)
      const ys = recent.map(c => c.close!)
      const xm = xs.reduce((a, b) => a + b) / n
      const ym = ys.reduce((a, b) => a + b) / n
      const slope = xs.reduce((s, x, i) => s + (x - xm) * (ys[i] - ym), 0) /
                    xs.reduce((s, x) => s + (x - xm) ** 2, 0)
      const lastClose = hist[hist.length - 1].close!
      const lastTime  = hist[hist.length - 1].time

      // Attach projection start to the last real candle
      hist[hist.length - 1].projected = lastClose

      // Future candles
      for (let i = 1; i <= 5; i++) {
        hist.push({
          time: lastTime + i * resSec,
          projected: Math.max(1, lastClose + slope * i),
          isFuture: true,
        })
      }
    }

    return hist
  })()

  // ── Y axis domain ───────────────────────────────────────────────────────────

  const histPoints = allChartData.filter(p => !p.isFuture)
  const nowTs = chartData && histPoints.length > 0 ? histPoints[histPoints.length - 1].time : 0

  const yDomain = (() => {
    if (!allChartData.length) return ['auto', 'auto'] as [string, string]
    const o = chartData?.overlays
    const vals = [
      ...allChartData.map(c => c.close ?? c.projected ?? 0).filter(v => v > 0),
      o?.active_strike, o?.breakeven, o?.zone_upper, o?.zone_lower,
    ].filter((v): v is number => v != null && v > 0)
    const mn = Math.min(...vals), mx = Math.max(...vals)
    const pad = (mx - mn) * 0.06
    return [Math.floor(mn - pad), Math.ceil(mx + pad)] as [number, number]
  })()

  const xDomain = allChartData.length
    ? [allChartData[0].time, allChartData[allChartData.length - 1].time] as [number, number]
    : (['dataMin', 'dataMax'] as any)

  // ── Chart click → info panel ────────────────────────────────────────────────

  function handleChartClick(data: any) {
    if (!data?.activePayload?.[0]) { setInfoPanel(null); return }
    const p = data.activePayload[0].payload as ChartPoint
    if (p.isFuture) { setInfoPanel({ type: 'projection' }); return }
    if (p.tradeWon !== undefined) { setInfoPanel({ type: 'trade', payload: p }); return }
    setInfoPanel(null)
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  const bots: BotFarmEntry[] = farmStatus?.bots ?? []
  const selectedBot = bots.find(b => b.id === botId)
  const o = chartData?.overlays
  const tradeCount = chartData?.trade_markers.length ?? 0
  const wins   = chartData?.trade_markers.filter(m => m.won).length ?? 0
  const losses = tradeCount - wins
  const res    = chartData?.resolution ?? '1D'

  return (
    <div className="flex flex-col min-h-screen bg-navy pb-24">

      {/* ── Sticky header ──────────────────────────────────────────────────── */}
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

        {bots.length > 0 && (
          <div className="relative mb-2">
            <select
              value={botId ?? ''}
              onChange={e => { setBotId(e.target.value || undefined); setInfoPanel(null) }}
              className="w-full appearance-none bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 pr-8 text-sm text-white focus:outline-none focus:border-green-600"
            >
              {bots.map(b => (
                <option key={b.id} value={b.id}>
                  {b.status === 'running' ? '🟢' : '🟡'} {b.name}{b.config_name ? ` — ${b.config_name}` : ''}
                </option>
              ))}
            </select>
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs">▼</span>
          </div>
        )}

        <div className="flex gap-2">
          {([7, 30, 90] as const).map(d => (
            <button key={d} onClick={() => { setDays(d); setInfoPanel(null) }}
              className={`flex-1 text-xs py-1.5 rounded-lg font-medium transition-colors ${
                days === d ? 'bg-green-800 text-green-200' : 'bg-slate-800 text-slate-400 hover:text-slate-200'
              }`}>{d}d</button>
          ))}
        </div>
      </div>

      <div className="px-4 pt-4 space-y-4">

        {/* ── Trade summary pills ─────────────────────────────────────────── */}
        {tradeCount > 0 && (
          <div className="flex gap-2 flex-wrap">
            <div className="flex items-center gap-1.5 bg-slate-800 rounded-full px-3 py-1.5 text-xs">
              <span className="text-green-400 font-bold">▲</span>
              <span className="text-slate-300">{wins} win{wins !== 1 ? 's' : ''}</span>
            </div>
            <div className="flex items-center gap-1.5 bg-slate-800 rounded-full px-3 py-1.5 text-xs">
              <span className="text-red-400 font-bold">▼</span>
              <span className="text-slate-300">{losses} loss{losses !== 1 ? 'es' : ''}</span>
            </div>
            <div className="flex items-center gap-1.5 bg-slate-800 rounded-full px-3 py-1.5 text-xs">
              <span className="text-slate-400">Win rate</span>
              <span className={`font-semibold ml-1 ${
                wins / tradeCount >= 0.6 ? 'text-green-400' : wins / tradeCount >= 0.5 ? 'text-amber-400' : 'text-red-400'
              }`}>{(wins / tradeCount * 100).toFixed(0)}%</span>
            </div>
          </div>
        )}

        {/* ── Loading / error ─────────────────────────────────────────────── */}
        {loading && !chartData && (
          <div className="h-64 flex items-center justify-center">
            <div className="flex gap-1">{[0,1,2].map(i => (
              <div key={i} className="w-2 h-2 rounded-full bg-green-500 animate-bounce"
                   style={{ animationDelay: `${i * 0.15}s` }} />
            ))}</div>
          </div>
        )}
        {error && <div className="h-32 flex items-center justify-center text-sm text-red-400">{error}</div>}

        {/* ── Chart ──────────────────────────────────────────────────────── */}
        {chartData && allChartData.length > 0 && (
          <div className="bg-card rounded-2xl border border-border overflow-hidden">

            {/* Legend + tap hint */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-3 pb-2 text-xs text-slate-400">
              <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-0.5 bg-green-500"/>BTC Price</span>
              {o?.active_strike   && <span className="flex items-center gap-1.5"><span className="inline-block w-3 border-t-2 border-dashed border-orange-400"/>Strike</span>}
              {o?.breakeven       && <span className="flex items-center gap-1.5"><span className="inline-block w-3 border-t-2 border-dashed border-emerald-400"/>Breakeven</span>}
              {o?.zone_lower      && <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-2.5 rounded-sm bg-blue-500 opacity-40"/>Entry Zone</span>}
              {tradeCount > 0     && <span className="flex items-center gap-1.5"><span className="text-green-400 font-bold text-xs">▲</span><span className="text-red-400 font-bold text-xs">▼</span>Trades</span>}
              <span className="flex items-center gap-1.5 ml-auto"><span className="inline-block w-3 border-t border-dashed border-slate-500"/>Projection</span>
            </div>
            <p className="text-xs text-slate-600 px-3 pb-2">Tap a trade marker or use the chips below for details</p>

            <ResponsiveContainer width="100%" height={310}>
              <ComposedChart
                data={allChartData}
                margin={{ top: 4, right: 12, bottom: 4, left: 0 }}
                onClick={handleChartClick}
                style={{ cursor: 'crosshair' }}
              >
                <defs>
                  <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#22c55e" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#22c55e" stopOpacity={0.02}/>
                  </linearGradient>
                  <linearGradient id="projGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#64748b" stopOpacity={0.15}/>
                    <stop offset="95%" stopColor="#64748b" stopOpacity={0}/>
                  </linearGradient>
                </defs>

                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false}/>

                <XAxis
                  dataKey="time" type="number" scale="time"
                  domain={xDomain}
                  tickFormatter={ts => fmtShort(ts, res)}
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  tickLine={false} axisLine={false} tickCount={5}
                />
                <YAxis
                  domain={yDomain}
                  tickFormatter={v => K(v)}
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  tickLine={false} axisLine={false} width={54} tickCount={5}
                />

                <Tooltip content={<ChartTooltip resolution={res}/>}/>

                {/* Entry zone */}
                {o?.zone_lower != null && o?.zone_upper != null && (
                  <ReferenceArea y1={o.zone_lower} y2={o.zone_upper}
                    fill="#3b82f6" fillOpacity={0.12}
                    stroke="#3b82f6" strokeOpacity={0.35} strokeDasharray="5 4" strokeWidth={1}
                  />
                )}

                {/* BTC price area */}
                <Area type="monotone" dataKey="close"
                  stroke="#22c55e" strokeWidth={2.5}
                  fill="url(#priceGrad)"
                  dot={<TradeDot/>}
                  activeDot={{ r: 5, fill: '#22c55e', stroke: 'white', strokeWidth: 2 }}
                  connectNulls={false}
                />

                {/* Projection dashed line */}
                <Line type="monotone" dataKey="projected"
                  stroke="#64748b" strokeWidth={1.5} strokeDasharray="5 4"
                  dot={false} activeDot={false} connectNulls
                />

                {/* NOW vertical line */}
                {nowTs > 0 && (
                  <ReferenceLine x={nowTs} stroke="#475569" strokeWidth={1.5}
                    label={{ value: 'Now', position: 'insideTopRight', fill: '#94a3b8', fontSize: 10 }}
                  />
                )}

                {/* Strike */}
                {o?.active_strike != null && (
                  <ReferenceLine y={o.active_strike} stroke="#f97316" strokeDasharray="7 4" strokeWidth={2}
                    label={{ value: `Strike ${K(o.active_strike)}`, position: 'insideTopLeft', fill: '#f97316', fontSize: 10 }}
                  />
                )}

                {/* Breakeven */}
                {o?.breakeven != null && (
                  <ReferenceLine y={o.breakeven} stroke="#34d399" strokeDasharray="5 4" strokeWidth={2}
                    label={{ value: `BE ${K(o.breakeven)}`, position: 'insideBottomLeft', fill: '#34d399', fontSize: 10 }}
                  />
                )}

                {/* Expiry vertical */}
                {o?.expiry_ts != null && o.expiry_ts > nowTs && (
                  <ReferenceLine x={o.expiry_ts} stroke="#a78bfa" strokeDasharray="4 4" strokeWidth={1.5}
                    label={{ value: 'Expiry', position: 'insideTopRight', fill: '#a78bfa', fontSize: 10 }}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>

            {/* Clickable key-level chips */}
            {(o?.active_strike || o?.breakeven || o?.zone_lower || o?.expiry_ts) && (
              <div className="flex flex-wrap gap-2 px-3 py-3 border-t border-slate-800">
                {o?.active_strike && (
                  <button onClick={() => setInfoPanel(p => p?.type === 'strike' ? null : { type: 'strike' })}
                    className={`text-xs px-2.5 py-1.5 rounded-full border font-medium transition-colors ${
                      infoPanel?.type === 'strike' ? 'bg-orange-900/60 border-orange-600 text-orange-300' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-orange-300'
                    }`}>
                    🎯 Strike {K(o.active_strike)}
                  </button>
                )}
                {o?.breakeven && (
                  <button onClick={() => setInfoPanel(p => p?.type === 'breakeven' ? null : { type: 'breakeven' })}
                    className={`text-xs px-2.5 py-1.5 rounded-full border font-medium transition-colors ${
                      infoPanel?.type === 'breakeven' ? 'bg-emerald-900/60 border-emerald-600 text-emerald-300' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-emerald-300'
                    }`}>
                    📈 Breakeven {K(o.breakeven)}
                  </button>
                )}
                {o?.zone_lower && (
                  <button onClick={() => setInfoPanel(p => p?.type === 'zone' ? null : { type: 'zone' })}
                    className={`text-xs px-2.5 py-1.5 rounded-full border font-medium transition-colors ${
                      infoPanel?.type === 'zone' ? 'bg-blue-900/60 border-blue-600 text-blue-300' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-blue-300'
                    }`}>
                    🔵 Entry Zone
                  </button>
                )}
                {o?.expiry_ts && (
                  <button onClick={() => setInfoPanel(p => p?.type === 'expiry' ? null : { type: 'expiry' })}
                    className={`text-xs px-2.5 py-1.5 rounded-full border font-medium transition-colors ${
                      infoPanel?.type === 'expiry' ? 'bg-violet-900/60 border-violet-600 text-violet-300' : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-violet-300'
                    }`}>
                    📅 Expiry {fmtDate(o.expiry_ts).replace(',', '')}
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Info panel ──────────────────────────────────────────────────── */}
        {infoPanel && chartData && (
          <InfoPanelContent
            panel={infoPanel}
            overlays={chartData.overlays}
            config={chartData.config}
            currentPrice={chartData.current_price}
            onDismiss={() => setInfoPanel(null)}
          />
        )}

        {/* ── Open position card ──────────────────────────────────────────── */}
        {chartData && o?.active_strike && (
          <div className="bg-card rounded-2xl border border-amber-800/60 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-bold text-white">Open Position</span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-amber-900/60 text-amber-300 border border-amber-700/60">PUT active</span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="bg-navy rounded-xl px-3 py-2 text-center">
                <p className="text-xs text-slate-500">Strike</p>
                <p className="text-sm font-semibold text-orange-400 mt-0.5">{K(o.active_strike)}</p>
                {chartData.current_price && <p className="text-xs text-slate-500">↑ {((chartData.current_price - o.active_strike) / chartData.current_price * 100).toFixed(1)}%</p>}
              </div>
              {o.breakeven && (
                <div className="bg-navy rounded-xl px-3 py-2 text-center">
                  <p className="text-xs text-slate-500">Breakeven</p>
                  <p className="text-sm font-semibold text-emerald-400 mt-0.5">{K(o.breakeven)}</p>
                  {chartData.current_price && <p className="text-xs text-slate-500">↑ {((chartData.current_price - o.breakeven) / chartData.current_price * 100).toFixed(1)}%</p>}
                </div>
              )}
              {o.zone_center && (
                <div className="bg-navy rounded-xl px-3 py-2 text-center">
                  <p className="text-xs text-slate-500">Next Entry</p>
                  <p className="text-sm font-semibold text-blue-400 mt-0.5">{K(o.zone_center)}</p>
                  <p className="text-xs text-slate-500">zone center</p>
                </div>
              )}
            </div>
            {o.expiry_ts && <p className="text-xs text-slate-400">Expires {fmtDate(o.expiry_ts)}</p>}
          </div>
        )}

        {/* ── Config strip ────────────────────────────────────────────────── */}
        {chartData?.config && (
          <div className="bg-card rounded-2xl border border-border p-4">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
              {selectedBot?.name ?? 'Bot'} — Active Config
            </p>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              {([
                ['IV Threshold',  `${(chartData.config.iv_rank_threshold * 100).toFixed(0)}%`],
                ['Delta Range',   `${chartData.config.target_delta_min}–${chartData.config.target_delta_max}`],
                ['DTE Range',     `${chartData.config.min_dte}–${chartData.config.max_dte}d`],
                ['OTM Offset',    `${(chartData.config.otm_offset * 100).toFixed(1)}%`],
                ['Max Eq / Leg',  `${(chartData.config.max_equity_per_leg * 100).toFixed(0)}%`],
                ['Premium Min',   `${(chartData.config.premium_fraction * 100).toFixed(2)}%`],
              ] as [string, string][]).map(([label, value]) => (
                <div key={label} className="flex justify-between">
                  <span className="text-slate-500">{label}</span>
                  <span className="text-slate-200 font-medium">{value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {chartData && tradeCount === 0 && (
          <p className="text-center py-4 text-xs text-slate-500">No trades recorded in this period</p>
        )}
      </div>
    </div>
  )
}
