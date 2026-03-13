import { useEffect, useState, useCallback } from 'react'
import {
  Sparkles,
  Save,
  Globe,
  X,
  GitBranch,
  Check,
  Pencil,
} from 'lucide-react'
import { appsApi, type ApiAppVersion } from '@/shared/lib/api'

interface VersionHistoryProps {
  appId: string
  currentVersionId: number | null
  onRevert: (versionId: number) => void
  onClose: () => void
}

const triggerIcon: Record<string, typeof Sparkles> = {
  ai: Sparkles,
  manual: Save,
  publish: Globe,
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  const now = Date.now()
  const diff = now - d.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function VersionHistory({ appId, currentVersionId, onRevert, onClose }: VersionHistoryProps) {
  const [versions, setVersions] = useState<ApiAppVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editLabel, setEditLabel] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    appsApi.listVersions(appId).then((data) => {
      setVersions(data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [appId])

  useEffect(() => { load() }, [load])

  // Reload when currentVersionId changes (new version created)
  useEffect(() => { load() }, [currentVersionId, load])

  const handleLabelSave = async (v: ApiAppVersion) => {
    const trimmed = editLabel.trim() || null
    setEditingId(null)
    try {
      const updated = await appsApi.updateVersionLabel(appId, v.id, trimmed)
      setVersions((prev) => prev.map((ver) => (ver.id === v.id ? { ...ver, label: updated.label } : ver)))
    } catch {
      // silently fail
    }
  }

  // Build a lookup of version_number -> id for branch detection
  const versionById = new Map(versions.map((v) => [v.id, v]))

  return (
    <div className="h-full flex flex-col bg-card text-foreground">
      {/* Header */}
      <div className="flex items-center justify-between h-10 px-3 border-b border-border shrink-0">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Version History
        </span>
        <button
          onClick={onClose}
          className="h-6 w-6 flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
        >
          <X size={12} />
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {loading && versions.length === 0 ? (
          <div className="p-4 text-xs text-muted-foreground text-center">Loading...</div>
        ) : versions.length === 0 ? (
          <div className="p-4 text-xs text-muted-foreground text-center">No versions yet</div>
        ) : (
          <div className="py-1">
            {[...versions].reverse().map((v) => {
              const isCurrent = v.id === currentVersionId
              const Icon = triggerIcon[v.trigger] ?? Save

              // Detect branching: parent isn't the immediately previous version
              const prevVersion = versions.find((pv) => pv.version_number === v.version_number - 1)
              const isBranched = v.parent_version_id != null &&
                prevVersion != null &&
                v.parent_version_id !== prevVersion.id
              const branchParent = isBranched ? versionById.get(v.parent_version_id!) : null

              return (
                <div key={v.id} className="group relative">
                  <button
                    onClick={() => { if (!isCurrent) onRevert(v.id) }}
                    disabled={isCurrent}
                    className={`w-full text-left px-3 py-2 transition-colors ${
                      isCurrent
                        ? 'bg-accent/50'
                        : 'hover:bg-accent/30'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <Icon size={12} className={`shrink-0 ${
                        v.trigger === 'ai' ? 'text-violet-400' :
                        v.trigger === 'publish' ? 'text-green-400' :
                        'text-muted-foreground'
                      }`} />
                      <span className="text-xs font-mono font-medium">
                        v{v.version_number}
                      </span>
                      {isCurrent && (
                        <span className="text-[10px] text-primary font-medium">current</span>
                      )}
                      <span className="ml-auto text-[10px] text-muted-foreground">
                        {formatTime(v.created_at)}
                      </span>
                    </div>

                    {/* Label (inline editable) */}
                    {editingId === v.id ? (
                      <div
                        className="mt-1 flex items-center gap-1"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          autoFocus
                          className="text-[11px] bg-background border border-border rounded px-1 py-0.5 w-full outline-none focus:border-primary"
                          value={editLabel}
                          onChange={(e) => setEditLabel(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleLabelSave(v)
                            if (e.key === 'Escape') setEditingId(null)
                          }}
                          onBlur={() => handleLabelSave(v)}
                        />
                        <button
                          onClick={() => handleLabelSave(v)}
                          className="shrink-0 text-muted-foreground hover:text-foreground"
                        >
                          <Check size={10} />
                        </button>
                      </div>
                    ) : (
                      <div className="mt-0.5 flex items-center gap-1">
                        {v.label ? (
                          <span className="text-[11px] text-muted-foreground truncate">{v.label}</span>
                        ) : (
                          <span className="text-[11px] text-muted-foreground/50 italic truncate">
                            {v.trigger === 'ai' && v.prompt
                              ? v.prompt.slice(0, 40) + (v.prompt.length > 40 ? '...' : '')
                              : 'No label'}
                          </span>
                        )}
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            setEditingId(v.id)
                            setEditLabel(v.label ?? '')
                          }}
                          className="shrink-0 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-foreground transition-opacity"
                        >
                          <Pencil size={9} />
                        </button>
                      </div>
                    )}

                    {/* Branch indicator */}
                    {isBranched && branchParent && (
                      <div className="mt-0.5 flex items-center gap-1 text-[10px] text-amber-400">
                        <GitBranch size={9} />
                        <span>branched from v{branchParent.version_number}</span>
                      </div>
                    )}
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
