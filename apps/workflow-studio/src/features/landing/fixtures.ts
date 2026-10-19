/**
 * Landing fixtures: real content copied from the engine, so the page renders without it.
 *   BUILD_NODES / BUILD_EDGES  a generic request-handling workflow (the shape of the `refund-triage` template),
 *                              laid out on a column × row grid.
 *   TEMPLATES                  the nine template names and categories (engine templates/*\/template.json).
 */
export interface BuildNode {
  name: string
  type: string
  col: number
  row: number
}

export const BUILD_COLS = 8
export const BUILD_ROWS = 4

export const BUILD_NODES: BuildNode[] = [
  { name: 'New request', type: 'Webhook', col: 0, row: 1 },
  { name: 'Validate', type: 'ExecuteWorkflow', col: 1, row: 1 },
  { name: 'Needs review?', type: 'If', col: 2, row: 1 },
  { name: 'Approval', type: 'Approval', col: 3, row: 2 },
  { name: 'Call API', type: 'HttpRequest', col: 4, row: 1 },
  { name: 'Succeeded?', type: 'If', col: 5, row: 1 },
  { name: 'Save record', type: 'Postgres', col: 6, row: 0 },
  { name: 'Notify team', type: 'ExecuteWorkflow', col: 4, row: 3 },
  { name: 'Log error', type: 'ExecuteWorkflow', col: 6, row: 2 },
  { name: 'Respond', type: 'Respond', col: 7, row: 1 },
]

export const BUILD_EDGES: [string, string][] = [
  ['New request', 'Validate'],
  ['Validate', 'Needs review?'],
  ['Needs review?', 'Call API'],
  ['Needs review?', 'Approval'],
  ['Approval', 'Call API'],
  ['Approval', 'Notify team'],
  ['Call API', 'Succeeded?'],
  ['Succeeded?', 'Save record'],
  ['Succeeded?', 'Log error'],
  ['Save record', 'Respond'],
  ['Notify team', 'Respond'],
  ['Log error', 'Respond'],
]

export const TEMPLATES: { name: string; category: string }[] = [
  { name: 'AML alert triage', category: 'Compliance' },
  { name: 'Card dispute intake', category: 'Cards' },
  { name: 'Customer notification', category: 'Messaging' },
  { name: 'Email a dataset report after approval', category: 'Data' },
  { name: 'Email a dataset report', category: 'Data' },
  { name: 'KYC review with compliance approval', category: 'Compliance' },
  { name: 'Loan pre-check', category: 'Lending' },
  { name: 'Profile a dataset', category: 'Data' },
  { name: 'Refund triage with manager approval', category: 'Payments' },
]
