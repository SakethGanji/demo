/**
 * Stage 5 wire types (DESIGN-STAGE5-UIS §3.1, §4.2, §6, §7). Frozen contract: do not invent fields.
 */
import type { Shape } from '@/features/workflow-editor/lib/shapes'

export type InterfaceKind = 'form' | 'chat' | 'custom'

export interface InterfaceAccess {
  mode: 'public' | 'studio'
  public_tasks?: boolean
  hide_progress?: boolean
}

export interface ScreenComponent {
  type: string
  [key: string]: unknown
}

export interface ScreenSpec {
  v: 1
  title?: string
  layout?: 'card' | 'page'
  components: ScreenComponent[]
}

export interface Interface {
  kind: InterfaceKind
  slug: string
  title: string
  description?: string
  trigger_node: string
  result_node?: string | null
  screens?: Record<string, ScreenSpec>
  app_id?: string | null
  access?: InterfaceAccess
}

export interface Wired {
  input: string[]
  tasks: string[]
  context: string[]
  result: string | null
}

export interface InterfaceIssue {
  code: string
  severity?: string
  message: string
  node?: string | null
}

export interface LiveInfo {
  slug: string
  version_id: number
  url: string
}

export interface InterfaceState {
  interface: Interface | null
  live: LiveInfo | null
  wired: Wired
  warnings: InterfaceIssue[]
}

export type FormWidget = 'text' | 'textarea' | 'number' | 'currency' | 'select' | 'radio' | 'checkbox' | 'email' | 'date'

export interface FormField {
  name: string
  label: string
  widget: FormWidget
  required: boolean
  help?: string
  placeholder?: string
  options?: { value: string | number; label: string }[]
  min?: number
  max?: number
  minLength?: number
  maxLength?: number
  pattern?: string
}

export interface FieldsUi {
  label?: string
  help?: string
  placeholder?: string
  widget?: FormWidget
}

export interface PublicSchema {
  kind: InterfaceKind
  title: string
  description?: string
  submit_label?: string
  fields: FormField[]
  screens?: { start?: ScreenSpec; result?: ScreenSpec }
}

export type RunStatus = 'running' | 'waiting' | 'success' | 'failed' | 'cancelled'

export interface TaskRef {
  task_id: string
  title: string
  due_at: string | null
}

export interface RunView {
  execution_id: string
  status: RunStatus
  progress?: { done: number; total: number; current?: string | null } | null
  waiting_for: TaskRef[]
  result: Record<string, unknown> | null
  message: string | null
  error: { code: string; message: string } | null
}

export interface SubmitResponse {
  submission_id: string
  execution_id: string
  run_token: string
  poll_url: string
}

export interface ChatMessage {
  seq: number
  role: 'user' | 'assistant' | 'error'
  content: string
  created_at: string
}

export interface ChatPage {
  messages: ChatMessage[]
  busy: boolean
  next_after: number | null
}

/** Approval form-field spec (§4.2). */
export interface ApprovalFieldSpec {
  name: string
  label?: string
  type: 'text' | 'textarea' | 'number' | 'select' | 'checkbox'
  required?: boolean
  options?: (string | { value: string; label?: string })[]
}

export type TaskStatus = 'open' | 'approved' | 'rejected' | 'timed_out' | 'cancelled'

export interface TaskSummary {
  id: string
  workflow_id: string
  workflow_name: string
  execution_id: string
  node_name: string
  title: string
  status: TaskStatus
  assignees: string[]
  due_at: string | null
  created_at: string
  decided_by: string | null
  decided_at: string | null
}

export interface Decision {
  decision: 'approved' | 'rejected'
  comment?: string | null
  form_data?: Record<string, unknown> | null
  decided_by: string
  decided_at: string
}

export interface TaskDetail extends TaskSummary {
  context: Record<string, unknown>
  form: ApprovalFieldSpec[]
  decision: Decision | null
  screen: ScreenSpec | null
  input: Record<string, unknown>
  allow_comment: boolean
  require_comment_on_reject: boolean
  on_timeout: string
  run_status: string
}

export type DecideOutcome = 'decided' | 'already_decided' | 'already_closed'

export interface DecideResult {
  task: TaskDetail
  outcome: DecideOutcome
}

export interface TaskPage {
  items: TaskSummary[]
  next_cursor: string | null
}

export interface TaskCounts {
  open: number
  by_workflow: Record<string, number>
}

export interface PreviewData {
  /** the run the data comes from (null when the workflow has no run yet) */
  execution_id?: string | null
  /** that run's raw status */
  status?: string | null
  input: Record<string, unknown>
  nodes: Record<string, unknown>
  tasks: TaskSummary[] | TaskDetail[]
  result: Record<string, unknown> | null
  run?: { id?: string; status?: string; started_at?: string } | null
}

export interface AiEditResult {
  spec: ScreenSpec
  summary: string
  warnings: string[]
}

/** Shape alias so callers need one import for form derivation. */
export type InputSchema = Shape
