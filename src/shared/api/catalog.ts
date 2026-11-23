// P1: projects, the lineage tree, paired compare, tags and notes, champions, retention (backend/app/catalog).
import { request } from './client'

export type NodeKind = 'dataset' | 'run' | 'version' | 'project'
export type TeamRef = { id: string; slug: string; name: string }
export type Project = { id: string; name: string; team_id?: string; team?: TeamRef; root_dataset_id: string | null; created_by?: string; description: string; created_at: string; n_datasets: number; n_runs: number; last_run_at: string | null; champion: { version_id: string; run_id: string; decided_by: string; decided_at: string } | null }
export type ChampionRequest = { id: string; project_id: string; version_id: string; run_id: string; status: 'pending' | 'approved' | 'rejected' | 'superseded' | 'withdrawn'; requested_by: string; requested_at: string; decided_by: string | null; decided_at: string | null; comment: string }
export type LNode = {
  kind: 'dataset' | 'run' | 'version' | 'join_frame'; id: string; depth: number; tags: string[]; notes: number
  /** P14: a join frame (dataset -joined_from / merged_from-> join_frame -trained_on-> run) */
  join_id?: string; join_name?: string; spec_version?: number; version_id?: string; grain_table?: string; frame_sha256?: string; n_rows?: number; cache_blob?: string; created_by?: string
  name?: string; rows?: number; op?: string | null; parent_id?: string | null; sha256?: string | null; state?: 'archived' | 'deleted' | null
  dataset_id?: string; status?: string; source?: string | null; run_kind?: string; parent_run_id?: string | null; primary_metric?: string | null; primary_score?: number | null; predictor_state?: string | null; created_at?: string
  model_name?: string; version?: number; run_id?: string; stage?: string; approval?: string; champion?: boolean
}
export type LEdge = { src: { kind: string; id: string }; dst: { kind: string; id: string }; relation: 'derived_from' | 'trained_on' | 'branched_from' | 'built_from' | 'compared_in' | 'joined_from' | 'merged_from'; actor: string; inferred: boolean; created_at: string
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
  projects: (team?: string | null) => request<{ projects: Project[] }>(`/projects${team ? `?team=${e(team)}` : ''}`).then((r) => r.projects),
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
  /** P18/U1: a project lives in a team; the current team (the sidebar switcher) is sent, so 2+ teams never get a 400. */
  createProject: (name: string, by: string, team_id: string | null, description = '') => json<Project>('/projects', 'POST', { name, by, description, ...(team_id ? { team_id } : {}) }),
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
  /** U5: a multi-table version, table by table (from its manifest ops); `changes` above stay the primary table's.
   *  Counts are null when no op recorded them (e.g. a key declaration rewrote nothing row by row). */
  table_changes?: DsTableChange[]
}
export type DsTableChange = { table: string; change: string; primary: boolean; rows_before: number | null; rows_after: number | null
  added: number | null; changed: number | null; removed: number | null }
export type DsRequest = { id: string; dataset_id: string; root_id: string; action: 'delete'; status: 'pending' | 'approved' | 'rejected' | 'withdrawn'; requested_by: string; requested_at: string; reason: string; decided_by: string | null; decided_at: string | null; comment: string }
export type DsAudit = { id: number; ts: string; actor: string; action: string; comment: string; details: Record<string, unknown> | null }
export type DsHistory = { root_id: string; name: string; project: { id: string; name: string } | null; state: string | null; card: DsCard; default_version_id: string | null; default_how: string; versions: DsVersion[]; requests: DsRequest[]; audit: DsAudit[]; focus: string; admins_configured: boolean; retention_days: number
  retired_versions: { id: string; model_name: string; version: number; retired_at: string; retired_by: string; dataset_id: string }[] }
export type DsRow = { team_id?: string; team?: TeamRef; access?: 'shared' | 'none'; root_id: string; name: string; source: string; state: string; project_id: string | null; project: string | null; owner: string; created_by: string | null; description: string; tags: string[]; n_versions: number; n_live: number; ops: string[]; default_version_id: string | null; default_how: string; rows: number; n_cols: number | null; target: string | null; created_at: string; last_change: string; n_runs: number; versions_in_state?: string[] }
export type DsColStat = { column: string; kind: string; in: 'both' | 'a' | 'b'; missing_a: number | null; missing_b: number | null; mean_a?: number | null; mean_b?: number | null; median_a?: number | null; median_b?: number | null; top_a?: { value: string; share: number }[] | null; top_b?: { value: string; share: number }[] | null; psi?: number | null; band?: string | null; note?: string }
export type DsDiff = {
  a: { id: string; name: string; rows: number; target: string | null; sha256: string | null }; b: { id: string; name: string; rows: number; target: string | null; sha256: string | null }; same_dataset: boolean
  schema: { columns_added: string[]; columns_removed: string[]; type_changes: { column: string; from: string; to: string; kind_from: string; kind_to: string }[]; dtype_changes: { column: string; from: string; to: string }[]; n_columns: [number, number]; renamed?: { from: string; to: string }[] }
  rows: { a: number; b: number; matched: number; unchanged: number; changed: number; added: number; removed: number; matched_by: string; note: string; changed_examples: { row_a: number; row_b: number; key?: unknown; cells: { column: string; a: unknown; b: unknown }[] }[] }
  columns: DsColStat[]; checks: DsWarning[]
  /** DL3: b is an edit of a: its operations with their cells, old → new (capped per operation) */
  edit?: { by: string; reason: string | null; ops: DsEditLog[]; renamed: Record<string, string> }
  /** P12: every table of both versions, changed or not (same sha256 = unchanged); the diff above is of one table */
  tables?: { table: string; status: 'changed' | 'unchanged' | 'added' | 'removed' | string; a_sha256: string | null; b_sha256: string | null; rows: [number | null, number | null]; note?: string }[]
  a_table?: string; b_table?: string; masked_columns?: string[]
}
// DL3: edit-as-version (backend/app/dataset_edit.py)
export type DsEditOp = { op: string; [k: string]: unknown }
export type DsEditLog = { i: number; op: string; summary: string; rows?: number; cells?: number; column?: string; examples: { row: number | string; column?: string; old?: unknown; new?: unknown; deleted?: Record<string, unknown>; added?: Record<string, unknown> }[]; examples_truncated: boolean }
export type DsGrid = { dataset_id: string; table?: string; columns: { name: string; kind: string; type?: string; masked?: boolean; mask?: string }[]; rows: Record<string, unknown>[]; total: number; matching: number; offset: number; limit: number; q: string
  /** P20: PII masking of this page */
  masked_columns?: string[]; masks?: Record<string, string>; unmasked?: boolean; unmasked_refused?: { action: string; needed: string; role: string | null; message: string } }
export type DsEditSummary = { matched: number; changed: number; added: number; removed: number; columns_before: number; columns_after: number; renamed: Record<string, string>
  checks: DsUploadSummary['checks']; edit_log: DsEditLog[]; ops_summary: string[]; n_ops: number; duplicate_of?: string }
export type DsEditPreview = DsEditSummary & { preview: true; v: number; parent: string; rows: number; n_cols: number; sha256: string | null; would_be_default: boolean; columns: string[]; sample: Record<string, unknown>[]; note: string
  /** F-X5b (J1): integrity codes (`key_invalidated`, `relation_dropped`) the commit must acknowledge, listed in checks.warnings */
  needs_acknowledge?: string[] }
export type DsUsage = { dataset_id: string; datasets: string[]; runs: { id: string; name: string; status: string; dataset_id: string; label: string; created_at: string; primary_metric: string | null; primary_score: number | null
    /** P14: a run that read this version through a join frame */
    via_join?: { join_frame_id: string; tables: string[]; relation: 'joined_from' | 'merged_from' } }[]
  join_frames?: { id: string; dataset_id: string; tables: string[]; relation: string }[]; versions: { id: string; model_name: string; version: number; run_id: string; status: string; stage: string; approval: string; dataset_id: string; retired: boolean; retired_at: string | null }[]; champions: ChampionRequest[]; blocks_archive: string[]; blocks_archive_or_delete: string[] }
export type DsUploadSummary = { matched_by: string; matched: number; changed: number; added: number; removed: number; reason?: string
  checks: { warnings: DsWarning[]; columns_added: string[]; columns_removed: string[]; type_changes: { column: string; kind_from: string; kind_to: string; from: string; to: string }[]; rows_before: number; rows_after: number } }
/** DL4 (#11): the live dataset an uploaded CSV most looks like (POST /datasets/match writes nothing) */
export type DsMatchCandidate = { root_id: string; name: string; version_id: string; v: number; next_v: number; target: string | null; rows: number; score: number
  columns_in_common: number; columns_added: string[]; columns_removed: string[]; same_target: boolean; name_similarity: number; reasons: string[] }
export type DsMatch = { filename: string; rows: number; columns: string[]; match: DsMatchCandidate | null; others: DsMatchCandidate[] }
export type DsVerify = { root_id: string; ok: boolean; results: { id: string; v: number; ok: boolean | null; status: string; stored: string | null; now?: string | null; remedy?: string; splits?: { file: string; status: string }[] }[] }

export const data = {
  search: (f: { q?: string; project?: string; tag?: string; owner?: string; op?: string; since?: string; until?: string; state?: string }) => {
    const p = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][])
    return request<{ datasets: DsRow[]; total: number }>(`/data/search?${p}`).then((r) => r.datasets)
  },
  history: (id: string) => request<DsHistory>(`/datasets/${e(id)}/history`, { timeoutMs: 30_000 }),
  /** DL4: does this file look like a dataset already here? Nothing is written. */
  match: (file: File) => { const form = new FormData(); form.append('file', file); return request<DsMatch>('/datasets/match', { method: 'POST', body: form, timeoutMs: 120_000 }) },
  /** DL4 (M-d): write back split files changed outside the app, from the copy recorded when they were drawn */
  restoreSplits: (id: string, by: string, reason: string) => json<{ restored: string[]; verify: DsVerify }>(`/datasets/${e(id)}/restore-splits`, 'POST', { by, reason }),
  /** P12/U2: `table` diffs one table of a multi-table version (the primary when omitted). */
  diff: (a: string, b: string, table?: string | null) => request<DsDiff>(`/datasets/${e(a)}/diff/${e(b)}${table ? `?table=${e(table)}` : ''}`, { timeoutMs: 120_000 }),
  usage: (id: string) => request<DsUsage>(`/datasets/${e(id)}/usage`),
  setDefault: (id: string, version_id: string, by: string, reason = '') => json<{ default_version_id: string; v: number; rollback: boolean; previous: string | null; note: string }>(`/datasets/${e(id)}/default`, 'POST', { version_id, by, reason }),
  editCard: (id: string, fields: Partial<DsCard> & { name?: string }, by: string) => json<DsCard>(`/datasets/${e(id)}/card`, 'PATCH', { ...fields, by }),
  archive: (id: string, archived: boolean, by: string, reason = '', only_version = false) => json<{ ids: string[]; state: string }>(`/datasets/${e(id)}/archive`, 'POST', { archived, by, reason, only_version }),
  requestDelete: (id: string, by: string, reason: string, only_version = false, freeze_holdout = false) => json<DsRequest & { deleted?: unknown[] }>(`/datasets/${e(id)}/delete`, 'POST', { by, reason, only_version, freeze_holdout }),
  decideDelete: (rid: string, approve: boolean, by: string, comment = '', withdraw = false) => json<DsRequest>(`/dataset-requests/${e(rid)}/decide`, 'POST', withdraw ? { withdraw: true, by, comment } : { approve, by, comment }),
  /** P12/P20/U2: `table` names a table; `unmasked` + `reason` ask for clear PII values (audited; refused → still masked
   *  with `unmasked_refused` in audit mode, 403 in enforce). */
  grid: (id: string, o: { offset?: number; limit?: number; q?: string; table?: string | null; unmasked?: boolean; reason?: string } = {}) =>
    request<DsGrid>(`/datasets/${e(id)}/grid?offset=${o.offset ?? 0}&limit=${o.limit ?? 50}&q=${e(o.q ?? '')}${o.table ? `&table=${e(o.table)}` : ''}${o.unmasked ? `&unmasked=1&reason=${e(o.reason ?? '')}` : ''}`, { timeoutMs: 60_000 }),
  /** DL3: typed operations → a NEW version (nothing modified in place). preview=true writes nothing. */
  editPreview: (id: string, ops: DsEditOp[], by: string, table?: string | null) => json<DsEditPreview>(`/datasets/${e(id)}/edit`, 'POST', { ops, by, preview: true, ...(table ? { table } : {}) }, 120_000),
  /** F-X5b (J1): `acknowledge` names the integrity codes the preview listed (by code), else 400 acknowledge_required. */
  edit: (id: string, ops: DsEditOp[], by: string, reason: string, make_default: 'auto' | 'no' = 'auto', table?: string | null, acknowledge: string[] = []) =>
    json<import('./types').DatasetDetail & { created: boolean; duplicate_of?: string; edit: DsEditSummary; default?: { is_default: boolean; why: string } }>(`/datasets/${e(id)}/edit`, 'POST', { ops, by, reason, make_default, ...(table ? { table } : {}), ...(acknowledge.length ? { acknowledge } : {}) }, 300_000),
  adoptDiskFile: (id: string, by: string, reason: string) => json<import('./types').DatasetDetail & { upload: DsUploadSummary }>(`/datasets/${e(id)}/adopt-disk-file`, 'POST', { by, reason }, 300_000),
  verify: (id: string, by: string, all = false) => json<DsVerify>(`/datasets/${e(id)}/verify`, 'POST', { by, all }, 120_000),
  exportUrl: (id: string, by: string, format: 'zip' | 'csv' = 'zip') => `/api/datasets/${e(id)}/export?format=${format}&by=${e(by)}`,
}

// P18: who am I and which teams (the sidebar's team switcher).
export type MyTeam = TeamRef & { state: string; role: string }
export type Me = { principal: { id: string; display_name: string } | null; teams: MyTeam[]; default_team: string | null; authz_mode: string; poc_mode?: boolean; hint?: string }
export const teams = {
  me: () => request<Me>('/me'),
}

// P05/P06: staged uploads → check → commit (backend/app/lake/upload.py). The check writes nothing but tmp files.
export type UpColumn = { name: string; type: string; nullable: boolean; reason: string; non_null?: number }
export type UpIssue = { code: string; message: string; column?: string; examples?: unknown[]; n?: number }
/** P20: a column's PII tag as the upload check shows it (the root's tag for a next version, else the suggestion). */
export type UpPii = { column: string; tag: string; mask: string; source: string; why?: string }
/** P13: a delta's classification against the parent table (the check's counts = the diff's after commit). */
export type UpDelta = { key: string[]; key_from: 'plan' | 'parent'; op_column: string; rows: number; ops: { insert: number; update: number; delete: number; upsert: number }
  inserted: number; updated: number; updated_unchanged: number; deleted: number; missing: number; update_of_missing_key: number; insert_of_existing_key: number
  parent_rows: number; rows_after: number; columns_missing: string[]; columns_new: string[]; holdout_rows_deleted: number; mode: 'carry' | 'append' | 'rewrite'
  first_row_id?: number; change?: string }
export type TableAction = 'new' | 'rewrite' | 'append' | 'delta' | 'carry' | 'drop' | 'skip'
export type UpTable = { name: string; source_file: string; bytes: number; encoding?: string; separator?: string; n_rows: number; n_cols: number
  schema: UpColumn[]; kept_as_text?: string[]; preview: { columns: string[]; rows: unknown[][] }
  /** P12: multi-table checks */
  action?: TableAction; sheet?: string | null; blocked?: boolean; pii?: UpPii[]; delta?: UpDelta; next_version?: NextVersion
  append?: { parent_rows: number; rows_added: number; rows_after: number }; parent_rows?: number; rows_added?: number; rows_after?: number
  /** P21: the dataset contract's result on this table */
  contract?: 'pass' | 'warn' | 'block' }
export type UpKey = { table: string; columns: string[]; valid: boolean; n_rows: number; n_duplicates: number; n_nulls: number; message?: string }
export type UpRelation = { name: string; from: { table: string; columns: string[] }; to: { table: string; columns: string[] }; cardinality: string
  n_from_rows: number; n_orphans: number; n_from_missing: number; coverage: string | number; to_unique: boolean; to_duplicates?: number; suggested?: boolean; declared_by?: string }
export type UpSheet = { file: string; sheet: string; table: string; hidden: boolean; rows: number; columns: number; reader?: string; included: boolean; why?: string }
export type UpContract = { contract_id?: string; version?: number; status: 'pass' | 'warn' | 'block'; n_rules: number; n_blocks: number; n_warnings: number } | null
/** The plan PATCH (P12/P13): per-table actions, keys, deltas; relations; the primary table. */
export type TablePlan = { action?: TableAction; key?: string[]; match?: MatchSpec; op_column?: string; ops?: Record<string, string>; allow_new_columns?: boolean
  /** P12 / F-X3a M3: declared column types, and the date formats that settle an ambiguous dd/mm vs mm/dd */
  types?: Record<string, string>; formats?: Record<string, string> }
export type UploadPlan = { table?: string; tables?: Record<string, TablePlan>; relations?: { name?: string; from: { table: string; columns: string[] }; to: { table: string; columns: string[] }; cardinality: 'n:1' | '1:1' }[]
  primary_table?: string; match?: MatchSpec }
export type Upload = { upload_id: string; status: string; team_id: string; project_id: string | null; parent_version_id: string | null; root_id: string | null
  files: { n: number; filename: string; bytes: number; sha256: string }[]; plan: Record<string, unknown>; check_sha256: string | null; committed_version_id: string | null }
export type MatchSpec = { method: 'key' | 'declared' | 'row_hash' | 'none'; columns?: string[] }
export type MatchWarning = { code: string; needs_ack: boolean; message: string; n?: number; of?: number; share?: string; columns?: string[] }
export type MatchCandidate = { columns: string[]; score: number; uniqueness: number; non_null: number; overlap: number; id_like: boolean; declared: boolean; valid: boolean
  parent: { rows: number; distinct: number; nulls: number; duplicates: number }; new: { rows: number; distinct: number; nulls: number; duplicates: number } }
export type MatchMethod = MatchSpec & { label: string; valid: boolean; roles: string[]; problem?: { code: string; message: string }
  matched?: number; changed?: number; unchanged?: number; added?: number; removed?: number; holdout_identity_lost?: number; parent_holdout?: number
  holdout_identity_lost_share?: number; matched_share?: number; warnings: MatchWarning[]; needs_acknowledge: string[] }
export type NextVersion = { parent_version_id: string; root_id: string; table: string; parent_rows: number; parent_target: string | null; parent_holdout?: number
  candidates?: MatchCandidate[]; methods?: MatchMethod[]; preselect?: MatchSpec | null; chosen?: MatchMethod
  contract?: { warnings: DsWarning[]; columns_added: string[]; columns_removed: string[]; type_changes: { column: string; from: string; to: string }[]; rows_before: number; rows_after: number }
  commit_needs?: { acknowledge_token: string }; note?: string }
export type UpCheck = { upload_id: string; kind: string; tables: UpTable[]; warnings: (UpIssue & { table?: string })[]; blocks: (UpIssue & { table?: string })[]; notes: string[]; check_sha256: string; seconds: number; next_version?: NextVersion
  /** P12: a multi-table check */
  primary_table?: string; carried?: string[]; dropped?: string[]; keys?: UpKey[]; relations?: UpRelation[]; suggested_relations?: UpRelation[]; sheets?: UpSheet[]
  commit_needs?: { match?: string[] | Record<string, unknown> }; contract?: UpContract }
export type UpCommitted = { version_id: string; root_id: string; version_no: number; name: string; n_rows: number; n_cols: number; repeat: boolean; deduped?: string | null
  default?: { is_default: boolean; why?: string }; match?: { matched: number; changed: number; added: number; removed: number; holdout_identity_lost: number; matched_by: string; acknowledged: string[] }
  /** P12/P13: several tables */
  n_tables?: number; primary_table?: string; tables?: { table_name: string; n_rows: number; n_cols: number; change: string; table_sha256: string }[]
  deltas?: Record<string, UpDelta>; matches?: Record<string, { matched: number; changed: number; added: number; removed: number; holdout_identity_lost: number; matched_by: string }> }

export const uploads = {
  /** POST /uploads (multipart): stage one or several files (P12: every CSV/Parquet is a table, every workbook sheet one);
   *  `parent_version_id` makes it that dataset's next version. U2: `project_id` goes in the query too, because the
   *  route authorises `data.upload` from the query. */
  stage: (file: File | File[], o: { by: string; projectId?: string | null; parentVersionId?: string | null; name?: string }) => {
    const form = new FormData()
    for (const f of Array.isArray(file) ? file : [file]) form.append('file', f)
    form.append('by', o.by)
    if (o.projectId) form.append('project_id', o.projectId)
    if (o.name) form.append('name', o.name)
    const q = new URLSearchParams()
    if (o.parentVersionId) q.set('parent_version_id', o.parentVersionId)
    else if (o.projectId) q.set('project_id', o.projectId)
    return request<Upload>(`/uploads${q.toString() ? `?${q}` : ''}`, { method: 'POST', body: form, timeoutMs: 600_000 })
  },
  /** PATCH the plan ({match} to simulate another key; P12/P13 {tables, relations, primary_table}): the next /check uses it. */
  plan: (up: string, plan: UploadPlan | Record<string, unknown>) => json<Upload>(`/uploads/${e(up)}`, 'PATCH', plan),
  check: (up: string) => json<UpCheck>(`/uploads/${e(up)}/check`, 'POST', {}, 600_000),
  commit: (up: string, b: { by: string; reason?: string; check_sha256: string; match?: MatchSpec | Record<string, MatchSpec>; acknowledge?: string[]; make_default?: boolean }) =>
    json<UpCommitted>(`/uploads/${e(up)}/commit`, 'POST', b, 600_000),
  discard: (up: string) => request<unknown>(`/uploads/${e(up)}`, { method: 'DELETE' }),
}
