import { useState, useEffect } from 'react'
import { getConfig, updateConfig, setMode, testConnection, getPresets, loadPreset, BotConfig, PresetsData } from '../api'

interface Props {
  onLogout: () => void
}

export default function Settings({ onLogout }: Props) {
  const [apiUrl, setApiUrl] = useState(localStorage.getItem('api_url') ?? '')
  const [apiKey, setApiKey] = useState(localStorage.getItem('api_key') ?? '')
  const [config, setConfig] = useState<BotConfig | null>(null)
  const [presets, setPresets] = useState<PresetsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [saveStatus, setSaveStatus] = useState('')
  const [modeConfirm, setModeConfirm] = useState(false)
  const [pendingMode, setPendingMode] = useState<'paper' | 'live' | null>(null)
  const [modeConfirmText, setModeConfirmText] = useState('')

  function showStatus(msg: string, ms = 3000) {
    setSaveStatus(msg)
    setTimeout(() => setSaveStatus(''), ms)
  }

  useEffect(() => {
    Promise.all([
      getConfig().catch(() => null),
      getPresets().catch(() => null),
    ]).then(([cfg, pr]) => {
      if (cfg) setConfig(cfg)
      setPresets(pr)
    }).finally(() => setLoading(false))
  }, [])

  async function saveApiSettings() {
    const clean = apiUrl.replace(/\/$/, '')
    localStorage.setItem('api_url', clean)
    localStorage.setItem('api_key', apiKey)
    showStatus('Testing connection…', 10000)
    const ok = await testConnection()
    showStatus(ok ? 'Connected ✓' : 'Connection failed — check URL and key')
  }

  async function saveConfig() {
    if (!config) return
    try {
      showStatus('Saving…', 10000)
      await updateConfig({
        delta_target_min: config.delta_target_min ?? undefined,
        delta_target_max: config.delta_target_max ?? undefined,
        min_dte: config.min_dte ?? undefined,
        max_dte: config.max_dte ?? undefined,
        premium_fraction_of_spot: config.premium_fraction_of_spot ?? undefined,
        starting_equity: config.starting_equity ?? undefined,
      })
      showStatus('Config saved ✓')
    } catch (e) {
      showStatus(String(e))
    }
  }

  async function handleLoadPreset(preset: 'sweep' | 'evolve') {
    try {
      showStatus('Loading…', 10000)
      await loadPreset(preset)
      // Refresh presets and config
      const [cfg, pr] = await Promise.all([
        getConfig().catch(() => null),
        getPresets().catch(() => null),
      ])
      if (cfg) setConfig(cfg)
      setPresets(pr)
      const label = preset === 'sweep' ? 'Sweep Best' : 'Evolved Best'
      showStatus(`${label} loaded — restart bot to apply ✓`, 5000)
    } catch (e) {
      showStatus(String(e))
    }
  }

  function requestModeSwitch(m: 'paper' | 'live') {
    setPendingMode(m)
    setModeConfirmText('')
    setModeConfirm(true)
  }

  async function confirmModeSwitch() {
    if (!pendingMode) return
    try {
      const confirmStr = pendingMode === 'live' ? 'SWITCH_TO_LIVE' : undefined
      await setMode(pendingMode, confirmStr)
      showStatus(`Mode switch to ${pendingMode} sent (takes effect on restart)`, 4000)
    } catch (e) {
      showStatus(String(e))
    }
    setModeConfirm(false)
    setPendingMode(null)
  }

  function updateField<K extends keyof BotConfig>(k: K, v: BotConfig[K]) {
    setConfig((c) => (c ? { ...c, [k]: v } : c))
  }

  return (
    <div className="p-4 space-y-4 pb-4">
      <h1 className="text-lg font-bold text-white pt-2">Settings</h1>

      {saveStatus && (
        <div
          className={`rounded-xl px-4 py-3 text-sm border ${
            saveStatus.includes('✓') || saveStatus.includes('sent')
              ? 'bg-green-950 border-green-800 text-green-300'
              : saveStatus.includes('fail') || saveStatus.includes('Error') || saveStatus.includes('400') || saveStatus.includes('404')
              ? 'bg-red-950 border-red-800 text-red-300'
              : 'bg-slate-800 border-border text-slate-300'
          }`}
        >
          {saveStatus}
        </div>
      )}

      {/* Parameter Presets */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <p className="text-sm font-semibold text-white">Parameter Presets</p>
        <p className="text-xs text-slate-400">
          Loading a preset updates config.yaml — restart the bot to apply changes.
        </p>

        {!loading && !presets && (
          <p className="text-xs text-slate-500">Could not load preset data.</p>
        )}

        {presets && (
          <div className="space-y-3">
            {/* Sweep Best */}
            <PresetCard
              title="Sweep Best"
              accent="amber"
              available={presets.sweep.available}
              fitness={presets.sweep.fitness}
              params={presets.sweep.params}
              isActive={presets.active === 'sweep'}
              onLoad={() => handleLoadPreset('sweep')}
              unavailableMsg="Run Parameter Sweep first"
            />

            {/* Evolved Best */}
            <PresetCard
              title="Evolved Best"
              accent="green"
              available={presets.evolve.available}
              fitness={presets.evolve.fitness}
              params={presets.evolve.params}
              isActive={presets.active === 'evolve'}
              onLoad={() => handleLoadPreset('evolve')}
              unavailableMsg="Run Evolve optimizer first"
            />
          </div>
        )}
      </div>

      {/* API connection */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <p className="text-sm font-semibold text-white">API Connection</p>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">API URL</label>
          <input
            type="url"
            value={apiUrl}
            onChange={(e) => setApiUrl(e.target.value)}
            placeholder="https://your-tunnel.trycloudflare.com"
            className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white placeholder-slate-600 focus:outline-none focus:border-green-500 text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">API Key</label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white placeholder-slate-600 focus:outline-none focus:border-green-500 text-sm font-mono"
          />
        </div>
        <button
          onClick={saveApiSettings}
          className="w-full bg-green-700 hover:bg-green-600 text-white font-medium py-2.5 rounded-xl text-sm"
        >
          Save & Test Connection
        </button>
      </div>

      {/* Trading config */}
      {!loading && config && (
        <div className="bg-card rounded-2xl p-4 border border-border space-y-4">
          <p className="text-sm font-semibold text-white">Trading Parameters</p>

          <NumberField
            label="Delta Target Min"
            value={config.delta_target_min}
            min={0.05}
            max={0.5}
            step={0.01}
            onChange={(v) => updateField('delta_target_min', v)}
          />
          <NumberField
            label="Delta Target Max"
            value={config.delta_target_max}
            min={0.05}
            max={0.5}
            step={0.01}
            onChange={(v) => updateField('delta_target_max', v)}
          />
          <NumberField
            label="Min DTE (days)"
            value={config.min_dte}
            min={1}
            max={60}
            step={1}
            onChange={(v) => updateField('min_dte', v)}
          />
          <NumberField
            label="Max DTE (days)"
            value={config.max_dte}
            min={1}
            max={90}
            step={1}
            onChange={(v) => updateField('max_dte', v)}
          />
          <NumberField
            label="Premium Fraction of Spot"
            value={config.premium_fraction_of_spot}
            min={0.001}
            max={0.1}
            step={0.001}
            onChange={(v) => updateField('premium_fraction_of_spot', v)}
          />
          <NumberField
            label="Starting Equity ($)"
            value={config.starting_equity}
            min={1000}
            max={1000000}
            step={1000}
            onChange={(v) => updateField('starting_equity', v)}
          />

          <button
            onClick={saveConfig}
            className="w-full bg-green-700 hover:bg-green-600 text-white font-medium py-2.5 rounded-xl text-sm"
          >
            Save Config
          </button>
        </div>
      )}

      {/* Mode switch */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <p className="text-sm font-semibold text-white">Trading Mode</p>
        <p className="text-xs text-slate-400">
          Switching modes takes effect on next bot restart.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <button
            onClick={() => requestModeSwitch('paper')}
            className="py-3 rounded-xl bg-amber-900 border border-amber-700 text-amber-300 text-sm font-semibold"
          >
            PAPER
          </button>
          <button
            onClick={() => requestModeSwitch('live')}
            className="py-3 rounded-xl bg-red-950 border border-red-800 text-red-300 text-sm font-semibold"
          >
            ⚠ LIVE
          </button>
        </div>
      </div>

      {/* Logout */}
      <button
        onClick={() => {
          localStorage.removeItem('api_url')
          localStorage.removeItem('api_key')
          onLogout()
        }}
        className="w-full py-3 rounded-xl border border-border text-slate-400 text-sm"
      >
        Reset & Re-configure
      </button>

      {/* Mode switch confirm dialog */}
      {modeConfirm && pendingMode === 'live' && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-red-800 rounded-2xl p-6 w-full max-w-sm">
            <div className="text-red-400 text-2xl mb-3">⚠️</div>
            <h3 className="font-bold text-white text-lg mb-2">Switch to LIVE Trading?</h3>
            <p className="text-slate-400 text-sm mb-4">
              This will switch the bot to <strong className="text-red-400">LIVE mode</strong> with
              real money on Deribit. Type <code className="text-red-300">SWITCH_TO_LIVE</code> to
              confirm.
            </p>
            <input
              type="text"
              value={modeConfirmText}
              onChange={(e) => setModeConfirmText(e.target.value)}
              placeholder="SWITCH_TO_LIVE"
              className="w-full bg-navy border border-red-800 rounded-xl px-3 py-2.5 text-white placeholder-slate-600 focus:outline-none text-sm font-mono mb-4"
            />
            <div className="flex gap-3">
              <button
                onClick={() => { setModeConfirm(false); setPendingMode(null) }}
                className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm"
              >
                Cancel
              </button>
              <button
                onClick={confirmModeSwitch}
                disabled={modeConfirmText !== 'SWITCH_TO_LIVE'}
                className="flex-1 py-3 rounded-xl bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
      {modeConfirm && pendingMode === 'paper' && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-border rounded-2xl p-6 w-full max-w-sm">
            <h3 className="font-bold text-white text-lg mb-2">Switch to Paper Trading?</h3>
            <p className="text-slate-400 text-sm mb-6">
              This will switch to paper (simulated) trading on next restart.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => { setModeConfirm(false); setPendingMode(null) }}
                className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm"
              >
                Cancel
              </button>
              <button
                onClick={confirmModeSwitch}
                className="flex-1 py-3 rounded-xl bg-amber-700 text-white text-sm font-semibold"
              >
                Switch to Paper
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

interface PresetCardProps {
  title: string
  accent: 'amber' | 'green'
  available: boolean
  fitness: number | null
  params: import('../api').PresetParams
  isActive: boolean
  onLoad: () => void
  unavailableMsg: string
}

function PresetCard({ title, accent, available, fitness, params, isActive, onLoad, unavailableMsg }: PresetCardProps) {
  const accentCls = {
    amber: {
      border: 'border-amber-800',
      badge: 'bg-amber-900 text-amber-300 border-amber-700',
      btn: 'bg-amber-800 hover:bg-amber-700 text-amber-200',
      activeBadge: 'bg-amber-900 text-amber-300 border border-amber-700',
    },
    green: {
      border: 'border-green-900',
      badge: 'bg-green-900 text-green-300 border-green-700',
      btn: 'bg-green-800 hover:bg-green-700 text-green-200',
      activeBadge: 'bg-green-900 text-green-300 border border-green-700',
    },
  }[accent]

  const iv = params.iv_rank_threshold
  const dMin = params.target_delta_min
  const dMax = params.target_delta_max
  const dteMin = params.min_dte
  const dteMax = params.max_dte
  const leg = params.max_equity_per_leg

  return (
    <div className={`rounded-xl p-3 bg-slate-900 border ${available ? accentCls.border : 'border-slate-700'}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-white">{title}</span>
          {isActive && (
            <span className={`px-2 py-0.5 rounded-full text-xs font-bold border ${accentCls.activeBadge}`}>
              ACTIVE
            </span>
          )}
        </div>
        {fitness != null && (
          <span className="text-xs text-slate-400 font-mono">fitness {fitness.toFixed(2)}</span>
        )}
      </div>

      {!available ? (
        <p className="text-xs text-slate-500">{unavailableMsg}</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 mb-3 text-xs">
            {iv != null && (
              <span className="text-slate-400">IV: <span className="text-slate-200">{(iv * 100).toFixed(1)}%</span></span>
            )}
            {dMin != null && dMax != null && (
              <span className="text-slate-400">Delta: <span className="text-slate-200">{dMin.toFixed(2)}–{dMax.toFixed(2)}</span></span>
            )}
            {dteMin != null && dteMax != null && (
              <span className="text-slate-400">DTE: <span className="text-slate-200">{dteMin}–{dteMax}d</span></span>
            )}
            {leg != null && (
              <span className="text-slate-400">Max Leg: <span className="text-slate-200">{(leg * 100).toFixed(1)}%</span></span>
            )}
          </div>
          <button
            onClick={onLoad}
            disabled={isActive}
            className={`w-full py-2 rounded-lg text-xs font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${accentCls.btn}`}
          >
            {isActive ? 'Currently Loaded' : 'Load'}
          </button>
        </>
      )}
    </div>
  )
}

function NumberField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string
  value: number | null | undefined
  min: number
  max: number
  step: number
  onChange: (v: number) => void
}) {
  return (
    <div>
      <div className="flex justify-between mb-1">
        <label className="text-xs text-slate-400">{label}</label>
        <span className="text-xs text-white font-mono">{value ?? '—'}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value ?? min}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-green-400"
      />
      <div className="flex justify-between text-xs text-slate-600 mt-0.5">
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  )
}
