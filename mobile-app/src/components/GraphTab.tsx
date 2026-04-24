import { useState, useEffect, useRef, useCallback } from 'react'
import {
  createChart,
  ColorType,
  LineStyle,
  CrosshairMode,
  CandlestickSeries,
  IChartApi,
} from 'lightweight-charts'
import { getChartData, ChartData, ChartOverlays } from '../api'

type Period = '7d' | '30d' | '90d'
const PERIOD_DAYS: Record<Period, number> = { '7d': 7, '30d': 30, '90d': 90 }

function fmt$(n: number | null | undefined) {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

// ── Info modal ─────────────────────────────────────────────────────────────────
function InfoModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-end" onClick={onClose}>
      <div
        className="w-full bg-slate-900 border-t border-slate-700 rounded-t-2xl p-5 max-h-[80vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-white">How to read this chart</h2>
          <button onClick={onClose} className="text-slate-400 text-2xl leading-none">×</button>
        </div>

        <Section title="📊 Candlesticks">
          Each candle = one time period (7d view = 4h candles, 30d/90d = daily).
          <br /><br />
          <strong className="text-green-400">Green candle</strong> — BTC closed higher than it opened.{' '}
          <strong className="text-red-400">Red candle</strong> — BTC closed lower. The thin wicks above and below show the highest and lowest prices reached during that period.
        </Section>

        <Section title="🟠 Strike line (solid orange)">
          The exact strike price of your active put option. BTC must stay{' '}
          <strong className="text-white">above</strong> this level at expiry for the option to expire worthless (your ideal outcome — you keep the full premium).
        </Section>

        <Section title="🟡 Breakeven line (amber dashed)">
          Strike price <em>minus</em> the premium collected per BTC. If BTC lands between the breakeven and strike at expiry, you still profit (premium covers the intrinsic loss). Below the breakeven = net loss.
        </Section>

        <Section title="🟢 Target zone (green dashed band)">
          The price range where the bot is configured to place puts right now, based on your OTM Offset setting. The upper dashed line is 50% of offset below spot, the lower is 150% below — the band shows the "landing corridor" for strike selection.
        </Section>

        <Section title="🔵 Trade markers">
          <strong className="text-blue-400">▲ Blue arrow (below bar)</strong> — a trade was opened at this candle.{' '}
          <strong className="text-green-400">▼ Green arrow</strong> — trade closed profitably.{' '}
          <strong className="text-red-400">▼ Red arrow</strong> — trade closed at a loss. The label shows the P&L in USD.
        </Section>

        <Section title="⏱ Time periods">
          <strong>7d</strong> — last 7 days, 6-hour candles (28 candles). Good for watching intraday moves around your strike.
          <br /><strong>30d</strong> — last 30 days, daily candles. Best view for a typical options cycle.
          <br /><strong>90d</strong> — 3 months, daily candles. Useful for context and trend.
        </Section>

        <Section title="🔄 Live updating">
          The chart refreshes every 30 seconds. The current (incomplete) candle is updated with the live BTC index price so you always see the real-time position relative to your strike.
        </Section>

        <Section title="📋 Config cards (below chart)">
          Each card reflects one setting from your active config — the same parameters the bot uses when selecting strikes and sizing positions. They update whenever you change config in Settings.
        </Section>

        <button
          onClick={onClose}
          className="mt-4 w-full py-3 rounded-xl bg-blue-600 text-white text-sm font-medium"
        >
          Got it
        </button>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <p className="text-sm font-medium text-white mb-1">{title}</p>
      <p className="text-xs text-slate-400 leading-relaxed">{children}</p>
    </div>
  )
}

// ── Config card ────────────────────────────────────────────────────────────────
function ConfigCard({
  label, value, sub, color,
}: { label: string; value: string; sub?: string; color: string }) {
  const colors: Record<string, string> = {
    green:  'text-green-400',
    blue:   'text-blue-400',
    purple: 'text-purple-400',
    amber:  'text-amber-400',
    orange: 'text-orange-400',
    teal:   'text-teal-400',
  }
  return (
    <div className="rounded-xl bg-slate-800/60 border border-slate-700/40 px-3 py-2.5">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-sm font-semibold ${colors[color] ?? 'text-white'}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function GraphTab() {
  const [period, setPeriod]       = useState<Period>('30d')
  const [chartData, setChartData] = useState<ChartData | null>(null)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState('')
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  const [showInfo, setShowInfo]   = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef    = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const linesRef     = useRef<any[]>([])

  // ── Fetch ────────────────────────────────────────────────────────────────────
  const fetchData = useCallback(async (p: Period) => {
    try {
      const data = await getChartData(PERIOD_DAYS[p])
      setChartData(data)
      setLastUpdate(new Date())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load chart')
    } finally {
      setLoading(false)
    }
  }, [])

  // Fetch on period change
  useEffect(() => {
    setLoading(true)
    setChartData(null)
    fetchData(period)
  }, [period, fetchData])

  // Live update every 30s
  useEffect(() => {
    const id = setInterval(() => fetchData(period), 30_000)
    return () => clearInterval(id)
  }, [period, fetchData])

  // ── Initialise chart ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0f1a' },
        textColor: '#64748b',
      },
      grid: {
        vertLines: { color: '#1a2234' },
        horzLines: { color: '#1a2234' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: '#1a2234',
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: '#1a2234',
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: false,
        fixRightEdge: false,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: true,
        mouseWheel: true,
        pinch: true,
      },
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor:        '#22c55e',
      downColor:      '#ef4444',
      borderVisible:  false,
      wickUpColor:    '#22c55e',
      wickDownColor:  '#ef4444',
    })

    chartRef.current = chart
    seriesRef.current = series

    // Auto-resize
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  // ── Update overlays when data changes ────────────────────────────────────────
  useEffect(() => {
    if (!chartData || !seriesRef.current || !chartRef.current) return
    const series = seriesRef.current
    const chart  = chartRef.current

    // Set candle data
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    series.setData(chartData.candles as any[])

    // Clear old price lines
    linesRef.current.forEach(pl => { try { series.removePriceLine(pl) } catch (_) {} })
    linesRef.current = []

    const addLine = (
      price: number | null,
      color: string,
      style: LineStyle,
      title: string,
      labelVisible = true,
      width: 1 | 2 = 1,
    ) => {
      if (price == null) return
      const pl = series.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: labelVisible, title })
      linesRef.current.push(pl)
    }

    const { overlays } = chartData

    // Target zone (dashed green lines top + bottom)
    addLine(overlays.zone_upper,  'rgba(34,197,94,0.5)',  LineStyle.Dashed, 'Target ▲', false)
    addLine(overlays.zone_lower,  'rgba(34,197,94,0.5)',  LineStyle.Dashed, 'Target ▼', false)

    // Active strike (solid orange — most important line)
    addLine(overlays.active_strike, '#f97316', LineStyle.Solid, `Strike  ${fmt$(overlays.active_strike)}`, true, 2)

    // Breakeven (amber dashed)
    addLine(overlays.breakeven, '#eab308', LineStyle.Dashed, `B/E  ${fmt$(overlays.breakeven)}`, true)

    // Trade markers
    applyMarkers(series, chartData, overlays)

    chart.timeScale().fitContent()
  }, [chartData])

  const cfg   = chartData?.config
  const ov    = chartData?.overlays
  const price = chartData?.current_price

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      {showInfo && <InfoModal onClose={() => setShowInfo(false)} />}

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="px-4 pt-4 pb-2 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-white">Strategy Chart</h1>
            <button
              onClick={() => setShowInfo(true)}
              className="w-6 h-6 rounded-full bg-slate-700 text-slate-300 text-xs flex items-center justify-center hover:bg-slate-600"
              title="How to read this chart"
            >
              ?
            </button>
          </div>
          {lastUpdate && (
            <p className="text-xs text-slate-600 mt-0.5">
              {price != null && <span className="text-slate-400 mr-2">{fmt$(price)}</span>}
              Updated {lastUpdate.toLocaleTimeString()}
            </p>
          )}
        </div>

        {/* Period toggle */}
        <div className="flex gap-1 bg-slate-800 rounded-lg p-1">
          {(['7d', '30d', '90d'] as Period[]).map(p => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                period === p ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* ── Chart area ──────────────────────────────────────────────────────── */}
      <div
        className="mx-3 rounded-xl overflow-hidden border border-slate-800"
        style={{ height: 300 }}
      >
        {loading && (
          <div className="h-full flex items-center justify-center bg-[#0a0f1a]">
            <span className="text-slate-500 text-sm animate-pulse">Loading chart…</span>
          </div>
        )}
        {!loading && error && (
          <div className="h-full flex items-center justify-center bg-[#0a0f1a] px-6 text-center">
            <span className="text-red-400 text-sm">{error}</span>
          </div>
        )}
        <div
          ref={containerRef}
          className="w-full h-full"
          style={{ display: loading || error ? 'none' : 'block' }}
        />
      </div>

      {/* ── Legend ──────────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-4 mt-2 mb-1">
        {[
          { color: 'bg-orange-400',                    label: 'Strike' },
          { color: 'border-b border-dashed border-yellow-400 w-5', label: 'Breakeven', dashed: true },
          { color: 'border-b border-dashed border-green-500 w-5',  label: 'Target zone', dashed: true },
          { color: 'text-blue-400 font-bold',           label: 'Entry', icon: '▲' },
          { color: 'text-green-400 font-bold',          label: 'Win',   icon: '●' },
          { color: 'text-red-400 font-bold',            label: 'Loss',  icon: '●' },
        ].map(({ color, label, dashed, icon }) => (
          <span key={label} className="flex items-center gap-1.5 text-xs text-slate-500">
            {icon
              ? <span className={color}>{icon}</span>
              : dashed
              ? <span className={`inline-block ${color}`} />
              : <span className={`inline-block w-4 h-0.5 ${color}`} />}
            {label}
          </span>
        ))}
      </div>

      {/* ── Config cards ────────────────────────────────────────────────────── */}
      <div className="px-3 pb-4 overflow-y-auto flex-1 mt-1">
        <div className="grid grid-cols-2 gap-2">
          <ConfigCard
            label="OTM Offset"
            value={cfg?.otm_offset != null ? `${(cfg.otm_offset * 100).toFixed(1)}% below spot` : '—'}
            sub={price && cfg?.otm_offset != null ? `Strike target ≈ ${fmt$(price * (1 - cfg.otm_offset))}` : undefined}
            color="green"
          />
          <ConfigCard
            label="Delta Target"
            value={cfg ? `Δ ${cfg.target_delta_min} – ${cfg.target_delta_max}` : '—'}
            sub="option delta range"
            color="blue"
          />
          <ConfigCard
            label="DTE Window"
            value={cfg ? `${cfg.min_dte} – ${cfg.max_dte} days` : '—'}
            sub="days to expiry at entry"
            color="purple"
          />
          <ConfigCard
            label="Leg Size"
            value={cfg?.max_equity_per_leg != null ? `${(cfg.max_equity_per_leg * 100).toFixed(0)}% of equity` : '—'}
            sub="per position"
            color="amber"
          />
          <ConfigCard
            label="IV Rank Min"
            value={cfg?.iv_rank_threshold != null ? `${cfg.iv_rank_threshold}%` : '—'}
            sub="minimum to enter"
            color="orange"
          />
          <ConfigCard
            label="Premium Target"
            value={cfg?.premium_fraction != null ? `${(cfg.premium_fraction * 100).toFixed(1)}% of spot` : '—'}
            sub={price && cfg?.premium_fraction != null
              ? `≈ ${fmt$(price * cfg.premium_fraction)} / BTC`
              : undefined}
            color="teal"
          />
        </div>

        {/* Active position row */}
        {ov?.active_strike && (
          <div className="mt-2 rounded-xl bg-orange-950/30 border border-orange-900/30 px-4 py-3">
            <p className="text-xs text-orange-400 font-medium mb-1.5">Active position</p>
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
              <span className="text-slate-300">
                Strike <span className="text-orange-300 font-semibold">{fmt$(ov.active_strike)}</span>
              </span>
              {ov.breakeven != null && (
                <span className="text-slate-300">
                  Breakeven <span className="text-yellow-300 font-semibold">{fmt$(ov.breakeven)}</span>
                </span>
              )}
              {price != null && ov.active_strike != null && (
                <span className="text-slate-300">
                  Buffer <span className={`font-semibold ${price > ov.active_strike ? 'text-green-400' : 'text-red-400'}`}>
                    {fmt$(price - ov.active_strike)}
                  </span>
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Trade marker helper ────────────────────────────────────────────────────────
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function applyMarkers(series: any, chartData: ChartData, overlays: ChartOverlays) {
  if (!chartData.trade_markers?.length && !overlays.expiry_ts) return

  // Build a set of valid candle timestamps for snapping
  const candleTimes = new Set(chartData.candles.map(c => c.time))

  const snap = (ts: number): number | null => {
    if (candleTimes.has(ts)) return ts
    // Find closest candle at or before ts
    const sorted = chartData.candles.map(c => c.time).filter(t => t <= ts)
    return sorted.length ? sorted[sorted.length - 1] : null
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markers: any[] = []

  for (const t of chartData.trade_markers) {
    const entryT = snap(t.entry_time)
    if (entryT != null) {
      markers.push({
        time: entryT,
        position: 'belowBar',
        color: '#60a5fa',
        shape: 'arrowUp',
        text: t.strike ? `$${t.strike.toLocaleString()}` : 'Entry',
      })
    }
    if (t.exit_time != null) {
      const exitT = snap(t.exit_time)
      if (exitT != null) {
        markers.push({
          time: exitT,
          position: 'aboveBar',
          color: t.won ? '#22c55e' : '#ef4444',
          shape: 'arrowDown',
          text: t.pnl_usd != null ? (t.pnl_usd >= 0 ? `+$${Math.round(t.pnl_usd)}` : `-$${Math.round(Math.abs(t.pnl_usd))}`) : 'Exit',
        })
      }
    }
  }

  // Expiry marker
  if (overlays.expiry_ts) {
    const expT = snap(overlays.expiry_ts)
    if (expT != null) {
      markers.push({
        time: expT,
        position: 'aboveBar',
        color: '#f97316',
        shape: 'circle',
        text: 'Expiry',
      })
    }
  }

  // Sort by time (required by lightweight-charts)
  markers.sort((a, b) => (a.time as number) - (b.time as number))
  if (markers.length) series.setMarkers(markers)
}
