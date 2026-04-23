import { useState, useEffect } from 'react'
import { listConfigs, NamedConfig, ConfigSource } from '../api'

export interface ConfigSelectorProps {
  value: string | null        // currently selected config name
  onChange: (name: string) => void
  label?: string              // e.g. "Testing config"
  showStats?: boolean         // show inline stats under the dropdown
  allowNew?: boolean          // show the "+ New" option (default true)
}

const SOURCE_BADGE: Record<ConfigSource, { label: string; cls: string }> = {
  evolved:  { label: 'Evolved',   cls: 'bg-green-900 text-green-300 border-green-700'  },
  manual:   { label: 'Manual',    cls: 'bg-slate-800 text-slate-400 border-slate-600'  },
  promoted: { label: 'Promoted',  cls: 'bg-amber-900 text-amber-300 border-amber-700'  },
}

function SourceBadge({ source }: { source: ConfigSource }) {
  const b = SOURCE_BADGE[source] ?? SOURCE_BADGE.manual
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium ${b.cls}`}>
      {b.label}
    </span>
  )
}

export default function ConfigSelector({
  value,
  onChange,
  label = 'Config',
  showStats = true,
  allowNew = true,
}: ConfigSelectorProps) {
  const [configs, setConfigs]   = useState<NamedConfig[]>([])
  const [loading, setLoading]   = useState(true)
  const [showNew, setShowNew]   = useState(false)
  const [newName, setNewName]   = useState('')

  useEffect(() => {
    setLoading(true)
    listConfigs()
      .then(setConfigs)
      .catch(() => setConfigs([]))
      .finally(() => setLoading(false))
  }, [])

  const selected = configs.find(c => c.name === value) ?? null

  function handleSelect(e: React.ChangeEvent<HTMLSelectElement>) {
    const v = e.target.value
    if (v === '__new__') {
      setShowNew(true)
    } else {
      setShowNew(false)
      onChange(v)
    }
  }

  function commitNew() {
    const n = newName.trim()
    if (!n) return
    setShowNew(false)
    setNewName('')
    onChange(n)
  }

  return (
    <div className="space-y-2">
      {/* Label row */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-400 font-medium">{label}</span>
        {selected && <SourceBadge source={selected.source} />}
      </div>

      {/* Dropdown */}
      <select
        value={value ?? ''}
        onChange={handleSelect}
        disabled={loading}
        className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-amber-500 disabled:opacity-50"
      >
        <option value="" disabled>
          {loading ? 'Loading configs…' : '— select a config —'}
        </option>
        {configs.map(c => (
          <option key={c.name} value={c.name}>
            {c.name}
            {c.fitness != null ? ` · fit ${c.fitness.toFixed(2)}` : ''}
          </option>
        ))}
        {allowNew && (
          <option value="__new__">＋ New manual config</option>
        )}
      </select>

      {/* New config name input */}
      {showNew && (
        <div className="flex gap-2">
          <input
            type="text"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && commitNew()}
            placeholder="e.g. my_custom_v1"
            className="flex-1 bg-navy border border-amber-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-amber-400 placeholder-slate-600"
            autoFocus
          />
          <button
            onClick={commitNew}
            disabled={!newName.trim()}
            className="px-4 py-2 bg-amber-700 hover:bg-amber-600 disabled:opacity-40 text-white text-sm rounded-xl font-medium"
          >
            Use
          </button>
          <button
            onClick={() => { setShowNew(false); setNewName('') }}
            className="px-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm rounded-xl"
          >
            ✕
          </button>
        </div>
      )}

      {/* Inline stats for selected config */}
      {showStats && selected && (
        <div className="flex flex-wrap gap-3 px-1 text-xs text-slate-400">
          {selected.fitness != null && (
            <span>Fitness <span className="text-green-400 font-mono">{selected.fitness.toFixed(3)}</span></span>
          )}
          {selected.total_return_pct != null && (
            <span>Return <span className={`font-mono ${selected.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {selected.total_return_pct >= 0 ? '+' : ''}{selected.total_return_pct.toFixed(1)}%
            </span></span>
          )}
          {selected.sharpe != null && (
            <span>Sharpe <span className="text-white font-mono">{selected.sharpe.toFixed(2)}</span></span>
          )}
          {selected.notes && (
            <span className="text-slate-600 italic">{selected.notes}</span>
          )}
        </div>
      )}
    </div>
  )
}
