import { useState, useEffect } from 'react'
import {
  testConnection, setMode,
  getNotifierConfig, setupNotifier, testNotifier,
  listConfigs, promoteConfig,
  saveConfig as apiSaveConfig,
  NotifierConfig, NamedConfig, ConfigSource,
} from '../api'
import InfoModal from './InfoModal'
import SystemGuide from './SystemGuide'

interface Props {
  onLogout: () => void
}

// ── Source badge ───────────────────────────────────────────────────────────────

const SOURCE_BADGE: Record<ConfigSource, { label: string; cls: string }> = {
  evolved:  { label: 'Evolved',  cls: 'bg-green-900 text-green-300 border-green-700'  },
  manual:   { label: 'Manual',   cls: 'bg-slate-800 text-slate-400 border-slate-600'  },
  promoted: { label: 'Promoted', cls: 'bg-amber-900 text-amber-300 border-amber-700'  },
}

function SourceBadge({ source }: { source: ConfigSource }) {
  const b = SOURCE_BADGE[source] ?? SOURCE_BADGE.manual
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium ${b.cls}`}>{b.label}</span>
  )
}

export default function Settings({ onLogout }: Props) {
  const [apiUrl, setApiUrl]               = useState(localStorage.getItem('api_url') ?? '')
  const [apiKey, setApiKey]               = useState(localStorage.getItem('api_key') ?? '')
  const [loading, setLoading]             = useState(true)
  const [saveStatus, setSaveStatus]       = useState('')
  const [info, setInfo]                   = useState<{ title: string; body: string } | null>(null)
  const [showGuide, setShowGuide]         = useState(false)
  const [modeConfirm, setModeConfirm]     = useState(false)
  const [pendingMode, setPendingMode]     = useState<'paper' | 'live' | null>(null)
  const [modeConfirmText, setModeConfirmText] = useState('')

  // Telegram
  const [notifierCfg, setNotifierCfg]     = useState<NotifierConfig | null>(null)
  const [tgToken, setTgToken]             = useState('')
  const [tgChatId, setTgChatId]           = useState('')
  const [tgStatus, setTgStatus]           = useState('')

  // Named configs
  const [namedConfigs, setNamedConfigs]   = useState<NamedConfig[]>([])
  const [newConfigName, setNewConfigName] = useState('')
  const [creatingConfig, setCreatingConfig] = useState(false)
  const [createMsg, setCreateMsg]         = useState('')
  const [promotingConfig, setPromotingConfig] = useState<string | null>(null)
  const [promoteConfirm, setPromoteConfirm]   = useState<NamedConfig | null>(null)
  const [promoteMsg, setPromoteMsg]           = useState('')
  const [promoteEquity, setPromoteEquity]     = useState('')

  function showStatus(msg: string, ms = 3000) {
    setSaveStatus(msg)
    setTimeout(() => setSaveStatus(''), ms)
  }

  async function loadData() {
    try {
      const [ntf, cfgs] = await Promise.all([
        getNotifierConfig().catch(() => null),
        listConfigs().catch(() => [] as NamedConfig[]),
      ])
      setNotifierCfg(ntf)
      setNamedConfigs(cfgs ?? [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadData() }, [])

  async function saveApiSettings() {
    const clean = apiUrl.replace(/\/$/, '')
    localStorage.setItem('api_url', clean)
    localStorage.setItem('api_key', apiKey)
    showStatus('Testing connection…', 10000)
    const ok = await testConnection()
    showStatus(ok ? 'Connected ✓' : 'Connection failed — check URL and key')
  }

  async function saveTelegram() {
    try {
      setTgStatus('Saving…')
      await setupNotifier(tgToken.trim(), tgChatId.trim())
      const ntf = await getNotifierConfig().catch(() => null)
      setNotifierCfg(ntf)
      setTgToken('')
      setTgChatId('')
      setTgStatus('Saved ✓')
      setTimeout(() => setTgStatus(''), 3000)
    } catch (e) { setTgStatus(String(e)) }
  }

  async function sendTestNotification() {
    try {
      setTgStatus('Sending…')
      await testNotifier()
      setTgStatus('Test message sent ✓')
      setTimeout(() => setTgStatus(''), 3000)
    } catch (e) { setTgStatus(String(e)) }
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
    } catch (e) { showStatus(String(e)) }
    setModeConfirm(false)
    setPendingMode(null)
  }

  async function handleCreateBlankConfig() {
    if (!newConfigName.trim()) return
    setCreatingConfig(true)
    setCreateMsg('')
    try {
      await apiSaveConfig({
        name: newConfigName.trim(),
        source: 'manual',
        params: {},
      })
      setCreateMsg(`✅ Created '${newConfigName.trim()}'`)
      setNewConfigName('')
      const cfgs = await listConfigs().catch(() => [] as NamedConfig[])
      setNamedConfigs(cfgs)
    } catch (e) {
      setCreateMsg(String(e))
    } finally {
      setCreatingConfig(false)
    }
  }

  async function handlePromote(cfg: NamedConfig) {
    const equity = parseFloat(promoteEquity)
    if (!equity || equity <= 0) return
    setPromotingConfig(cfg.name)
    setPromoteMsg('')
    try {
      const r = await promoteConfig(cfg.name, equity)
      setPromoteMsg(`✅ ${r.message ?? `'${cfg.name}' promoted — live bot will restart`} | $${equity.toLocaleString()} starting equity`)
      setPromoteConfirm(null)
      setPromoteEquity('')
      const cfgs = await listConfigs().catch(() => [] as NamedConfig[])
      setNamedConfigs(cfgs)
    } catch (e) {
      setPromoteMsg(`❌ ${String(e)}`)
    } finally {
      setPromotingConfig(null)
    }
  }

  return (
    <div className="p-4 space-y-4 pb-4">
      <h1 className="text-lg font-bold text-white pt-2">Settings</h1>

      {showGuide && <SystemGuide onClose={() => setShowGuide(false)} />}

      {info && <InfoModal title={info.title} body={info.body} onClose={() => setInfo(null)} />}

      {saveStatus && (
        <div className={`rounded-xl px-4 py-3 text-sm border ${
          saveStatus.includes('✓') || saveStatus.includes('sent')
            ? 'bg-green-950 border-green-800 text-green-300'
            : saveStatus.includes('fail') || saveStatus.includes('Error') || saveStatus.includes('400') || saveStatus.includes('404')
            ? 'bg-red-950 border-red-800 text-red-300'
            : 'bg-slate-800 border-border text-slate-300'
        }`}>
          {saveStatus}
        </div>
      )}

      {/* ── API Connection ───────────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <p className="text-sm font-semibold text-white">API Connection</p>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">API URL</label>
          <input
            type="url"
            value={apiUrl}
            onChange={e => setApiUrl(e.target.value)}
            placeholder="https://bot.banksiaspringsfarm.com"
            className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white placeholder-slate-600 focus:outline-none focus:border-green-500 text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">API Key</label>
          <input
            type="password"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
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

      {/* ── Telegram Notifications ───────────────────────────────────────── */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <p className="text-sm font-semibold text-white">Telegram Notifications</p>
        {notifierCfg?.configured ? (
          <div className="bg-green-950 border border-green-800 rounded-xl px-3 py-2">
            <p className="text-xs text-green-300">
              ✓ Configured — chat ID {notifierCfg.chat_id}
              {notifierCfg.bot_token_hint && ` · token …${notifierCfg.bot_token_hint}`}
            </p>
          </div>
        ) : (
          <p className="text-xs text-slate-400">
            Enter your Telegram bot token and chat ID to receive trade alerts.
          </p>
        )}
        <div>
          <label className="text-xs text-slate-400 mb-1 block">Bot Token</label>
          <input
            type="password"
            value={tgToken}
            onChange={e => setTgToken(e.target.value)}
            placeholder={notifierCfg?.configured ? '(unchanged)' : '1234567890:AAF…'}
            className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white placeholder-slate-600 focus:outline-none focus:border-green-500 text-sm font-mono"
          />
        </div>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">Chat ID</label>
          <input
            type="text"
            value={tgChatId}
            onChange={e => setTgChatId(e.target.value)}
            placeholder={notifierCfg?.chat_id || '-100123456789'}
            className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white placeholder-slate-600 focus:outline-none focus:border-green-500 text-sm font-mono"
          />
        </div>
        {tgStatus && (
          <p className={`text-xs px-3 py-2 rounded-lg border ${tgStatus.includes('✓') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-slate-800 border-border text-slate-300'}`}>
            {tgStatus}
          </p>
        )}
        <div className="flex gap-2">
          <button
            onClick={saveTelegram}
            disabled={!tgToken || !tgChatId}
            className="flex-1 bg-green-700 hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-medium py-2.5 rounded-xl text-sm"
          >
            Save
          </button>
          {notifierCfg?.configured && (
            <button
              onClick={sendTestNotification}
              className="flex-1 bg-slate-700 hover:bg-slate-600 text-white font-medium py-2.5 rounded-xl text-sm"
            >
              Send Test
            </button>
          )}
        </div>
      </div>

      {/* ── Named Config Manager ─────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-white">Named Config Manager</p>
          <span className="text-xs text-slate-500">{namedConfigs.length} configs</span>
        </div>
        <p className="text-xs text-slate-400">
          Manage your saved configurations. "Set as Live" promotes a config to the active bot.
        </p>

        {promoteMsg && (
          <p className={`text-xs px-3 py-2 rounded-lg border ${promoteMsg.startsWith('✅') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-red-950 border-red-800 text-red-300'}`}>{promoteMsg}</p>
        )}

        {!loading && namedConfigs.length === 0 && (
          <p className="text-xs text-slate-500">
            No named configs yet — save evolved configs from the Pipeline tab.
          </p>
        )}

        <div className="space-y-2">
          {namedConfigs.map(cfg => (
            <div key={cfg.name} className="rounded-xl bg-navy border border-border px-3 py-3">
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm font-medium text-white truncate">{cfg.name}</span>
                  <SourceBadge source={cfg.source} />
                </div>
              </div>
              <div className="flex flex-wrap gap-3 mb-2 text-xs text-slate-500">
                {cfg.fitness != null && <span>Fitness {cfg.fitness.toFixed(2)}</span>}
                {cfg.total_return_pct != null && (
                  <span className={cfg.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}>
                    {cfg.total_return_pct >= 0 ? '+' : ''}{cfg.total_return_pct.toFixed(1)}%
                  </span>
                )}
                {cfg.sharpe != null && <span>Sharpe {cfg.sharpe.toFixed(2)}</span>}
                <span>{new Date(cfg.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })}</span>
                {cfg.notes && <span className="italic text-slate-600">{cfg.notes}</span>}
              </div>
              <button
                onClick={() => { setPromoteConfirm(cfg); setPromoteMsg('') }}
                disabled={promotingConfig === cfg.name}
                className="w-full py-2 rounded-xl bg-green-800 hover:bg-green-700 disabled:opacity-40 text-green-200 text-xs font-semibold"
              >
                {promotingConfig === cfg.name ? 'Promoting…' : 'Set as Live'}
              </button>
            </div>
          ))}
        </div>

        {/* Create new blank config */}
        <div className="pt-2 border-t border-border space-y-2">
          <p className="text-xs text-slate-400 font-medium">Create blank manual config</p>
          <div className="flex gap-2">
            <input
              type="text"
              value={newConfigName}
              onChange={e => setNewConfigName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateBlankConfig()}
              placeholder="e.g. my_manual_v1"
              className="flex-1 bg-navy border border-border rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-amber-500 placeholder-slate-600"
            />
            <button
              onClick={handleCreateBlankConfig}
              disabled={creatingConfig || !newConfigName.trim()}
              className="px-4 py-2 bg-amber-700 hover:bg-amber-600 disabled:opacity-40 text-white text-sm rounded-xl font-medium"
            >
              {creatingConfig ? 'Creating…' : '+'}
            </button>
          </div>
          {createMsg && (
            <p className={`text-xs px-3 py-2 rounded-lg border ${createMsg.startsWith('✅') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-red-950 border-red-800 text-red-300'}`}>{createMsg}</p>
          )}
        </div>
      </div>

      {/* ── Trading Mode ─────────────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-3">
        <p className="text-sm font-semibold text-white">Trading Mode</p>
        <p className="text-xs text-slate-400">Switching modes takes effect on next bot restart.</p>
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

      {/* ── Strategy Reference ───────────────────────────────────────────── */}
      <button
        onClick={() => setShowGuide(true)}
        className="w-full flex items-center gap-3 bg-card border border-border rounded-2xl px-4 py-3 text-left hover:border-slate-600 transition-colors"
      >
        <span className="text-xl">📖</span>
        <div>
          <p className="text-white text-sm font-medium">View Strategy Guide</p>
          <p className="text-slate-400 text-xs">How the bot executes trades — code + plain English</p>
        </div>
        <span className="ml-auto text-slate-500 text-sm">→</span>
      </button>

      {/* ── App Info ─────────────────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl p-4 border border-border space-y-2">
        <p className="text-sm font-semibold text-white">App Info</p>
        <div className="space-y-1 text-xs text-slate-500">
          <p>BTC Wheel Bot Mobile · 5-tab restructure</p>
          <a
            href="https://github.com/banksiasprings/btc-wheel-bot"
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-400 hover:text-blue-300"
          >
            GitHub →
          </a>
        </div>
      </div>

      {/* ── Logout ───────────────────────────────────────────────────────── */}
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

      {/* ── Mode switch confirm dialogs ───────────────────────────────────── */}
      {modeConfirm && pendingMode === 'live' && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-red-800 rounded-2xl p-6 w-full max-w-sm">
            <div className="text-red-400 text-2xl mb-3">⚠️</div>
            <h3 className="font-bold text-white text-lg mb-2">Switch to LIVE Trading?</h3>
            <p className="text-slate-400 text-sm mb-4">
              This will switch the bot to <strong className="text-red-400">LIVE mode</strong> with real money on Deribit.
              Type <code className="text-red-300">SWITCH_TO_LIVE</code> to confirm.
            </p>
            <input
              type="text"
              value={modeConfirmText}
              onChange={e => setModeConfirmText(e.target.value)}
              placeholder="SWITCH_TO_LIVE"
              className="w-full bg-navy border border-red-800 rounded-xl px-3 py-2.5 text-white placeholder-slate-600 focus:outline-none text-sm font-mono mb-4"
            />
            <div className="flex gap-3">
              <button onClick={() => { setModeConfirm(false); setPendingMode(null) }} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">Cancel</button>
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
            <p className="text-slate-400 text-sm mb-6">This will switch to paper (simulated) trading on next restart.</p>
            <div className="flex gap-3">
              <button onClick={() => { setModeConfirm(false); setPendingMode(null) }} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">Cancel</button>
              <button onClick={confirmModeSwitch} className="flex-1 py-3 rounded-xl bg-amber-700 text-white text-sm font-semibold">Switch to Paper</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Promote confirm dialog ────────────────────────────────────────── */}
      {promoteConfirm && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-green-700 rounded-2xl p-6 w-full max-w-sm space-y-4">
            <div className="text-3xl">⬆️</div>
            <h3 className="font-bold text-white text-lg">Set as Live Config?</h3>
            <p className="text-slate-400 text-sm">
              You're about to promote{' '}
              <span className="text-green-400 font-medium">{promoteConfirm.name}</span> to the live bot.
            </p>
            <div className="space-y-1.5">
              <label className="text-xs text-slate-400 font-medium">Actual deposit amount (USD)</label>
              <div className="flex items-center gap-2 bg-slate-900 border border-border rounded-xl px-3 py-2.5">
                <span className="text-slate-400 text-sm">$</span>
                <input
                  type="number"
                  min="1"
                  step="any"
                  value={promoteEquity}
                  onChange={e => setPromoteEquity(e.target.value)}
                  placeholder="e.g. 5000"
                  className="flex-1 bg-transparent text-white text-sm focus:outline-none"
                />
              </div>
            </div>
            <div className="bg-red-950 border border-red-800 rounded-xl px-3 py-2.5 space-y-1">
              <p className="text-red-300 text-xs font-semibold">⚠️ The live bot will switch to MAINNET. Real money will be traded.</p>
              <p className="text-red-300 text-xs">⚠️ The bot will restart with the new configuration.</p>
            </div>
            <div className="flex gap-3">
              <button onClick={() => { setPromoteConfirm(null); setPromoteEquity('') }} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">Cancel</button>
              <button
                onClick={() => handlePromote(promoteConfirm)}
                disabled={!!promotingConfig || !promoteEquity || parseFloat(promoteEquity) <= 0}
                className="flex-1 py-3 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-bold"
              >
                {promotingConfig ? 'Promoting…' : 'Promote to Live'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
