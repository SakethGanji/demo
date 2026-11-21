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
  warnings: DsWarning[]; used_by: { runs: number; versions: number; champion: boolean }
}
export type DsRequest = { id: string; dataset_id: string; root_id: string; action: 'delete'; status: 'pending' | 'approved' | 'rejected' | 'withdrawn'; requested_by: string; requested_at: string; reason: string; decided_by: string | null; decided_at: string | null; comment: string }
export type DsAudit = { id: number; ts: string; actor: string; action: string; comment: string; details: Record<string, unknown> | null }
export type DsHistory = { root_id: string; name: string; project: { id: string; name: string } | null; state: string | null; card: DsCard; default_version_id: string | null; default_how: string; versions: DsVersion[]; requests: DsRequest[]; audit: DsAudit[]; focus: string; admins_configured: boolean }
export type DsRow = { root_id: string; name: string; source: string; state: string; project_id: string | null; project: string | null; owner: string; created_by: string | null; description: string; tags: string[]; n_versions: number; n_live: number; ops: string[]; default_version_id: string | null; default_how: string; rows: number; n_cols: number | null; target: string | null; created_at: string; last_change: string; n_runs: number }
export type DsColStat = { column: string; kind: string; in: 'both' | 'a' | 'b'; missing_a: number | null; missing_b: number | null; mean_a?: number | null; mean_b?: number | null; median_a?: number | null; median_b?: number | null; top_a?: { value: string; share: number }[] | null; top_b?: { value: string; share: number }[] | null; psi?: number | null; band?: string | null; note?: string }
export type DsDiff = {
  a: { id: string; name: string; rows: number; target: string | null; sha256: string | null }; b: { id: string; name: string; rows: number; target: string | null; sha256: string | null }; same_dataset: boolean
  schema: { columns_added: string[]; columns_removed: string[]; type_changes: { column: string; from: string; to: string; kind_from: string; kind_to: string }[]; dtype_changes: { column: string; from: string; to: string }[]; n_columns: [number, number] }
  rows: { a: number; b: number; matched: number; unchanged: number; changed: number; added: number; removed: number; matched_by: string; note: string; changed_examples: { row_a: number; row_b: number; key?: unknown; cells: { column: string; a: unknown; b: unknown }[] }[] }
  columns: DsColStat[]; checks: DsWarning[]
}
export type DsUsage = { dataset_id: string; datasets: string[]; runs: { id: string; name: string; status: string; dataset_id: string; label: string; created_at: string; primary_metric: string | null; primary_score: number | null }[]; versions: { id: string; model_name: string; version: number; run_id: string; status: string; stage: string; approval: string; dataset_id: string }[]; champions: ChampionRequest[]; blocks_archive_or_delete: string[] }
export type DsVerify = { root_id: string; ok: boolean; results: { id: string; v: number; ok: boolean | null; status: string; stored: string | null; now?: string | null }[] }

export const data = {
  search: (f: { q?: string; project?: string; tag?: string; owner?: string; op?: string; since?: string; until?: string; state?: string }) => {
    const p = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][])
    return request<{ datasets: DsRow[]; total: number }>(`/data/search?${p}`).then((r) => r.datasets)
  },
  history: (id: string) => request<DsHistory>(`/datasets/${e(id)}/history`, { timeoutMs: 30_000 }),
  uploadVersion: (id: string, file: File, by: string, rowKey?: string) => {
    const form = new FormData()
    form.append('file', file); form.append('by', by)
    if (rowKey !== undefined) form.append('row_key', rowKey)
    return request<import('./types').DatasetDetail & { upload: { matched_by: string; matched: number; changed: number; added: number; removed: number; checks: { warnings: DsWarning[] } } }>(`/datasets/${e(id)}/versions`, { method: 'POST', body: form, timeoutMs: 300_000 })
  },
  diff: (a: string, b: string) => request<DsDiff>(`/datasets/${e(a)}/diff/${e(b)}`, { timeoutMs: 120_000 }),
  usage: (id: string) => request<DsUsage>(`/datasets/${e(id)}/usage`),
  setDefault: (id: string, version_id: string, by: string, reason = '') => json<{ default_version_id: string; v: number; rollback: boolean; previous: string | null; note: string }>(`/datasets/${e(id)}/default`, 'POST', { version_id, by, reason }),
  editCard: (id: string, fields: Partial<DsCard> & { name?: string }, by: string) => json<DsCard>(`/datasets/${e(id)}/card`, 'PATCH', { ...fields, by }),
  archive: (id: string, archived: boolean, by: string, reason = '') => json<{ ids: string[]; state: string }>(`/datasets/${e(id)}/archive`, 'POST', { archived, by, reason }),
  requestDelete: (id: string, by: string, reason: string) => json<DsRequest & { deleted?: unknown[] }>(`/datasets/${e(id)}/delete`, 'POST', { by, reason }),
  decideDelete: (rid: string, approve: boolean, by: string, comment = '') => json<DsRequest>(`/dataset-requests/${e(rid)}/decide`, 'POST', { approve, by, comment }),
  verify: (id: string, by: string, all = false) => json<DsVerify>(`/datasets/${e(id)}/verify`, 'POST', { by, all }, 120_000),
  exportUrl: (id: string, by: string, format: 'zip' | 'csv' = 'zip') => `/api/datasets/${e(id)}/export?format=${format}&by=${e(by)}`,
}
