/**
 * "vN ▾": the workflow's published versions. Picking one draws its diff on the canvas (green new, amber
 * changed, faded removed, via historyStore marks) with a compare bar: Restore it as the draft, or Done.
 * historyStore.open stays the source of truth, so ⌘K "Version history" opens this too.
 */
import { useEffect, type RefObject } from 'react'
import { toast } from 'sonner'
import { RotateCcw } from 'lucide-react'
import { workflowsApi } from '@/shared/lib/api'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useHistoryStore } from '../../stores/historyStore'
import { fromBackendWorkflow } from '../../lib/workflowTransform'
import { buildVersionDiffView } from '../../lib/versionDiffView'
import { cx } from '@/shared/glass/cx'
import { Pop, timeAgo } from './ui'
import { diffDraft, useVersionDetail, useVersions } from './versions'

export function VersionsPopover({ open, onClose, anchor }: { open: boolean; onClose: () => void; anchor: RefObject<HTMLElement | null> }) {
  const { versions, isPending, isError } = useVersions()
  const selected = useHistoryStore((s) => s.selectedVersion)
  const selectVersion = useHistoryStore((s) => s.selectVersion)
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const isActive = useWorkflowStore((s) => s.isActive)
  return (
    <Pop open={open} onClose={onClose} anchor={anchor} className="ge-verpop" testId="ge-versions-pop" label="Versions">
      <h6>Versions · pick one to compare with the draft</h6>
      {!workflowId || workflowId === 'new' ? (
        <p className="ge-hint">Save the workflow to start its history.</p>
      ) : isPending ? (
        <p className="ge-hint">Loading versions…</p>
      ) : isError ? (
        <p className="ge-hint">Couldn't load versions.</p>
      ) : versions.length === 0 ? (
        <p className="ge-hint" data-testid="history-empty">No versions yet. Publish to create v1.</p>
      ) : (
        versions.map((v, i) => (
          <button
            key={v.id}
            type="button"
            className={cx('ge-vr', i === 0 && 'cur')}
            aria-pressed={selected === v.version_number}
            onClick={() => {
              useHistoryStore.getState().setOpen(true)
              selectVersion(selected === v.version_number ? null : v.version_number)
              onClose()
            }}
            data-testid={`history-version-${v.version_number}`}
          >
            <span className={cx('ge-vd', i === 0 && isActive && 'ok')} />
            <span className="ge-vt">
              <b>
                v{v.version_number}
                {i === 0 ? (isActive ? ' · live' : ' · latest') : ''}
              </b>
              <small>{v.message || 'Published'}</small>
            </span>
            <span className="when">{timeAgo(v.created_at)}</span>
            <span className="ge-hint">compare</span>
          </button>
        ))
      )}
    </Pop>
  )
}

/** While a version is picked: its diff on the canvas, and Restore / Done. */
export function CompareBar() {
  const histOpen = useHistoryStore((s) => s.open)
  const selected = useHistoryStore((s) => s.selectedVersion)
  const marks = useHistoryStore((s) => s.marks)
  const setDiff = useHistoryStore((s) => s.setDiff)
  const setOpen = useHistoryStore((s) => s.setOpen)
  const { versions } = useVersions()
  const v = versions.find((x) => x.version_number === selected)
  const detail = useVersionDetail(v?.id)
  const nodes = useWorkflowStore((s) => s.nodes)
  const edges = useWorkflowStore((s) => s.edges)
  const dirty = useWorkflowStore((s) => s._isDirty)

  useEffect(() => {
    if (!histOpen || !detail.data) return
    const { marks: m, ghostNodes } = buildVersionDiffView(detail.data.definition, nodes, edges)
    // the shared view ignores renames / disable / on-error; take "changed" from the fuller draft diff
    const ids = new Map(nodes.map((n) => [(n.data as { name?: string }).name, n.id]))
    for (const name of diffDraft(detail.data.definition, nodes, edges).changed) {
      const id = ids.get(name) ?? name
      if (!m[id]) m[id] = 'changed'
    }
    setDiff(m, ghostNodes)
  }, [histOpen, detail.data, nodes, edges, setDiff])

  if (!histOpen || selected == null || !v) return null
  const counts = { added: 0, removed: 0, changed: 0 }
  for (const m of Object.values(marks)) counts[m] += 1
  const restore = async () => {
    const wid = useWorkflowStore.getState().workflowId
    if (!wid) return
    try {
      const wf = await workflowsApi.restoreVersion(wid, v.id)
      useWorkflowStore.getState().loadWorkflow(fromBackendWorkflow(wf))
      toast.success(`Restored v${v.version_number} as the draft`)
      setOpen(false)
    } catch (e) {
      toast.error('Could not restore this version', { description: e instanceof Error ? e.message : String(e) })
    }
  }
  return (
    <div className="ge-fl ge-cmpbar" role="status" data-testid="history-diff-summary">
      <span className="ge-dot" style={{ background: 'var(--ai)' }} />
      <span>
        Draft vs <b>v{v.version_number}</b>: {counts.added} new · {counts.changed} changed · {counts.removed} removed
        {dirty ? ' · restoring replaces unsaved edits' : ''}
      </span>
      <button type="button" className="ge-btn sm" onClick={() => void restore()} data-testid="history-restore">
        <RotateCcw /> Restore v{v.version_number}
      </button>
      <button type="button" className="ge-btn ghost sm" onClick={() => setOpen(false)} data-testid="history-done">
        Done
      </button>
    </div>
  )
}

export default VersionsPopover
