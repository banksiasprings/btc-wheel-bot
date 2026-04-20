import { useState, useEffect } from 'react'
import { api, getSettings, saveSettings } from '../api.js'

const C = { card: '#1e293b', green: '#22c55e', red: '#ef4444', amber: '#f59e0b', muted: '#94a3b8' }

function Field({ label, value, onChange, type = 'number', step, min, max }) {
  return (
    <div>
      <label className="text-xs mb-1 block" style={{ color: C.muted }}>{label}</label>
      <input
        type={type}
        step={step}
        min={min}
        max={max}
        className="w-full rounded-lg px-3 py-2.5 text-sm text-white outline-none"
        style={{ background: '#0f172a', border: '1px solid #334155' }}
        value={value ?? ''}
        onChange={e => onChange(type === 'number' ? parseFloat(e.target.value) : e.target.value)}
      />
    </div>
  )
}

export default function TabSettings({ onReconfigure }) {
  const settings = getSettings()
  const [url,    setUrl]    = useState(settings.baseUrl)
  const [key,    setKey]    = useState(settings.apiKey)
  const [config, setConfig] = useState({})
  const [saved,  setSaved]  = useState('')
  const [error,  setError]  = useState('')
  const [liveConfirm, setLiveConfirm] = useState(false)
  const [modeBusy,    setModeBusy]    = useState(false)
  const [status,      setStatus]      = useState(null)

  useEffect(() => {
    api.config().then(setConfig).catch(e => setError(e.message))
    api.status().then(setStatus).catch(() => {})
  }, [])

  function patch(key, val) {
    setConfig(c => ({ ...c, [key]: val }))
  }

  async function saveConn() {
    saveSettings({ baseUrl: url, apiKey: key })
    try {
      await api.status()
      setSaved('Connection saved ✓')
    } catch (e) {
      setError(`Connection failed: ${e.message}`)
    }
  }

  async function saveConfig() {
    try {
      await api.updateConfig(config)
      setSaved('Config saved ✓')
    } catch (e) {
      setError(e.message)
    }
  }

  async function switchMode(mode) {
    setModeBusy(true)
    try {
      await api.setMode(mode, mode === 'live' ? 'SWITCH_TO_LIVE' : '')
      setSaved(`Mode set to ${mode} — restart bot`)
      setLiveConfirm(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setModeBusy(false)
    }
  }

  const isLive = status?.mode === 'live'

  return (
    <div className="p-4 flex flex-col gap-4 pb-8" style={{ paddingTop: 'env(safe-area-inset-top,12px)' }}>
      <h1 className="text-lg font-bold text-white">Settings</h1>

      {error && (
        <div className="rounded-lg px-4 py-3 text-sm" style={{ background: '#ef444422', color: C.red }}>
          {error}
          <button className="ml-2 underline" onClick={() => setError('')}>dismiss</button>
        </div>
      )}
      {saved && (
        <div className="rounded-lg px-4 py-3 text-sm" style={{ background: '#22c55e22', color: C.green }}>
          {saved}
          <button className="ml-2 underline" onClick={() => setSaved('')}>dismiss</button>
        </div>
      )}

      {/* Connection */}
      <div className="rounded-xl p-4 flex flex-col gap-3" style={{ background: C.card }}>
        <div className="font-semibold text-white">Connection</div>
        <Field label="API URL" value={url} onChange={setUrl} type="text" />
        <Field label="API Key" value={key} onChange={setKey} type="text" />
        <button
          className="rounded-lg py-2.5 text-sm font-semibold text-white"
          style={{ background: C.green }}
          onClick={saveConn}
        >
          Save Connection
        </button>
        <button
          className="text-xs underline text-center"
          style={{ color: C.muted }}
          onClick={onReconfigure}
        >
          Re-run setup wizard
        </button>
      </div>

      {/* Strategy config */}
      <div className="rounded-xl p-4 flex flex-col gap-3" style={{ background: C.card }}>
        <div className="font-semibold text-white">Strategy Config</div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="IV Rank Threshold" value={config.iv_rank_threshold} onChange={v => patch('iv_rank_threshold', v)} step="0.05" min="0" max="1" />
          <Field label="Delta Min"         value={config.target_delta_min}  onChange={v => patch('target_delta_min', v)}  step="0.025" min="0.05" max="0.5" />
          <Field label="Delta Max"         value={config.target_delta_max}  onChange={v => patch('target_delta_max', v)}  step="0.025" min="0.1" max="0.6" />
          <Field label="Min DTE"           value={config.min_dte}           onChange={v => patch('min_dte', v)}           step="1" min="1" max="30" />
          <Field label="Max DTE"           value={config.max_dte}           onChange={v => patch('max_dte', v)}           step="1" min="7" max="60" />
          <Field label="Max Equity/Leg"    value={config.max_equity_per_leg} onChange={v => patch('max_equity_per_leg', v)} step="0.01" min="0.01" max="0.5" />
          <Field label="Premium Frac"      value={config.premium_fraction_of_spot} onChange={v => patch('premium_fraction_of_spot', v)} step="0.001" min="0.001" max="0.1" />
          <Field label="Starting Equity $" value={config.starting_equity}   onChange={v => patch('starting_equity', v)}  step="1000" min="1000" />
        </div>
        <div className="flex items-center gap-3">
          <label className="text-xs flex-1" style={{ color: C.muted }}>Regime Filter</label>
          <input
            type="checkbox"
            checked={!!config.use_regime_filter}
            onChange={e => patch('use_regime_filter', e.target.checked ? 1 : 0)}
            className="w-4 h-4"
          />
        </div>
        {!!config.use_regime_filter && (
          <Field label="Regime MA Days" value={config.regime_ma_days} onChange={v => patch('regime_ma_days', v)} step="10" min="10" max="200" />
        )}
        <button
          className="rounded-lg py-2.5 text-sm font-semibold text-white"
          style={{ background: C.green }}
          onClick={saveConfig}
        >
          Save Config
        </button>
      </div>

      {/* Mode toggle */}
      <div className="rounded-xl p-4 flex flex-col gap-3" style={{ background: C.card, border: `1px solid ${C.red}44` }}>
        <div className="font-semibold text-white">Trading Mode</div>
        <div className="flex gap-2">
          <span className="rounded-full px-3 py-1 text-xs font-bold"
                style={{ background: isLive ? '#33333300' : C.green + '22', color: isLive ? C.muted : C.green, border: `1px solid ${isLive ? '#334155' : C.green}` }}>
            PAPER
          </span>
          <span className="rounded-full px-3 py-1 text-xs font-bold"
                style={{ background: isLive ? C.red + '22' : '#33333300', color: isLive ? C.red : C.muted, border: `1px solid ${isLive ? C.red : '#334155'}` }}>
            LIVE
          </span>
        </div>

        {!liveConfirm ? (
          <div className="flex gap-2">
            <button
              className="flex-1 rounded-lg py-2.5 text-sm font-semibold text-white disabled:opacity-50"
              style={{ background: C.green }}
              disabled={modeBusy || !isLive}
              onClick={() => switchMode('paper')}
            >
              Switch to Paper
            </button>
            <button
              className="flex-1 rounded-lg py-2.5 text-sm font-semibold text-white"
              style={{ background: isLive ? C.muted : C.red }}
              disabled={modeBusy || isLive}
              onClick={() => setLiveConfirm(true)}
            >
              Switch to LIVE
            </button>
          </div>
        ) : (
          <div className="rounded-lg p-3 flex flex-col gap-2"
               style={{ background: C.red + '22', border: `1px solid ${C.red}` }}>
            <p className="text-xs font-bold" style={{ color: C.red }}>
              ⚠️ REAL MONEY WARNING — Switching to live mode will place real orders on Deribit.
              Make sure your API keys are configured and you understand the risks.
            </p>
            <div className="flex gap-2">
              <button
                className="flex-1 rounded-lg py-2 text-sm font-semibold text-white"
                style={{ background: '#334155' }}
                onClick={() => setLiveConfirm(false)}
              >
                Cancel
              </button>
              <button
                className="flex-1 rounded-lg py-2 text-sm font-semibold text-white"
                style={{ background: C.red }}
                disabled={modeBusy}
                onClick={() => switchMode('live')}
              >
                {modeBusy ? '…' : 'I understand — Go LIVE'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
