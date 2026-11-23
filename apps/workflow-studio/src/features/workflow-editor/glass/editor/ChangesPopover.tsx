/** "N changes" in the draft bar: what the draft adds, changes and removes against the live version. */
import type { RefObject } from 'react'
import { useWorkflowStore } from '../../stores/workflowStore'
import { getEditorAction } from '../../stores/editorActionsStore'
import { useGlassStore } from '../state/glassStore'
import type { WorkflowNodeData } from '../../types/workflow'
import { Pop } from './ui'
import type { DraftChanges } from './versions'

export function ChangesPopover({ open, onClose, anchor, changes, version }: { open: boolean; onClose: () => void; anchor: RefObject<HTMLElement | null>; changes: DraftChanges; version: number | null }) {
  const nodes = useWorkflowStore((s) => s.nodes)
  const byName = (name: string) => nodes.find((n) => n.type === 'workflowNode' && (n.data as WorkflowNodeData).name === name)
  const go = (name: string) => {
    const n = byName(name)
    if (!n) return
    useWorkflowStore.getState().setSelectedNode(n.id)
    useGlassStore.getState().select(name)
    useGlassStore.getState().setInspector('compact')
    getEditorAction('focus-node')?.(n.id)
    onClose()
  }
  const rows = [
    ...changes.diff.added.map((n) => ({ op: 'add' as const, name: n, what: 'New step' })),
    ...changes.diff.changed.map((n) => ({ op: 'chg' as const, name: n, what: 'Settings or wiring changed' })),
    ...changes.diff.removed.map((n) => ({ op: 'rem' as const, name: n, what: 'Removed from the draft' })),
  ]
  return (
    <Pop open={open} onClose={onClose} anchor={anchor} className="ge-chgpop" testId="ge-changes-pop" label="Draft changes">
      <h6>{version != null ? `Draft vs v${version} (live)` : 'Not published yet'}</h6>
      {rows.length === 0 && <p className="ge-hint">No changes since the last version.</p>}
      {rows.map((r) => {
        const label = (byName(r.name)?.data as WorkflowNodeData | undefined)?.label ?? r.name
        return (
          <button key={`${r.op}-${r.name}`} type="button" className="cr" onClick={() => go(r.name)} disabled={r.op === 'rem'} data-testid="ge-change-row" data-op={r.op}>
            <span className={`op ${r.op}`}>{r.op === 'add' ? '+' : r.op === 'rem' ? '−' : '~'}</span>
            <b>{label}</b>
            <small>{r.what}</small>
          </button>
        )
      })}
    </Pop>
  )
}

export default ChangesPopover
