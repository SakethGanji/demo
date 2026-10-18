/**
 * Typed endpoint functions for the project-centric engine API (UI-DESIGN §11).
 * `p` is a project id or slug (the engine resolves both); hooks always pass the id.
 */

import type { DriftAlert } from '@/features/workflow-editor/lib/testsAi'
import type {
  AiEditResult, DecideResult, Interface, InterfaceState, PreviewData, TaskCounts, TaskDetail, TaskPage,
} from '@/features/interfaces/lib/types'
import { request, requestRaw, requestText, requestBlob } from './http'
import type {
  ApiKey,
  ApiKeyCreated,
  ApiUsage,
  BrokerToken,
  CodeModule,
  Column,
  DataTable,
  DryRunResult,
  EffectiveVar,
  MissingSecret,
  ModuleWriteResult,
  PreviewToken,
  Project,
  ProjectCreate,
  ProjectListItem,
  ProjectSummary,
  ProjectUpdate,
  ProjectUpdatePreview,
  Release,
  ReleaseDiff,
  Route,
  RouteTestRequest,
  RouteTestResult,
  RouteWriteResult,
  Row,
  RowFilter,
  RowPage,
  RunDetail,
  RunDraftRequest,
  RunDraftResult,
  DailyRuns,
  RunListItem,
  SandboxStatus,
  SdkFunction,
  SdkReference,
  SecretCreate,
  SecretUpdate,
  SpecRevision,
  Change,
  UsedBy,
} from './types'
import type { ApiAppDetail, ApiAppListItem } from '@/shared/lib/api'
import type { ApiWorkflowSummary, ApiCreateResponse, BackendWorkflow } from '@/shared/lib/backendTypes'

const enc = encodeURIComponent
const P = (p: string) => `/api/projects/${enc(p)}`

export const projectsApi = {
  list: () => request<ProjectListItem[]>('/api/projects'),
  get: (p: string) => request<Project>(P(p)),
  create: (b: ProjectCreate) => request<Project>('/api/projects', { method: 'POST', body: b }),
  update: (p: string, b: ProjectUpdate) => request<Project>(P(p), { method: 'PUT', body: b }),
  previewUpdate: (p: string, b: ProjectUpdate) =>
    request<ProjectUpdatePreview>(P(p), { method: 'PUT', body: b, query: { dry_run: true } }),
  remove: (p: string) => request<{ success: true }>(P(p), { method: 'DELETE' }),
  summary: (p: string) => request<ProjectSummary>(`${P(p)}/summary`),
  listWorkflows: (p: string) => request<ApiWorkflowSummary[]>(`${P(p)}/workflows`),
  /** Open drift alerts for every workflow in the project (one request). */
  drift: (p: string) => request<{ alerts: DriftAlert[] }>(`${P(p)}/drift`),
  createWorkflow: (p: string, b: BackendWorkflow) =>
    request<ApiCreateResponse>(`${P(p)}/workflows`, { method: 'POST', body: b }),
  listApps: (p: string) => request<ApiAppListItem[]>(`${P(p)}/apps`),
  createApp: (
    p: string,
    b: { name: string; definition: Record<string, unknown>; description?: string; workflow_ids?: string[] },
  ) => request<ApiAppDetail>(`${P(p)}/apps`, { method: 'POST', body: b }),
}

export interface RunsQuery {
  limit?: number
  before?: string
  trigger?: string
  status?: string
  route_id?: string
  environment?: string
  workflow_id?: string
}

export const runsApi = {
  /** Returns the page plus the `X-Next-Cursor` header (exposed via CORS, §9 C1). */
  list: async (p: string, q: RunsQuery = {}): Promise<{ runs: RunListItem[]; nextCursor: string | null }> => {
    const { data, res } = await requestRaw(`${P(p)}/runs`, { query: { limit: 50, ...q } as never })
    return { runs: data as RunListItem[], nextCursor: res.headers.get('X-Next-Cursor') }
  },
  get: (p: string, x: string) => request<RunDetail>(`${P(p)}/runs/${enc(x)}`),
  /** Per-day totals and failures for the last `days` days, computed in SQL (not limited to the newest 100 runs). */
  daily: (p: string, q: { days?: number; environment?: string } = {}) =>
    request<DailyRuns[]>(`${P(p)}/runs/daily`, { query: { days: 30, ...q } as never }),
}

export const secretsApi = {
  list: (p: string, environment?: string) =>
    request<EffectiveVar[]>(`${P(p)}/secrets`, { query: { environment } }),
  create: (p: string, b: SecretCreate) => request<EffectiveVar>(`${P(p)}/secrets`, { method: 'POST', body: b }),
  update: (p: string, id: number, b: SecretUpdate) =>
    request<EffectiveVar>(`${P(p)}/secrets/${id}`, { method: 'PUT', body: b }),
  remove: (p: string, id: number) => request<{ success: true }>(`${P(p)}/secrets/${id}`, { method: 'DELETE' }),
  missing: (p: string, environment?: string) =>
    request<MissingSecret[]>(`${P(p)}/secrets/missing`, { query: { environment } }),
}

export const sdkApi = {
  functions: () => request<SdkFunction[]>('/api/sdk/functions'),
  reference: () => request<SdkReference>('/api/sdk/reference'),
  pythonZip: () => requestBlob('/api/sdk/python.zip'),
  mintToken: (p: string, environment: string) =>
    request<BrokerToken>(`${P(p)}/broker-tokens`, { method: 'POST', body: { environment } }),
}

export const sandboxApi = {
  status: () => request<SandboxStatus>('/api/sandbox/status'),
}

export const routesApi = {
  list: (p: string) => request<Route[]>(`${P(p)}/routes`),
  get: (p: string, r: string) => request<Route>(`${P(p)}/routes/${enc(r)}`),
  update: (p: string, r: string, b: Partial<Route>, dryRun = false) =>
    request<RouteWriteResult>(`${P(p)}/routes/${enc(r)}`, { method: 'PUT', body: b, query: { dry_run: dryRun || undefined } }),
  test: (p: string, r: string, b: RouteTestRequest) =>
    request<RouteTestResult>(`${P(p)}/routes/${enc(r)}/test`, { method: 'POST', body: b }),
  usedBy: (p: string, r: string) => request<UsedBy[]>(`${P(p)}/routes/${enc(r)}/used-by`),
  createStubs: (p: string, b: { app_id?: string; routes: Array<Record<string, unknown>> }) =>
    request<{ created: Route[]; existing: Route[] }>(`${P(p)}/routes/stubs`, { method: 'POST', body: b }),
}

export const specApi = {
  openapi: (p: string, environment?: string) =>
    request<Record<string, unknown>>(`${P(p)}/openapi.json`, { query: { environment } }),
  clientTs: (p: string, environment?: string) => requestText(`${P(p)}/client.ts`, { query: { environment } }),
  revisions: (p: string) => request<SpecRevision[]>(`${P(p)}/spec/revisions`),
  diff: (p: string, from: number, to: number) =>
    request<{ changes: Change[] }>(`${P(p)}/spec/diff`, { query: { from, to } }),
}

export const apiKeysApi = {
  list: (p: string) => request<ApiKey[]>(`${P(p)}/api-keys`),
  create: (p: string, b: { name: string; environment: string }) =>
    request<ApiKeyCreated>(`${P(p)}/api-keys`, { method: 'POST', body: b }),
  revoke: (p: string, id: string) => request<unknown>(`${P(p)}/api-keys/${enc(id)}`, { method: 'DELETE' }),
}

export const appApiApi = {
  previewToken: (p: string, appId: string) =>
    request<PreviewToken>(`${P(p)}/apps/${enc(appId)}/preview-token`, { method: 'POST' }),
  apiUsage: (appId: string) => request<ApiUsage[]>(`/api/apps/${enc(appId)}/api-usage`),
}

export interface RowsQuery {
  limit?: number
  cursor?: string | null
  filter?: RowFilter | null
  sort?: string
  count?: boolean
}

export const tablesApi = {
  list: (p: string) => request<DataTable[]>(`${P(p)}/tables`),
  get: (p: string, t: string) => request<DataTable>(`${P(p)}/tables/${enc(t)}`),
  create: (p: string, b: { name: string; columns: Column[]; description?: string }) =>
    request<DataTable>(`${P(p)}/tables`, { method: 'POST', body: b }),
  update: (p: string, t: string, b: { name?: string; columns?: Column[]; description?: string }) =>
    request<DataTable>(`${P(p)}/tables/${enc(t)}`, { method: 'PUT', body: b }),
  remove: (p: string, t: string) => request<unknown>(`${P(p)}/tables/${enc(t)}`, { method: 'DELETE' }),
  rows: (p: string, t: string, q: RowsQuery = {}) =>
    request<RowPage>(`${P(p)}/tables/${enc(t)}/rows`, {
      query: {
        limit: q.limit ?? 50,
        cursor: q.cursor ?? undefined,
        filter: q.filter && Object.keys(q.filter).length ? JSON.stringify(q.filter) : undefined,
        sort: q.sort,
        count: q.count ? true : undefined,
      },
    }),
  insert: (p: string, t: string, data: Record<string, unknown>) =>
    request<Row>(`${P(p)}/tables/${enc(t)}/rows`, { method: 'POST', body: { data } }),
  insertMany: (p: string, t: string, rows: Record<string, unknown>[]) =>
    request<{ inserted: number; rows: Row[] }>(`${P(p)}/tables/${enc(t)}/rows`, { method: 'POST', body: { rows } }),
  patchRow: (p: string, t: string, id: number, data: Record<string, unknown>) =>
    request<Row>(`${P(p)}/tables/${enc(t)}/rows/${id}`, { method: 'PATCH', body: { data } }),
  deleteRow: (p: string, t: string, id: number) =>
    request<unknown>(`${P(p)}/tables/${enc(t)}/rows/${id}`, { method: 'DELETE' }),
}

export const modulesApi = {
  codeMap: (p: string, source: string, moduleId: string | null, signal?: AbortSignal) =>
    request<import('./codeMapTypes').CodeMapResult>(`${P(p)}/modules/code-map`, { method: 'POST', body: { source, module_id: moduleId }, signal }),
  list: (p: string) => request<CodeModule[]>(`${P(p)}/modules`),
  get: (p: string, m: string) => request<CodeModule>(`${P(p)}/modules/${enc(m)}`),
  create: (p: string, b: { name: string; source: string }) =>
    request<ModuleWriteResult>(`${P(p)}/modules`, { method: 'POST', body: b }),
  createDryRun: (p: string, b: { name: string; source: string }, signal?: AbortSignal) =>
    request<DryRunResult>(`${P(p)}/modules`, { method: 'POST', body: b, query: { dry_run: true }, signal }),
  update: (p: string, m: string, b: { source?: string; base_sha?: string | null; name?: string }) =>
    request<ModuleWriteResult>(`${P(p)}/modules/${enc(m)}`, { method: 'PUT', body: b }),
  updateDryRun: (p: string, m: string, b: { source: string; base_sha?: string | null }, signal?: AbortSignal) =>
    request<DryRunResult>(`${P(p)}/modules/${enc(m)}`, { method: 'PUT', body: b, query: { dry_run: true }, signal }),
  remove: (p: string, m: string) => request<unknown>(`${P(p)}/modules/${enc(m)}`, { method: 'DELETE' }),
  removeDryRun: (p: string, m: string) =>
    request<{ routes: { removed: Array<Record<string, unknown>> }; breaking_changes: Change[] }>(
      `${P(p)}/modules/${enc(m)}`,
      { method: 'DELETE', query: { dry_run: true } },
    ),
  runDraft: (p: string, b: RunDraftRequest, signal?: AbortSignal) =>
    request<RunDraftResult>(`${P(p)}/modules/run-draft`, { method: 'POST', body: b, signal }),
}

export const releasesApi = {
  list: (p: string, environment?: string) => request<Release[]>(`${P(p)}/releases`, { query: { environment } }),
  get: (p: string, rel: string) => request<Release>(`${P(p)}/releases/${enc(rel)}`),
  create: (p: string, b: { environment: string; notes?: string }, dryRun = false) =>
    request<Release & Record<string, unknown>>(`${P(p)}/releases`, {
      method: 'POST',
      body: b,
      query: { dry_run: dryRun || undefined },
    }),
  promote: (p: string, rel: string, b: { to_environment: string; notes?: string }, dryRun = false) =>
    request<Release & Record<string, unknown>>(`${P(p)}/releases/${enc(rel)}/promote`, {
      method: 'POST',
      body: b,
      query: { dry_run: dryRun || undefined },
    }),
  rollback: (p: string, env: string, b: { release_id?: string } = {}) =>
    request<Release & Record<string, unknown>>(`${P(p)}/environments/${enc(env)}/rollback`, { method: 'POST', body: b }),
  diff: (p: string, rel: string, against = 'active') =>
    request<ReleaseDiff>(`${P(p)}/releases/${enc(rel)}/diff`, { query: { against } }),
}

// ── Stage 5: interfaces and approval tasks (DESIGN-STAGE5-UIS §7.1, internal / studio) ──

const W = (id: string) => `/api/workflows/${enc(id)}`

export interface TaskQuery {
  status?: 'open' | 'closed' | 'all'
  workflow_id?: string
  assignee?: string
  cursor?: string | null
  limit?: number
}

export const interfacesApi = {
  get: (wf: string) => request<InterfaceState>(`${W(wf)}/interface`),
  /** Full replace; `null` removes the interface. */
  put: (wf: string, b: Interface | null) => request<Omit<InterfaceState, 'live'>>(`${W(wf)}/interface`, { method: 'PUT', body: b }),
  /** Not saved: the studio PUTs the result. */
  generate: (wf: string, b: { kind: Interface['kind']; slug?: string }) =>
    request<{ interface: Interface }>(`${W(wf)}/interface/generate`, { method: 'POST', body: b }),
  previewSubmit: (wf: string, fields: Record<string, unknown>, idempotencyKey: string) =>
    request<{ execution_id: string; submission_id: string }>(`${W(wf)}/interface/preview/submit`, {
      method: 'POST',
      body: { fields },
      headers: { 'Idempotency-Key': idempotencyKey },
    }),
  previewData: (wf: string, executionId?: string | null) =>
    request<PreviewData>(`${W(wf)}/interface/preview/data`, { query: { execution_id: executionId ?? undefined } }),
  aiEdit: (wf: string, screenId: string, b: { instruction: string; spec: unknown }) =>
    request<AiEditResult>(`${W(wf)}/interface/screens/${enc(screenId)}/ai-edit`, { method: 'POST', body: b }),
  clientTs: (wf: string) => requestText(`${W(wf)}/interface/client.ts`),
  promote: async (wf: string) => {
    const { data, res } = await requestRaw(`${W(wf)}/interface/promote`, { method: 'POST', body: {} })
    return { ...(data as { app_id: string; app_url: string }), created: res.status === 201 }
  },
}

export const tasksApi = {
  list: (p: string, q: TaskQuery = {}) =>
    request<TaskPage>('/api/tasks', { query: { project_id: p, status: 'open', limit: 25, ...q } as never }),
  get: (id: string) => request<TaskDetail>(`/api/tasks/${enc(id)}`),
  counts: (p: string) => request<TaskCounts>('/api/tasks/counts', { query: { project_id: p } }),
  /** `Idempotency-Key` is required; one key per page view makes a double click a replay (§5.4). */
  decide: (
    id: string,
    b: { decision: 'approved' | 'rejected'; comment?: string; form_data?: Record<string, unknown>; decided_by: string },
    idempotencyKey: string,
  ) =>
    request<DecideResult>(`/api/tasks/${enc(id)}/decide`, {
      method: 'POST',
      body: b,
      headers: { 'Idempotency-Key': idempotencyKey },
    }),
}
