/**
 * Approval mocks in the Tests panel (DESIGN-STAGE5-UIS §4.2, §5.1): an Approval has effect `human`, so a test needs
 * a mock `{items, port}` where `port` is the decision taken. No new mock concept: the existing port field.
 */
export const APPROVAL_NODE_TYPE = 'Approval'
export const APPROVAL_PORTS = ['approved', 'rejected', 'timed_out'] as const
export type ApprovalPort = (typeof APPROVAL_PORTS)[number]

export const APPROVAL_PORT_LABELS: Record<ApprovalPort, string> = {
  approved: 'Approved',
  rejected: 'Rejected',
  timed_out: 'Timed out',
}

export function isApprovalType(type: string | undefined): boolean {
  return type === APPROVAL_NODE_TYPE
}

/** The output item an Approval emits on resume: the input passes through and `approval` is merged in. */
export function sampleApprovalItems(port: ApprovalPort): string {
  const decision = port === 'timed_out' ? 'timed_out' : port
  return JSON.stringify([{ approval: { decision, comment: '', form_data: {}, decided_by: port === 'timed_out' ? 'system:timeout' : 'tester' } }], null, 2)
}

/** Picking "Return items" on an Approval pre-fills the common case: approved, with a matching item. */
export function approvalItemsPatch(currentText: string, currentPort: string): { text: string; port: ApprovalPort } {
  const port = (APPROVAL_PORTS as readonly string[]).includes(currentPort) ? (currentPort as ApprovalPort) : 'approved'
  return { port, text: currentText.trim() ? currentText : sampleApprovalItems(port) }
}
