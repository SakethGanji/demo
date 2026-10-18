/**
 * Canvas chips under a node's label (DESIGN-STAGE5-UIS §11.5):
 *   Approval:    "N waiting" (open tasks, polled) and "Custom UI · Review" when the interface has a task screen for it
 *   FormTrigger: "Form · N fields"
 */
import { Badge } from '@/shared/components/ui/badge'
import { useWorkflowOpenTasks } from '@/features/approvals/hooks/useTasks'
import { useInterfaceState } from '@/features/interfaces/hooks/useInterfaceState'
import { deriveFormFields } from '@/features/interfaces/lib/formFields'
import { waitingLabel, pluralize } from '@/features/interfaces/lib/format'
import type { Shape } from '../../lib/shapes'
import { useEditorProjectStore } from '../../stores/editorProjectStore'
import { useWorkflowStore } from '../../stores/workflowStore'

const chipCls = 'h-4 px-1.5 text-[10.5px]'

function ApprovalChips({ name }: { name: string }) {
  const wf = useWorkflowStore((s) => s.workflowId) ?? null
  const pid = useEditorProjectStore((s) => s.projectId)
  const open = useWorkflowOpenTasks(pid, wf)
  const iface = useInterfaceState(wf)
  const count = open.data?.[name] ?? 0
  const screen = iface.data?.interface?.screens?.[`task:${name}`]
  if (count === 0 && !screen) return null
  return (
    <div className="nodrag mt-1 flex flex-wrap items-center justify-center gap-1">
      {count > 0 && (
        <Badge variant="warning" className={chipCls} data-testid="node-approval-waiting">{waitingLabel(count)}</Badge>
      )}
      {screen && (
        <Badge variant="ai" className={chipCls} data-testid="node-approval-custom-ui">Custom UI · {screen.title ?? 'Review'}</Badge>
      )}
    </div>
  )
}

function FormTriggerChips({ parameters }: { parameters?: Record<string, unknown> }) {
  const schema = parameters?.input_schema as Shape | undefined
  const fields = deriveFormFields(schema, {
    fieldOrder: Array.isArray(parameters?.field_order) ? (parameters?.field_order as string[]) : undefined,
  })
  return (
    <div className="nodrag mt-1 flex justify-center">
      <Badge variant="neutral" className={chipCls} data-testid="node-form-fields">Form · {pluralize(fields.length, 'field')}</Badge>
    </div>
  )
}

export function NodeChips({ type, name, parameters }: { type: string; name: string; parameters?: Record<string, unknown> }) {
  if (type === 'Approval') return <ApprovalChips name={name} />
  if (type === 'FormTrigger') return <FormTriggerChips parameters={parameters} />
  return null
}
