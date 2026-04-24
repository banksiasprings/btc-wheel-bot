import { useState, useEffect } from 'react'
import SetupScreen from './components/SetupScreen'
import Dashboard from './components/Dashboard'
import Pipeline from './components/Pipeline'
import Performance from './components/Performance'
import Diagnostics from './components/Diagnostics'
import Settings from './components/Settings'

type Tab = 'dashboard' | 'pipeline' | 'performance' | 'diagnostics' | 'settings'

const TAB_ICONS: Record<Tab, string> = {
  dashboard:   '🏠',
  pipeline:    '🗺',
  performance: '📈',
  diagnostics: '🔬',
  settings:    '⚙',
}

const TAB_LABELS: Record<Tab, string> = {
  dashboard:   'Dashboard',
  pipeline:    'Pipeline',
  performance: 'Performance',
  diagnostics: 'Diagnostics',
  settings:    'Settings',
}

const TABS: Tab[] = ['dashboard', 'pipeline', 'performance', 'diagnostics', 'settings']

export default function App() {
  const [isSetup, setIsSetup]   = useState(false)
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')

  useEffect(() => {
    const url = localStorage.getItem('api_url')
    const key = localStorage.getItem('api_key')
    setIsSetup(!!(url && key))
  }, [])

  if (!isSetup) {
    return <SetupScreen onSetupComplete={() => setIsSetup(true)} />
  }

  return (
    <div className="flex flex-col h-screen bg-navy text-white overflow-hidden">
      <main className="flex-1 overflow-y-auto">
        {activeTab === 'dashboard'   && <Dashboard onNavigateTo={(tab) => setActiveTab(tab as Tab)} />}
        {activeTab === 'pipeline'    && <Pipeline />}
        {activeTab === 'performance' && <Performance />}
        {activeTab === 'diagnostics' && <Diagnostics />}
        {activeTab === 'settings'    && <Settings onLogout={() => setIsSetup(false)} />}
      </main>

      {/* Bottom tab bar */}
      <nav className="flex-shrink-0 bg-card border-t border-border pb-safe">
        <div className="flex">
          {TABS.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 flex flex-col items-center py-3 text-xs transition-colors ${
                activeTab === tab
                  ? 'text-green-400'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <span className="text-xl mb-0.5">{TAB_ICONS[tab]}</span>
              <span>{TAB_LABELS[tab]}</span>
            </button>
          ))}
        </div>
      </nav>
    </div>
  )
}
