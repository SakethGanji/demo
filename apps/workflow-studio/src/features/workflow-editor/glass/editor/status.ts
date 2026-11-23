/** Small shared mappings for the glass editor: engine/run statuses to the glass status vocabulary. */
import { isTriggerNode } from '../lib/pageSentences'
import { isTriggerType } from '../../lib/nodeConfig'
import { useWorkflowStore } from '../../stores/workflowStore'
import type { StepStatus } from '@/shared/glass/cx'
import type { NodeExecutionData } from '../../types/workflow'

export function execToStatus(s: NodeExecutionData['status'] | null | undefined): StepStatus {
  switch (s) {
    case 'running': return 'running'
    case 'success': return 'ok'
    case 'error': return 'fail'
    case 'waiting': return 'need'
    default: return 'pending'
  }
}

/** A run's status (`success`, `error`, `running`, `waiting`, `cancelled`…) as a glass status. */
export function runToStatus(s: string | null | undefined): StepStatus {
  switch (s) {
    case 'success': case 'completed': return 'ok'
    case 'error': case 'failed': case 'crashed': return 'fail'
    case 'running': case 'new': case 'queued': return 'running'
    case 'waiting': case 'paused': return 'need'
    case 'cancelled': case 'canceled': return 'skip'
    default: return 'pending'
  }
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** Whether a canvas node starts the workflow (by its type, or by having no inputs). */
export function isTriggerData(data: { type?: string; inputCount?: number } | undefined): boolean {
  if (!data) return false
  return isTriggerNode(data.type ?? '') || isTriggerType(data.type ?? '', useWorkflowStore.getState().nodeTypesMap) || data.inputCount === 0
}
