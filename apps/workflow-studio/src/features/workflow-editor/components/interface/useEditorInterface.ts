/** The editor's view of the workflow's interface: what the canvas "Try it" button and the dock read. */
import { FileText, LayoutTemplate, MessageSquare } from 'lucide-react'
import { useInterfaceState } from '@/features/interfaces/hooks/useInterfaceState'
import type { InterfaceKind } from '@/features/interfaces/lib/types'
import { useWorkflowStore } from '../../stores/workflowStore'

/** The canvas button's label, the dock's title and their icon, per interface kind. */
export const DOCK_LABEL: Record<InterfaceKind, { label: string; title: string; icon: typeof FileText }> = {
  chat: { label: 'Chat', title: 'Chat', icon: MessageSquare },
  form: { label: 'Open form', title: 'Form', icon: FileText },
  custom: { label: 'Open screen', title: 'Screen', icon: LayoutTemplate },
}

/** The saved workflow's interface, or null (unsaved workflow, none set, or still loading). */
export function useEditorInterface() {
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const q = useInterfaceState(workflowId)
  return { workflowId, iface: q.data?.interface ?? null }
}
