import { useState, useEffect } from 'react'
import {
  testConnection, setMode,
  getNotifierConfig, setupNotifier, testNotifier,
  getTradingPaused, pauseTrading, resumeTrading,
  NotifierConfig,
} from '../api'
import { saveApiKey, loadApiKey, DEFAULT_URL } from '../credentials'
import InfoModal from './InfoModal'
import SystemGuide from './SystemGuide'
import ConfigLibrary from './ConfigLibrary'

declare const __APP_VERSION__: string
const APP_VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'

type UpdateState = 'idle' | 'checking' | 'current' | 'available'

// ── Collapsible section wrapper ────────────────────────────────────────────────

function Section({
  title, badge, defaultOpen = false, children,
}: {
  title: string
  badge?: React.ReactNode
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3.5 text-left"
        onClick={() => setOpen(o => !o)}
      >
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold text-white">{title}</p>
          {badge}
        </div>
        <span className="text-slate-500 text-xs flex-shrink-0">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="border-t border-border/40 px-4 pb-4 pt-3 space-y-3">
          {children}
        </div>
      )}
    </div>
  )
}

// ── Main Settings ──────────────────────────────────────────────────────────────

export default function Settings() {
  const [apiKey, setApiKey]               = useState(loadApiKey())
  const [configLibOpen, setConfigLibOpen] = useState(false)
  const [loading, setLoading]             = useState(true)
  const [saveStatus, setSaveStatus]       = useState('')
  const [info, setInfo]                   = useState<{ title: string; body: string } | null>(null)
  const [showGuide, setShowGuide]         = useState(false)
  const [modeConfirm, setModeConfirm]     = useState(false)
  const [pendingMode, setPendingMode]     = useState<'paper' | 'live' | null>(null)
  const [modeConfirmText, setModeConfirmText] = useState('')

  // Pause All Trading (global)
  const [tradingPaused, setTradingPaused] = useState<boolean | null>(null)
  const [pauseBusy, setPauseBusy]         = useState(false)
  const [pauseConfirm, setPauseConfirm]   = useState(false)
  const [restartHelpOpen, setRestartHelpOpen] = useState(false)

  // Telegram
  const [notifierCfg, setNotifierCfg]     = useState<NotifierConfig | null>(null)
  const [tgToken, setTgToken]             = useState('')
  const [tgChatId, setTgChatId]           = useState('')
  const [tgStatus, setTgStatus]           = useState('')

  // PWA update check
  const [updateState, setUpdateState]     = useState<UpdateState>('idle')

  async function checkForUpdate() {
    if (updateState === 'checking') return
    if (updateState === 'available') {
      window.location.reload()
      return
    }
    setUpdateState('checking')
    try {
      if (!('serviceWorker' in navigator)) {
        setUpdateState('current')
        setTimeout(() => setUpdateState('idle'), 3000)
        return
      }
      const reg = await navigator.serviceWorker.getRegistration()
      if (!reg) {
        setUpdateState('current')
        setTimeout(() => setUpdateState('idle'), 3000)
        return
      }
      let foundNew = false
      const onFound = () => { foundNew = true }
      reg.addEventListener('updatefound', onFound)
      try {
        await reg.update()
      } catch { /* network errors handled below */ }
      // Give the browser a moment for updatefound to fire
      await new Promise(r => setTimeout(r, 1500))
      reg.removeEventListener('updatefound', onFound)
      if (foundNew || reg.installing || reg.waiting) {
        setUpdateState('available')
      } else {
        setUpdateState('current')
        setTimeout(() => setUpdateState('idle'), 3000)
      }
    } catch {
      setUpdateState('current')
      setTimeout(() => setUpdateState('idle'), 3000)
    }
  }

  function showStatus(msg: string, ms = 3000) {
    setSaveStatus(msg)
    setTimeout(() => setSaveStatus(''), ms)
  }

  async function loadData() {
    try {
      const ntf = await getNotifierConfig().catch(() => null)
      setNotifierCfg(ntf)
      const pauseState = await getTradingPaused().catch(() => null)
      if (pauseState) setTradingPaused(pauseState.paused)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadData() }, [])

  async function handlePauseToggle() {
    if (tradingPaused === null) return
    if (!tradingPaused) {
      // Confirm before pausing — visible action
      setPauseConfirm(true)
      return
    }
    // Resuming is a one-tap action
    setPauseBusy(true)
    try {
      const r = await resumeTrading()
      setTradingPaused(r.paused)
      showStatus('Trading resumed ✓', 3000)
    } catch (e) {
      showStatus(String(e))
    } finally {
      setPauseBusy(false)
    }
  }

  async function confirmPause() {
    setPauseConfirm(false)
    setPauseBusy(true)
    try {
      const r = await pauseTrading()
      setTradingPaused(r.paused)
      showStatus('Trading paused ✓', 3000)
    } catch (e) {
      showStatus(String(e))
    } finally {
      setPauseBusy(false)
    }
  }

  async function saveApiSettings() {
    saveApiKey(apiKey.trim())
    showStatus('Testing connection…', 10000)
    const ok = await testConnection()
    showStatus(ok ? 'Connected ✓' : 'Connection failed — check the API key')
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

  return (
    <div className="p-4 space-y-3 pb-6">
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

      {/* ── Pause All Trading ───────────────────────────────────────────────── */}
      {tradingPaused !== null && (
        <div className={`rounded-2xl border overflow-hidden ${
          tradingPaused
            ? 'bg-amber-950/40 border-amber-700'
            : 'bg-card border-border'
        }`}>
          <button
            onClick={handlePauseToggle}
            disabled={pauseBusy}
            className="w-full flex items-center justify-between gap-3 px-4 py-3.5 text-left disabled:opacity-60"
          >
            <div className="min-w-0">
              <p className="text-sm font-semibold text-white">Pause All Trading</p>
              <p className="text-xs text-slate-400 mt-0.5">
                Existing open positions continue running to expiry. Only new entries are blocked.
              </p>
            </div>
            <span className={`flex-shrink-0 text-xs px-3 py-1.5 rounded-full border font-bold whitespace-nowrap ${
              tradingPaused
                ? 'bg-amber-900/80 text-amber-200 border-amber-600'
                : 'bg-green-900/80 text-green-300 border-green-700'
            }`}>
              {pauseBusy
                ? '…'
                : tradingPaused
                  ? '⏸ Trading Paused'
                  : '● Trading Active'}
            </span>
          </button>
          {tradingPaused && (
            <div className="border-t border-amber-700/40 px-4 py-2 text-xs text-amber-300">
              New positions are blocked across all bots. Tap to resume.
            </div>
          )}
        </div>
      )}

      {/* ── API Connection ──────────────────────────────────────────────────── */}
      <Section title="API Connection" defaultOpen>
        <div className="bg-navy rounded-xl px-3 py-2.5">
          <p className="text-xs text-slate-500 mb-0.5">Server</p>
          <p className="text-xs text-slate-300 font-mono">{DEFAULT_URL}</p>
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
      </Section>

      {/* ── Telegram Notifications ──────────────────────────────────────────── */}
      <Section
        title="Telegram Notifications"
        badge={
          notifierCfg?.configured
            ? <span className="text-xs px-2 py-0.5 rounded-full bg-green-900 text-green-400 border border-green-800">✓ Active</span>
            : <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-500 border border-border">Not set</span>
        }
      >
        {notifierCfg?.configured && (
          <div className="bg-green-950 border border-green-800 rounded-xl px-3 py-2">
            <p className="text-xs text-green-300">
              Chat ID {notifierCfg.chat_id}
              {notifierCfg.bot_token_hint && ` · token …${notifierCfg.bot_token_hint}`}
            </p>
          </div>
        )}
        {!notifierCfg?.configured && (
          <p className="text-xs text-slate-400">Enter your Telegram bot token and chat ID to receive trade alerts.</p>
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
      </Section>

      {/* ── Config Library ──────────────────────────────────────────────────── */}
      <div className="bg-card rounded-2xl border border-border overflow-hidden">
        <button
          onClick={() => setConfigLibOpen(o => !o)}
          className="w-full flex items-center justify-between px-4 py-3.5 text-left"
        >
          <p className="text-sm font-semibold text-white">Config Library</p>
          <span className="text-slate-500 text-xs">{configLibOpen ? '▲' : '▼'}</span>
        </button>
        {configLibOpen && (
          <div className="border-t border-border/40 p-4">
            <ConfigLibrary />
          </div>
        )}
      </div>

      {/* ── Trading Mode ────────────────────────────────────────────────────── */}
      <Section title="Trading Mode">
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
      </Section>

      {/* ── Strategy Guide ──────────────────────────────────────────────────── */}
      <button
        onClick={() => setShowGuide(true)}
        className="w-full flex items-center gap-3 bg-card border border-border rounded-2xl px-4 py-3.5 text-left hover:border-slate-600 transition-colors"
      >
        <span className="text-xl">📖</span>
        <div>
          <p className="text-white text-sm font-medium">Strategy & Architecture Guide</p>
          <p className="text-slate-400 text-xs">How the bot works — pipeline, farm, risk controls</p>
        </div>
        <span className="ml-auto text-slate-500 text-sm">→</span>
      </button>

      {/* ── App Info ────────────────────────────────────────────────────────── */}
      <Section title="App Info">
        <div className="space-y-2 text-xs text-slate-400">
          <div className="flex justify-between">
            <span>App</span>
            <span className="text-slate-300">BTC Wheel Bot</span>
          </div>
          <div className="flex justify-between">
            <span>Architecture</span>
            <span className="text-slate-300">Farm · Pipeline · Black Swan</span>
          </div>
          <div className="flex justify-between">
            <span>Exchange</span>
            <span className="text-slate-300">Deribit (BTC Options)</span>
          </div>
          <div className="flex justify-between">
            <span>Strategy</span>
            <span className="text-slate-300">Wheel · Cash-Secured PUTs</span>
          </div>
          <a
            href="https://github.com/banksiasprings/btc-wheel-bot"
            target="_blank"
            rel="noopener noreferrer"
            className="block text-blue-400 hover:text-blue-300 pt-1"
          >
            GitHub →
          </a>

          {/* How to restart the farm */}
          <div className="pt-3 mt-2 border-t border-border/40">
            <button
              onClick={() => setRestartHelpOpen(o => !o)}
              className="w-full flex items-center justify-between text-left"
            >
              <span className="text-xs text-slate-300 font-medium">How to restart the farm</span>
              <span className="text-slate-500 text-xs">{restartHelpOpen ? '▲' : '▼'}</span>
            </button>
            {restartHelpOpen && (
              <ol className="mt-2 space-y-1.5 text-xs text-slate-400 list-decimal pl-5">
                <li>Tap <span className="text-slate-300 font-medium">"Pause All Trading"</span> above to stop new entries.</li>
                <li>Restart the farm process on the server.</li>
                <li>Tap <span className="text-slate-300 font-medium">"Resume Trading"</span> to re-enable new entries.</li>
              </ol>
            )}
          </div>
        </div>
        <button
          onClick={checkForUpdate}
          disabled={updateState === 'checking'}
          className={`w-full flex items-center justify-between rounded-xl px-3 py-2.5 text-xs font-medium border transition-colors ${
            updateState === 'available'
              ? 'bg-amber-950 border-amber-700 text-amber-300 hover:bg-amber-900'
              : updateState === 'current'
              ? 'bg-green-950 border-green-800 text-green-300'
              : 'bg-navy border-border text-slate-300 hover:border-slate-600'
          }`}
        >
          <span className="flex items-center gap-2">
            <span className="text-slate-500">v{APP_VERSION}</span>
          </span>
          <span className="flex items-center gap-2">
            {updateState === 'checking' && (
              <span className="inline-block h-3 w-3 rounded-full border-2 border-slate-500 border-t-transparent animate-spin" />
            )}
            <span>
              {updateState === 'idle'      && 'Check for Update'}
              {updateState === 'checking'  && 'Checking…'}
              {updateState === 'current'   && 'Up to date ✓'}
              {updateState === 'available' && 'Update available — tap to reload'}
            </span>
          </span>
        </button>
      </Section>

      {/* ── Mode switch confirm dialogs ─────────────────────────────────────── */}
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

      {/* ── Pause All Trading confirmation ──────────────────────────────────── */}
      {pauseConfirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-amber-700 rounded-2xl p-6 w-full max-w-sm">
            <div className="text-amber-400 text-2xl mb-3">⏸</div>
            <h3 className="font-bold text-white text-lg mb-2">Pause All Trading?</h3>
            <p className="text-slate-400 text-sm mb-4">
              No new positions will be opened on any bot until you resume. Existing open
              positions continue running to expiry.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setPauseConfirm(false)}
                className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm"
              >
                Cancel
              </button>
              <button
                onClick={confirmPause}
                className="flex-1 py-3 rounded-xl bg-amber-700 text-white text-sm font-semibold"
              >
                Pause Trading
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
