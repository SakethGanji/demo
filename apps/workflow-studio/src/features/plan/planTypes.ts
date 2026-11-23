/**
 * The project plan as the engine stores it (`/api/projects/{p}/plan*`): one versioned, approved description of the whole
 * project (requirements, data model, modules and routes, apps and screens, integrations, environments) that every builder
 * builds against. Shapes follow `services/project_plan.normalize` on the engine.
 */

export type ColumnType = 'string' | 'number' | 'integer' | 'boolean' | 'datetime' | 'json'
export const COLUMN_TYPES: ColumnType[] = ['string', 'number', 'integer', 'boolean', 'datetime', 'json']

export interface PlanColumn {
  name: string
  type: ColumnType | string
  required: boolean
  default?: unknown
  unique?: boolean
  index?: boolean
  enum?: string[]
  ref?: string
  description?: string
  deprecated?: boolean
}

export interface PlanTransition {
  from: string
  to: string
  route?: string
  who?: string
  effect?: string
}

export interface PlanTable {
  name: string
  owner: string | null
  writers: string[]
  description: string
  columns: PlanColumn[]
  indexes: string[][]
  business_key: string[]
  audit: boolean
  soft_delete: boolean
  retention: { policy: 'keep' | 'days' | 'last_n'; value: number | null }
  state_machine: { field: string; initial: string; final: string[]; transitions: PlanTransition[] } | null
  growth_note?: string
}

export interface PlanModule {
  key: string
  kind: 'shared' | 'routes'
  purpose: string
  depends_on: string[]
  owns: string[]
  reads: string[]
  integrations: string[]
  helpers: Array<{ name: string; signature: string; purpose: string }>
}

export interface PlanRouteField {
  name: string
  type?: string
  required?: boolean
}

export interface PlanRoute {
  key: string
  module: string
  purpose: string
  auth: string
  request: { fields: PlanRouteField[] }
  response: { fields: PlanRouteField[] }
  errors: Array<{ status: number; code: string }>
  idempotency: string
  pagination: boolean | null
  integrations: string[]
  tables: { reads: string[]; writes: string[] }
}

export interface PlanScreen {
  name: string
  purpose: string
  routes: string[]
  states: Record<string, string>
  journeys: string[]
}

export interface PlanApp {
  key: string
  name: string
  app_id: string | null
  users: string
  screens: PlanScreen[]
}

export interface PlanAnswer {
  id: string
  topic: string
  question: string
  answer: string
  round: number
  source: 'model' | 'check' | 'domain' | 'plan'
}

export interface PlanBody {
  v: number
  title: string
  summary: string
  requirements: {
    /** the first message, as the user typed it (plans drafted before this was kept have none) */
    request?: string
    answers: PlanAnswer[]
    assumptions: string[]
    limitations: string[]
    acceptance: Array<{ id: string; text: string; covers: string[] }>
  }
  roles_note: string
  environments: Array<{ name: string; base_url: string; live: boolean; refused_actions: string[] }>
  secrets: Array<{ name: string; purpose: string; environments: string[]; allowed_hosts: string[] }>
  integrations: Array<{
    key: string
    kind: string
    purpose: string
    job_types: string[]
    contract: Record<string, unknown> | null
    secrets: string[]
    hosts: string[]
    smoke: Record<string, unknown> | null
  }>
  data: { conventions: Record<string, unknown>; tables: PlanTable[] }
  modules: PlanModule[]
  routes: PlanRoute[]
  apps: PlanApp[]
  nfr: Record<string, unknown>
  build_order: string[]
  migrations: Array<Record<string, unknown>>
}

/** A validation issue: `path` names the item (`data.tables.runs.columns.status`, `routes.POST /runs`, `secrets.NAME`). */
export interface PlanIssue {
  code: string
  path: string
  message: string
  severity: 'error' | 'warning'
  needs_input: boolean
  [extra: string]: unknown
}

export interface PlanQuestion {
  id: string
  topic: string
  question: string
  why?: string
  options?: string[]
  default?: string | null
  source?: string
  parts?: string[]
  /** the heading it sits under in the checklist ("People and access", "Data", …) */
  group?: string
  /** pick several options (checkboxes) instead of one (radios) */
  multi?: boolean
  /** the options preselected; the answer is them joined by "; " */
  suggested?: string[]
  /** a free-text "other" is allowed next to the options */
  other?: boolean
}

export type PlanStatus = 'draft' | 'approved' | 'superseded'
export type PlanPhase = 'questions' | 'review' | 'approved' | 'superseded'

export interface PlanVersion {
  id: string
  project_id: string
  version: number
  status: PlanStatus
  phase: PlanPhase
  base_version: number | null
  body: PlanBody
  questions: PlanQuestion[]
  round: number
  created_by: string | null
  approved_by: string | null
  approved_at: string | null
  created_at: string
  updated_at: string
  issues?: PlanIssue[]
}

export type PlanVersionSummary = Omit<PlanVersion, 'body'> & { title: string }

export interface PlanState {
  approved: PlanVersion | null
  draft: PlanVersion | null
  versions: PlanVersionSummary[]
}

export interface PlanChange {
  kind: string
  key: string
  change: string
  breaking: boolean
  [detail: string]: unknown
}

export interface PlanDiffResult {
  changes: PlanChange[]
  breaking: boolean
  affected: { modules: string[]; apps: string[]; tables: string[] }
}

export interface PlanMigration {
  version?: number
  table: string
  ops: Array<Record<string, unknown>>
  refused: Array<Record<string, unknown> & { reason?: string }>
}

export type ApproveResult = PlanVersion & {
  diff: PlanDiffResult
  migrations: PlanMigration[]
  knowledge: Record<string, number>
}

export type ReviseResult = PlanVersion & { diff: PlanDiffResult; affected: PlanDiffResult['affected'] }

/** `POST /plan/draft` result (JSON mode). */
export interface PlanTiming {
  step: string
  wall_s: number
  model_calls: number
  model_s: number
  plan_bytes: number
  calls: Array<{ tool: string; s: number }>
}

export interface DraftResult {
  plan: PlanVersion | ReviseResult
  scrubbed?: boolean
  /** planner wall time and model calls for this step */
  timing?: PlanTiming
  diff?: PlanDiffResult
  affected?: PlanDiffResult['affected']
}

/** `POST /plan/check` (the builder gates): how much of the approved plan the saved project delivers. */
export interface PlanCheck {
  has_plan: boolean
  ok: boolean
  plan_version?: number
  missing?: {
    modules: string[]
    routes: Array<{ route: string; module?: string | null; reason: string }>
    [k: string]: unknown
  }
  stale?: Array<{ module: string; built_against: number; approved: number; affected_by: string }>
  [k: string]: unknown
}

export type ModuleBuildState = 'built' | 'partial' | 'stale' | 'not built' | 'unknown'
