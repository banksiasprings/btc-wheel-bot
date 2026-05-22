import { useState, useEffect, useCallback } from 'react'
import {
  getRLTrainingStatus, getRLCheckpoints, retrainRL, getFarmStatus,
  RLTrainingStatus, RLCheckpoint,
} from '../api'

// RL Training tab — replaces the old Backtest view. The file kept its old
// name (Backtest.tsx) to avoid breaking imports in App.tsx; the content here
// is purely RL Agent v1 training observability.

function fmtNum(n: number | undefined | null, digits = 0): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function fmtElapsed(sec: number | undefined | null): string {
  if (sec == null) return '—'
  if (sec < 60) return `${Math.round(sec)}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
}

interface RLFallbackInfo {
  ai_running:    boolean
  ai_equity:     number | null
  ai_trades:     number | null
  ai_return_pct: number | null
}

export default function Backtest() {
  const [status, setStatus]             = useState<RLTrainingStatus | null>(null)
  const [statusErr, setStatusErr]       = useState(false)
  const [checkpoints, setCheckpoints]   = useState<RLCheckpoint[]>([])
  const [ckptErr, setCkptErr]           = useState(false)
  const [loading, setLoading]           = useState(true)
  const [fallback, setFallback]         = useState<RLFallbackInfo | null>(null)
  const [retrainBusy, setRetrainBusy]   = useState(false)
  const [retrainMsg, setRetrainMsg]     = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setStatusErr(false)
    setCkptErr(false)

    const [statusRes, ckptRes, farmRes] = await Promise.allSettled([
      getRLTrainingStatus(),
      getRLCheckpoints(),
      getFarmStatus(),
    ])

    if (statusRes.status === 'fulfilled') setStatus(statusRes.value)
    else { setStatus(null); setStatusErr(true) }

    if (ckptRes.status === 'fulfilled') setCheckpoints(ckptRes.value.checkpoints)
    else { setCheckpoints([]); setCkptErr(true) }

    if (farmRes.status === 'fulfilled') {
      const rl = farmRes.value.bots.find(b => b.id === 'rl-agent-v1') ?? null
      if (rl) {
        setFallback({
          ai_running:    rl.status === 'running',
          ai_equity:     rl.metrics.current_equity ?? null,
          ai_trades:     rl.metrics.num_trades ?? null,
          ai_return_pct: rl.metrics.total_return_pct ?? null,
        })
      } else {
        setFallback(null)
      }
    }

    setLoading(false)
  }, [])

  useEffect(() => {
    refresh()
    // Poll every 15s while the tab is mounted — training metrics change slowly.
    const id = setInterval(refresh, 15_000)
    return () => clearInterval(id)
  }, [refresh])

  async function handleRetrain() {
    setRetrainBusy(true)
    setRetrainMsg(null)
    try {
      const r = await retrainRL()
      setRetrainMsg(r.message ?? `✅ Retrain started${r.pid ? ` (PID ${r.pid})` : ''}`)
      setTimeout(refresh, 1500)
    } catch (e) {
      const msg = String(e)
      if (msg.includes('404')) {
        setRetrainMsg('⏳ Coming soon — backend endpoint /rl/retrain not yet wired')
      } else {
        setRetrainMsg(`Error: ${msg}`)
      }
    } finally {
      setRetrainBusy(false)
      setTimeout(() => setRetrainMsg(null), 6000)
    }
  }

  const progressPct = (() => {
    if (!status?.total_timesteps || !status?.target_timesteps) return null
    return Math.min(100, (status.total_timesteps / status.target_timesteps) * 100)
  })()

  return (
    <div className="min-h-screen bg-navy text-white pb-24">
      <header className="sticky top-0 z-10 bg-navy border-b border-slate-800 px-4 py-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold">🧠 RL Training</h1>
            <p className="text-[11px] text-slate-400">Reinforcement-learning agent v1 — live training observability</p>
          </div>
          <button
            onClick={refresh}
            className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 font-medium"
          >
            ↻ Refresh
          </button>
        </div>
      </header>

      <div className="px-4 py-3 space-y-3">

        {/* ── Training status card ────────────────────────────────────────── */}
        <div className="bg-card rounded-2xl border border-border p-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wide">Current Run</p>
            {status?.running ? (
              <span className="flex items-center gap-1.5 text-xs font-bold text-green-400">
                <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                Training
              </span>
            ) : statusErr ? (
              <span className="text-xs font-medium text-slate-500">Status API unavailable</span>
            ) : (
              <span className="text-xs font-medium text-slate-500">Idle</span>
            )}
          </div>

          {loading && <p className="text-xs text-slate-500">Loading…</p>}

          {/* When backend has the endpoint — full progress bar */}
          {status && (
            <>
              {progressPct != null && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Progress</span>
                    <span className="text-white font-mono font-bold">
                      {fmtNum(status.total_timesteps)} / {fmtNum(status.target_timesteps)}
                    </span>
                  </div>
                  <div className="h-2 rounded-full bg-slate-700 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-green-400 transition-all duration-500"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                  <p className="text-xs text-slate-500 text-right font-mono">
                    {progressPct.toFixed(1)}% complete
                  </p>
                </div>
              )}

              {status.total_timesteps != null && progressPct == null && (
                <div className="flex justify-between text-xs">
                  <span className="text-slate-500">Total timesteps</span>
                  <span className="text-white font-mono font-bold">{fmtNum(status.total_timesteps)}</span>
                </div>
              )}
            </>
          )}

          {/* Fallback when /rl/training/status is missing — show what we know */}
          {statusErr && !loading && (
            <div className="bg-slate-900/50 border border-slate-700 rounded-xl px-3 py-2.5">
              <p className="text-xs text-amber-300">⏳ Training status endpoint coming soon</p>
              <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                The bot is training in the background. Once <code className="text-slate-300">/rl/training/status</code> is wired up, live progress will appear here.
              </p>
              {fallback && (
                <div className="grid grid-cols-3 gap-2 mt-3 pt-3 border-t border-slate-800">
                  <div className="text-center">
                    <p className="text-[10px] text-slate-500 uppercase">RL Bot</p>
                    <p className={`text-xs font-bold ${fallback.ai_running ? 'text-green-400' : 'text-slate-400'}`}>
                      {fallback.ai_running ? 'Running' : 'Stopped'}
                    </p>
                  </div>
                  <div className="text-center">
                    <p className="text-[10px] text-slate-500 uppercase">Trades</p>
                    <p className="text-xs text-white font-mono font-bold">{fmtNum(fallback.ai_trades)}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-[10px] text-slate-500 uppercase">Return</p>
                    <p className={`text-xs font-mono font-bold ${
                      (fallback.ai_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}>
                      {fallback.ai_return_pct != null ? `${fallback.ai_return_pct >= 0 ? '+' : ''}${fallback.ai_return_pct.toFixed(2)}%` : '—'}
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Training metrics card ───────────────────────────────────────── */}
        {status && (
          <div className="bg-card rounded-2xl border border-border p-4 space-y-2">
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-1">Training Metrics</p>
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-navy rounded-xl px-3 py-2.5">
                <p className="text-[10px] text-slate-500 uppercase">Explained Variance</p>
                <p className={`text-base font-bold font-mono ${
                  (status.explained_variance ?? 0) >= 0.9 ? 'text-green-400' :
                  (status.explained_variance ?? 0) >= 0.5 ? 'text-amber-400' : 'text-red-400'
                }`}>
                  {status.explained_variance != null ? status.explained_variance.toFixed(3) : '—'}
                </p>
                <p className="text-[10px] text-slate-600 mt-0.5">Higher = better value-function fit</p>
              </div>
              <div className="bg-navy rounded-xl px-3 py-2.5">
                <p className="text-[10px] text-slate-500 uppercase">Mean Reward</p>
                <p className={`text-base font-bold font-mono ${
                  (status.mean_reward ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                }`}>
                  {status.mean_reward != null
                    ? `${status.mean_reward >= 0 ? '+' : ''}${status.mean_reward.toFixed(2)}`
                    : '—'}
                </p>
                <p className="text-[10px] text-slate-600 mt-0.5">Average per episode</p>
              </div>
              <div className="bg-navy rounded-xl px-3 py-2.5">
                <p className="text-[10px] text-slate-500 uppercase">FPS</p>
                <p className="text-base font-bold font-mono text-white">
                  {fmtNum(status.fps)}
                </p>
                <p className="text-[10px] text-slate-600 mt-0.5">Frames/sec</p>
              </div>
              <div className="bg-navy rounded-xl px-3 py-2.5">
                <p className="text-[10px] text-slate-500 uppercase">Elapsed</p>
                <p className="text-base font-bold font-mono text-white">
                  {fmtElapsed(status.time_elapsed_sec)}
                </p>
                <p className="text-[10px] text-slate-600 mt-0.5">{status.iterations != null ? `iter ${status.iterations}` : 'this run'}</p>
              </div>
            </div>
          </div>
        )}

        {/* ── Retrain button ──────────────────────────────────────────────── */}
        <div className="bg-card rounded-2xl border border-border p-4 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wide">Retrain</p>
            <span className="text-[10px] text-slate-600">PPO · MlpPolicy</span>
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            Kick off a fresh training run from the latest checkpoint. Existing models stay safe in <code className="text-slate-400">rl_agent/checkpoints/</code>.
          </p>
          <button
            onClick={handleRetrain}
            disabled={retrainBusy}
            className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-colors ${
              retrainBusy
                ? 'bg-slate-700 text-slate-400'
                : 'bg-emerald-700 hover:bg-emerald-600 active:bg-emerald-800 text-white'
            }`}
          >
            {retrainBusy ? '⏳ Sending…' : '🔄 Start Retraining'}
          </button>
          {retrainMsg && (
            <p className={`text-xs px-3 py-2 rounded-lg border ${
              retrainMsg.startsWith('✅')
                ? 'bg-green-950 border-green-800 text-green-300'
                : retrainMsg.startsWith('⏳')
                  ? 'bg-amber-950 border-amber-800 text-amber-300'
                  : 'bg-red-950 border-red-800 text-red-300'
            }`}>
              {retrainMsg}
            </p>
          )}
        </div>

        {/* ── Checkpoint browser ──────────────────────────────────────────── */}
        <div className="bg-card rounded-2xl border border-border p-4 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wide">Checkpoints</p>
            {!ckptErr && (
              <span className="text-[10px] text-slate-600">{checkpoints.length} saved</span>
            )}
          </div>

          {ckptErr && !loading && (
            <div className="bg-slate-900/50 border border-slate-700 rounded-xl px-3 py-2.5">
              <p className="text-xs text-amber-300">⏳ Checkpoint API coming soon</p>
              <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                Checkpoints are saved to <code className="text-slate-300">rl_agent/checkpoints/</code> every 100k timesteps. Browse via SSH or wait for the <code className="text-slate-300">/rl/checkpoints</code> endpoint.
              </p>
            </div>
          )}

          {!ckptErr && checkpoints.length === 0 && !loading && (
            <p className="text-xs text-slate-500 py-3 text-center">No checkpoints yet — first save at 100k steps.</p>
          )}

          {!ckptErr && checkpoints.length > 0 && (
            <div className="space-y-1.5 max-h-96 overflow-y-auto">
              {checkpoints.map((c, i) => {
                const isSpecial = c.is_best || c.is_final
                return (
                  <div
                    key={i}
                    className={`flex items-center justify-between bg-navy rounded-lg px-3 py-2 ${
                      isSpecial ? 'border border-amber-700/50' : ''
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <p className="text-xs font-mono text-white truncate">{c.name}</p>
                        {c.is_best  && <span className="text-[9px] px-1 rounded bg-amber-800 text-amber-100">BEST</span>}
                        {c.is_final && <span className="text-[9px] px-1 rounded bg-green-800 text-green-100">FINAL</span>}
                      </div>
                      <p className="text-[10px] text-slate-500">
                        {c.timesteps != null ? `${fmtNum(c.timesteps)} steps · ` : ''}
                        {fmtBytes(c.size_bytes)} ·{' '}
                        {(() => {
                          const d = new Date(c.modified_at)
                          return isNaN(d.getTime()) ? c.modified_at : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                        })()}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

      </div>
    </div>
  )
}
