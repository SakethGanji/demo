/**
 * Notebook contract: a hand mirror of apps/workflow-engine/src/schemas/notebook.py (BUILD-PLAN §1.3).
 * Change both together. Timestamps are ISO strings. Errors arrive as ApiError (shared/api/http.ts) with
 * `code` one of NotebookErrorCode and extras in `details` (e.g. run_in_progress -> {run_id}).
 */

// ---- enums ---------------------------------------------------------------------------------------------------------

export const CELL_KINDS = [
  'query', 'http', 'workflow', 'rule', 'change', 'data', 'store', 'browser', 'for_each', 'ask', 'note', 'wait',
] as const
export type CellKind = (typeof CELL_KINDS)[number]
/** Kinds that were removed: creating one is refused (kind_removed); a stored one loads with removed=true, read-only. */
export const REMOVED_KINDS = ['approval', 'person'] as const
export type RemovedKind = (typeof REMOVED_KINDS)[number]
/** What a stored cell's kind can be: a live kind or a removed one. */
export type StoredCellKind = CellKind | RemovedKind

export type CellEffect = 'read' | 'write' | 'none' | 'llm'
export type CreatedVia = 'user' | 'agent' | 'template'
export type NotebookStatus = 'idle' | 'running' | 'needs_you' | 'failed' | 'done'
export type RunStatus = 'running' | 'waiting' | 'done' | 'failed' | 'stopped_at_write' | 'needs_you' | 'cancelled' | 'interrupted'
export type ResultStatus =
  | 'success' | 'error' | 'skipped_ask' | 'guard_false' | 'kept_earlier' | 'needs_approval' | 'waiting' | 'applied'
  | 'discarded' | 'cancelled'
export type OnApplied = 'stop' | 'skip'
export type StopReason = 'applied_write' | 'cell_failed' | 'needs_approval' | 'waiting' | 'cancelled'

/** dataset / report: engine keep.py KeepTargetX (W2), beyond the base schema. */
export type KeepTarget = 'workflow' | 'test_suite' | 'scheduled_check' | 'dataset' | 'report'
export type KeepItemStatus = 'ok' | 'needs_choice' | 'blocked'
export type KeepItemKind = 'params' | 'trigger' | 'slots' | 'run_as' | 'writes' | 'empty_policy' | 'excluded'
export type ParamMode = 'fixed' | 'input'
export type WriteMode = 'approval' | 'auto'
export type EmptyPolicy = 'stop_quietly' | 'continue'
/** takeover / handback: browser cells only (the run is paused while the user drives the live page). */
export type ControlAction = 'pause' | 'resume' | 'step' | 'stop' | 'takeover' | 'handback'

/** Kinds that cannot sit inside a for_each body. */
export const TOP_LEVEL_ONLY_KINDS: readonly CellKind[] = ['for_each', 'ask', 'note', 'wait', 'change']

// ---- error codes ---------------------------------------------------------------------------------------------------

export type NotebookErrorCode =
  // HTTP-level (detail.code)
  | 'notebook_not_found' | 'cell_not_found' | 'run_not_found' | 'template_not_found'
  | 'invalid_spec' | 'unknown_kind' | 'not_select' | 'connection_readonly' | 'connection_not_found' | 'invalid_parent'
  | 'invalid' | 'cell_applied' | 'run_in_progress' | 'not_pending' | 'unbound_credential'
  | 'spec_export_unavailable' | 'not_implemented' | 'kind_removed'
  // cell-level (CellResult.error.code)
  | 'bad_ref' | 'llm_unavailable' | 'browser_unavailable' | 'query_failed' | 'http_failed' | 'workflow_failed'
  | 'rule_failed' | 'change_failed' | 'store_failed' | 'row_cap_exceeded' | 'timeout' | 'cancelled' | 'internal'

export interface CellError {
  code: NotebookErrorCode | string
  message: string
  [extra: string]: unknown
}

// ---- per-kind specs ------------------------------------------------------------------------------------------------
// Values may be references: "{{cells.c3.rows}}", "{{cells.c3.count}}", "{{cells.c3.rows[0].id}}", "{{row.wire_id}}".

export type Ref = string

export interface QuerySpec { connection: string; sql: string; params?: Record<string, unknown>; limit?: number }

export interface HttpExpect { status?: number | null; json_path_equals?: Record<string, unknown> | null }
export type HttpMethod = 'GET' | 'HEAD' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
export type HttpAuthType = 'none' | 'bearer' | 'basic' | 'api_key'
/** `secret` is a project secret's key: the request uses it as {{ $vars.KEY }}; its value is never stored or shown. */
export interface HttpAuth { type: HttpAuthType; secret?: string | null; username?: string | null; name?: string | null; add_to?: 'header' | 'query' }
export type AssertionKind = 'status' | 'time' | 'json_path' | 'header' | 'schema'
export type AssertionOp = 'eq' | 'ne' | 'lt' | 'le' | 'gt' | 'ge' | 'contains' | 'not_contains' | 'exists' | 'not_exists' | 'matches' | 'in'
export interface HttpAssertion { kind: AssertionKind; op?: AssertionOp; path?: string | null; name?: string | null; value?: unknown; json_schema?: Record<string, unknown> | null }
export interface HttpExtract { name: string; source?: 'json_path' | 'header' | 'status'; path?: string | null }
export interface HttpSpec {
  method?: HttpMethod
  url?: string | null
  api_ref?: string | null
  params?: Record<string, unknown>
  headers?: Record<string, string>
  body?: unknown
  body_type?: 'json' | 'form'
  auth?: HttpAuth | null
  assertions?: HttpAssertion[]
  extract?: HttpExtract[]
  expect?: HttpExpect | null
  credential_slot?: string | null
}
/** One checked assertion on an http result: the sentence, pass/fail, and the actual value it saw. */
export interface AssertionResult { label: string; kind: AssertionKind | 'expect'; passed: boolean; actual: unknown; expected?: unknown; message?: string | null }
/** CellResult.output of an http cell. */
export interface HttpOutput {
  status?: number
  method?: string
  url?: string
  headers?: Record<string, string>
  body?: unknown
  ms?: number | null
  size?: number | null
  passed?: boolean
  request?: { method: string; url: string; headers?: string[]; body_type?: string | null }
  assertions?: AssertionResult[]
  vars?: Record<string, unknown>
  last?: { status?: number; ms?: number | null; size?: number | null; at?: string; body?: unknown } | null
}

// ---- environments (per notebook) -----------------------------------------------------------------------------------
export interface EnvVar { name: string; value?: string | null; secret?: string | null }
export interface EnvSet { name: string; vars: EnvVar[] }
export interface NotebookEnvironments { active?: string | null; sets: EnvSet[] }

export interface ImportHttpRequest { curl?: string | null; spec_id?: string | null; operation_ids?: string[] | null; add?: boolean; by?: string | null }
export interface ImportHttpResponse { planned: { title?: string | null; spec: HttpSpec }[]; warnings: string[]; cells: Cell[] }

/** One take-over input: x/y are fractions of the frame (0..1). */
export interface CellInput { type: 'click' | 'type' | 'key' | 'scroll'; x?: number; y?: number; text?: string; key?: string; dx?: number; dy?: number }

export interface WorkflowSpec { workflow_id: string; version?: number | 'published'; inputs?: Record<string, unknown> }

export type CompareOp = 'eq' | 'ne' | 'lt' | 'le' | 'gt' | 'ge'
export interface RuleField { left: string; right: string; op?: CompareOp | 'approx'; tolerance?: number | null }
export interface RuleWhere {
  field: string
  op: CompareOp | 'in' | 'not_in' | 'contains' | 'is_null' | 'not_null' | 'older_than'
  value?: unknown
}
export interface RuleSpec {
  op: 'compare' | 'filter' | 'count' | 'assert'
  left: Ref | unknown
  right?: Ref | unknown
  key?: string | string[] | null
  fields?: RuleField[]
  where?: RuleWhere[]
}

export interface ChangeSpec {
  connection: string
  table: string
  key: Record<string, unknown>
  set: Record<string, unknown>
  reason?: string
  idempotency_key?: string | null
}

export interface DataSpec {
  columns?: string[]
  rows?: Record<string, unknown>[] | unknown[][]
  csv?: string | null
  /** GET /notebooks/{id} cuts rows to 50: never PATCH such a spec back */
  rows_truncated?: boolean
  row_count?: number
}

export interface StoreSpec {
  table_id?: string | null
  new_table?: string | null
  mode?: 'append' | 'upsert'
  key?: string | string[] | null
  rows: Ref | unknown
}

export type BrowserActionName =
  | 'goto' | 'click' | 'fill' | 'select' | 'press' | 'hover' | 'upload' | 'otp' | 'waitFor' | 'download'
  | 'expectVisible' | 'expectText' | 'expectCount' | 'done'
/** One test-builder Action (apps/test-builder/src/actions.ts); the jobs-service validates it strictly. */
export interface BrowserStep { action: BrowserActionName; [field: string]: unknown }
export interface BrowserSpec {
  base_url: string
  allowed_hosts?: string[]
  credential_slot?: string | null
  steps?: BrowserStep[]
  expect_per_row?: boolean | null
  writes?: boolean
}

export type ForEachMode = 'sequential' | 'parallel' | 'child_task'
export interface ForEachSpec {
  over: Ref | unknown
  max_rows?: number
  stop_on_fail?: boolean
  /** child_task: coming (create refuses it) */
  mode?: ForEachMode
  /** parallel only, 1..16 */
  concurrency?: number
}
/** The stored spec of a removed kind (approval / person): shown read-only, never edited. */
export type RemovedSpec = Record<string, unknown>
export interface WaitCondition { connection: string; sql: string; params?: Record<string, unknown>; expect?: 'nonempty' | 'empty' }
/** duration: sleep ("30s", "2h"); condition: poll a read-only query until it holds or timeout. Event waits are coming. */
export interface WaitSpec { mode?: 'duration' | 'condition'; duration?: string | null; condition?: WaitCondition | null; poll_every?: string; timeout?: string }
export interface AskSpec { text: string }
export interface NoteSpec { markdown?: string }

export interface SpecByKind {
  query: QuerySpec
  http: HttpSpec
  workflow: WorkflowSpec
  rule: RuleSpec
  change: ChangeSpec
  approval: RemovedSpec
  data: DataSpec
  store: StoreSpec
  browser: BrowserSpec
  for_each: ForEachSpec
  ask: AskSpec
  note: NoteSpec
  person: RemovedSpec
  wait: WaitSpec
}
export type CellSpec = SpecByKind[StoredCellKind]

export interface Guard {
  left: Ref | unknown
  op?: CompareOp | 'nonempty' | 'empty'
  right?: unknown
}

// ---- responses -----------------------------------------------------------------------------------------------------

/** CellResult.output for row-producing cells. Other kinds put a summary object here. */
export interface RowsPreview {
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated?: boolean
  cap?: number | null
  [extra: string]: unknown
}

export interface CellResult {
  id: string
  run_id: string
  cell_id: string
  row_index: number | null
  status: ResultStatus
  output: (RowsPreview | Record<string, unknown>) | null
  output_ref: string | null
  error: CellError | null
  execution_id: string | null
  job_id: string | null
  ms: number | null
  created_at: string
  run_n: number | null
}

export interface Cell<K extends StoredCellKind = StoredCellKind> {
  id: string
  notebook_id: string
  parent_cell_id: string | null
  position: number
  /** c1, c2, ... used in references */
  name: string
  kind: K
  title: string | null
  spec: SpecByKind[K]
  guard: Guard | null
  created_by: string | null
  created_via: CreatedVia
  created_at: string
  updated_at: string
  // computed by the engine
  effect: CellEffect
  needs_ai: boolean
  stale: boolean
  latest: CellResult | null
  children: Cell[] | null
  applied: boolean
  /** a removed kind (approval / person) kept from an older notebook: rendered read-only, skipped by runs */
  removed?: boolean
}

export interface RunStop {
  reason: StopReason
  cell_id: string | null
  applied_at?: string | null
  applied_by?: string | null
  [extra: string]: unknown
}

export interface RunSummary {
  id: string
  notebook_id: string
  n: number
  from_position: number
  status: RunStatus
  stop: RunStop | null
  on_applied: OnApplied
  triggered_by: string | null
  run_as: string | null
  via_agent: string | null
  heartbeat_at: string | null
  started_at: string
  finished_at: string | null
}

export interface Run extends RunSummary { results: CellResult[] }

export interface KeptEntry { target: KeepTarget; workflow_id: string; at: string; by: string | null }

export interface LastRunInfo {
  n: number
  status: RunStatus
  at: string
  triggered_by: string | null
  run_as: string | null
  via_agent: string | null
}

export interface CheckSummary { pass: number; fail: number; last: string[] }

export interface NotebookListItem {
  id: string
  title: string
  status: NotebookStatus
  cell_count: number
  no_ai_count: number
  needs_ai_count: number
  needs_you: boolean
  writes_env: string | null
  last_run: LastRunInfo | null
  kept: KeptEntry[]
  check: CheckSummary | null
  updated_at: string
  created_by: string | null
}

export interface Notebook {
  id: string
  project_id: string
  team_id: string | null
  title: string
  description: string | null
  status: NotebookStatus
  environment: string | null
  template_slug: string | null
  run_as: string | null
  created_by: string | null
  active_run_id: string | null
  kept: KeptEntry[]
  created_at: string
  updated_at: string
  cells: Cell[]
  /** latest 20, newest first */
  runs: RunSummary[]
}

export interface CapabilityFlag { available: boolean; reason: string | null }
export interface Capabilities { ai: CapabilityFlag; browser: CapabilityFlag; writes: { available: boolean } }

export interface NotebookTemplate {
  slug: string
  title: string
  description: string
  icon: string | null
  keeps_as: KeepTarget | null
  cells: { kind: CellKind; title: string | null }[]
}

// ---- requests ------------------------------------------------------------------------------------------------------

export interface NotebookCreate {
  title: string
  template?: string | null
  environment?: string | null
  connection?: string | null
  created_by?: string | null
  description?: string | null
}

export interface NotebookPatch { title?: string; description?: string | null; environment?: string | null; run_as?: string | null }

export interface CellCreate<K extends CellKind = CellKind> {
  kind: K
  title?: string | null
  spec: SpecByKind[K]
  guard?: Guard | null
  parent_cell_id?: string | null
  created_by?: string | null
}

export interface CellPatch { title?: string | null; spec?: CellSpec; guard?: Guard | null; clear_guard?: boolean }
export interface CellPatchResponse { cell: Cell; stale_from: string | null }

export interface RunCreate {
  from_cell_id?: string | null
  on_applied?: OnApplied
  reapply?: string[]
  triggered_by?: string | null
  run_as?: string | null
  via_agent?: string | null
}

export interface ApplyRequest { approved_by: string; idempotency_key?: string | null }
export interface DiscardRequest { by?: string | null }
/** POST /notebook-cells/{id}/retry-rows: rerun a for-each for some rows only (default: the failed rows). */
export interface RetryRowsRequest { rows?: number[] | null; triggered_by?: string | null; run_as?: string | null; via_agent?: string | null }
export interface PreviewCellInputs { cell_id: string; name: string; kind: StoredCellKind; inputs: Record<string, unknown> | null; error: CellError | null }
export interface PreviewRow { i: number; row: unknown; cells: PreviewCellInputs[] }
/** GET /notebook-cells/{id}/preview?n=5: the first n rows' resolved body inputs, nothing run. */
export interface ForEachPreview { total: number; rows: PreviewRow[]; error: CellError | null }
export interface CellActionResponse { result: CellResult; run: RunSummary }

export interface RowsPage { columns: string[]; rows: Record<string, unknown>[]; total: number }
export interface RowsQuery { run?: string; row?: number; offset?: number; limit?: number }

export interface ControlRequest { action: ControlAction }

/** Rows the user ticked in a result table, sent with an Ask ("release the selected wires"). */
export interface AskSelection { cell: string; key: string; keys: (string | number | boolean | null)[] }
export interface AskRequest { text: string; by?: string | null; selection?: AskSelection | null }
export interface AskResponse { ask_cell: Cell<'ask'>; planned: Cell[] }

export interface ReportResponse { markdown: string }

export interface TraceEntry {
  ts: string
  cell_id: string | null
  run_n: number | null
  kind: string
  text: string
  ms: number | null
  execution_id: string | null
  job_id: string | null
}

export interface ArtifactInfo { name: string; size: number; content_type: string }

// ---- keep ----------------------------------------------------------------------------------------------------------

export interface KeepParam { ref: string; mode?: ParamMode; name?: string | null }
/** business_day / mailbox (coming) / after_deploy: engine keep.py KeepTriggerX. */
export type KeepTriggerType = 'manual' | 'cron' | 'business_day' | 'mailbox' | 'after_deploy'
export interface KeepTrigger { type?: KeepTriggerType; cron?: string | null }
export interface KeepSlot { cell_id: string; credential_id: string | null }
export interface KeepWrite { cell_id: string; mode?: WriteMode }

export interface KeepRequest {
  target: KeepTarget
  /** empty = all keepable cells */
  cell_ids?: string[]
  params?: KeepParam[]
  trigger?: KeepTrigger
  slots?: KeepSlot[]
  run_as?: string | null
  writes?: KeepWrite[]
  empty_policy?: EmptyPolicy
  name?: string | null
  by?: string | null
}

export interface KeepChoice { value: unknown; label: string }
export interface KeepItem {
  key: string
  kind: KeepItemKind
  status: KeepItemStatus
  message: string
  cell_id: string | null
  choices: KeepChoice[] | null
}

export interface KeepPreview {
  items: KeepItem[]
  definition: Record<string, unknown>
  can_save: boolean
  can_schedule: boolean
}

export interface KeepResponse { workflow_id: string; target: KeepTarget; schedule_active: boolean; spec_url: string | null; webhook_url?: string | null; table?: string | null }

export interface CheckRun { execution_id: string; status: string; start_time: string | null; ms: number | null }
export interface ChecksResponse { workflow_id: string | null; runs: CheckRun[]; pass: number; fail: number }
