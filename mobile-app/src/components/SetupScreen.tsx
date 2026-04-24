import { useState } from 'react'
import { testConnection } from '../api'
import { saveApiKey, DEFAULT_URL } from '../credentials'

interface Props {
  onSetupComplete: () => void
}

export default function SetupScreen({ onSetupComplete }: Props) {
  const [key, setKey]     = useState('')
  const [status, setStatus] = useState<'idle' | 'testing' | 'ok' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  async function handleSave() {
    if (!key.trim()) {
      setStatus('error')
      setErrorMsg('API key is required')
      return
    }
    saveApiKey(key.trim())
    setStatus('testing')
    setErrorMsg('')
    const ok = await testConnection()
    if (ok) {
      setStatus('ok')
      setTimeout(onSetupComplete, 600)
    } else {
      setStatus('error')
      setErrorMsg('Could not reach the bot. Check the API key and try again.')
    }
  }

  return (
    <div className="min-h-screen bg-navy flex flex-col items-center justify-center px-6 pt-safe">
      {/* Logo */}
      <div className="mb-8 text-center">
        <div className="w-20 h-20 mx-auto mb-4 rounded-2xl bg-card flex items-center justify-center border border-border">
          <span className="text-4xl text-green-400">⬡</span>
        </div>
        <h1 className="text-2xl font-bold text-white">Wheel Bot</h1>
        <p className="text-slate-400 text-sm mt-1">BTC Wheel Strategy Monitor</p>
      </div>

      {/* Form */}
      <div className="w-full max-w-sm space-y-4">
        <div className="bg-card border border-border rounded-2xl px-4 py-3">
          <p className="text-xs text-slate-500">Connecting to</p>
          <p className="text-sm text-slate-300 font-mono truncate">{DEFAULT_URL}</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1.5">
            API Key
          </label>
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSave()}
            placeholder="32-character hex key"
            autoFocus
            className="w-full bg-card border border-border rounded-xl px-4 py-3 text-white placeholder-slate-500 focus:outline-none focus:border-green-500 text-sm font-mono"
          />
          <p className="text-xs text-slate-500 mt-1.5">
            Found in <code className="text-slate-400">.env</code> as WHEEL_API_KEY on your server
          </p>
        </div>

        {status === 'error' && (
          <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-red-300 text-sm">
            {errorMsg}
          </div>
        )}
        {status === 'ok' && (
          <div className="bg-green-950 border border-green-800 rounded-xl px-4 py-3 text-green-300 text-sm">
            Connected! Loading…
          </div>
        )}

        <button
          onClick={handleSave}
          disabled={status === 'testing'}
          className="w-full bg-green-600 hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3.5 rounded-xl transition-colors"
        >
          {status === 'testing' ? 'Testing connection…' : 'Connect'}
        </button>
      </div>
    </div>
  )
}
