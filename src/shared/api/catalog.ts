// P1: projects, the lineage tree, paired compare, tags and notes, champions, retention (backend/app/catalog).
import { request } from './client'

export type NodeKind = 'dataset' | 'run' | 'version' | 'project'
export type Project = { id: string; name: string; root_dataset_id: string | null; created_by?: string; description: string; created_at: string; n_datasets: number; n_runs: number; last_run_at: string | null; champion: { version_id: string; run_id: string; decided_by: string; decided_at: string } | null }
export type ChampionRequest = { id: string; project_id: string; version_id: string; run_id: string; status: 'pending' | 'approved' | 'rejected' | 'superseded' | 'withdrawn'; requested_by: string; requested_at: string; decided_by: string | null; decided_at: string | null; comment: string }
export type LNode = {
  kind: 'dataset' | 'run' | 'version'; id: string; depth: number; tags: string[]; notes: number
  name?: string; rows?: number; op?: string | null; parent_id?: string | null; sha256?: string | null; state?: 'archived' | 'deleted' | null
  dataset_id?: string; status?: string; source?: string | null; run_kind?: string; parent_run_id?: string | null; primary_metric?: string | null; primary_score?: number | null; predictor_state?: string | null; created_at?: string
  model_name?: string; version?: number; run_id?: string; stage?: string; approval?: string; champion?: boolean
}
export type LEdge = { src: { kind: string; id: string }; dst: { kind: string; id: string }; relation: 'derived_from' | 'trained_on' | 'branched_from' | 'built_from' | 'compared_in'; actor: string; inferred: boolean; created_at: string
  meta: { source?: string; patch?: string; experiment?: string; review_id?: string; hypothesis_id?: string; hypothesis?: string; verdict?: string; op?: string; [k: string]: unknown } }
export type LReview = { id: string; run_id: string; status: string | null; by: string | null; mode: string | null; started_at: string | null; finished_at: string | null }
export type LineageTree = { focus: { kind: string; id: string }; root: { kind: string; id: string }; project: Project | null; champion: ChampionRequest | null; nodes: LNode[]; edges: LEdge[]; reviews: LReview[]; counts: Record<string, number> }
export type CompareSide = { kind: 'run' | 'version'; id: string; label: string; run_id: string; dataset_id: string; eval_metric: string | null; holdout_score: number | null; holdout_rows: number | null; predictor_state: string | null; note?: string }
export type Compare = {
  a: CompareSide; b: CompareSide; paired: boolean; label: string
  reason?: string; table?: { side: string; id: string; label: string; dataset_id: string; metric: string | null; holdout_score: number | null; holdout_rows: number | null }[]; difference?: number | null; difference_note?: string
  metric?: string; metric_key?: string; metric_note?: string; auc?: { a: number; b: number; delta: number; note: string }; partial?: boolean; overlap?: string | null; rows_a?: number; rows_b?: number; B_note?: string
  score_a?: number; score_b?: number; delta?: number; ci?: [number | null, number | null]; level?: number; B?: number; rows?: number; same_rows?: boolean; verdict?: string; method?: string; sign?: string; warning?: string
}
export type Notes = { tags: string[]; notes: { id: number; value: string; actor: string; created_at: string }[] }

const json = <T,>(path: string, method: string, body: unknown, timeoutMs = 30_000) =>
  request<T>(path, { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs })
const e = encodeURIComponent

export const catalog = {
  projects: () => request<{ projects: Project[] }>('/projects').then((r) => r.projects),
  project: (id: string) => request<Project & { champion: ChampionRequest | null; champion_requests: ChampionRequest[] }>(`/projects/${e(id)}`),
  lineage: (kind: NodeKind, id: string) => request<LineageTree>(`/lineage/${kind}/${e(id)}`, { timeoutMs: 30_000 }),
  compare: (a: string, b: string) => json<Compare>('/compare', 'POST', { a, b }, 60_000),
  annotations: (kind: NodeKind, id: string) => request<Notes>(`/annotations/${kind}/${e(id)}`),
  tag: (kind: NodeKind, id: string, tag: string, by: string) => json<Notes>(`/annotations/${kind}/${e(id)}`, 'POST', { tag, by }),
  note: (kind: NodeKind, id: string, note: string, by: string) => json<Notes>(`/annotations/${kind}/${e(id)}`, 'POST', { note, by }),
  // a tag may contain '/': the API route takes the rest of the path, so the slashes stay unencoded
  untag: (kind: NodeKind, id: string, tag: string, by: string) => request<Notes>(`/annotations/${kind}/${e(id)}/tags/${tag.split('/').map(e).join('/')}?by=${e(by)}`, { method: 'DELETE' }),
  editNote: (kind: NodeKind, id: string, noteId: number, note: string, by: string) => json<Notes>(`/annotations/${kind}/${e(id)}/notes/${noteId}`, 'PATCH', { note, by }),
  deleteNote: (kind: NodeKind, id: string, noteId: number, by: string) => request<Notes>(`/annotations/${kind}/${e(id)}/notes/${noteId}?by=${e(by)}`, { method: 'DELETE' }),
  createProject: (name: string, by: string, description = '') => json<Project>('/projects', 'POST', { name, by, description }),
  moveDataset: (project: string, dataset_id: string, by: string) => json<{ datasets: number; runs: number }>(`/projects/${e(project)}/datasets`, 'POST', { dataset_id, by }),
  requestChampion: (project: string, version_id: string, by: string, comment = '', override_reason = '') => json<ChampionRequest>(`/projects/${e(project)}/champion`, 'POST', { version_id, by, comment, ...(override_reason ? { override_reason } : {}) }),
  decideChampion: (id: string, approve: boolean, by: string, comment = '', override_reason = '') => json<ChampionRequest>(`/champions/${e(id)}/decide`, 'POST', { approve, by, comment, ...(override_reason ? { override_reason } : {}) }),
}

type RunT = import('./types').Run
/** F-P2: one page of runs (the list shape, newest first), scoped to a project in SQL; `total` = runs matching. */
export const runsPage = (projectId: string | null, offset = 0, limit = 100) =>
  request<{ runs: RunT[]; total: number }>(`/runs?kind=all&limit=${limit}&offset=${offset}${projectId ? `&project_id=${e(projectId)}` : ''}`, { timeoutMs: 60_000 })
/** F-P2: chosen runs by id (the list shape): a selection from a link, or the rows whose status is changing. */
export const runsByIds = (ids: string[]) =>
  ids.length ? request<{ runs: RunT[] }>(`/runs?kind=all&ids=${ids.map(e).join(',')}`).then((r) => r.runs) : Promise.resolve([] as RunT[])
/** F-P2: the queued and running runs only, indexed columns (cheap enough to poll every few seconds). */
export const activeRuns = (projectId: string | null) =>
  request<{ runs: { id: string; status: string }[] }>(`/runs?kind=all&light=true&status=queued,running${projectId ? `&project_id=${e(projectId)}` : ''}`).then((r) => r.runs)

/** F-P2: the runs Register… offers: the newest 100 finished tabular runs (never the whole unscoped list), plus `include`. */
export const registrableRuns = async (include: string | null) => {
  const r = await request<{ runs: RunT[] }>('/runs?status=succeeded&limit=100', { timeoutMs: 60_000 })
  if (include && !r.runs.some((x) => x.id === include)) r.runs.unshift(...(await runsByIds([include])))
  return r.runs
}

// DL1: a dataset (a root and its versions): history, upload as a version, diff, usage, default, card, archive, delete,
// integrity, export, search (backend/app/catalog/dsets.py).
export type DsCard = { root_id: string; description: string; owner: string; source: string; label_definition: string; known_issues: string; default_version_id: string | null; updated_at: string | null; updated_by: string | null }
export type DsWarning = { code: string; message: string; column?: string; values?: string[] }
export type DsVersion = {
  id: string; v: number; name: string; filename: string; source: string; note: string; created_at: string; created_by: string | null; rows: number; n_cols: number | null; target: string | null
  parent_id: string | null; root_id: string | null; sha256: string | null; op: string | null; state: 'archived' | 'deleted' | null; state_at: string | null; state_by: string | null
  verified_at: string | null; verified_ok: number | null; size_bytes: number; is_default: boolean; tags: string[]; n_changes: number | null; paired_with_parent: boolean | null
  changes: { matched_by?: string; matched?: number; changed?: number; added?: number; removed?: number; rows_before?: number; rows_after?: number; relabelled?: number; dropped?: number; synthetic?: number }
  warnings: DsWarning[]; used_by: { runs: number; versions: number; champion: boolean }; reason: string | null; state_scope?: 'dataset' | 'version' | null
  /** DL3: an in-app edit: each operation and what it touched */
  edit_ops?: { i: number; op: string; summary: string; rows: number | null; cells: number | null; column: string | null }[]; duplicate_of?: string
}
export type DsRequest = { id: string; dataset_id: string; root_id: string; action: 'delete'; status: 'pending' | 'approved' | 'rejected' | 'withdrawn'; requested_by: string; requested_at: string; reason: string; decided_by: string | null; decided_at: string | null; comment: string }
export type DsAudit = { id: number; ts: string; actor: string; action: string; comment: string; details: Record<string, unknown> | null }
export type DsHistory = { root_id: string; name: string; project: { id: string; name: string } | null; state: string | null; card: DsCard; default_version_id: string | null; default_how: string; versions: DsVersion[]; requests: DsRequest[]; audit: DsAudit[]; focus: string; admins_configured: boolean; retention_days: number
  retired_versions: { id: string; model_name: string; version: number; retired_at: string; retired_by: string; dataset_id: string }[] }
export type DsRow = { root_id: string; name: string; source: string; state: string; project_id: string | null; project: string | null; owner: string; created_by: string | null; description: string; tags: string[]; n_versions: number; n_live: number; ops: string[]; default_version_id: string | null; default_how: string; rows: number; n_cols: number | null; target: string | null; created_at: string; last_change: string; n_runs: number; versions_in_state?: string[] }
export type DsColStat = { column: string; kind: string; in: 'both' | 'a' | 'b'; missing_a: number | null; missing_b: number | null; mean_a?: number | null; mean_b?: number | null; median_a?: number | null; median_b?: number | null; top_a?: { value: string; share: number }[] | null; top_b?: { value: string; share: number }[] | null; psi?: number | null; band?: string | null; note?: string }
export type DsDiff = {
  a: { id: string; name: string; rows: number; target: string | null; sha256: string | null }; b: { id: string; name: string; rows: number; target: string | null; sha256: string | null }; same_dataset: boolean
  schema: { columns_added: string[]; columns_removed: string[]; type_changes: { column: string; from: string; to: string; kind_from: string; kind_to: string }[]; dtype_changes: { column: string; from: string; to: string }[]; n_columns: [number, number]; renamed?: { from: string; to: string }[] }
  rows: { a: number; b: number; matched: number; unchanged: number; changed: number; added: number; removed: number; matched_by: string; note: string; changed_examples: { row_a: number; row_b: number; key?: unknown; cells: { column: string; a: unknown; b: unknown }[] }[] }
  columns: DsColStat[]; checks: DsWarning[]
  /** DL3: b is an edit of a: its operations with their cells, old → new (capped per operation) */
  edit?: { by: string; reason: string | null; ops: DsEditLog[]; renamed: Record<string, string> }
}
// DL3: edit-as-version (backend/app/dataset_edit.py)
export type DsEditOp = { op: string; [k: string]: unknown }
export type DsEditLog = { i: number; op: string; summary: string; rows?: number; cells?: number; column?: string; examples: { row: number | string; column?: string; old?: unknown; new?: unknown; deleted?: Record<string, unknown>; added?: Record<string, unknown> }[]; examples_truncated: boolean }
export type DsGrid = { dataset_id: string; columns: { name: string; kind: string }[]; rows: Record<string, unknown>[]; total: number; matching: number; offset: number; limit: number; q: string }
export type DsEditSummary = { matched: number; changed: number; added: number; removed: number; columns_before: number; columns_after: number; renamed: Record<string, string>
  checks: DsUploadSummary['checks']; edit_log: DsEditLog[]; ops_summary: string[]; n_ops: number; duplicate_of?: string }
export type DsEditPreview = DsEditSummary & { preview: true; v: number; parent: string; rows: number; n_cols: number; sha256: string; would_be_default: boolean; columns: string[]; sample: Record<string, unknown>[]; note: string }
export type DsUsage = { dataset_id: string; datasets: string[]; runs: { id: string; name: string; status: string; dataset_id: string; label: string; created_at: string; primary_metric: string | null; primary_score: number | null }[]; versions: { id: string; model_name: string; version: number; run_id: string; status: string; stage: string; approval: string; dataset_id: string; retired: boolean; retired_at: string | null }[]; champions: ChampionRequest[]; blocks_archive: string[]; blocks_archive_or_delete: string[] }
export type DsUploadSummary = { matched_by: string; matched: number; changed: number; added: number; removed: number; reason?: string
  checks: { warnings: DsWarning[]; columns_added: string[]; columns_removed: string[]; type_changes: { column: string; kind_from: string; kind_to: string; from: string; to: string }[]; rows_before: number; rows_after: number } }
export type DsUploadPreview = DsUploadSummary & { preview: true; v: number; parent: string; filename: string; rows: number; n_cols: number; sha256: string; would_be_default: boolean; note: string; kept_as_text?: string[] }
/** DL4 (#11): the live dataset an uploaded CSV most looks like (POST /datasets/match writes nothing) */
export type DsMatchCandidate = { root_id: string; name: string; version_id: string; v: number; next_v: number; target: string | null; rows: number; score: number
  columns_in_common: number; columns_added: string[]; columns_removed: string[]; same_target: boolean; name_similarity: number; sample: boolean; reasons: string[] }
export type DsMatch = { filename: string; rows: number; columns: string[]; match: DsMatchCandidate | null; others: DsMatchCandidate[] }
export type DsVerify = { root_id: string; ok: boolean; results: { id: string; v: number; ok: boolean | null; status: string; stored: string | null; now?: string | null; remedy?: string; splits?: { file: string; status: string }[] }[] }

export const data = {
  search: (f: { q?: string; project?: string; tag?: string; owner?: string; op?: string; since?: string; until?: string; state?: string }) => {
    const p = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][])
    return request<{ datasets: DsRow[]; total: number }>(`/data/search?${p}`).then((r) => r.datasets)
  },
  history: (id: string) => request<DsHistory>(`/datasets/${e(id)}/history`, { timeoutMs: 30_000 }),
  /** DL2: preview=true writes nothing; make_default 'auto' = the default when it has no contract warnings, 'no' = never. */
  uploadVersion: (id: string, file: File, by: string, o: { rowKey?: string; preview?: boolean; reason?: string; makeDefault?: 'auto' | 'no' } = {}) => {
    const form = new FormData()
    form.append('file', file); form.append('by', by)
    if (o.rowKey !== undefined) form.append('row_key', o.rowKey)
    if (o.preview) form.append('preview', 'true')
    if (o.reason) form.append('reason', o.reason)
    if (o.makeDefault) form.append('make_default', o.makeDefault)
    return request<import('./types').DatasetDetail & { upload: DsUploadSummary & { default: { is_default: boolean; why: string; how_to_promote?: string } } }>(`/datasets/${e(id)}/versions`, { method: 'POST', body: form, timeoutMs: 300_000 })
  },
  previewVersion: (id: string, file: File, by: string, rowKey?: string) => {
    const form = new FormData()
    form.append('file', file); form.append('by', by); form.append('preview', 'true')
    if (rowKey !== undefined) form.append('row_key', rowKey)
    return request<DsUploadPreview>(`/datasets/${e(id)}/versions`, { method: 'POST', body: form, timeoutMs: 300_000 })
  },
  /** DL4: does this file look like a dataset already here? Nothing is written. */
  match: (file: File) => { const form = new FormData(); form.append('file', file); return request<DsMatch>('/datasets/match', { method: 'POST', body: form, timeoutMs: 120_000 }) },
  /** DL4 (M-d): write back split files changed outside the app, from the copy recorded when they were drawn */
  restoreSplits: (id: string, by: string, reason: string) => json<{ restored: string[]; verify: DsVerify }>(`/datasets/${e(id)}/restore-splits`, 'POST', { by, reason }),
  diff: (a: string, b: string) => request<DsDiff>(`/datasets/${e(a)}/diff/${e(b)}`, { timeoutMs: 120_000 }),
  usage: (id: string) => request<DsUsage>(`/datasets/${e(id)}/usage`),
  setDefault: (id: string, version_id: string, by: string, reason = '') => json<{ default_version_id: string; v: number; rollback: boolean; previous: string | null; note: string }>(`/datasets/${e(id)}/default`, 'POST', { version_id, by, reason }),
  editCard: (id: string, fields: Partial<DsCard> & { name?: string }, by: string) => json<DsCard>(`/datasets/${e(id)}/card`, 'PATCH', { ...fields, by }),
  archive: (id: string, archived: boolean, by: string, reason = '', only_version = false) => json<{ ids: string[]; state: string }>(`/datasets/${e(id)}/archive`, 'POST', { archived, by, reason, only_version }),
  requestDelete: (id: string, by: string, reason: string, only_version = false, freeze_holdout = false) => json<DsRequest & { deleted?: unknown[] }>(`/datasets/${e(id)}/delete`, 'POST', { by, reason, only_version, freeze_holdout }),
  decideDelete: (rid: string, approve: boolean, by: string, comment = '', withdraw = false) => json<DsRequest>(`/dataset-requests/${e(rid)}/decide`, 'POST', withdraw ? { withdraw: true, by, comment } : { approve, by, comment }),
  grid: (id: string, o: { offset?: number; limit?: number; q?: string } = {}) =>
    request<DsGrid>(`/datasets/${e(id)}/grid?offset=${o.offset ?? 0}&limit=${o.limit ?? 50}&q=${e(o.q ?? '')}`, { timeoutMs: 60_000 }),
  /** DL3: typed operations → a NEW version (nothing modified in place). preview=true writes nothing. */
  editPreview: (id: string, ops: DsEditOp[], by: string) => json<DsEditPreview>(`/datasets/${e(id)}/edit`, 'POST', { ops, by, preview: true }, 120_000),
  edit: (id: string, ops: DsEditOp[], by: string, reason: string, make_default: 'auto' | 'no' = 'auto') =>
    json<import('./types').DatasetDetail & { created: boolean; duplicate_of?: string; edit: DsEditSummary; default?: { is_default: boolean; why: string } }>(`/datasets/${e(id)}/edit`, 'POST', { ops, by, reason, make_default }, 300_000),
  adoptDiskFile: (id: string, by: string, reason: string) => json<import('./types').DatasetDetail & { upload: DsUploadSummary }>(`/datasets/${e(id)}/adopt-disk-file`, 'POST', { by, reason }, 300_000),
  verify: (id: string, by: string, all = false) => json<DsVerify>(`/datasets/${e(id)}/verify`, 'POST', { by, all }, 120_000),
  exportUrl: (id: string, by: string, format: 'zip' | 'csv' = 'zip') => `/api/datasets/${e(id)}/export?format=${format}&by=${e(by)}`,
}
