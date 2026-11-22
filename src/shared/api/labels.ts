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
/** LB-A's spec (`ensemble.labelschema/1`): one entry per labelled column; classes are plain strings (keys 1–9 in order). */
export type SpecColumn = { column: string; role?: 'target' | 'feature'; type?: 'class' | 'number' | 'text' | 'date'; classes?: string[]; multi?: boolean; hotkeys?: Record<string, string> }
export type SchemaSpec = { format?: string; columns: SpecColumn[]; task_type?: string; [k: string]: unknown }
export type LabelSchema = { id?: string; version: number; spec: SchemaSpec; guidelines?: string; guidelines_sha256?: string; created_at?: string; created_by?: string }
export type Layer = {
  id: string; root_id: string; table_name: string; team_id?: string; name: string; purpose?: string; kind: LayerKind
  is_default?: boolean | number; state: 'active' | 'frozen' | 'archived'; next_seq?: number; created_at?: string; created_by?: string
  schema?: LabelSchema | null; schema_version?: number | null; n_events?: number
}
/** One column's carry-over status on a version (§5.2). */
export type ColStatus = { column: string; live?: number; stale?: number; conflict?: number; orphaned?: number; unlabelled?: number; base_changed?: number; total?: number; labelled?: number }
/** LB-A: `{version_id, through_seq, rows_in_version, columns: {col: counts}, totals}`; counts are ok · in_base · stale ·
 *  base_changed · conflict · orphaned · disputed · prelabel_only (+ live = ok + in_base and unlabelled in totals). */
export type StatusCounts = { ok?: number; in_base?: number; live?: number; stale?: number; base_changed?: number; conflict?: number; orphaned?: number; disputed?: number; prelabel_only?: number; unlabelled?: number; schema_dropped?: number }
export type LayerStatus = {
  layer_id?: string; version_id: string; through_seq?: number; rows_in_version?: number
  columns?: Record<string, StatusCounts> | ColStatus[]; totals?: StatusCounts; by_source?: Record<string, number>
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
  /** LB-B: assignments by status (open / leased / done / skipped / expired) on GET /label-tasks/{t} */
  counts?: Record<string, number>; team_id?: string
}
export type TaskMember = { principal_id: string; display_name?: string; role: 'annotator' | 'reviewer'; guest?: boolean | number; paused_at?: string | null; paused_reason?: string | null }
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
  is_gold?: boolean; is_blind?: boolean; leased_until?: string; current?: unknown; column?: string
}
export type Answer = { assignment_id: string; value?: unknown; skip?: boolean; flag?: boolean; confirm?: boolean; note?: string; ms?: number }
export type AnswerResult = { written?: number; skipped?: number; gold?: { graded: number; correct: number }; paused?: false | { paused: boolean; reason?: string; accuracy?: number } }
export type GoldAcc = { n: number; correct: number; accuracy: number | null; wilson_lo?: number; wilson_hi?: number }
export type Quality = {
  task_id?: string; fleiss_kappa?: number | null; fleiss_rows?: number
  cohen_kappa?: { a: string; b: string; n_shared: number; kappa: number | null; agreement?: number; reliable?: boolean }[]
  annotators?: { principal_id: string; display_name?: string | null; items?: number; median_ms?: number | null; items_per_hour?: number | null; gold?: GoldAcc | null
    agreement_with_majority?: number | null; paused?: boolean; anchoring?: boolean | null }[]
  anchoring?: { flag?: boolean; shown_agree?: number | null; blind_agree?: number | null; [k: string]: unknown } | null
  backlog?: number; done?: number; disputed?: number; adjudicated?: number; classes?: string[]
}
/** LB-B review items: disputed → votes; gold_fail → actor, answer, gold; flagged → actor, note. */
export type ReviewItem = { row_id: number; column?: string; votes?: { actor: string; display_name?: string; value: unknown }[]; you_voted?: boolean
  actor?: string; answer?: unknown; gold?: unknown; note?: string | null; context?: Record<string, unknown>; masked?: string[] }

export type Prompt = { id: string; layer_id: string; name: string; version: number; model: string; effort?: string; template?: string; prompt_sha256?: string
  spec?: { model?: string; template?: string; context?: string[]; column?: string; classes?: string[]; send_masked?: string[] }
  calibration?: Calibration | null; created_at?: string; created_by?: string }
/** LB-B's calibration record (§5.10): accuracy on the calibration gold, τ* on one half certified on the other. */
export type Calibration = { id?: string; n?: number; accuracy?: number | null; tau_star?: number | null; target?: number; passed?: boolean
  per_class?: Record<string, { n: number; recall?: number; accuracy?: number | null }>
  thresholds?: { tau: number; coverage: number; n: number; acc: number | null; acc_lo?: number }[]
  certify?: { tau: number; coverage: number; n: number; acc: number | null; acc_lo?: number } | null; confusion?: [string, string, number][] }
/** The team policy key LB-B reads (`teams.policy.llm_labeling`): off unless enabled; the destination must be allowed. */
export type LlmPolicy = { enabled?: boolean; destinations?: string[]; max_usd_month?: number; max_rows_job?: number; [k: string]: unknown }
export type LlmCols = { sent?: string[]; masked?: string[]; free_text?: string[]; excluded?: { column: string; reason: string }[] }
export type LlmRun = {
  id: string; prompt_id: string; job_id?: string; purpose: 'preview' | 'calibrate' | 'label'; status: string; destination: string; mode?: string; samples_k?: number
  columns_sent?: LlmCols; max_usd?: string | number; est_usd?: string | number | null; spent_usd?: string | number
  tokens_in?: number; tokens_out?: number; tokens_cached?: number; rows_total?: number | null; rows_done?: number; rows_cached?: number; rows_failed?: number; pii_redactions?: number
  created_at?: string; created_by?: string; finished_at?: string | null; error?: string | null
  result?: { calibration?: Calibration; auto_accepted?: number; prelabel_only?: number; events_written?: number; budget_stopped?: boolean; pii_values_found?: number
    calibration_used?: { id: string; tau_star: number | null; passed?: boolean } | null } | null
}
export type LlmStarted = { id: string; job_id: string; status: string; estimate?: { est_usd: number; [k: string]: unknown }; rows?: number; columns?: LlmCols }
export type LlmPreview = { prompt_id: string; estimate?: { est_usd: number; [k: string]: unknown }; columns?: LlmCols; labels?: { row_id: number; label: string; confidence: number; rationale?: string }[]
  spent_usd?: number; pii_redactions?: number; pii_values_found?: number }
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
  addMember: (t: string, name: string, role: 'annotator' | 'reviewer', b: string) =>
    json<{ principal_id: string; role: string; guest: boolean }>(`/label-tasks/${e(t)}/members`, 'POST', { principal: name, role, by: b }),
  resume: (t: string, p: string, b: string) => json<unknown>(`/label-tasks/${e(t)}/members/${e(p)}/resume`, 'POST', { by: b }),
  removeMember: (t: string, p: string, b: string) => request<unknown>(`/label-tasks/${e(t)}/members/${e(p)}${by(b)}`, { method: 'DELETE' }),
  setGold: (t: string, body: { pool: 'qa' | 'few_shot' | 'calibration'; column?: string; items?: { row_id: number; value: unknown }[]; from_version?: string; n?: number }, b: string) =>
    json<{ added: number; pools: Record<string, number> }>(`/label-tasks/${e(t)}/gold`, 'POST', { ...body, by: b }, 120_000),
  gold: (t: string) => request<{ pools: Record<string, number> }>(`/label-tasks/${e(t)}/gold`),
  fill: (t: string, body: Fill, b: string) => json<Job & { added?: number; n?: number }>(`/label-tasks/${e(t)}/fill`, 'POST', { ...body, by: b }, 120_000),
  next: (t: string, n: number, b: string) => json<unknown>(`/label-tasks/${e(t)}/next?n=${n}`, 'POST', { by: b }).then(list<Item>('items')),
  answers: (t: string, items: Answer[], b: string) => json<AnswerResult>(`/label-tasks/${e(t)}/answers`, 'POST', { items, by: b }),
  review: (t: string, kind: string) => request<unknown>(`/label-tasks/${e(t)}/review?kind=${e(kind)}`).then(list<ReviewItem>('items')),
  adjudicate: (t: string, item: { row_id: number; column?: string; value: unknown; comment?: string }, b: string) =>
    json<{ adjudicated: number }>(`/label-tasks/${e(t)}/adjudicate`, 'POST', { items: [item], by: b }),
  quality: (t: string) => request<Quality>(`/label-tasks/${e(t)}/quality`),

  prompts: (l: string) => request<unknown>(`/label-layers/${e(l)}/prompts`).then(list<Prompt>('prompts')),
  createPrompt: (l: string, body: { name: string; column?: string; template: string; model?: string; context: string[]; effort?: string }, b: string) =>
    json<Prompt>(`/label-layers/${e(l)}/prompts`, 'POST', { ...body, by: b }),
  preview: (p: string, n: number, versionId: string, b: string, destination = 'fake') => json<LlmPreview>(`/label-prompts/${e(p)}/preview`, 'POST', { n, version_id: versionId, destination, max_usd: 0.5, by: b }, 120_000),
  calibrate: (p: string, body: { version_id: string; task_id?: string; max_usd: number; destination?: string }, b: string) => json<LlmStarted>(`/label-prompts/${e(p)}/calibrate`, 'POST', { destination: 'fake', ...body, by: b }, 120_000),
  run: (p: string, body: { version_id: string; selector: unknown; k?: number; max_usd: number; destination?: string; stop_at_cap?: boolean; task_id?: string }, b: string) =>
    json<LlmStarted>(`/label-prompts/${e(p)}/runs`, 'POST', { destination: 'fake', mode: 'online', ...body, by: b }, 120_000),
  llmRun: (r: string) => request<LlmRun>(`/label-llm-runs/${e(r)}`),
  unmaskRequest: (l: string, columns: string[], reason: string, b: string) => json<{ id: string; status: string }>(`/label-layers/${e(l)}/llm-unmasked-requests`, 'POST', { columns, reason, by: b }),

  snapshots: (l: string) => request<unknown>(`/label-layers/${e(l)}/snapshots`).then(list<Snapshot>('snapshots')),
  snapshot: (l: string, policy: Record<string, unknown>, b: string) => json<Job & Partial<Snapshot>>(`/label-layers/${e(l)}/snapshots`, 'POST', { policy, by: b }, 120_000),
  getSnapshot: (s: string) => request<Snapshot>(`/label-snapshots/${e(s)}`),
  verifySnapshot: (s: string) => request<{ ok: boolean; [k: string]: unknown }>(`/label-snapshots/${e(s)}/verify`),
  approveSnapshot: (s: string, comment: string, b: string) => json<Snapshot>(`/label-snapshots/${e(s)}/approve`, 'POST', { comment, by: b }),
  materialise: (s: string, body: { version_id: string; columns?: string[]; holdout?: string; reason: string }, b: string) =>
    json<Job>(`/label-snapshots/${e(s)}/materialise`, 'POST', { ...body, by: b }, 120_000),

  proposals: (l: string) => request<unknown>(`/label-layers/${e(l)}/proposals`).then(list<Proposal>('proposals')),
  decide: (p: string, decision: 'approve' | 'reject', comment: string, b: string) => json<Proposal & Job>(`/label-proposals/${e(p)}/decide`, 'POST', { approve: decision === 'approve', decision, comment, by: b }, 120_000),
  job: (id: string) => request<Job>(`/jobs/${e(id)}`),
  /** LB-A: a run's overlay pin re-verified (404 no_overlay when the run trained without one). */
  runVerify: (run: string) => request<{ run_id: string; snapshot_id: string; overlay_sha256: string; ok: boolean | null; status: string; rows_applied?: number; overlay_blob_ok?: boolean; overlay_sha_ok?: boolean }>(`/label-runs/${e(run)}/verify`, { timeoutMs: 60_000 }),
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
/** The first labelled column of a spec (the one a class task is about). */
export const specCol = (spec?: SchemaSpec | null, column?: string | null): SpecColumn | null =>
  (spec?.columns ?? []).find((c) => !column || c.column === column) ?? spec?.columns?.[0] ?? null
export const classesOf = (cs: (LabelClass | string)[] | undefined | null): LabelClass[] =>
  (cs ?? []).map((c, i) => (typeof c === 'string' ? { value: c, hotkey: i < 9 ? String(i + 1) : undefined } : { ...c, hotkey: c.hotkey ?? (i < 9 ? String(i + 1) : undefined) }))
export const num = (v: unknown): number | null => (v == null || v === '' ? null : Number.isFinite(Number(v)) ? Number(v) : null)
export const arr = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : typeof v === 'string' ? (() => { try { const j = JSON.parse(v); return Array.isArray(j) ? j.map(String) : [] } catch { return v ? v.split(',').map((x) => x.trim()) : [] } })() : [])
export const countsOf = (s: Snapshot) => (typeof s.counts === 'string' ? (() => { try { return JSON.parse(s.counts as string) } catch { return {} } })() : s.counts ?? {}) as NonNullable<Exclude<Snapshot['counts'], string>>
