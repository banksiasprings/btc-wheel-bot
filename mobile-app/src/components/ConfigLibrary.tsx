import { useState, useEffect, useCallback, useRef } from 'react'
import {
  listConfigs, NamedConfig, ConfigStatus, ConfigSource,
  setConfigStatus, renameConfig, updateConfigNotes,
  duplicateConfig, archiveConfig, deleteConfig,
  startPaperTesting, stopPaperTesting,
  promoteConfig,
  saveConfig as apiSaveConfig,
} from '../api'

// ── Status config ──────────────────────────────────────────────────────────────

const STATUS_META: Record<ConfigStatus, { label: string; dot: string; badge: string }> = {
  draft:     { label: 'Draft',     dot: 'bg-slate-500',   badge: 'bg-slate-800 text-slate-400 border-slate-600'  },
  validated: { label: 'Validated', dot: 'bg-blue-500',    badge: 'bg-blue-900 text-blue-300 border-blue-700'     },
  paper:     { label: 'Paper',     dot: 'bg-amber-400 shadow-[0_0_6px_#fbbf24]', badge: 'bg-amber-900 text-amber-300 border-amber-700' },
  ready:     { label: 'Ready',     dot: 'bg-green-400 shadow-[0_0_6px_#22c55e]', badge: 'bg-green-900 text-green-300 border-green-700' },
  live:      { label: 'Live',      dot: 'bg-green-400 shadow-[0_0_8px_#22c55e]', badge: 'bg-green-800 text-green-200 border-green-600' },
  archived:  { label: 'Archived',  dot: 'bg-slate-700',   badge: 'bg-slate-900 text-slate-500 border-slate-700'  },
}

const SOURCE_META: Record<ConfigSource, { label: string; cls: string }> = {
  evolved:    { label: 'Evolved',    cls: 'bg-green-900 text-green-300 border-green-700'  },
  manual:     { label: 'Manual',     cls: 'bg-slate-800 text-slate-400 border-slate-600'  },
  promoted:   { label: 'Promoted',   cls: 'bg-amber-900 text-amber-300 border-amber-700'  },
  duplicated: { label: 'Duplicated', cls: 'bg-purple-900 text-purple-300 border-purple-700' },
}

const STATUS_ORDER: ConfigStatus[] = ['draft', 'validated', 'paper', 'ready', 'live', 'archived']
type FilterPill = 'all' | ConfigStatus

// ── Sub-components ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ConfigStatus }) {
  const m = STATUS_META[status] ?? STATUS_META.draft
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-full border font-medium ${m.badge}`}>
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${m.dot}`} />
      {m.label}
    </span>
  )
}

function SourceBadge({ source }: { source: ConfigSource }) {
  const m = SOURCE_META[source] ?? SOURCE_META.manual
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium ${m.cls}`}>{m.label}</span>
  )
}

function ConfirmDialog({
  title, body, confirmLabel, danger,
  onConfirm, onCancel, children,
}: {
  title: string
  body: string
  confirmLabel: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
  children?: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
      <div className={`bg-card border rounded-2xl p-6 w-full max-w-sm space-y-4 ${danger ? 'border-red-800' : 'border-border'}`}>
        <h3 className="font-bold text-white text-lg">{title}</h3>
        <p className="text-slate-400 text-sm">{body}</p>
        {children}
        <div className="flex gap-3">
          <button onClick={onCancel} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`flex-1 py-3 rounded-xl text-white text-sm font-semibold ${
              danger ? 'bg-red-700 hover:bg-red-600' : 'bg-green-700 hover:bg-green-600'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Config Card ────────────────────────────────────────────────────────────────

function ConfigCard({
  config,
  onRefresh,
}: {
  config: NamedConfig
  onRefresh: () => void
}) {
  const [busy, setBusy]               = useState(false)
  const [msg, setMsg]                 = useState('')
  const [editingName, setEditingName] = useState(false)
  const [nameInput, setNameInput]     = useState(config.name)
  const [editingNotes, setEditingNotes] = useState(false)
  const [notesInput, setNotesInput]   = useState(config.notes ?? '')
  const [showDuplicateDialog, setShowDuplicateDialog] = useState(false)
  const [dupName, setDupName]         = useState('')
  const [showDeleteConfirm, setShowDeleteConfirm]     = useState(false)
  const [showArchiveConfirm, setShowArchiveConfirm]   = useState(false)
  const [showStopConfirm, setShowStopConfirm]         = useState(false)
  const [showPromoteConfirm, setShowPromoteConfirm]   = useState(false)
  const [promoteEquity, setPromoteEquity] = useState('')
  const nameInputRef = useRef<HTMLInputElement>(null)

  const status = config.status ?? 'draft'
  const sm = STATUS_META[status] ?? STATUS_META.draft

  function flash(text: string, ms = 3000) {
    setMsg(text)
    setTimeout(() => setMsg(''), ms)
  }

  async function withBusy(fn: () => Promise<void>) {
    setBusy(true)
    try { await fn() } catch (e) { flash(String(e)) } finally { setBusy(false) }
  }

  async function handleRename() {
    const newName = nameInput.trim()
    if (!newName || newName === config.name) { setEditingName(false); return }
    await withBusy(async () => {
      await renameConfig(config.name, newName)
      flash(`Renamed to '${newName}'`)
      setEditingName(false)
      onRefresh()
    })
  }

  async function handleSaveNotes() {
    await withBusy(async () => {
      await updateConfigNotes(config.name, notesInput.trim())
      setEditingNotes(false)
      onRefresh()
    })
  }

  async function handleDuplicate() {
    if (!dupName.trim()) return
    await withBusy(async () => {
      await duplicateConfig(config.name, dupName.trim())
      flash(`Duplicated as '${dupName.trim()}'`)
      setShowDuplicateDialog(false)
      setDupName('')
      onRefresh()
    })
  }

  async function handleDelete() {
    await withBusy(async () => {
      await deleteConfig(config.name)
      setShowDeleteConfirm(false)
      onRefresh()
    })
  }

  async function handleArchive() {
    await withBusy(async () => {
      await archiveConfig(config.name)
      setShowArchiveConfirm(false)
      onRefresh()
    })
  }

  async function handleStartPaper() {
    await withBusy(async () => {
      await startPaperTesting(config.name)
      flash('Paper testing started — farm will pick this up shortly')
      onRefresh()
    })
  }

  async function handleStopPaper() {
    await withBusy(async () => {
      await stopPaperTesting(config.name)
      setShowStopConfirm(false)
      flash('Paper testing stopped')
      onRefresh()
    })
  }

  async function handlePromote() {
    const equity = parseFloat(promoteEquity)
    if (!equity || equity <= 0) return
    await withBusy(async () => {
      const r = await promoteConfig(config.name, equity)
      flash(`Promoted to live — ${r.message ?? ''}`)
      setShowPromoteConfirm(false)
      setPromoteEquity('')
      onRefresh()
    })
  }

  return (
    <div className={`bg-card rounded-2xl border overflow-hidden ${
      status === 'live'   ? 'border-green-600' :
      status === 'paper'  ? 'border-amber-700' :
      status === 'ready'  ? 'border-green-800' :
      'border-border'
    }`}>
      {/* Header row */}
      <div className="px-4 pt-3 pb-2">
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-2 flex-wrap flex-1 min-w-0">
            <StatusBadge status={status} />
            <SourceBadge source={config.source ?? 'manual'} />
          </div>
          <span className="text-xs text-slate-600 flex-shrink-0">
            {config.created_at ? new Date(config.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' }) : ''}
          </span>
        </div>

        {/* Config name — tappable to rename inline */}
        {editingName ? (
          <div className="flex items-center gap-2 mb-2">
            <input
              ref={nameInputRef}
              type="text"
              value={nameInput}
              onChange={e => setNameInput(e.target.value)}
              onBlur={handleRename}
              onKeyDown={e => { if (e.key === 'Enter') handleRename(); if (e.key === 'Escape') { setEditingName(false); setNameInput(config.name) } }}
              className="flex-1 bg-navy border border-green-600 rounded-lg px-2 py-1.5 text-white text-sm focus:outline-none"
              autoFocus
            />
            <button onClick={handleRename} className="text-xs px-2 py-1 bg-green-700 rounded-lg text-white">Save</button>
          </div>
        ) : (
          <button
            onClick={() => { setEditingName(true); setNameInput(config.name); setTimeout(() => nameInputRef.current?.select(), 50) }}
            className="text-sm font-semibold text-white hover:text-green-400 transition-colors text-left w-full mb-2"
            disabled={status === 'live'}
          >
            {config.name}
          </button>
        )}

        {/* Stats row */}
        {(config.fitness != null || config.total_return_pct != null || config.sharpe != null) && (
          <div className="flex flex-wrap gap-3 text-xs text-slate-500 mb-2">
            {config.fitness != null       && <span>Fitness <span className="text-slate-300 font-mono">{config.fitness.toFixed(2)}</span></span>}
            {config.total_return_pct != null && (
              <span>Return <span className={`font-mono font-medium ${config.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {config.total_return_pct >= 0 ? '+' : ''}{config.total_return_pct.toFixed(1)}%
              </span></span>
            )}
            {config.sharpe != null        && <span>Sharpe <span className="text-slate-300 font-mono">{config.sharpe.toFixed(2)}</span></span>}
          </div>
        )}

        {/* Notes — tappable to edit */}
        {editingNotes ? (
          <div className="space-y-1.5 mb-2">
            <textarea
              value={notesInput}
              onChange={e => setNotesInput(e.target.value)}
              onBlur={handleSaveNotes}
              rows={2}
              placeholder="Notes…"
              className="w-full bg-navy border border-amber-600 rounded-lg px-2 py-1.5 text-white text-xs focus:outline-none resize-none"
              autoFocus
            />
            <div className="flex gap-1.5">
              <button onClick={handleSaveNotes} className="text-xs px-2 py-1 bg-amber-700 rounded-lg text-white">Save</button>
              <button onClick={() => { setEditingNotes(false); setNotesInput(config.notes ?? '') }} className="text-xs px-2 py-1 bg-slate-700 rounded-lg text-slate-300">Cancel</button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setEditingNotes(true)}
            className="text-xs text-slate-500 italic text-left w-full hover:text-slate-400 transition-colors mb-1"
            disabled={status === 'live'}
          >
            {config.notes ? config.notes : <span className="opacity-50">Tap to add notes…</span>}
          </button>
        )}

        {/* Status feedback */}
        {msg && (
          <p className={`text-xs px-2 py-1.5 rounded-lg border mt-1 ${
            msg.startsWith('❌') ? 'bg-red-950 border-red-800 text-red-300' : 'bg-green-950 border-green-800 text-green-300'
          }`}>{msg}</p>
        )}
      </div>

      {/* Action buttons — contextual per status */}
      <div className="px-4 pb-3">
        <div className="flex flex-wrap gap-2">
          {/* Draft actions */}
          {status === 'draft' && (
            <>
              <ActionBtn
                onClick={handleStartPaper}
                disabled={busy}
                className="bg-amber-800 hover:bg-amber-700 text-amber-200"
              >Start Paper</ActionBtn>
              <ActionBtn onClick={() => { setShowDuplicateDialog(true); setDupName(`${config.name} copy`) }} disabled={busy} className="bg-slate-700 hover:bg-slate-600 text-slate-200">Duplicate</ActionBtn>
              <ActionBtn onClick={() => setShowDeleteConfirm(true)} disabled={busy} className="bg-red-950 hover:bg-red-900 text-red-400">Delete</ActionBtn>
            </>
          )}

          {/* Validated actions */}
          {status === 'validated' && (
            <>
              <ActionBtn onClick={handleStartPaper} disabled={busy} className="bg-amber-800 hover:bg-amber-700 text-amber-200">Start Paper</ActionBtn>
              <ActionBtn onClick={() => { setShowDuplicateDialog(true); setDupName(`${config.name} copy`) }} disabled={busy} className="bg-slate-700 hover:bg-slate-600 text-slate-200">Duplicate</ActionBtn>
              <ActionBtn onClick={() => setShowArchiveConfirm(true)} disabled={busy} className="bg-slate-700 hover:bg-slate-600 text-slate-300">Archive</ActionBtn>
            </>
          )}

          {/* Paper actions */}
          {status === 'paper' && (
            <>
              <ActionBtn onClick={() => setShowStopConfirm(true)} disabled={busy} className="bg-orange-900 hover:bg-orange-800 text-orange-300">Stop Testing</ActionBtn>
              <ActionBtn onClick={() => { setShowDuplicateDialog(true); setDupName(`${config.name} copy`) }} disabled={busy} className="bg-slate-700 hover:bg-slate-600 text-slate-200">Duplicate</ActionBtn>
            </>
          )}

          {/* Ready actions */}
          {status === 'ready' && (
            <>
              <ActionBtn onClick={() => setShowPromoteConfirm(true)} disabled={busy} className="bg-green-700 hover:bg-green-600 text-white font-semibold">Promote to Live</ActionBtn>
              <ActionBtn onClick={() => setShowStopConfirm(true)} disabled={busy} className="bg-orange-900 hover:bg-orange-800 text-orange-300">Stop Testing</ActionBtn>
              <ActionBtn onClick={() => { setShowDuplicateDialog(true); setDupName(`${config.name} copy`) }} disabled={busy} className="bg-slate-700 hover:bg-slate-600 text-slate-200">Duplicate</ActionBtn>
            </>
          )}

          {/* Live: read-only */}
          {status === 'live' && (
            <span className="text-xs text-green-400 font-medium px-1">Live bot — no changes allowed</span>
          )}

          {/* Archived actions */}
          {status === 'archived' && (
            <>
              <ActionBtn onClick={() => { setShowDuplicateDialog(true); setDupName(`${config.name} copy`) }} disabled={busy} className="bg-slate-700 hover:bg-slate-600 text-slate-200">Duplicate</ActionBtn>
              <ActionBtn onClick={() => setShowDeleteConfirm(true)} disabled={busy} className="bg-red-950 hover:bg-red-900 text-red-400">Delete</ActionBtn>
            </>
          )}
        </div>
      </div>

      {/* ── Dialogs ── */}

      {showDuplicateDialog && (
        <ConfirmDialog
          title="Duplicate Config"
          body={`Create a copy of '${config.name}'`}
          confirmLabel="Duplicate"
          onConfirm={handleDuplicate}
          onCancel={() => { setShowDuplicateDialog(false); setDupName('') }}
        >
          <input
            type="text"
            value={dupName}
            onChange={e => setDupName(e.target.value)}
            placeholder="New config name"
            className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-green-500"
            autoFocus
          />
        </ConfirmDialog>
      )}

      {showDeleteConfirm && (
        <ConfirmDialog
          title="Delete Config?"
          body={`This will permanently delete '${config.name}'. This cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={handleDelete}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}

      {showArchiveConfirm && (
        <ConfirmDialog
          title="Archive Config?"
          body={`Archive '${config.name}'? It will be hidden from the main list but history is preserved.`}
          confirmLabel="Archive"
          onConfirm={handleArchive}
          onCancel={() => setShowArchiveConfirm(false)}
        />
      )}

      {showStopConfirm && (
        <ConfirmDialog
          title="Stop Paper Testing?"
          body={`Stop paper testing for '${config.name}'? The farm bot will be stopped.`}
          confirmLabel="Stop Testing"
          danger
          onConfirm={handleStopPaper}
          onCancel={() => setShowStopConfirm(false)}
        />
      )}

      {showPromoteConfirm && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-green-700 rounded-2xl p-6 w-full max-w-sm space-y-4">
            <div className="text-3xl">⬆️</div>
            <h3 className="font-bold text-white text-lg">Promote to Live?</h3>
            <p className="text-slate-400 text-sm">
              Promoting <span className="text-green-400 font-medium">{config.name}</span> will switch the live bot to this config on mainnet.
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
                  autoFocus
                />
              </div>
            </div>
            <div className="bg-red-950 border border-red-800 rounded-xl px-3 py-2.5 space-y-1">
              <p className="text-red-300 text-xs font-semibold">⚠️ The live bot will switch to MAINNET. Real money will be traded.</p>
            </div>
            <div className="flex gap-3">
              <button onClick={() => { setShowPromoteConfirm(false); setPromoteEquity('') }} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">Cancel</button>
              <button
                onClick={handlePromote}
                disabled={busy || !promoteEquity || parseFloat(promoteEquity) <= 0}
                className="flex-1 py-3 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-bold"
              >
                {busy ? 'Promoting…' : 'Promote to Live'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ActionBtn({
  children, onClick, disabled, className,
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
  className: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`text-xs px-3 py-1.5 rounded-xl font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors ${className}`}
    >
      {children}
    </button>
  )
}

// ── ConfigLibrary main component ───────────────────────────────────────────────

export default function ConfigLibrary() {
  const [configs, setConfigs]             = useState<NamedConfig[]>([])
  const [loading, setLoading]             = useState(true)
  const [filter, setFilter]               = useState<FilterPill>('all')
  const [showArchived, setShowArchived]   = useState(false)
  const [showNewDialog, setShowNewDialog] = useState(false)
  const [newName, setNewName]             = useState('')
  const [creating, setCreating]           = useState(false)
  const [createMsg, setCreateMsg]         = useState('')
  const [showFilterPills, setShowFilterPills] = useState(false)

  const load = useCallback(async () => {
    try {
      const cfgs = await listConfigs(showArchived)
      setConfigs(cfgs)
    } catch (e) {
      console.error('Failed to load configs', e)
    } finally {
      setLoading(false)
    }
  }, [showArchived])

  useEffect(() => { load() }, [load])

  async function handleCreateNew() {
    if (!newName.trim()) return
    setCreating(true)
    setCreateMsg('')
    try {
      await apiSaveConfig({ name: newName.trim(), source: 'manual', params: {} })
      setCreateMsg(`Created '${newName.trim()}'`)
      setNewName('')
      setShowNewDialog(false)
      await load()
    } catch (e) {
      setCreateMsg(String(e))
    } finally {
      setCreating(false)
    }
  }

  const filtered = configs.filter(c => {
    if (filter === 'all') return true
    return (c.status ?? 'draft') === filter
  })

  // Count per status for filter pills
  const counts: Partial<Record<ConfigStatus | 'all', number>> = { all: configs.length }
  for (const c of configs) {
    const s = (c.status ?? 'draft') as ConfigStatus
    counts[s] = (counts[s] ?? 0) + 1
  }

  const FILTER_PILLS: { id: FilterPill; label: string }[] = [
    { id: 'all',       label: 'All'       },
    { id: 'draft',     label: 'Draft'     },
    { id: 'validated', label: 'Validated' },
    { id: 'paper',     label: 'Paper'     },
    { id: 'ready',     label: 'Ready'     },
    { id: 'live',      label: 'Live'      },
    { id: 'archived',  label: 'Archived'  },
  ]

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold text-white">Config Library</p>
          <span className="text-xs text-slate-500">{configs.length}</span>
          <button
            onClick={() => setShowFilterPills(f => !f)}
            className="text-xs text-slate-500 hover:text-slate-300 ml-1"
          >
            {showFilterPills ? '▲ filters' : '▼ filters'}
          </button>
        </div>
        <button
          onClick={() => { setShowNewDialog(true); setCreateMsg(''); setNewName('') }}
          className="text-xs px-3 py-1.5 bg-amber-700 hover:bg-amber-600 text-white rounded-xl font-medium"
        >
          + New Config
        </button>
      </div>

      {/* Filter pills */}
      {showFilterPills && (
        <div className="flex flex-wrap gap-1.5">
          {FILTER_PILLS.map(p => {
            const count = counts[p.id] ?? 0
            if (p.id !== 'all' && count === 0) return null
            return (
              <button
                key={p.id}
                onClick={() => {
                  setFilter(p.id)
                  if (p.id === 'archived') setShowArchived(true)
                  else setShowArchived(false)
                }}
                className={`text-xs px-2.5 py-1 rounded-full border font-medium transition-colors ${
                  filter === p.id
                    ? 'bg-green-800 border-green-600 text-green-200'
                    : 'bg-navy border-border text-slate-400 hover:border-slate-500'
                }`}
              >
                {p.label} {count > 0 && <span className="opacity-70">{count}</span>}
              </button>
            )
          })}
        </div>
      )}

      {/* Intro text */}
      <p className="text-xs text-slate-500">
        Each config moves through: Draft → Validated → Paper → Ready → Live. Tap a config name to rename it inline.
      </p>

      {/* Config cards */}
      {loading ? (
        <div className="py-6 text-center text-slate-500 text-sm">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="py-6 text-center">
          <p className="text-slate-500 text-sm">
            {filter === 'all' ? 'No configs yet.' : `No ${filter} configs.`}
          </p>
          {filter === 'all' && (
            <p className="text-slate-600 text-xs mt-1">Save evolved configs from the Pipeline tab, or tap + New Config above.</p>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map(cfg => (
            <ConfigCard key={cfg.name} config={cfg} onRefresh={load} />
          ))}
        </div>
      )}

      {/* New config dialog */}
      {showNewDialog && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="bg-card border border-border rounded-2xl p-6 w-full max-w-sm space-y-4">
            <h3 className="font-bold text-white text-lg">New Manual Config</h3>
            <p className="text-slate-400 text-sm">Create a blank config you can populate later via the pipeline.</p>
            <input
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateNew()}
              placeholder="e.g. my_strategy_v1"
              className="w-full bg-navy border border-border rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-amber-500 placeholder-slate-600"
              autoFocus
            />
            {createMsg && (
              <p className={`text-xs px-3 py-2 rounded-lg border ${createMsg.includes('Created') ? 'bg-green-950 border-green-800 text-green-300' : 'bg-red-950 border-red-800 text-red-300'}`}>
                {createMsg}
              </p>
            )}
            <div className="flex gap-3">
              <button onClick={() => setShowNewDialog(false)} className="flex-1 py-3 rounded-xl bg-slate-700 text-white text-sm">Cancel</button>
              <button
                onClick={handleCreateNew}
                disabled={creating || !newName.trim()}
                className="flex-1 py-3 rounded-xl bg-amber-700 hover:bg-amber-600 disabled:opacity-40 text-white text-sm font-semibold"
              >
                {creating ? 'Creating…' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
