/**
 * Unsaved changes are never silently lost: leaving the workflow (another route in the studio) with an unsaved
 * draft asks "Save / Discard / Cancel"; closing or reloading the tab gets the browser's own beforeunload prompt.
 * Switching Page | Editor | Definition or picking a run (?execution=) stays on the same path and is not blocked.
 */
import { useState } from 'react'
import { useBlocker } from '@tanstack/react-router'
import { Dialog } from '@base-ui/react/dialog'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useSaveWorkflow } from '../../hooks/useWorkflowApi'
import './glass-editor.css'

export function UnsavedGuard() {
  const { saveWorkflow, isSaving } = useSaveWorkflow()
  const [error, setError] = useState<string | null>(null)
  const blocker = useBlocker({
    shouldBlockFn: ({ current, next }) => {
      const st = useWorkflowStore.getState()
      if (!st._isDirty || current.pathname === next.pathname) return false
      // the first save of a new workflow moves /workflows/new to /workflows/<its id>: that is not leaving
      if (st.workflowId && current.pathname.endsWith('/workflows/new') && next.pathname.endsWith(`/workflows/${st.workflowId}`)) return false
      return true
    },
    enableBeforeUnload: () => useWorkflowStore.getState()._isDirty,
    withResolver: true,
  })
  const open = blocker.status === 'blocked'
  const cancel = () => {
    setError(null)
    blocker.reset?.()
  }
  const save = async () => {
    setError(null)
    try {
      await saveWorkflow()
      blocker.proceed?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const discard = () => {
    setError(null)
    useWorkflowStore.getState().markAsSaved()
    blocker.proceed?.()
  }

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && cancel()}>
      <Dialog.Portal>
        <Dialog.Backdrop className="ge ge-ndvdim" />
        <Dialog.Popup className="ge ge-confirm" data-testid="unsaved-dialog">
          <Dialog.Title className="ge-confirm-t">Unsaved changes</Dialog.Title>
          <Dialog.Description className="ge-confirm-d">This draft has changes that are not saved. Save them before you leave?</Dialog.Description>
          {error && (
            <p className="ge-confirm-err" role="alert" data-testid="unsaved-error">
              Could not save: {error}
            </p>
          )}
          <div className="ge-confirm-row">
            <button type="button" className="ge-btn ghost" onClick={cancel} data-testid="unsaved-cancel">
              Cancel
            </button>
            <button type="button" className="ge-btn dsoft" onClick={discard} data-testid="unsaved-discard">
              Discard
            </button>
            <button type="button" className="ge-btn primary" onClick={() => void save()} disabled={isSaving} data-testid="unsaved-save">
              {isSaving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
