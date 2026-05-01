import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ReferenceArea,
} from 'recharts'
import {
  getChartData, ChartData, getFarmStatus, FarmStatus, BotFarmEntry,
  getBotLiveState, BotLiveState,
  getStatus, StatusData,
  getHedge, HedgeData,
  closeFarmBotPosition, closePosition,
} from '../api'
import { applyBotOrder } from '../lib/botOrder'

// Heartbeat is considered stale if older than this — matches api.py's
// 3-minute dead-bot detection threshold in /status.
const HEARTBEAT_STALE_SEC = 180

// Risk-level → colour mapping for the position chip and danger banner.
const RISK_STYLE: Record<'ok' | 'caution' | 'danger', { dot: string; bg: string; text: string; border: string; label: string }> = {
  ok:      { dot: 'bg-green-500',  bg: 'bg-green-900/40',  text: 'text-green-300',  border: 'border-green-700/60',  label: 'OK' },
  caution: { dot: 'bg-amber-500',  bg: 'bg-amber-900/40',  text: 'text-amber-300',  border: 'border-amber-700/60',  label: 'Caution' },
  danger:  { dot: 'bg-red-500',    bg: 'bg-red-900/50',    text: 'text-red-300',    border: 'border-red-700/60',    label: 'Danger' },
}

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

type InfoType = 'trade' | 'strike' | 'breakeven' | 'zone' | 'expiry' | 'projection' | 'est_strike' | 'est_breakeven'
interface InfoPanel { type: InfoType; payload?: ChartPoint }

// ── Helpers ───────────────────────────────────────────────────────────────────

const K = (n: number) => n >= 1000 ? `$${(n / 1000).toFixed(1)}k` : `$${n.toFixed(0)}`

function fmtUsd(n: number | null | undefined, signed = false): string {
  if (n == null || !isFinite(n)) return '—'
  const sign = signed && n > 0 ? '+' : ''
  const abs = Math.abs(n)
  if (abs >= 1000) return `${sign}${n < 0 ? '-' : ''}$${(abs / 1000).toFixed(2)}k`
  return `${sign}$${n.toFixed(2)}`
}

function fmtPct(n: number | null | undefined, signed = true): string {
  if (n == null || !isFinite(n)) return '—'
  const sign = signed && n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function fmtBtc(n: number | null | undefined, signed = false): string {
  if (n == null || !isFinite(n)) return '—'
  const sign = signed && n > 0 ? '+' : ''
  return `${sign}${n.toFixed(4)} BTC`
}

function fmtAge(s: number | null | undefined): string {
  if (s == null || !isFinite(s)) return '—'
  if (s < 60)   return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`
}

function daysHeld(entryDate?: string): number | null {
  if (!entryDate) return null
  const t = Date.parse(entryDate)
  if (isNaN(t)) return null
  return Math.max(0, Math.floor((Date.now() - t) / 86_400_000))
}

function fmtDate(ts: number) {
  return new Date(ts * 1000).toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

function fmtShort(ts: number, res: string) {
  const d = new Date(ts * 1000)
  return res === '360'
    ? d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric' })
    : d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric' })
}

// Live countdown to expiry
function fmtCountdown(expiryTs: number, refNow: number): string {
  const msLeft = expiryTs * 1000 - refNow
  if (msLeft <= 0) return 'Expired'
  const totalMins = Math.floor(msLeft / 60_000)
  const d = Math.floor(totalMins / 1440)
  const h = Math.floor((totalMins % 1440) / 60)
  const m = totalMins % 60
  if (d >= 2) return `${d}d ${h}h left`
  if (d === 1) return `1d ${h}h left`
  if (h > 0) return `${h}h ${m}m left`
  return `${m}m left`
}

function dteFromTs(expiryTs: number, refNow: number): number {
  return Math.max(0, Math.ceil((expiryTs * 1000 - refNow) / 86_400_000))
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
      const expTs  = o.expiry_ts!
      const strike = o.active_strike
      const be     = o.breakeven
      const dLeft  = dteFromTs(expTs, Date.now())
      const cd     = fmtCountdown(expTs, Date.now())
      const bufPct = strike && currentPrice
        ? ((currentPrice - strike) / currentPrice * 100).toFixed(1) : null
      const beAbovePct = be && currentPrice
        ? ((currentPrice - be) / currentPrice * 100).toFixed(1) : null

      icon  = '📅'
      color = dLeft <= 1 ? 'text-red-400' : (dLeft <= 3 ? 'text-amber-400' : 'text-violet-300')
      title = `Expiry — ${fmtDate(expTs)}  (${cd})`

      body = [
        `This PUT option expires in ${dLeft > 0 ? `${dLeft} day${dLeft !== 1 ? 's' : ''}` : 'less than 24 hours'}. At expiry, one of two things happens:`,
        `✅ WIN: BTC is above ${strike ? K(strike) : 'the strike'} — the option expires worthless. The bot keeps every dollar of premium collected as pure profit, then starts a new cycle.`,
        `❌ LOSS: BTC is below ${strike ? K(strike) : 'the strike'} — the option is exercised ("assigned"). The bot is forced to buy BTC at ${strike ? K(strike) : 'the strike'} regardless of the market price. The premium collected upfront partially offsets this loss.`,
        be
          ? `📈 Break-even is ${K(be)}${beAbovePct ? ` — BTC is currently ${beAbovePct}% above that level` : ''}. Above ${K(be)}, the trade is profitable. Below it, losses exceed the premium collected.`
          : '',
        bufPct
          ? `🛡️ Safety buffer: BTC is currently ${bufPct}% above the strike. It would need to fall ${bufPct}% just to put the trade at risk.`
          : '',
        `No action needed from you — the bot monitors this position automatically and will roll or close it if risk thresholds are hit.`,
      ].filter(Boolean)
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
    case 'est_strike': {
      const estStrike = o.zone_center ?? o.zone_lower
      const otm = cfg ? `${(cfg.otm_offset * 100).toFixed(1)}%` : null
      icon = '🎯'
      color = 'text-amber-400'
      title = `Est. Next Strike — ${estStrike ? K(estStrike) : '?'}`
      body = [
        `This is where the bot would likely sell its next PUT — the center of the current entry zone, based on the ${otm ?? 'configured'} out-of-the-money offset applied to the current BTC price.`,
        `The bot does NOT have an open position yet. This line is estimated — the actual strike chosen at entry may differ depending on available strikes on Deribit.`,
        `The bot enters when IV Rank rises above ${cfg ? `${(cfg.iv_rank_threshold * 100).toFixed(0)}%` : 'the threshold'}. Right now it is monitoring and waiting for that signal.`,
      ]
      break
    }
    case 'est_breakeven': {
      const estStrike = o.zone_center ?? o.zone_lower
      const pf = cfg?.premium_fraction
      const estBE = estStrike && pf ? estStrike * (1 - pf) : null
      icon = '📈'
      color = 'text-teal-400'
      title = `Est. Breakeven — ${estBE ? K(estBE) : '?'}`
      body = [
        `Estimated breakeven if the bot enters near the current zone center (${estStrike ? K(estStrike) : '?'}).`,
        `Calculated as: strike × (1 − premium fraction). The premium fraction (${pf ? (pf * 100).toFixed(2) + '%' : '?'}) is the minimum premium the bot requires as a % of the strike.`,
        `If BTC stays above this level at expiry, the trade is profitable. Below this, losses exceed the premium collected.`,
        `This is an estimate only — the actual breakeven depends on the premium received at trade entry.`,
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

  // Live bot state for the Open Position card (farm-bot context)
  const [liveState, setLiveState]   = useState<BotLiveState | null>(null)
  // Status of the main bot (used when no farm bot is selected)
  const [botStatus, setBotStatus]   = useState<StatusData | null>(null)
  // Hedge (BTC-PERP) state from main bot
  const [hedgeData, setHedgeData]   = useState<HedgeData | null>(null)

  // Emergency-close confirm + result message
  const [closeConfirm, setCloseConfirm] = useState<boolean>(false)
  const [closing, setClosing]           = useState(false)
  const [closeMsg, setCloseMsg]         = useState<string>('')
  const closeMsgTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    getFarmStatus().then(fs => {
      setFarmStatus(fs)
      if (!botId && fs.bots.length > 0) {
        // Apply custom farm order first, then prefer any bot with an open position,
        // otherwise default to the first in the user's preferred order (not server order)
        const ordered = applyBotOrder(fs.bots)
        const withPos = ordered.find(b => b.has_open_position)
        setBotId((withPos ?? ordered[0]).id)
      }
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

  // ── Live state (bot health + position numbers) ─────────────────────────────
  // Refresh in parallel with the chart, but on a faster cadence (15s) so the
  // health banner and P&L numbers don't lag behind real position changes.
  const loadLive = useCallback(async () => {
    if (botId) {
      try { setLiveState(await getBotLiveState(botId)) }
      catch { setLiveState(null) }
      // Farm bot — refresh farm status too so position_risk stays current
      try { setFarmStatus(await getFarmStatus()) }
      catch { /* leave previous farmStatus */ }
    } else {
      try { setBotStatus(await getStatus()) }
      catch { setBotStatus(null) }
    }
    try { setHedgeData(await getHedge()) }
    catch { setHedgeData(null) }
  }, [botId])

  // Clear bot-specific state when the user switches bots so we never show
  // the previous bot's numbers paired with the new bot's chart.
  useEffect(() => { setLiveState(null); setCloseMsg('') }, [botId])
  useEffect(() => { loadLive() }, [loadLive])
  useEffect(() => { const id = setInterval(loadLive, 15_000); return () => clearInterval(id) }, [loadLive])

  // ── Emergency close ────────────────────────────────────────────────────────
  async function executeClose() {
    setCloseConfirm(false)
    setClosing(true)
    setCloseMsg('Sending close command…')
    try {
      if (botId) await closeFarmBotPosition(botId)
      else       await closePosition()
      setCloseMsg('✅ Close command sent — bot will execute on next cycle')
      // Refresh live state and chart after a beat so the user sees the change
      setTimeout(() => { loadLive(); load() }, 2000)
    } catch (e) {
      setCloseMsg(`❌ ${String(e)}`)
    } finally {
      setClosing(false)
      if (closeMsgTimer.current) clearTimeout(closeMsgTimer.current)
      closeMsgTimer.current = setTimeout(() => setCloseMsg(''), 6000)
    }
  }
  useEffect(() => () => { if (closeMsgTimer.current) clearTimeout(closeMsgTimer.current) }, [])

  // Live countdown — ticks every minute so the countdown stays fresh
  const [countdownNow, setCountdownNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setCountdownNow(Date.now()), 60_000)
    return () => clearInterval(id)
  }, [])

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

      // Extend into the future far enough to always show the expiry line.
      // For the 7-day view (6h candles), the default 30% lookahead is only 18 hours —
      // too short if expiry is several days out. Use 60% for short windows.
      const lookaheadCandles = days <= 7
        ? Math.max(8, Math.round(days * 0.60))   // 7d: at least 2 days of projection
        : Math.max(3, Math.round(days * 0.30))
      const expiryTs = chartData.overlays?.expiry_ts
      const minTsNeeded = expiryTs ? expiryTs + resSec * 2 : 0   // two candles past expiry
      let i = 1
      while (i <= lookaheadCandles || lastTime + i * resSec < minTsNeeded) {
        hist.push({
          time: lastTime + i * resSec,
          projected: Math.max(1, lastClose + slope * i),
          isFuture: true,
        })
        i++
        if (i > 365) break  // safety cap
      }
    }

    return hist
  })()

  // ── Monitoring / estimated overlays (when no open position) ────────────────

  const isMonitoring = !!chartData && !chartData.overlays?.active_strike
  const estStrike    = isMonitoring ? (chartData?.overlays?.zone_center ?? chartData?.overlays?.zone_lower ?? null) : null
  const estBreakeven = estStrike && chartData?.config?.premium_fraction != null
    ? estStrike * (1 - chartData.config.premium_fraction)
    : null

  // ── Y axis domain ───────────────────────────────────────────────────────────

  const histPoints = allChartData.filter(p => !p.isFuture)
  const nowTs = chartData && histPoints.length > 0 ? histPoints[histPoints.length - 1].time : 0

  const yDomain = (() => {
    if (!allChartData.length) return ['auto', 'auto'] as [string, string]
    const o = chartData?.overlays
    const vals = [
      ...allChartData.map(c => c.close ?? c.projected ?? 0).filter(v => v > 0),
      o?.active_strike, o?.breakeven, o?.zone_upper, o?.zone_lower,
      estStrike, estBreakeven,
    ].filter((v): v is number => v != null && v > 0)
    const mn = Math.min(...vals), mx = Math.max(...vals)
    const pad = (mx - mn) * 0.06
    return [Math.floor(mn - pad), Math.ceil(mx + pad)] as [number, number]
  })()

  // xDomain: always extend right edge to include expiry + breathing room
  const xDomain: [number, number] | string[] = (() => {
    if (!allChartData.length) return ['dataMin', 'dataMax']
    const minX = allChartData[0].time
    let maxX = allChartData[allChartData.length - 1].time
    const expiryTs = chartData?.overlays?.expiry_ts
    if (expiryTs && expiryTs > nowTs && nowTs > 0) {
      // Right-pad by 12% of the historical window so the expiry label has room
      const histSpan = Math.max(1, nowTs - minX)
      maxX = Math.max(maxX, expiryTs + histSpan * 0.12)
    }
    return [minX, maxX]
  })()

  // ── Chart click → info panel ────────────────────────────────────────────────

  function handleChartClick(data: any) {
    if (!data?.activePayload?.[0]) { setInfoPanel(null); return }
    const p = data.activePayload[0].payload as ChartPoint
    if (p.isFuture) { setInfoPanel({ type: 'projection' }); return }
    if (p.tradeWon !== undefined) { setInfoPanel({ type: 'trade', payload: p }); return }
    setInfoPanel(null)
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  const bots: BotFarmEntry[] = useMemo(
    () => applyBotOrder(farmStatus?.bots ?? []),
    [farmStatus?.bots]
  )
  const selectedBot = bots.find(b => b.id === botId)
  const o = chartData?.overlays
  const tradeCount = chartData?.trade_markers.length ?? 0
  const wins   = chartData?.trade_markers.filter(m => m.won).length ?? 0
  const losses = tradeCount - wins
  const res    = chartData?.resolution ?? '1D'

  // ── Live position values (prefer BotLiveState, fall back to chart overlays) ──
  const livePos       = liveState?.position
  const hasLivePos    = !!livePos?.open
  const contracts     = livePos?.contracts ?? null
  const premUsd       = livePos?.premium_collected ?? null
  const unrlPnlUsd    = livePos?.unrealized_pnl_usd ?? null
  const unrlPnlPct    = livePos?.unrealized_pnl_pct ?? null
  const entryPxBtc    = livePos?.entry_price ?? null
  const currentPxBtc  = livePos?.current_price ?? null
  const livePosDelta  = livePos?.current_delta ?? livePos?.delta ?? null
  const ivRankLive    = liveState?.state?.iv_rank ?? null
  const ivRankThresh  = chartData?.config?.iv_rank_threshold ?? null
  const maxAdvDelta   = chartData?.config?.max_adverse_delta ?? 0.40
  const positionRisk  = (selectedBot?.position_risk ?? 'ok') as 'ok' | 'caution' | 'danger'
  const heldDays      = daysHeld(livePos?.entry_date)

  // ── Health flags ───────────────────────────────────────────────────────────
  // Source of truth depends on context: farm bot uses BotLiveState, otherwise
  // the main bot's /status endpoint.
  const killSwitchActive = !!liveState?.kill_switch_active
  const heartbeatAge     = liveState?.heartbeat_age_seconds ?? null
  const heartbeatStale   = botId
    ? (heartbeatAge != null && heartbeatAge > HEARTBEAT_STALE_SEC)
    : (botStatus != null && !botStatus.bot_running)
  const botStopped = botId
    ? (selectedBot?.status === 'stopped' || selectedBot?.status === 'error')
    : (botStatus != null && !botStatus.bot_running)
  const showHealthBanner = killSwitchActive || heartbeatStale || botStopped

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
                  {b.has_open_position ? '📋' : b.status === 'running' ? '🟢' : '🟡'} {b.name}{b.config_name ? ` — ${b.config_name}` : ''}{b.has_open_position && b.open_position?.strike ? ` (PUT $${(b.open_position.strike/1000).toFixed(0)}k)` : ''}
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

        {/* ── Health banner ─────────────────────────────────────────────────
            Surfaces conditions that make the chart misleading: kill switch
            engaged, bot stopped, or heartbeat older than HEARTBEAT_STALE_SEC.
            Without this the chart can look live while the bot is dead. */}
        {showHealthBanner && (
          <div className={`rounded-2xl border px-4 py-3 flex items-start gap-3 ${
            killSwitchActive || botStopped
              ? 'bg-red-950 border-red-700'
              : 'bg-amber-950/60 border-amber-700/60'
          }`}>
            <span className="text-xl flex-shrink-0 mt-0.5">
              {killSwitchActive ? '🛑' : botStopped ? '⏹️' : '⚠️'}
            </span>
            <div className="flex-1 min-w-0">
              <p className={`font-semibold text-sm ${
                killSwitchActive || botStopped ? 'text-red-300' : 'text-amber-300'
              }`}>
                {killSwitchActive
                  ? 'Kill switch active — trading halted'
                  : botStopped
                    ? `Bot is ${selectedBot?.status ?? 'stopped'} — no live updates`
                    : 'Stale heartbeat — bot may be unresponsive'}
              </p>
              <p className={`text-xs mt-0.5 ${
                killSwitchActive || botStopped ? 'text-red-400' : 'text-amber-400/90'
              }`}>
                {killSwitchActive && 'Delete the KILL_SWITCH file (or use the Farm tab) to resume.'}
                {!killSwitchActive && botStopped && 'Restart the bot from the Farm tab to resume.'}
                {!killSwitchActive && !botStopped && heartbeatStale && (
                  <>Last heartbeat {fmtAge(heartbeatAge)} — chart values shown may not reflect current state.</>
                )}
              </p>
            </div>
          </div>
        )}

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
        {error && !chartData && (
          <div className="h-32 flex flex-col items-center justify-center gap-3">
            <div className="flex gap-1">{[0,1,2].map(i => (
              <div key={i} className="w-2 h-2 rounded-full bg-yellow-500 animate-bounce"
                   style={{ animationDelay: `${i * 0.2}s` }} />
            ))}</div>
            <span className="text-xs text-muted-foreground">Reconnecting…</span>
          </div>
        )}
        {error && chartData && (
          <div className="flex items-center justify-between px-4 py-1 bg-yellow-500/10 border-b border-yellow-500/20">
            <span className="text-xs text-yellow-400">⚡ Updating…</span>
          </div>
        )}

        {/* ── Chart ──────────────────────────────────────────────────────── */}
        {chartData && allChartData.length > 0 && (
          <div className="bg-card rounded-2xl border border-border overflow-hidden">

            {/* Legend + tap hint */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-3 pb-2 text-xs text-slate-400">
              <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-0.5 bg-green-500"/>BTC Price</span>
              {o?.active_strike   && <span className="flex items-center gap-1.5"><span className="inline-block w-3 border-t-2 border-dashed border-orange-400"/>Strike</span>}
              {o?.breakeven       && <span className="flex items-center gap-1.5"><span className="inline-block w-3 border-t-2 border-dashed border-emerald-400"/>Breakeven</span>}
              {estStrike != null  && <span className="flex items-center gap-1.5"><span className="inline-block w-3 border-t-2 border-dashed border-amber-500 opacity-60"/>Est. Strike</span>}
              {estBreakeven != null && <span className="flex items-center gap-1.5"><span className="inline-block w-3 border-t-2 border-dashed border-teal-400 opacity-60"/>Est. BE</span>}
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

                {/* Est. Strike (monitoring mode) */}
                {estStrike != null && (
                  <ReferenceLine y={estStrike} stroke="#f59e0b" strokeDasharray="4 6" strokeWidth={1.5} strokeOpacity={0.6}
                    label={{ value: `Est. Strike ${K(estStrike)}`, position: 'insideTopLeft', fill: '#f59e0b', fontSize: 9, opacity: 0.75 }}
                  />
                )}

                {/* Est. Breakeven (monitoring mode) */}
                {estBreakeven != null && (
                  <ReferenceLine y={estBreakeven} stroke="#2dd4bf" strokeDasharray="3 7" strokeWidth={1.5} strokeOpacity={0.55}
                    label={{ value: `Est. BE ${K(estBreakeven)}`, position: 'insideBottomLeft', fill: '#2dd4bf', fontSize: 9, opacity: 0.7 }}
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
            {(o?.active_strike || o?.breakeven || o?.zone_lower || o?.expiry_ts || estStrike || estBreakeven) && (
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
                {/* Estimated chips — shown in monitoring mode (no active position) */}
                {estStrike != null && (
                  <button onClick={() => setInfoPanel(p => p?.type === 'est_strike' ? null : { type: 'est_strike' })}
                    className={`text-xs px-2.5 py-1.5 rounded-full border font-medium transition-colors opacity-75 ${
                      infoPanel?.type === 'est_strike' ? 'bg-amber-900/60 border-amber-600 text-amber-300' : 'bg-slate-800 border-slate-700 border-dashed text-slate-400 hover:text-amber-300'
                    }`}>
                    🎯 Est. Strike {K(estStrike)}
                  </button>
                )}
                {estBreakeven != null && (
                  <button onClick={() => setInfoPanel(p => p?.type === 'est_breakeven' ? null : { type: 'est_breakeven' })}
                    className={`text-xs px-2.5 py-1.5 rounded-full border font-medium transition-colors opacity-75 ${
                      infoPanel?.type === 'est_breakeven' ? 'bg-teal-900/60 border-teal-600 text-teal-300' : 'bg-slate-800 border-slate-700 border-dashed text-slate-400 hover:text-teal-300'
                    }`}>
                    📈 Est. BE {K(estBreakeven)}
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
                {o?.expiry_ts && (() => {
                  const cd   = fmtCountdown(o.expiry_ts, countdownNow)
                  const dte  = dteFromTs(o.expiry_ts, countdownNow)
                  const isUrgent = dte <= 3
                  return (
                    <button onClick={() => setInfoPanel(p => p?.type === 'expiry' ? null : { type: 'expiry' })}
                      className={`text-xs px-2.5 py-1.5 rounded-full border font-medium transition-colors ${
                        infoPanel?.type === 'expiry'
                          ? 'bg-violet-900/60 border-violet-600 text-violet-300'
                          : isUrgent
                            ? 'bg-red-900/40 border-red-700/60 text-red-300 animate-pulse'
                            : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-violet-300'
                      }`}>
                      📅 {cd}
                    </button>
                  )
                })()}
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

        {/* ── Open position card ────────────────────────────────────────────
            Strike / breakeven / next-entry come from chart overlays. The
            P&L row, contracts, premium, delta, IV, and days-held come from
            BotLiveState — these stay '—' when liveState hasn't loaded yet
            so the user knows the chart-side data is still authoritative. */}
        {chartData && o?.active_strike && (
          <div className="bg-card rounded-2xl border border-amber-800/60 p-4 space-y-3">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-sm font-bold text-white">Open Position</span>
              <div className="flex items-center gap-2">
                {hasLivePos && (
                  <span className={`text-xs px-2 py-0.5 rounded-full border flex items-center gap-1.5 ${RISK_STYLE[positionRisk].bg} ${RISK_STYLE[positionRisk].text} ${RISK_STYLE[positionRisk].border}`}>
                    <span className={`inline-block w-1.5 h-1.5 rounded-full ${RISK_STYLE[positionRisk].dot}`}/>
                    {RISK_STYLE[positionRisk].label}
                  </span>
                )}
                <span className="text-xs px-2 py-0.5 rounded-full bg-amber-900/60 text-amber-300 border border-amber-700/60">
                  {(livePos?.type ?? 'put').replace('short_', '').toUpperCase()} active
                </span>
              </div>
            </div>

            {/* Top row: strike / breakeven / next-entry */}
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

            {/* P&L hero row */}
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-navy rounded-xl px-3 py-2.5">
                <p className="text-xs text-slate-500">Unrealised P&amp;L</p>
                <p className={`text-base font-bold mt-0.5 ${
                  unrlPnlUsd == null ? 'text-slate-400'
                    : unrlPnlUsd >= 0 ? 'text-green-400' : 'text-red-400'
                }`}>{fmtUsd(unrlPnlUsd, true)}</p>
                <p className={`text-xs ${
                  unrlPnlPct == null ? 'text-slate-500'
                    : unrlPnlPct >= 0 ? 'text-green-500/80' : 'text-red-500/80'
                }`}>{fmtPct(unrlPnlPct)} of equity</p>
              </div>
              <div className="bg-navy rounded-xl px-3 py-2.5">
                <p className="text-xs text-slate-500">Premium Collected</p>
                <p className="text-base font-bold text-amber-300 mt-0.5">{fmtUsd(premUsd)}</p>
                <p className="text-xs text-slate-500">
                  {contracts != null ? `${contracts} contract${contracts !== 1 ? 's' : ''}` : '—'}
                </p>
              </div>
            </div>

            {/* Detail grid: entry vs current, delta, IV, days held */}
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 px-1 pt-1 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-500">Entry premium</span>
                <span className="text-slate-200 font-medium">{entryPxBtc != null ? `${entryPxBtc.toFixed(4)} BTC` : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Current mark</span>
                <span className="text-slate-200 font-medium">{currentPxBtc != null ? `${currentPxBtc.toFixed(4)} BTC` : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Delta</span>
                <span className={`font-medium ${
                  livePosDelta == null ? 'text-slate-200'
                    : Math.abs(livePosDelta) >= maxAdvDelta ? 'text-red-400'
                    : Math.abs(livePosDelta) >= maxAdvDelta * 0.75 ? 'text-amber-300'
                    : 'text-slate-200'
                }`}>
                  {livePosDelta != null ? Math.abs(livePosDelta).toFixed(3) : '—'}
                  <span className="text-slate-500 font-normal"> / {maxAdvDelta.toFixed(2)}</span>
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">IV rank</span>
                <span className="text-slate-200 font-medium">
                  {ivRankLive != null ? `${(ivRankLive * 100).toFixed(0)}%` : '—'}
                  {ivRankThresh != null && (
                    <span className="text-slate-500 font-normal"> / {(ivRankThresh * 100).toFixed(0)}%</span>
                  )}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Days held</span>
                <span className="text-slate-200 font-medium">
                  {heldDays != null ? `${heldDays}d` : '—'}
                  {livePos?.entry_date && (
                    <span className="text-slate-500 font-normal"> · since {livePos.entry_date}</span>
                  )}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Heartbeat</span>
                <span className={`font-medium ${heartbeatStale ? 'text-amber-400' : 'text-slate-200'}`}>
                  {heartbeatAge != null ? fmtAge(heartbeatAge) : '—'}
                </span>
              </div>
            </div>

            {o.expiry_ts && (() => {
              const cd  = fmtCountdown(o.expiry_ts, countdownNow)
              const dte = dteFromTs(o.expiry_ts, countdownNow)
              return (
                <>
                  <div className="flex items-center justify-between">
                    <p className="text-xs text-slate-400">Expires {fmtDate(o.expiry_ts)}</p>
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                      dte <= 1 ? 'bg-red-900/50 text-red-300' :
                      dte <= 3 ? 'bg-amber-900/50 text-amber-300' :
                                 'bg-slate-700 text-slate-300'
                    }`}>⏱ {cd}</span>
                  </div>
                  {dte <= 4 && (
                    <div className={`rounded-xl px-3 py-2.5 text-xs leading-relaxed ${
                      dte <= 1
                        ? 'bg-red-900/40 border border-red-700/60 text-red-200'
                        : 'bg-amber-900/40 border border-amber-700/60 text-amber-200'
                    }`}>
                      <p className="font-semibold mb-1">
                        {dte <= 1 ? '🚨 Expiring today or tomorrow' : `⚠️ ${dte} days to expiry`}
                      </p>
                      <p>
                        {o.active_strike
                          ? `Win: BTC stays above ${K(o.active_strike)} → full premium kept. Loss: BTC falls below ${K(o.active_strike)} → assignment (bot buys BTC at strike, premium offsets part of the loss).`
                          : 'Option is close to expiry. Tap the 📅 chip above for a full breakdown.'}
                        {' '}No action needed — the bot handles this automatically.
                      </p>
                    </div>
                  )}
                </>
              )
            })()}

            {/* Emergency close — last in the card so it doesn't pull focus */}
            <div className="pt-1">
              <button
                onClick={() => setCloseConfirm(true)}
                disabled={closing}
                className="w-full py-2.5 rounded-xl bg-red-900/70 hover:bg-red-800/80 disabled:opacity-50 disabled:cursor-not-allowed text-red-200 text-sm font-semibold border border-red-800/60 transition-colors"
              >
                🆘 Emergency Close Position
              </button>
              {closeMsg && (
                <p className={`text-xs mt-2 text-center ${
                  closeMsg.startsWith('❌') ? 'text-red-400'
                    : closeMsg.startsWith('✅') ? 'text-green-400'
                    : 'text-slate-400'
                }`}>{closeMsg}</p>
              )}
            </div>
          </div>
        )}

        {/* ── Hedge sub-card ────────────────────────────────────────────────
            Only visible when the wheel bot's delta hedge has a non-zero
            BTC-PERPETUAL position. Hidden when flat (clean state). */}
        {hedgeData?.enabled && hedgeData.perp_position_btc !== 0 && (
          <div className="bg-card rounded-2xl border border-purple-800/60 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">🛡️</span>
                <span className="text-sm font-bold text-white">Delta Hedge</span>
              </div>
              <span className="text-xs px-2 py-0.5 rounded-full bg-purple-900/60 text-purple-300 border border-purple-700/60">
                BTC-PERP
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-navy rounded-xl px-3 py-2.5">
                <p className="text-xs text-slate-500">Position</p>
                <p className={`text-base font-bold mt-0.5 ${
                  hedgeData.perp_position_btc >= 0 ? 'text-green-400' : 'text-red-400'
                }`}>{fmtBtc(hedgeData.perp_position_btc, true)}</p>
                <p className="text-xs text-slate-500">
                  {hedgeData.avg_entry_price > 0 ? `entry ${K(hedgeData.avg_entry_price)}` : '—'}
                </p>
              </div>
              <div className="bg-navy rounded-xl px-3 py-2.5">
                <p className="text-xs text-slate-500">Unrealised P&amp;L</p>
                <p className={`text-base font-bold mt-0.5 ${
                  hedgeData.unrealised_pnl_usd == null ? 'text-slate-400'
                    : hedgeData.unrealised_pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'
                }`}>{fmtUsd(hedgeData.unrealised_pnl_usd, true)}</p>
                <p className="text-xs text-slate-500">
                  realised {fmtUsd(hedgeData.realised_pnl_usd, true)}
                </p>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 px-1 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-500">Funding paid</span>
                <span className={`font-medium ${
                  hedgeData.funding_paid_usd > 0 ? 'text-red-400'
                    : hedgeData.funding_paid_usd < 0 ? 'text-green-400'
                    : 'text-slate-200'
                }`}>{fmtUsd(hedgeData.funding_paid_usd, true)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Rebalances</span>
                <span className="text-slate-200 font-medium">{hedgeData.rebalance_count}</span>
              </div>
            </div>
          </div>
        )}

        {/* ── Monitoring card — shown when no open position ────────────────── */}
        {isMonitoring && chartData && (
          <div className="bg-card rounded-2xl border border-slate-700 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="relative flex h-2.5 w-2.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-60"/>
                  <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-amber-500"/>
                </span>
                <span className="text-sm font-bold text-white">Monitoring — No Open Position</span>
              </div>
              <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700">Waiting</span>
            </div>
            <p className="text-xs text-slate-400 leading-relaxed">
              The bot is watching the market but has not yet entered a trade.
              It will sell a PUT option when IV Rank rises above{' '}
              <span className="text-white font-medium">
                {chartData.config ? `${(chartData.config.iv_rank_threshold * 100).toFixed(0)}%` : 'the threshold'}
              </span>
              {' '}— indicating options are expensive enough to collect meaningful premium.
            </p>
            {(estStrike || estBreakeven || o?.zone_lower) && (
              <div className="grid grid-cols-3 gap-2">
                {estStrike != null && (
                  <div className="bg-navy rounded-xl px-3 py-2 text-center border border-dashed border-amber-800/50">
                    <p className="text-xs text-slate-500">Est. Strike</p>
                    <p className="text-sm font-semibold text-amber-400/80 mt-0.5">{K(estStrike)}</p>
                    {chartData.current_price && <p className="text-xs text-slate-500">↑ {((chartData.current_price - estStrike) / chartData.current_price * 100).toFixed(1)}%</p>}
                  </div>
                )}
                {estBreakeven != null && (
                  <div className="bg-navy rounded-xl px-3 py-2 text-center border border-dashed border-teal-800/50">
                    <p className="text-xs text-slate-500">Est. BE</p>
                    <p className="text-sm font-semibold text-teal-400/80 mt-0.5">{K(estBreakeven)}</p>
                    {chartData.current_price && <p className="text-xs text-slate-500">↑ {((chartData.current_price - estBreakeven) / chartData.current_price * 100).toFixed(1)}%</p>}
                  </div>
                )}
                {o?.zone_lower && o?.zone_upper && (
                  <div className="bg-navy rounded-xl px-3 py-2 text-center">
                    <p className="text-xs text-slate-500">Entry Zone</p>
                    <p className="text-xs font-semibold text-blue-400/80 mt-0.5">{K(o.zone_lower)}</p>
                    <p className="text-xs text-slate-500">↕ {K(o.zone_upper)}</p>
                  </div>
                )}
              </div>
            )}
            <p className="text-xs text-slate-600 italic">Values marked Est. are projections based on current config — actual levels depend on market conditions at entry.</p>
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

      {/* ── Emergency-close confirm modal ─────────────────────────────────── */}
      {closeConfirm && (() => {
        const strike = livePos?.strike ?? o?.active_strike ?? null
        const spot   = livePos?.current_spot ?? chartData?.current_price ?? null
        const optType = (livePos?.type ?? 'short_put').replace('short_', '')
        const isItm = strike != null && spot != null && (
          (optType === 'put' && spot < strike) ||
          (optType === 'call' && spot > strike)
        )
        return (
          <div
            className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-sm px-4 pb-8"
            onClick={() => setCloseConfirm(false)}
          >
            <div
              className="w-full max-w-sm bg-card border border-red-800 rounded-2xl p-5 space-y-4"
              onClick={e => e.stopPropagation()}
            >
              <div className="flex items-center gap-3">
                <span className="text-2xl">🆘</span>
                <div>
                  <p className="font-bold text-white text-base">Emergency Close Position?</p>
                  <p className="text-xs text-slate-400 mt-0.5">{selectedBot?.name ?? 'Main bot'}</p>
                </div>
              </div>

              <div className="bg-slate-800 rounded-xl px-3 py-2.5 space-y-1">
                {strike != null && (
                  <p className="text-xs text-slate-300">
                    <span className="text-slate-500">Position: </span>
                    Short {optType.toUpperCase()} @ {K(strike)}
                  </p>
                )}
                {spot != null && (
                  <p className="text-xs text-slate-300">
                    <span className="text-slate-500">BTC Spot: </span>{K(spot)}
                  </p>
                )}
                {livePosDelta != null && (
                  <p className="text-xs text-slate-300">
                    <span className="text-slate-500">Delta: </span>{Math.abs(livePosDelta).toFixed(3)}
                  </p>
                )}
                {unrlPnlUsd != null && (
                  <p className={`text-xs font-semibold ${unrlPnlUsd >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    Unrealised P&amp;L: {fmtUsd(unrlPnlUsd, true)}
                  </p>
                )}
                {isItm && <p className="text-xs text-red-400 font-semibold">⚠️ Option is currently in the money</p>}
              </div>

              <p className="text-slate-300 text-sm">
                This sends a buy-back command to the bot. It will execute a market order to close the short option on its next cycle (within seconds if running).
              </p>

              <div className="flex gap-3">
                <button
                  onClick={() => setCloseConfirm(false)}
                  className="flex-1 py-2.5 rounded-xl bg-slate-700 hover:bg-slate-600 text-white text-sm font-medium transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={executeClose}
                  className="flex-1 py-2.5 rounded-xl bg-red-700 hover:bg-red-600 text-white text-sm font-bold transition-colors"
                >
                  Close Position
                </button>
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
