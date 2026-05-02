import { useEffect, useState, useCallback } from 'react'
import {
  getForecastSnapshots,
  getForecastSnapshotDetail,
  ForecastSnapshotSummary,
} from '../api'

// Status badge colours mirror the dashboard's Forecasts tab so a user
// switching between mobile and desktop sees the same colour story.
const STATUS_META: Record<ForecastSnapshotSummary['status'], { icon: string; bg: string; border: string; text: string; label: string }> = {
  pending:  { icon: '🕐', bg: 'bg-slate-800/60',   border: 'border-slate-600',   text: 'text-slate-400', label: 'Pending'  },
  due:      { icon: '⏰', bg: 'bg-amber-900/40',  border: 'border-amber-600',  text: 'text-amber-300', label: 'Due'       },
  pass:     { icon: '🟢', bg: 'bg-green-900/30',  border: 'border-green-600',  text: 'text-green-300', label: 'Pass'      },
  warning:  { icon: '🟡', bg: 'bg-amber-900/40',  border: 'border-amber-600',  text: 'text-amber-300', label: 'Warning'   },
  fail:     { icon: '🔴', bg: 'bg-red-900/30',    border: 'border-red-600',    text: 'text-red-300',   label: 'Fail'      },
  unknown:  { icon: '⚫', bg: 'bg-slate-800/60',   border: 'border-slate-600',   text: 'text-slate-400', label: 'Unknown'   },
}

function fmtPct(n: number | null | undefined, dec = 2): string {
  if (n == null || isNaN(n)) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(dec)}%`
}

function fmtDate(iso: string): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
  } catch {
    return iso.slice(0, 19)
  }
}

function daysUntil(iso: string): number | null {
  if (!iso) return null
  try {
    const ms = new Date(iso).getTime() - Date.now()
    return Math.floor(ms / (1000 * 60 * 60 * 24))
  } catch {
    return null
  }
}

interface FindingDict {
  metric:   string
  severity: 'pass' | 'warning' | 'fail'
  expected: unknown
  actual:   unknown
  message:  string
}

function SnapshotCard({ s, onExpand, expanded, detail }: {
  s: ForecastSnapshotSummary
  onExpand: () => void
  expanded: boolean
  detail: Record<string, unknown> | null
}) {
  const meta = STATUS_META[s.status] ?? STATUS_META.unknown
  const days = daysUntil(s.validate_after)

  // Derive the "compare" line ergonomically based on status
  let compareLine = ''
  if (s.status === 'pending' && days != null) {
    compareLine = days >= 0 ? `validates in ~${days} day${days === 1 ? '' : 's'}` : 'overdue'
  } else if (s.status === 'due') {
    compareLine = 'horizon elapsed — run validate'
  } else if (s.actual_return != null && s.forecast_return != null) {
    compareLine = `forecast ${fmtPct(s.forecast_return)} → actual ${fmtPct(s.actual_return)}`
  }

  const findings: FindingDict[] = expanded && detail
    ? (((detail.validation as Record<string, unknown>)?.findings) as FindingDict[]) || []
    : []
  const badFindings = findings.filter(f => f.severity !== 'pass')

  return (
    <div className={`${meta.bg} border ${meta.border} rounded-xl p-3 mb-2`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-lg">{meta.icon}</span>
          <div className="min-w-0">
            <p className="text-sm font-bold text-white truncate">{s.snapshot_id}</p>
            <p className="text-xs text-slate-400 truncate">
              {s.bot ? <span className="text-blue-300">{s.bot}</span> : <span>main bot</span>}
              {' · '}{s.horizon_days}d horizon
              {s.note ? <span className="italic"> · {s.note.slice(0, 30)}{s.note.length > 30 ? '…' : ''}</span> : null}
            </p>
          </div>
        </div>
        <span className={`text-xs font-semibold uppercase tracking-wide ${meta.text}`}>{meta.label}</span>
      </div>

      <div className="mt-2 grid grid-cols-3 gap-2 text-center">
        <div className="bg-slate-900/60 rounded-lg py-1.5">
          <p className="text-[10px] text-slate-500 uppercase">Forecast Return</p>
          <p className="text-xs font-bold text-white">{fmtPct(s.forecast_return)}</p>
        </div>
        <div className="bg-slate-900/60 rounded-lg py-1.5">
          <p className="text-[10px] text-slate-500 uppercase">Actual Return</p>
          <p className={`text-xs font-bold ${
            s.actual_return == null ? 'text-slate-500'
              : s.actual_return >= 0 ? 'text-green-400' : 'text-red-400'
          }`}>{s.actual_return == null ? '—' : fmtPct(s.actual_return)}</p>
        </div>
        <div className="bg-slate-900/60 rounded-lg py-1.5">
          <p className="text-[10px] text-slate-500 uppercase">Trades (act/exp)</p>
          <p className="text-xs font-bold text-white">
            {s.actual_trades ?? '—'} / {s.forecast_trades?.toFixed(1) ?? '—'}
          </p>
        </div>
      </div>

      {compareLine ? (
        <p className="mt-2 text-[11px] text-slate-400 italic">{compareLine}</p>
      ) : null}

      <button
        type="button"
        className="mt-2 w-full text-[11px] text-slate-400 hover:text-white py-1"
        onClick={onExpand}
      >
        {expanded ? 'Hide details ▲' : 'Show details ▼'}
      </button>

      {expanded ? (
        <div className="mt-2 pt-2 border-t border-slate-700 space-y-2">
          <p className="text-[11px] text-slate-500">
            Created {fmtDate(s.created_at)} · Validate after {fmtDate(s.validate_after)}
          </p>
          {badFindings.length > 0 ? (
            <div className="space-y-1">
              <p className="text-[11px] uppercase text-slate-400 font-semibold">Findings</p>
              {badFindings.map((f, i) => {
                const sevColour =
                  f.severity === 'fail'    ? 'border-red-600 text-red-300'    :
                  f.severity === 'warning' ? 'border-amber-600 text-amber-300' :
                                              'border-slate-600 text-slate-400'
                return (
                  <div key={i} className={`text-[11px] border-l-2 ${sevColour} pl-2 leading-tight`}>
                    <span className="font-semibold uppercase">{f.metric}</span>{' '}
                    <span className="opacity-75">[{f.severity}]</span>
                    <p className="text-slate-300 mt-0.5">{f.message}</p>
                  </div>
                )
              })}
            </div>
          ) : detail ? (
            <p className="text-[11px] text-slate-500 italic">No findings — all metrics inside forecast envelope.</p>
          ) : (
            <p className="text-[11px] text-slate-500 italic">Loading…</p>
          )}
        </div>
      ) : null}
    </div>
  )
}

export default function Forecasts() {
  const [snapshots, setSnapshots] = useState<ForecastSnapshotSummary[]>([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [expanded, setExpanded]   = useState<string | null>(null)
  const [details, setDetails]     = useState<Record<string, Record<string, unknown>>>({})

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getForecastSnapshots()
      setSnapshots(data.snapshots || [])
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleExpand = useCallback(async (s: ForecastSnapshotSummary) => {
    const key = `${s.bot ?? 'main'}/${s.snapshot_id}`
    if (expanded === key) {
      setExpanded(null)
      return
    }
    setExpanded(key)
    if (!details[key]) {
      try {
        const detail = await getForecastSnapshotDetail(s.snapshot_id, s.bot ?? null)
        setDetails(prev => ({ ...prev, [key]: detail }))
      } catch (e) {
        setError(`Couldn't load detail: ${e}`)
      }
    }
  }, [expanded, details])

  // Aggregate counts for the header strip
  const counts = snapshots.reduce<Record<string, number>>((acc, s) => {
    acc[s.status] = (acc[s.status] || 0) + 1
    return acc
  }, {})

  return (
    <div className="min-h-screen bg-navy text-white pb-20">
      <header className="sticky top-0 z-10 bg-navy border-b border-slate-800 px-4 py-3 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold">📊 Forecasts</h1>
          <p className="text-[11px] text-slate-400">Backtest predictions vs actual outcomes</p>
        </div>
        <button
          onClick={refresh}
          className="text-xs bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded-lg"
        >
          🔄
        </button>
      </header>

      <div className="px-4 py-3">
        {/* Status counts */}
        <div className="flex gap-2 mb-3 flex-wrap text-[11px]">
          {(['pass', 'warning', 'fail', 'due', 'pending'] as const).map(s => {
            const meta = STATUS_META[s]
            const n = counts[s] || 0
            if (n === 0) return null
            return (
              <span key={s} className={`${meta.bg} ${meta.text} border ${meta.border} rounded-full px-2 py-0.5`}>
                {meta.icon} {n} {meta.label}
              </span>
            )
          })}
          {snapshots.length === 0 && !loading ? null :
            <span className="text-slate-500">{snapshots.length} total</span>
          }
        </div>

        {error ? (
          <div className="bg-red-900/40 border border-red-700 rounded-xl p-3 text-sm text-red-300">
            {error}
          </div>
        ) : null}

        {loading ? (
          <p className="text-slate-400 text-sm py-8 text-center">Loading…</p>
        ) : snapshots.length === 0 ? (
          <div className="bg-slate-800/60 border border-slate-700 rounded-xl p-6 text-center">
            <p className="text-slate-300 text-sm font-medium">No forecast snapshots yet</p>
            <p className="text-slate-500 text-xs mt-2 leading-relaxed">
              Snapshots are created on the dashboard's Forecasts tab or by the
              scheduled Sunday routine. They freeze the backtest's prediction
              and validate against real trades after the horizon elapses.
            </p>
          </div>
        ) : (
          snapshots.map(s => {
            const key = `${s.bot ?? 'main'}/${s.snapshot_id}`
            return (
              <SnapshotCard
                key={key}
                s={s}
                onExpand={() => handleExpand(s)}
                expanded={expanded === key}
                detail={details[key] || null}
              />
            )
          })
        )}
      </div>
    </div>
  )
}
