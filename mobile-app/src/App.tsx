import { useState, useEffect } from 'react'
import SetupScreen from './components/SetupScreen'
import Farm from './components/Farm'
import Pipeline from './components/Pipeline'
import Performance from './components/Performance'
import Settings from './components/Settings'
import { hasCredentials } from './credentials'

type Tab = 'farm' | 'performance' | 'pipeline' | 'settings'

const TAB_ICONS: Record<Tab, string> = {
  farm:        '🤖',
  performance: '📈',
  pipeline:    '🗺',
  settings:    '⚙',
}

const TAB_LABELS: Record<Tab, string> = {
  farm:        'Farm',
  performance: 'Performance',
  pipeline:    'Pipeline',
  settings:    'Settings',
}

const TABS: Tab[] = ['farm', 'performance', 'pipeline', 'settings']

export default function App() {
  const [isSetup, setIsSetup]   = useState(false)
  const [activeTab, setActiveTab] = useState<Tab>('farm')

  useEffect(() => {
    setIsSetup(hasCredentials())
  }, [])

  if (!isSetup) {
    return <SetupScreen onSetupComplete={() => setIsSetup(true)} />
  }

  return (
    <div className="flex flex-col h-screen bg-navy text-white overflow-hidden">
      <main className="flex-1 overflow-y-auto">
        {activeTab === 'farm'        && <Farm />}
        {activeTab === 'performance' && <Performance />}
        {activeTab === 'pipeline'    && <Pipeline />}
        {activeTab === 'settings'    && <Settings />}
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
