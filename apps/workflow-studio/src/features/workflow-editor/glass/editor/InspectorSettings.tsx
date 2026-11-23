/**
 * Settings: what happens when the step fails (stop / continue / error output, the engine's `on_error`),
 * retries with delay, notes, and disable. Same fields and engine mapping as the legacy settings tab.
 */
import type { Node } from '@xyflow/react'
import { useWorkflowStore } from '../../stores/workflowStore'
import type { OnErrorMode, WorkflowNodeData } from '../../types/workflow'

const ON_ERROR: { v: OnErrorMode; label: string }[] = [
  { v: 'stop', label: 'Stop the workflow' },
  { v: 'continue', label: 'Continue the workflow' },
  { v: 'error_output', label: 'Send to the error output' },
]

function Toggle({ on, onChange, label, hint, testId }: { on: boolean; onChange: (v: boolean) => void; label: string; hint?: string; testId?: string }) {
  return (
    <button type="button" className="ge-sw" onClick={() => onChange(!on)} role="switch" aria-checked={on} aria-label={label} data-testid={testId}>
      <span>
        {label}
        {hint && <small>{hint}</small>}
      </span>
      <span className="ge-tgl" aria-checked={on} aria-hidden />
    </button>
  )
}

export function InspectorSettings({ node }: { node: Node<WorkflowNodeData> }) {
  const update = useWorkflowStore((s) => s.updateNodeData)
  const setOnError = useWorkflowStore((s) => s.setNodeOnError)
  const d = node.data
  const retries = d.retryOnFail || 0
  return (
    <div className="ge-settings" data-testid="inspector-settings">
      <section className="ge-isec">
        <h6>When it fails</h6>
        <div className="ge-flds">
          <div className="ge-fld">
            <label htmlFor={`ge-onerr-${node.id}`}>On error</label>
            <div className="ge-inp">
              <select
                id={`ge-onerr-${node.id}`}
                value={d.onError ?? 'stop'}
                onChange={(e) => setOnError(node.id, e.target.value as OnErrorMode)}
                data-testid="node-setting-on-error"
              >
                {ON_ERROR.map((o) => (
                  <option key={o.v} value={o.v}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>
          <Toggle on={retries > 0} onChange={(v) => update(node.id, { retryOnFail: v ? 3 : 0 })} label="Retry on fail" hint="Try again before giving up" testId="ndv-retry-on-fail" />
          {retries > 0 && (
            <div className="ge-flds two">
              <div className="ge-fld">
                <label htmlFor={`ge-ret-${node.id}`}>Retries</label>
                <div className="ge-inp">
                  <input
                    id={`ge-ret-${node.id}`}
                    type="number"
                    min={1}
                    max={10}
                    value={retries}
                    onChange={(e) => update(node.id, { retryOnFail: Math.min(10, Math.max(1, parseInt(e.target.value) || 1)) })}
                    data-testid="ndv-retries"
                  />
                </div>
              </div>
              <div className="ge-fld">
                <label htmlFor={`ge-del-${node.id}`}>Delay</label>
                <div className="ge-inp">
                  <input
                    id={`ge-del-${node.id}`}
                    type="number"
                    min={0}
                    step={100}
                    value={d.retryDelay || 1000}
                    onChange={(e) => update(node.id, { retryDelay: Math.max(0, parseInt(e.target.value) || 1000) })}
                    data-testid="ndv-retry-delay"
                  />
                  <span className="suf">ms</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>
      <section className="ge-isec">
        <h6>Notes</h6>
        <textarea
          className="ge-ta"
          rows={3}
          placeholder="Why this step exists, what to watch for…"
          value={d.notes || ''}
          onChange={(e) => update(node.id, { notes: e.target.value })}
          data-testid="ndv-notes"
          aria-label="Notes"
        />
      </section>
      <section className="ge-isec">
        <Toggle on={!!d.disabled} onChange={(v) => update(node.id, { disabled: v })} label="Disable this step" hint="Skipped when the workflow runs" testId="ndv-disable-node" />
      </section>
    </div>
  )
}

export default InspectorSettings
