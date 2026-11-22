// LB-C: the labeling API (docs/plans/labeling.md §6.2): label layers on a dataset root, their carry-over status on a
// version, events, tasks and queues, QA and agreement, LLM labeling, snapshots, materialise and proposals.
// Shapes follow §3.1; every field the UI does not strictly need is optional so the screens survive small changes.
import { request } from './client'

const e = encodeURIComponent
const json = <T,>(path: string, method: string, body: unknown, timeoutMs = 30_000) =>
  request<T>(path, { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs })
/** Some list routes answer `{items: [...]}` / `{layers: [...]}`, others a bare array: accept both. */
const list = <T,>(key: string) => (r: unknown): T[] => (Array.isArray(r) ? r : ((r as Record<string, unknown>)?.[key] ?? (r as Record<string, unknown>)?.items ?? [])) as T[]

export type LayerKind = 'labels' | 'corrections' | 'outcomes'
export type LabelClass = { value: string; label?: string; hotkey?: string; description?: string }
export type SchemaSpec = { task_type?: string; column?: string; columns?: string[]; classes?: (LabelClass | string)[]; multi?: boolean; kind?: string; [k: string]: unknown }
export type LabelSchema = { id?: string; version: number; spec: SchemaSpec; guidelines?: string; guidelines_sha256?: string; created_at?: string; created_by?: string }
export type Layer = {
  id: string; root_id: string; table_name: string; team_id?: string; name: string; purpose?: string; kind: LayerKind
  is_default?: boolean | number; state: 'active' | 'frozen' | 'archived'; next_seq?: number; created_at?: string; created_by?: string
  schema?: LabelSchema | null; schema_version?: number | null; n_events?: number
}
/** One column's carry-over status on a version (§5.2). */
export type ColStatus = { column: string; live?: number; stale?: number; conflict?: number; orphaned?: number; unlabelled?: number; base_changed?: number; total?: number; labelled?: number }
export type LayerStatus = {
  layer_id?: string; version_id: string; rows?: number; columns?: ColStatus[]
  live?: number; stale?: number; conflict?: number; orphaned?: number; unlabelled?: number; base_changed?: number
  by_source?: Record<string, number>; computed_in_ms?: number; note?: string
}
export type LabelEvent = { id?: string; seq?: number; row_id: number; column_name?: string; column?: string; kind?: string; value: unknown; source?: string; actor?: string
  confidence?: string | number | null; created_at?: string; stale?: boolean; task_id?: string | null; provenance?: Record<string, unknown> }

export type TaskType = 'class' | 'multi_label' | 'number' | 'text' | 'cell_correction'
export type Task = {
  id: string; layer_id: string; name: string; task_type: TaskType | string; consensus?: string; schema_version?: number
  columns?: string[]; context?: string[]; columns_json?: string; context_json?: string
  votes_per_row?: number; gold_share?: string | number; blind_share?: string | number; show_prelabel?: boolean | number; review?: string
  min_gold_accuracy?: string | number; state: string; created_at?: string; created_by?: string
  members?: TaskMember[]; progress?: TaskProgress; guidelines?: string; classes?: (LabelClass | string)[]
}
export type TaskMember = { principal_id: string; display_name?: string; role: 'annotator' | 'reviewer'; guest?: boolean; paused_at?: string | null; paused_reason?: string | null }
export type TaskProgress = { total?: number; open?: number; leased?: number; done?: number; skipped?: number; disputed?: number; gold?: number; labelled_rows?: number }
export type TaskCreate = {
  name: string; task_type: TaskType; columns: string[]; context: string[]; consensus?: string; votes_per_row?: number
  gold_share?: number; blind_share?: number; show_prelabel?: boolean; review?: 'none' | 'disputed' | 'all'; min_gold_accuracy?: number
  schema?: { spec: SchemaSpec; guidelines: string }; by?: string
}
export type Fill = { strategy: string; n: number; version_id: string; run_id?: string | null }
/** A leased item (`POST /label-tasks/{t}/next`): the row's context, masked for the viewer, and a pre-label when allowed. */
export type Item = {
  assignment_id: string; row_id: number; version_id?: string; item_key?: string; reason?: string
  context: Record<string, unknown>; masked?: string[]; prelabel?: { value: unknown; source?: string; confidence?: number | string | null } | null
  is_gold?: boolean; is_blind?: boolean; leased_until?: string; current?: unknown
}
export type Answer = { assignment_id: string; value?: unknown; skip?: boolean; flag?: boolean; note?: string; ms?: number }
export type Quality = {
  task_id?: string; kappa?: number | null; fleiss_kappa?: number | null; pairwise?: { a: string; b: string; kappa: number | null; n: number }[]
  annotators?: { principal_id: string; display_name?: string; items?: number; per_hour?: number | null; gold_n?: number; gold_correct?: number
    gold_accuracy?: number | null; gold_ci?: [number, number] | null; kappa?: number | null; anchoring?: boolean | number | null; paused?: boolean; paused_reason?: string | null }[]
  agreement?: number | null; n_items?: number; disputed?: number; confusion?: unknown
}
export type ReviewItem = { assignment_id?: string; row_id: number; column?: string; kind?: string; context?: Record<string, unknown>; masked?: string[]
  votes: { actor: string; display_name?: string; value: unknown; source?: string; gold_accuracy?: number | null; created_at?: string }[]; you_voted?: boolean; gold?: unknown; reason?: string }

export type Prompt = { id: string; layer_id: string; name: string; version: number; model: string; effort?: string; template: string; context?: string[]; context_json?: string
  prompt_sha256?: string; calibration?: Calibration | null; created_at?: string; created_by?: string }
export type Calibration = { n?: number; correct?: number; accuracy?: number | null; ci?: [number, number] | null; wilson?: [number, number] | null; tau?: number | null; tau_star?: number | null
  coverage_at_tau?: number | null; accuracy_at_tau?: number | null; per_class?: Record<string, { n: number; accuracy: number | null }>; reliability?: { bin: number; conf: number; acc: number; n: number }[]; run_id?: string }
export type LlmPolicy = { enabled?: boolean; destination?: string; destinations?: string[]; reason?: string; max_usd_per_run?: number; send_masked?: boolean }
export type LlmPii = { sent?: string[]; excluded?: string[]; masked?: string[]; reason?: Record<string, string>; unmasked_request?: { id: string; status: string } | null }
export type LlmRun = {
  id: string; prompt_id: string; job_id?: string; purpose: 'preview' | 'calibrate' | 'label'; status: string; destination: string; mode?: string; samples_k?: number
  columns_sent?: string[] | string; columns_excluded?: string[]; max_usd?: string | number; est_usd?: string | number | null; spent_usd?: string | number
  tokens_in?: number; tokens_out?: number; tokens_cached?: number; rows_total?: number | null; rows_done?: number; rows_cached?: number; rows_failed?: number; pii_redactions?: number
  created_at?: string; created_by?: string; finished_at?: string | null; calibration?: Calibration | null; results?: { row_id: number; value: unknown; confidence?: number | null; gold?: unknown }[]
  accepted?: number; sent_to_humans?: number; error?: string | null
}
export type Snapshot = {
  id: string; layer_id: string; through_seq: number; schema_version?: number; policy: unknown; policy_sha256?: string; blob_sha256?: string
  snapshot_sha256: string; values_sha256?: string; counts?: { applied?: number; disputed?: number; prelabel_only?: number; by_source?: Record<string, number>; by_status?: Record<string, number> } | string
  state: 'draft' | 'approved' | 'superseded' | 'purged'; created_at: string; created_by: string; approved_by?: string | null; approved_at?: string | null; job_id?: string
}
export type Proposal = { id: string; layer_id: string; origin: string; base_version_id: string; n_cells: number; reason: string; status: string
  proposed_by: string; proposed_at: string; decided_by?: string | null; decided_at?: string | null; comment?: string; applied_version_id?: string | null
  cells?: { row_id: number; column: string; old?: unknown; new?: unknown; masked?: boolean }[] }
export type Job = { job_id?: string; id?: string; status?: string; snapshot_id?: string; version_id?: string; run_id?: string; [k: string]: unknown }

const by = (b?: string) => (b ? `${'?'}by=${e(b)}` : '')

export const labels = {
  layers: (root: string) => request<unknown>(`/datasets/${e(root)}/label-layers`).then(list<Layer>('layers')),
  createLayer: (root: string, body: { table: string; name: string; kind: LayerKind; purpose?: string }, b: string) =>
    json<Layer>(`/datasets/${e(root)}/label-layers`, 'POST', { ...body, by: b }),
  layer: (l: string) => request<Layer>(`/label-layers/${e(l)}`),
  patchLayer: (l: string, body: { state?: string; name?: string }, b: string) => json<Layer>(`/label-layers/${e(l)}`, 'PATCH', { ...body, by: b }),
  addSchema: (l: string, spec: SchemaSpec, guidelines: string, b: string) => json<LabelSchema>(`/label-layers/${e(l)}/schemas`, 'POST', { spec, guidelines, by: b }),
  status: (l: string, versionId: string) => request<LayerStatus>(`/label-layers/${e(l)}/status?version_id=${e(versionId)}`, { timeoutMs: 60_000 }),
  events: (l: string, afterSeq = 0, limit = 50) => request<unknown>(`/label-layers/${e(l)}/events?after_seq=${afterSeq}&limit=${limit}`).then(list<LabelEvent>('events')),
  append: (l: string, events: Partial<LabelEvent>[], b: string, versionId?: string) =>
    json<{ seqs?: number[]; appended?: number }>(`/label-layers/${e(l)}/events`, 'POST', { events, by: b, ...(versionId ? { version_id: versionId } : {}) }),
  row: (l: string, rowId: number) => request<{ row_id: number; events: LabelEvent[]; resolved?: unknown; stale?: boolean }>(`/label-layers/${e(l)}/rows/${rowId}`),

  tasks: (l: string) => request<unknown>(`/label-layers/${e(l)}/tasks`).then(list<Task>('tasks')),
  createTask: (l: string, body: TaskCreate) => json<Task>(`/label-layers/${e(l)}/tasks`, 'POST', body),
  task: (t: string) => request<Task>(`/label-tasks/${e(t)}`),
  patchTask: (t: string, body: Record<string, unknown>, b: string) => json<Task>(`/label-tasks/${e(t)}`, 'PATCH', { ...body, by: b }),
  addMember: (t: string, name: string, role: 'annotator' | 'reviewer', b: string, guest = false) =>
    json<unknown>(`/label-tasks/${e(t)}/members`, 'POST', { name, principal: name, role, guest, by: b }),
  removeMember: (t: string, p: string, b: string) => request<unknown>(`/label-tasks/${e(t)}/members/${e(p)}${by(b)}`, { method: 'DELETE' }),
  setGold: (t: string, items: { row_id: number; column: string; value: unknown; pool?: string }[], b: string) => json<unknown>(`/label-tasks/${e(t)}/gold`, 'POST', { items, by: b }),
  gold: (t: string) => request<unknown>(`/label-tasks/${e(t)}/gold`).then(list<{ row_id: number; column_name?: string; value: unknown; pool: string }>('gold')),
  fill: (t: string, body: Fill, b: string) => json<Job & { added?: number; n?: number }>(`/label-tasks/${e(t)}/fill`, 'POST', { ...body, by: b }, 120_000),
  next: (t: string, n: number, b: string) => json<unknown>(`/label-tasks/${e(t)}/next?n=${n}`, 'POST', { by: b }).then(list<Item>('items')),
  answers: (t: string, items: Answer[], b: string) => json<{ accepted?: number; paused?: boolean; gold?: { correct: number; n: number } }>(`/label-tasks/${e(t)}/answers`, 'POST', { items, by: b }),
  review: (t: string, kind: string) => request<unknown>(`/label-tasks/${e(t)}/review?kind=${e(kind)}`).then(list<ReviewItem>('items')),
  adjudicate: (t: string, body: { row_id: number; column?: string; value: unknown; assignment_id?: string; note?: string }, b: string) =>
    json<unknown>(`/label-tasks/${e(t)}/adjudicate`, 'POST', { ...body, by: b }),
  quality: (t: string) => request<Quality>(`/label-tasks/${e(t)}/quality`),

  prompts: (l: string) => request<unknown>(`/label-layers/${e(l)}/prompts`).then(list<Prompt>('prompts')),
  createPrompt: (l: string, body: { name: string; template: string; model?: string; context: string[]; effort?: string }, b: string) =>
    json<Prompt>(`/label-layers/${e(l)}/prompts`, 'POST', { ...body, by: b }),
  llmPolicy: (l: string) => request<LlmPolicy & { pii?: LlmPii }>(`/label-layers/${e(l)}/llm-policy`),
  preview: (p: string, n: number, versionId: string, b: string) => json<LlmRun & Job>(`/label-prompts/${e(p)}/preview`, 'POST', { n, version_id: versionId, by: b }, 120_000),
  calibrate: (p: string, body: { version_id: string; task_id?: string; max_usd?: number }, b: string) => json<LlmRun & Job>(`/label-prompts/${e(p)}/calibrate`, 'POST', { ...body, by: b }, 120_000),
  run: (p: string, body: { version_id: string; selector: unknown; k?: number; mode?: 'online' | 'batch'; max_usd: number }, b: string) =>
    json<LlmRun & Job>(`/label-prompts/${e(p)}/runs`, 'POST', { ...body, by: b }, 120_000),
  llmRuns: (l: string) => request<unknown>(`/label-layers/${e(l)}/llm-runs`).then(list<LlmRun>('runs')),
  llmRun: (r: string) => request<LlmRun>(`/label-llm-runs/${e(r)}`),
  cancelRun: (r: string, b: string) => json<LlmRun>(`/label-llm-runs/${e(r)}/cancel`, 'POST', { by: b }),
  acceptRun: (r: string, body: { tau?: number | null }, b: string) => json<{ accepted?: number }>(`/label-llm-runs/${e(r)}/accept`, 'POST', { ...body, by: b }),
  toHumans: (r: string, body: { task_id: string; tau?: number | null }, b: string) => json<{ added?: number }>(`/label-llm-runs/${e(r)}/send-to-task`, 'POST', { ...body, by: b }),
  unmaskRequest: (l: string, columns: string[], reason: string, b: string) => json<{ id: string; status: string }>(`/label-layers/${e(l)}/llm-unmasked-requests`, 'POST', { columns, reason, by: b }),

  snapshots: (l: string) => request<unknown>(`/label-layers/${e(l)}/snapshots`).then(list<Snapshot>('snapshots')),
  snapshot: (l: string, policy: Record<string, unknown>, b: string) => json<Job & Partial<Snapshot>>(`/label-layers/${e(l)}/snapshots`, 'POST', { policy, by: b }, 120_000),
  getSnapshot: (s: string) => request<Snapshot>(`/label-snapshots/${e(s)}`),
  verifySnapshot: (s: string) => request<{ ok: boolean; [k: string]: unknown }>(`/label-snapshots/${e(s)}/verify`),
  approveSnapshot: (s: string, comment: string, b: string) => json<Snapshot>(`/label-snapshots/${e(s)}/approve`, 'POST', { comment, by: b }),
  materialise: (s: string, body: { version_id: string; columns?: string[]; holdout?: string; reason: string }, b: string) =>
    json<Job>(`/label-snapshots/${e(s)}/materialise`, 'POST', { ...body, by: b }, 120_000),

  proposals: (l: string) => request<unknown>(`/label-layers/${e(l)}/proposals`).then(list<Proposal>('proposals')),
  decide: (p: string, decision: 'approve' | 'reject', comment: string, b: string) => json<Proposal & Job>(`/label-proposals/${e(p)}/decide`, 'POST', { decision, comment, by: b }, 120_000),
  job: (id: string) => request<Job>(`/jobs/${e(id)}`),
}

/** Wilson score interval for k of n at 95 % (the calibration report's accuracy ± CI when the API sends none). */
export function wilson(k: number, n: number, z = 1.96): [number, number] | null {
  if (!n) return null
  const p = k / n, d = 1 + (z * z) / n
  const c = (p + (z * z) / (2 * n)) / d
  const h = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / d
  return [Math.max(0, c - h), Math.min(1, c + h)]
}
/** A task's or schema's classes as `{value, hotkey}`; plain strings get 1–9 in order. */
export const classesOf = (cs: (LabelClass | string)[] | undefined | null): LabelClass[] =>
  (cs ?? []).map((c, i) => (typeof c === 'string' ? { value: c, hotkey: i < 9 ? String(i + 1) : undefined } : { ...c, hotkey: c.hotkey ?? (i < 9 ? String(i + 1) : undefined) }))
export const num = (v: unknown): number | null => (v == null || v === '' ? null : Number.isFinite(Number(v)) ? Number(v) : null)
export const arr = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : typeof v === 'string' ? (() => { try { const j = JSON.parse(v); return Array.isArray(j) ? j.map(String) : [] } catch { return v ? v.split(',').map((x) => x.trim()) : [] } })() : [])
export const countsOf = (s: Snapshot) => (typeof s.counts === 'string' ? (() => { try { return JSON.parse(s.counts as string) } catch { return {} } })() : s.counts ?? {}) as NonNullable<Exclude<Snapshot['counts'], string>>
