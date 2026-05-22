import { useState } from 'react'
import Farm from './components/Farm'
import Performance from './components/Performance'
import Backtest from './components/Backtest'
import Settings from './components/Settings'
import TradingView from './components/TradingView'

type Tab =
  | 'farm' | 'trading' | 'performance'
  | 'rl' | 'settings'

const TAB_ICONS: Record<Tab, string> = {
  farm:        '🤖',
  trading:     '📊',
  performance: '📈',
  rl:          '🧠',
  settings:    '⚙',
}

const TAB_LABELS: Record<Tab, string> = {
  farm:        'Farm',
  trading:     'Trading',
  performance: 'Performance',
  rl:          'RL Training',
  settings:    'Settings',
}

const TABS: Tab[] = [
  'farm', 'trading', 'performance', 'rl', 'settings',
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('farm')

  return (
    <div className="flex flex-col h-screen bg-navy text-white overflow-hidden">
      <main className="flex-1 overflow-y-auto">
        {activeTab === 'farm'        && <Farm onNavigate={(tab) => setActiveTab(tab as Tab)} />}
        {activeTab === 'trading'     && <TradingView />}
        {activeTab === 'performance' && <Performance />}
        {activeTab === 'rl'          && <Backtest />}
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
