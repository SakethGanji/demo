/**
 * The live preview (DESIGN-STAGE5-UIS §0, §11.1): the chosen screen rendered by the same ScreenRenderer the public
 * pages use, on real run data (`preview/data`). Form fields come from the FormTrigger on the canvas, so editing
 * the trigger updates the preview without a round trip.
 */
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Callout } from '@/shared/components/ui/surface'
import { interfacesApi, runsApi, tasksApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'
import { InterfaceForm } from '@/features/interfaces/form/InterfaceForm'
import { ScreenRenderer } from '@/features/interfaces/renderer/ScreenRenderer'
import { deriveFormFields } from '@/features/interfaces/lib/formFields'
import { previewScreenData, previewScreenIds, runPickerLabel } from '@/features/interfaces/lib/interfaceDraft'
import { newIdempotencyKey } from '@/features/interfaces/lib/keys'
import type { Interface } from '@/features/interfaces/lib/types'
import type { Shape } from '../../lib/shapes'
import { useWorkflowStore } from '../../stores/workflowStore'
import type { WorkflowNodeData } from '../../types/workflow'

const LATEST = '__latest'

function useTriggerFields(triggerNode: string) {
  const nodes = useWorkflowStore((s) => s.nodes)
  return useMemo(() => {
    const n = nodes.find((x) => x.type === 'workflowNode' && (x.data as WorkflowNodeData).name === triggerNode)
    const p = (n?.data as WorkflowNodeData | undefined)?.parameters
    return deriveFormFields(p?.input_schema as Shape | undefined, {
      fieldOrder: Array.isArray(p?.field_order) ? (p?.field_order as string[]) : undefined,
      fieldsUi: (p?.fields_ui as never) ?? undefined,
    })
  }, [nodes, triggerNode])
}

export function InterfacePreview({
  workflowId, projectId, iface, screenId, onScreen,
}: {
  workflowId: string
  projectId: string
  iface: Interface
  /** the selected screen id (controlled, so the Ask bar edits what the author is looking at) */
  screenId: string
  onScreen: (id: string) => void
}) {
  const qc = useQueryClient()
  const ids = previewScreenIds(iface)
  const [execId, setExecId] = useState<string | null>(null)
  const fields = useTriggerFields(iface.trigger_node)

  const runs = useQuery({
    queryKey: qk.interfaces.runs(projectId, workflowId),
    queryFn: () => runsApi.list(projectId, { workflow_id: workflowId, limit: 10 }),
    enabled: iface.kind === 'custom',
    staleTime: 10_000,
    select: (r) => r.runs.filter((x) => !/test/i.test(x.mode)),
  })
  const data = useQuery({
    queryKey: qk.interfaces.preview(workflowId, execId),
    queryFn: () => interfacesApi.previewData(workflowId, execId),
    enabled: iface.kind === 'custom',
    retry: false,
    // a fresh preview run is still filling in: follow it until it finishes or pauses
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  })

  // A task screen shows the task's context and form: preview/data only lists summaries, so read the detail.
  const taskNode = screenId.startsWith('task:') ? screenId.slice(5) : null
  const taskId = (data.data?.tasks ?? []).find((t) => t.node_name === taskNode)?.id
  const taskDetail = useQuery({
    queryKey: qk.tasks.detail(taskId ?? ''),
    queryFn: () => tasksApi.get(taskId as string),
    enabled: !!taskId,
    retry: false,
  })

  const previewSubmit = async (body: Record<string, unknown>) => {
    const res = await interfacesApi.previewSubmit(workflowId, body, newIdempotencyKey())
    toast.success('Preview run started')
    setExecId(res.execution_id)
    void qc.invalidateQueries({ queryKey: qk.interfaces.runs(projectId, workflowId) })
  }
  const form = <InterfaceForm fields={fields} onSubmit={previewSubmit} />

  const screenItems = Object.fromEntries(ids.map((i) => [i, i === 'start' ? 'Start form' : i === 'result' ? 'Result' : i.replace(/^task:/, 'Task: ')]))
  const runItems: Record<string, string> = { [LATEST]: 'Live data · latest run', ...Object.fromEntries((runs.data ?? []).map((r) => [r.id, runPickerLabel(r.id)])) }
  if (execId && !runItems[execId]) runItems[execId] = runPickerLabel(execId)

  return (
    <div className="flex flex-col gap-2" data-testid="interface-preview" data-kind={iface.kind} data-screen={screenId}>
      {iface.kind === 'custom' && (
        <div className="flex items-center gap-2">
          <Select value={screenId} onValueChange={(v) => v && onScreen(v)} items={screenItems}>
            <SelectTrigger size="sm" className="min-w-0 flex-1" data-testid="interface-preview-screen-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ids.map((i) => <SelectItem key={i} value={i}>{screenItems[i]}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={execId ?? LATEST} onValueChange={(v) => setExecId(!v || v === LATEST ? null : v)} items={runItems}>
            <SelectTrigger size="sm" className="min-w-0 flex-1" data-testid="interface-preview-run">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(runItems).map(([id, label]) => <SelectItem key={id} value={id}>{label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      )}

      {iface.kind === 'form' && <div className="tile p-3">{form}</div>}

      {iface.kind === 'chat' && (
        <Callout tone="info">Chat is tried on the live page once the workflow is published.</Callout>
      )}

      {iface.kind === 'custom' && (
        <>
          {data.isError && <Callout tone="warn" data-testid="interface-preview-nodata">No run data yet: showing the screen without it. {errorMessage(data.error)}</Callout>}
          {iface.screens?.[screenId] ? (
            <ScreenRenderer
              spec={iface.screens[screenId]}
              data={previewScreenData(data.data, screenId, taskDetail.data)}
              slots={{ input_form: () => form }}
              className="max-h-[360px] overflow-y-auto"
            />
          ) : (
            <Callout tone="warn">There is no “{screenId}” screen yet.</Callout>
          )}
        </>
      )}
    </div>
  )
}
