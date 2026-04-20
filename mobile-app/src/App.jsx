import { useState, useEffect, useCallback } from 'react'
import { api, getSettings, saveSettings } from './api.js'
import TabDashboard from './tabs/TabDashboard.jsx'
import TabTrades    from './tabs/TabTrades.jsx'
import TabOptimizer from './tabs/TabOptimizer.jsx'
import TabSettings  from './tabs/TabSettings.jsx'

const TABS = [
  { id: 'dashboard', label: 'Dashboard', icon: '📊' },
  { id: 'trades',    label: 'Trades',    icon: '📋' },
  { id: 'optimizer', label: 'Optimizer', icon: '🧬' },
  { id: 'settings',  label: 'Settings',  icon: '⚙️'  },
]

// ── Setup screen ──────────────────────────────────────────────────────────────

function SetupScreen({ onSave }) {
  const [url,  setUrl]  = useState('')
  const [key,  setKey]  = useState('')
  const [msg,  setMsg]  = useState('')
  const [busy, setBusy] = useState(false)

  async function test() {
    setBusy(true)
    setMsg('')
    saveSettings({ baseUrl: url, apiKey: key })
    try {
      await api.status()
      setMsg('✅ Connected!')
      setTimeout(() => onSave(), 800)
    } catch (e) {
      setMsg(`❌ ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-6 gap-6"
         style={{ background: '#0f172a' }}>
      <div className="text-5xl">₿</div>
      <h1 className="text-2xl font-bold text-white">Wheel Bot Setup</h1>
      <p className="text-slate-400 text-sm text-center">
        Enter your tunnel URL and API key to connect.
      </p>

      <div className="w-full max-w-sm flex flex-col gap-4">
        <div>
          <label className="text-xs text-slate-400 mb-1 block">API URL</label>
          <input
            className="w-full rounded-lg px-4 py-3 text-white text-sm outline-none focus:ring-2"
            style={{ background: '#1e293b', border: '1px solid #334155', ringColor: '#22c55e' }}
            placeholder="https://your-tunnel.trycloudflare.com"
            value={url}
            onChange={e => setUrl(e.target.value)}
            autoCapitalize="none"
            autoCorrect="off"
          />
        </div>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">API Key</label>
          <input
            className="w-full rounded-lg px-4 py-3 text-white text-sm outline-none"
            style={{ background: '#1e293b', border: '1px solid #334155' }}
            placeholder="32-char hex key from .env"
            value={key}
            onChange={e => setKey(e.target.value)}
            autoCapitalize="none"
            autoCorrect="off"
          />
        </div>

        <button
          className="w-full rounded-lg py-3 font-semibold text-white text-sm disabled:opacity-50"
          style={{ background: '#22c55e' }}
          disabled={busy || !url || !key}
          onClick={test}
        >
          {busy ? 'Testing…' : 'Test & Save'}
        </button>

        {msg && (
          <p className="text-center text-sm" style={{ color: msg.startsWith('✅') ? '#22c55e' : '#ef4444' }}>
            {msg}
          </p>
        )}
      </div>
    </div>
  )
}

// ── Main App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [activeTab,  setActiveTab]  = useState('dashboard')
  const [configured, setConfigured] = useState(false)

  useEffect(() => {
    const { baseUrl, apiKey } = getSettings()
    setConfigured(!!(baseUrl && apiKey))
  }, [])

  if (!configured) {
    return <SetupScreen onSave={() => setConfigured(true)} />
  }

  return (
    <div className="flex flex-col" style={{ minHeight: '100svh', background: '#0f172a' }}>
      {/* Content area */}
      <div className="flex-1 overflow-y-auto pb-20">
        {activeTab === 'dashboard' && <TabDashboard />}
        {activeTab === 'trades'    && <TabTrades />}
        {activeTab === 'optimizer' && <TabOptimizer />}
        {activeTab === 'settings'  && <TabSettings onReconfigure={() => setConfigured(false)} />}
      </div>

      {/* Bottom tab bar */}
      <nav
        className="fixed bottom-0 left-0 right-0 flex border-t"
        style={{
          background: '#0f172a',
          borderColor: '#1e293b',
          paddingBottom: 'env(safe-area-inset-bottom)',
        }}
      >
        {TABS.map(tab => (
          <button
            key={tab.id}
            className="flex-1 flex flex-col items-center py-2 gap-0.5 text-xs"
            style={{ color: activeTab === tab.id ? '#22c55e' : '#64748b' }}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="text-lg leading-none">{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </nav>
    </div>
  )
}
