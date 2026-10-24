/**
 * Data: what this step received and produced in the run on the canvas: item counts and timing, the error if it
 * failed, the output as a table or JSON, and Pin (freeze this output for later test steps) / Open in modal.
 */
import { useState } from 'react'
import { Maximize2, Pin, PinOff } from 'lucide-react'
import { DataTablePreview } from '@/shared/glass/DataTablePreview'
import { cx } from '@/shared/glass/cx'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useNDVStore } from '../../stores/ndvStore'
import { useNodeExecution } from '../../hooks/useWorkflowSelectors'
import { useNodeActions } from '../../hooks/useNodeActions'
import { unwrapItems } from './GlassEdge'
import { fmtMs } from './ui'

export function InspectorData({ nodeId }: { nodeId: string }) {
  const exec = useNodeExecution(nodeId)
  const pinned = useWorkflowStore((s) => (s.pinnedData[nodeId]?.length ?? 0) > 0)
  const pinnedItems = useWorkflowStore((s) => s.pinnedData[nodeId])
  const unpin = useWorkflowStore((s) => s.unpinNodeData)
  const openNDV = useNDVStore((s) => s.openNDV)
  const actions = useNodeActions()
  const [mode, setMode] = useState<'table' | 'json'>('table')

  const ran = exec && exec.status !== 'idle'
  const input = unwrapItems(exec?.input?.items)
  const output = pinned && !ran ? unwrapItems(pinnedItems as unknown[]) : unwrapItems(exec?.output?.items)
  const err = exec?.output?.error
  const ms = exec?.metrics?.executionTimeMs ?? (exec?.startTime && exec?.endTime ? exec.endTime - exec.startTime : null)

  return (
    <div data-testid="inspector-data">
      <section className="ge-isec">
        <h6>
          {ran ? 'In the run on the canvas' : pinned ? 'Pinned output' : 'No run yet'}
          <span className="sp" />
          <div className="ge-seg sm" role="group" aria-label="View as">
            <button type="button" aria-pressed={mode === 'table'} onClick={() => setMode('table')}>Table</button>
            <button type="button" aria-pressed={mode === 'json'} onClick={() => setMode('json')}>JSON</button>
          </div>
        </h6>
        {ran && (
          <div className="ge-mets">
            <span className="ge-met"><small>in</small>{input.length}</span>
            <span className={cx('ge-met', exec.status === 'error' ? 'err' : 'ok')}><small>out</small>{output.length}</span>
            {ms != null && <span className="ge-met"><small>took</small>{fmtMs(ms)}</span>}
            {exec.metrics?.retries ? <span className="ge-met warn"><small>retries</small>{exec.metrics.retries}</span> : null}
          </div>
        )}
        {err && <div className="ge-rout err" role="alert" data-testid="inspector-data-error">{err}</div>}
        {!ran && !pinned ? (
          <p className="ge-empty">Run the workflow or test this step to see what it receives and produces.</p>
        ) : output.length === 0 && !err ? (
          <p className="ge-empty">No items came out.</p>
        ) : mode === 'table' ? (
          <DataTablePreview rows={output} preview={6} onMore={() => openNDV(nodeId)} data-testid="inspector-data-table" />
        ) : (
          <pre className="ge-codep" data-testid="inspector-data-json">{JSON.stringify(output.slice(0, 20), null, 2)}</pre>
        )}
      </section>
      <div className="ge-pbtns">
        {pinned ? (
          <button type="button" className="ge-btn sm" onClick={() => unpin(nodeId)} data-testid="inspector-unpin">
            <PinOff /> Unpin
          </button>
        ) : (
          <button type="button" className="ge-btn sm" onClick={() => actions.pinFromLastRun(nodeId)} disabled={!ran || !output.length} data-testid="inspector-pin">
            <Pin /> Pin this output
          </button>
        )}
        <button type="button" className="ge-btn ghost sm" onClick={() => openNDV(nodeId)} data-testid="inspector-open-modal">
          <Maximize2 /> Open with input and output
        </button>
      </div>
    </div>
  )
}

export default InspectorData
