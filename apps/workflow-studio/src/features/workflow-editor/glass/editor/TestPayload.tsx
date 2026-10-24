/**
 * The trigger's test payload (mockup "Test payload"): the JSON the next test run starts with, typed by hand or
 * copied from a recent run's trigger data, with a validity line and ▶ Run / ■ Stop. The text lives in
 * editorLayoutStore.payloadInput, so the header's Run and ⌘↵ use the same payload.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Play, Square } from 'lucide-react'
import CodeEditor from '@/shared/components/ui/code-editor'
import { executionsApi } from '@/shared/lib/api'
import { cx } from '@/shared/glass/cx'
import { useEditorLayoutStore } from '../../stores/editorLayoutStore'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useGlassStore } from '../state/glassStore'
import { useWorkflowRuns } from '../run/useRunData'
import { escapeControlCharsInJsonStrings, runWithPayload, useRunControl } from './runControl'
import { timeAgo } from './ui'

function validity(raw: string): { ok: boolean; text: string } {
  if (!raw.trim()) return { ok: true, text: 'Empty: the run starts with {}' }
  try {
    const v = JSON.parse(raw)
    return { ok: true, text: Array.isArray(v) ? `Valid JSON · ${v.length} items` : `Valid JSON · ${Object.keys(v ?? {}).length} fields` }
  } catch (e) {
    try {
      JSON.parse(escapeControlCharsInJsonStrings(raw))
      return { ok: true, text: 'Valid after escaping line breaks in strings' }
    } catch {
      return { ok: false, text: `Not valid JSON: ${(e as Error).message}` }
    }
  }
}

export function TestPayload({ triggerName }: { triggerName: string }) {
  const raw = useEditorLayoutStore((s) => s.payloadInput)
  const setRaw = useEditorLayoutStore((s) => s.setPayloadInput)
  const source = useGlassStore((s) => s.payloadSource)
  const setSource = useGlassStore((s) => s.setPayloadSource)
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const executing = useRunControl((s) => s.executing)
  const cancel = useRunControl((s) => s.cancel)
  const runs = useWorkflowRuns(workflowId)
  const recent = useMemo(() => (runs.data?.pages[0]?.runs ?? []).slice(0, 4), [runs.data])
  const [loading, setLoading] = useState<string | null>(null)
  const v = validity(raw)

  const fromRun = async (id: string) => {
    setLoading(id)
    try {
      const d = await executionsApi.get(id)
      const items = (d.node_data?.[triggerName] ?? []).map((x) => x.json)
      if (!items.length) {
        toast.message('That run has no trigger data')
        return
      }
      setRaw(JSON.stringify(items.length === 1 ? items[0] : items, null, 2))
      setSource({ kind: 'run', runId: id })
    } catch (e) {
      toast.error('Could not load that run', { description: e instanceof Error ? e.message : String(e) })
    } finally {
      setLoading(null)
    }
  }

  return (
    <section className="ge-isec" data-testid="ge-test-payload">
      <h6>
        Test payload<span className="sp" />
        <span>what the next test run starts with</span>
      </h6>
      {recent.length > 0 && (
        <div className="ge-pays" role="group" aria-label="Payload source">
          <button type="button" className="ge-pchip" aria-pressed={source.kind !== 'run'} onClick={() => setSource({ kind: 'typed' })}>
            Typed
          </button>
          {recent.map((r) => (
            <button
              key={r.id}
              type="button"
              className="ge-pchip"
              aria-pressed={source.kind === 'run' && source.runId === r.id}
              onClick={() => void fromRun(r.id)}
              disabled={loading === r.id}
              title={`Use the trigger data of run ${r.id}`}
              data-testid="ge-payload-from-run"
            >
              From run · {timeAgo(r.start_time)}
            </button>
          ))}
        </div>
      )}
      <div className="ge-leaf">
        <CodeEditor
          value={raw}
          onChange={(t) => {
            setRaw(t)
            if (source.kind !== 'typed') setSource({ kind: 'typed' })
          }}
          language="json"
          minHeight="90px"
          maxHeight="240px"
          testId="payload-editor"
          placeholder='{ "field": "value" }'
        />
      </div>
      <div className={cx('ge-pv', v.ok ? 'ok' : 'bad')} data-testid="ge-payload-validity">
        {v.text}
      </div>
      <div className="ge-pbtns">
        {executing ? (
          <button type="button" className="ge-btn stop sm" onClick={() => cancel?.()} data-testid="ge-payload-stop">
            <Square fill="currentColor" /> Stop
          </button>
        ) : (
          <button type="button" className="ge-btn primary sm" onClick={() => runWithPayload()} disabled={!v.ok} data-testid="ge-payload-run">
            <Play /> Run with this payload
          </button>
        )}
      </div>
    </section>
  )
}

export default TestPayload
