/**
 * Engine contract types (UI-INTEGRATION §3.2 + UI-DESIGN §9/§10 corrections).
 * Shapes verified against the engine on branch revert-to-5684ba5.
 */

export type JsonSchema = Record<string, unknown>

// ── projects ────────────────────────────────────────────────────────────────
export type EnvKind = 'draft' | 'release'
export interface ProjectEnvironment {
  name: string
  kind: EnvKind
}
export interface Project {
  id: string
  name: string
  slug: string
  description: string | null
  environments: ProjectEnvironment[]
  default_environment: string
  live_environment: string
  settings: { egress_allowlist?: string[]; [k: string]: unknown }
  created_at: string
  updated_at: string
}
export type HealthStatus = 'ok' | 'healthy' | 'degraded' | 'failing' | 'unknown'
export interface ProjectCounts {
  apps: number
  workflows: number
  routes: number
  tables: number
  modules?: number
}
export interface LastRun {
  execution_id: string
  status: string
  mode: string
  start_time: string
}
export interface ProjectHealth {
  status: HealthStatus
  runs_24h: number
  failed_24h: number
  p50_ms?: number | null
  reason?: string | null
}
export interface ProjectListItem extends Project {
  counts: ProjectCounts
  last_run: LastRun | null
  health: ProjectHealth
  missing_secrets?: number
  routes_preview?: Array<{ method: HttpMethod; path: string; name: string; handler_type?: string }>
}
export interface ProjectSummary {
  project: Project
  counts: ProjectCounts
  last_run: LastRun | null
  health: ProjectHealth
  missing_secrets: number
  stub_routes?: number
  api_keys?: number
  active_releases?: Record<string, { id: string; number: number; created_at: string } | null>
}
export interface ProjectCreate {
  name: string
  slug?: string
  description?: string
}
export type ProjectUpdate = Partial<
  Pick<Project, 'name' | 'description' | 'slug' | 'environments' | 'default_environment' | 'live_environment' | 'settings'>
>
export interface ProjectUpdatePreview {
  dry_run: true
  affected_apps?: Array<{ app_id: string; name: string; slug: string | null; access?: string }>
  [k: string]: unknown
}

// ── runs ────────────────────────────────────────────────────────────────────
export type TriggerType =
  | 'manual'
  | 'webhook'
  | 'cron'
  | 'retry'
  | 'route_test'
  | 'draft_test'
  | 'generate_self_test'
  | 'app'
  | 'app_preview'
  | 'api_key'
  | 'public'
export interface RunTrigger {
  type?: TriggerType | string
  method?: string
  route?: string
  path?: string
  route_id?: string | null
  app_id?: string | null
  release_id?: string | null
  module_name?: string
  source?: string
  [k: string]: unknown
}
export interface RunListItem {
  id: string
  workflow_id: string
  workflow_name: string
  status: string
  mode: string
  start_time: string
  end_time: string | null
  error_count: number
  environment: string | null
  trigger: RunTrigger | null
  route_id: string | null
  route: string | null
  app_id: string | null
  duration_ms: number | null
  /** HTTP status the route answered with; null for runs that were not a route call */
  status_code: number | null
}
/** `GET …/runs/daily`: one entry per UTC day, oldest first, zero-filled */
export interface DailyRuns {
  date: string
  total: number
  failed: number
}
export interface BrokerCall {
  id: string
  execution_id: string | null
  project_id: string
  environment: string
  origin: string
  node_type: string
  function?: string
  parameters?: unknown
  params_redacted?: unknown
  status: 'ok' | 'denied' | 'error'
  error_code: string | null
  error: string | null
  duration_ms: number
  created_at: string
  [k: string]: unknown
}
export interface SandboxTrace {
  duration_ms: number
  exit_code: number | null
  stdout: string
  stderr: string
  truncated: boolean
  timed_out?: boolean
}
export interface RunTrace {
  sandbox?: SandboxTrace | null
  broker_calls?: BrokerCall[]
}
export interface RunError {
  type: string
  message: string
  traceback?: string | null
  line?: number | null
  module?: string | null
  code?: string | null
  table?: string | null
}
export interface RunDetail extends RunListItem {
  errors: Array<{ node_name: string; error: string; timestamp?: string; error_code?: string | null; details?: unknown }>
  node_data: Record<string, Array<{ json: unknown }>>
  node_metrics: Record<string, unknown>
  metadata: {
    request?: unknown
    response?: unknown
    error?: RunError | null
    trigger?: RunTrigger
    [k: string]: unknown
  } | null
  trace: RunTrace | null
}

// ── secrets ─────────────────────────────────────────────────────────────────
export type VarType = 'secret' | 'string' | 'number'
export interface EffectiveVar {
  id: number
  key: string
  environment: string
  type: VarType
  value: string | null
  description: string | null
  scope: 'project' | 'team'
  overrides_team: boolean
  overridden: boolean
  allowed_hosts: string[] | null
  updated_at: string
}
export interface SecretCreate {
  key: string
  value: string
  environment: string
  type: VarType
  description?: string
  allowed_hosts?: string[]
}
export interface SecretUpdate {
  value?: string
  description?: string
  allowed_hosts?: string[] | null
}
export type SecretRef =
  | { kind: 'workflow'; id: string; name: string; node: string }
  | { kind: 'module'; id: string; name: string; line: number }
export interface MissingSecret {
  key: string
  missing_in: string[]
  referenced_by: SecretRef[]
}

// ── sdk ─────────────────────────────────────────────────────────────────────
export interface SdkFunction {
  function: string
  node_type: string
  description: string
  parameters: JsonSchema
  returns?: JsonSchema
  egress: boolean
  python_names?: Record<string, string>
  [k: string]: unknown
}
export interface SdkReferenceParam {
  name: string
  annotation: string | null
  default: unknown
  required: boolean
  keyword_only: boolean
}
export interface SdkReferenceEntry {
  name: string
  kind: string
  signature: string
  params?: SdkReferenceParam[]
  doc?: string
  [k: string]: unknown
}
export interface SdkReference {
  version: string
  entries: SdkReferenceEntry[]
  rules?: string[] | string
  [k: string]: unknown
}
export interface BrokerToken {
  token: string
  expires_at: string
}
export type SandboxStatus =
  | { backend: string; available: true; version?: string }
  | { backend: string; available: false; reason?: string }

// ── routes ──────────────────────────────────────────────────────────────────
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
export type RouteAuth = 'none' | 'app_token' | 'api_key'
export type ResponseMode = 'auto' | 'first_item' | 'all_items'
export type RouteHandler =
  | { type: 'workflow'; workflow_id: string; name?: string }
  | { type: 'stub' }
  | { type: 'python'; module_id: string; function: string; line?: number; module_name?: string }
export interface Route {
  id: string
  project_id: string
  name: string
  method: HttpMethod
  path: string
  auth: RouteAuth
  description: string | null
  request_schema: JsonSchema | null
  response_schema: JsonSchema | null
  response_mode: ResponseMode
  validate_request: boolean
  timeout_s?: number
  handler: RouteHandler
  source: 'ui' | 'code' | 'app_stub'
  used_by_count: number
  url: string
  created_at: string
  updated_at: string
}
export interface ChangeAffected {
  app_id: string
  app_name: string
  file: string
  line: number
}
export interface Change {
  kind: string
  route: string
  operation: string
  field?: string
  breaking: boolean
  affected: ChangeAffected[]
}
export interface RouteWriteResult {
  route: Route
  spec_revision: number
  breaking_changes: Change[]
}
export interface RouteTestRequest {
  params?: Record<string, string>
  query?: Record<string, string>
  headers?: Record<string, string>
  body?: unknown
  environment: string
  apply_schemas?: boolean
}
export interface RouteTestResult {
  status: number
  headers: Record<string, string>
  body: unknown
  duration_ms: number
  execution_id: string | null
  inferred?: { request_schema: JsonSchema; response_schema: JsonSchema } | null
  applied?: boolean
  error: RunError | null
  trace: RunTrace | null
  spec_revision?: number
}
export interface SpecRevision {
  revision: number
  spec_hash: string
  created_at: string
  changes: Change[]
}
export interface ApiKey {
  id: string
  name: string
  environment: string
  prefix: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}
export interface ApiKeyCreated extends ApiKey {
  key: string
}
export interface UsedBy {
  app_id: string
  app_name: string
  version_id: number
  file: string
  line: number
  operation: string
}

// ── app ↔ api ───────────────────────────────────────────────────────────────
export interface PreviewToken {
  token: string
  expires_at: string
  environment: string
}
export type ApiUsageStatus = 'ok' | 'missing' | 'changed' | 'stub'
export interface ApiUsage {
  operation: string
  method: string | null
  path: string | null
  file: string
  line: number
  route_id: string | null
  status: ApiUsageStatus
  message?: string | null
}

// ── data ────────────────────────────────────────────────────────────────────
export type ColumnType = 'string' | 'number' | 'integer' | 'boolean' | 'datetime' | 'json'
export interface Column {
  name: string
  type: ColumnType
  required?: boolean
  default?: unknown
}
export interface DataTable {
  id: string
  project_id: string
  name: string
  description: string | null
  columns: Column[]
  row_count?: number
  created_at: string
  updated_at: string
}
export interface Row {
  id: number
  data: Record<string, unknown>
  created_at: string
  updated_at: string
}
export interface RowPage {
  rows: Row[]
  next_cursor: string | null
  count?: number
}
export type RowFilter = Record<string, unknown>

// ── modules ─────────────────────────────────────────────────────────────────
export interface CodeModule {
  id: string
  project_id: string
  name: string
  source?: string
  source_sha: string
  created_at: string
  updated_at: string
  route_count?: number
}
export interface PlannedRoute {
  id?: string
  method: HttpMethod
  path: string
  name: string
  function: string
  line?: number
  auth?: RouteAuth
}
export interface DryRunResult {
  dry_run: true
  routes: { created: PlannedRoute[]; updated: PlannedRoute[]; removed: PlannedRoute[] }
  breaking_changes: Change[]
  spec_revision: null
  spec_hash: null
  errors?: Array<{ line: number; column?: number | null; message: string }>
}
export interface ModuleWriteResult {
  module: CodeModule
  routes: { created: PlannedRoute[]; updated: PlannedRoute[]; removed: PlannedRoute[] }
  errors: Array<{ line: number; message: string }>
  spec_revision: number
  spec_hash: string
  breaking_changes: Change[]
}

// ── generate / run-draft (UI-DESIGN §10) ────────────────────────────────────
export interface DeclaredTable {
  name: string
  columns: Column[]
}
export interface AnalysisTable {
  name: string
  exists: boolean
  columns: Column[] | null
  lines: number[]
}
export interface AnalysisSecret {
  key: string
  present_in: string[]
  missing_in: string[]
  allowed_hosts: string[] | null
  bound: boolean
  lines: number[]
}
export interface AnalysisProblem {
  code: string
  line: number | null
  column?: number | null
  message: string
}
export interface GenerateAnalysis {
  attempt?: number
  routes: { created: PlannedRoute[]; updated: PlannedRoute[]; removed: PlannedRoute[] }
  errors: AnalysisProblem[]
  conflicts: Array<{ kind: 'route' | 'module_name'; [k: string]: unknown }>
  tables: AnalysisTable[]
  secrets: AnalysisSecret[]
  warnings: Array<{ code: string; message: string; line?: number }>
}
export interface SelfTestResult {
  route: string
  ok?: boolean
  status?: number
  duration_ms?: number
  error_code?: string | null
  body_preview?: string
  execution_id?: string | null
  skipped?: 'writes' | 'path_params' | 'limit'
}
export interface GenerateResult {
  module_name: string
  source: string
  summary: string
  attempts: number
  model: string
  analysis: GenerateAnalysis
  self_test: SelfTestResult[] | null
  tables?: DeclaredTable[]
}
export interface GenerateRequest {
  prompt: string
  module_id?: string | null
  module_name?: string | null
  current_source?: string | null
  conversation?: Array<{ role: 'user' | 'assistant'; content: string }>
  self_test?: boolean
}
export interface RunDraftRequest {
  source: string
  module_name: string
  module_id?: string | null
  method: string
  path: string
  query?: Record<string, string>
  headers?: Record<string, string>
  body?: unknown
  virtual_tables?: string[]
}
export interface RunDraftResult {
  status: number
  headers: Record<string, string>
  body: unknown
  duration_ms: number
  execution_id: string | null
  route: { method: string; path: string; name: string; function: string; line: number }
  trace: RunTrace | null
  error: RunError | null
  inferred?: { request_schema: JsonSchema; response_schema: JsonSchema } | null
}

// ── releases ────────────────────────────────────────────────────────────────
export type ReleaseStatus = 'active' | 'superseded' | 'rolled_back' | 'blocked'
export interface ReleaseCheck {
  id: string
  status: 'pass' | 'warn' | 'fail'
  message?: string
  details?: Record<string, unknown>
}
export interface Release {
  id: string
  project_id: string
  environment: string
  number: number
  is_active: boolean
  status: ReleaseStatus
  checks: ReleaseCheck[]
  source_release_id: string | null
  notes: string | null
  created_by: string | null
  created_at: string
  snapshot?: Record<string, unknown>
}
export interface ReleaseDryRun {
  dry_run: true
  checks: ReleaseCheck[]
  diff?: ReleaseDiff
  ok?: boolean
  [k: string]: unknown
}
export interface ReleaseDiff {
  routes: Change[] | Array<Record<string, unknown>>
  apps: Array<{ app_id: string; from_version: number | null; to_version: number | null; name?: string }>
  workflows: Array<{ workflow_id: string; from_version: number | null; to_version: number | null; name?: string }>
  secrets: Array<{ key: string; change: string }>
  modules?: Array<Record<string, unknown>>
  [k: string]: unknown
}
