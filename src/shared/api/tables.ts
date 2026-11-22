// U2: the tables of a version (P12), saved joins and join frames (P14), runs on a table or a join (P12/P14/P21).
import { request } from './client'
import type { Run, RunCreate } from './types'
import type { UpRelation } from './catalog'

const e = encodeURIComponent
const json = <T,>(path: string, method: string, body: unknown, timeoutMs = 30_000) =>
  request<T>(path, { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs })

export type VTableKey = { columns: string[]; kind: string; valid: boolean; declared_by?: string; n_duplicates: number; n_nulls: number }
export type VTable = {
  name: string; table_name: string; ordinal: number; primary: boolean; table_sha256: string; n_rows: number; n_cols: number; bytes: number
  change: string; from_version: string | null; from_table: string | null; source_file?: string | null; row_id_min?: number; row_id_max?: number
  keys: VTableKey[]; files: { part: number; sha256: string; role: string; n_rows: number }[]
  splits: { id: string; split_key: string; split_sha256: string; n_holdout: number; carried_from: string | null }[]
  schema?: { name: string; type: string; nullable?: boolean; reason?: string }[]
}
export type VersionTables = { id: string; root_id: string; version_no: number; storage: string; data_sha256: string; n_tables: number; n_rows: number; n_cols: number
  primary_table: string; target: string | null; op: string; created_by: string; created_at: string; reason: string | null; parent_id: string | null; state: string | null
  tables: VTable[]; relations: UpRelation[] }
export type Relations = { version_id: string; relations: UpRelation[]; suggested: UpRelation[]; tables: string[] }
export type NewVersion = { version_id?: string; id?: string; version_no?: number; deduped?: string | null; [k: string]: unknown }

/** P14 join spec (`ensemble.join/1`). */
export type JoinAgg = { name: string; fn: 'count' | 'count_distinct' | 'sum' | 'mean' | 'min' | 'max'; column?: string; window?: { date: string; relative_to: string; days: number } }
export type JoinTableRef = string | { root_id?: string; version_id: string; table: string }
export type JoinStep = { type: 'n:1' | '1:1'; table: JoinTableRef; on: (string | [string, string])[]; columns?: string[] | null; prefix?: string }
  | { type: 'aggregate'; table: JoinTableRef; on: (string | [string, string])[]; aggs: JoinAgg[]; prefix?: string }
export type JoinSpec = { grain: string; joins: JoinStep[]; where?: null; format?: string }
export type JoinCoverage = { join: number; table: string; version_id: string; type: string; on: [string, string][]; table_rows: number; grain_rows: number
  matched: number; coverage: number | null; orphans: number; missing_key: number; rows_aggregated?: number; columns: string[]
  key?: { how?: 'declared' | 'checked'; unique?: boolean; repeated?: number } }
export type JoinValidation = { version_id: string; grain: string; grain_rows: number; inputs: FrameInput[]; coverage: JoinCoverage[]; columns: { name: string; from: string }[]; n_columns: number }
export type SavedJoin = { id: string; root_id: string; team_id: string; name: string; spec_version: number; spec_sha256: string; state: string; created_at: string; created_by: string
  spec: JoinSpec; validation?: JoinValidation; frames?: JoinFrame[] }
export type FrameInput = { role: string; version_id: string; root_id: string; table: string; table_sha256: string; n_rows: number }
export type JoinFrame = { id: string; join_id: string; version_id: string; grain_table: string; frame_sha256: string; n_rows: number; cache_blob: string; created_at: string; created_by: string
  inputs: FrameInput[]; schema: { name: string; type: string }[]; join_name?: string; spec_version?: number
  cached?: boolean; seconds?: number; bytes?: number; rematerialised?: boolean; same_frame_as_version?: string; coverage?: JoinCoverage[]
  join?: { id: string; name: string; spec_version: number; spec_sha256: string }
  verify?: { ok: boolean; cache: { recorded: string; now: string; ok: boolean }; inputs: { version_id: string; table: string; pinned: string; now: string; ok: boolean }[] }; runs?: { run_id: string }[] }
export type FrameRows = { join_frame_id: string; n_rows: number; columns: string[]; rows: unknown[][]; row_ids: number[]; masked_columns: string[]; masks: Record<string, string> }

/** The plain messages for P14's 400 codes (the API's own message follows). */
export const JOIN_CODES: Record<string, string> = {
  one_to_many: 'A joined row would repeat a grain row (1:n). Join on a key of that table, or aggregate it instead.',
  not_one_to_one: 'The grain side repeats, so this is not 1:1: use n:1.',
  column_unknown: 'A column named in the join is not in that table.',
  table_unknown: 'That table is not in this version.',
  join_types: 'The two join columns hold different kinds of value (a number against text).',
  agg_type: 'sum and mean need a number column.',
  column_collision: 'Two frame columns would have the same name: set a prefix.',
  where_unsupported: 'A filter (where) is not supported yet: filter the grain table with an edit first.',
  same_spec: 'This exact join is already saved under another name.',
  label_not_in_grain: 'The label must be a column of the grain table (the holdout is the grain\'s split).',
  grain_violated: 'The frame would not have exactly one row per grain row.',
  bad_spec: 'The join is not well formed.',
  input_not_shared: 'A table from another team is joined, and that team has not shared it with yours.',
  pii_masked_share: 'The share of that table masks PII, so its PII columns cannot be joined: leave them out (columns) or ask for an unmasked share.',
}

export const tables = {
  version: (root: string, v: string) => request<VersionTables>(`/datasets/${e(root)}/versions/${e(v)}`, { timeoutMs: 30_000 }),
  /** PUT a key on a table: a NEW version with every table carried (200 deduped when it is already declared). */
  setKey: (v: string, table: string, columns: string[], by: string, reason = '') =>
    json<NewVersion>(`/datasets/${e(v)}/tables/${e(table)}/key`, 'PUT', { columns, by, ...(reason ? { reason } : {}) }, 120_000),
  relations: (v: string) => request<Relations>(`/datasets/${e(v)}/relations`, { timeoutMs: 30_000 }),
  /** PUT a relation: a NEW version, orphans counted. */
  setRelation: (v: string, r: { name: string; from: { table: string; columns: string[] }; to: { table: string; columns: string[] }; cardinality?: string }, by: string) =>
    json<NewVersion>(`/datasets/${e(v)}/relations/${e(r.name)}`, 'PUT', { from: r.from, to: r.to, cardinality: r.cardinality ?? 'n:1', by }, 120_000),
}

export const joins = {
  list: (root: string) => request<{ root_id: string; joins: SavedJoin[] }>(`/datasets/${e(root)}/joins`).then((r) => r.joins),
  get: (root: string, id: string) => request<SavedJoin>(`/datasets/${e(root)}/joins/${e(id)}`),
  /** Validated against `version_id` (else the root's default) before anything is saved: 400 with a code when it is bad. */
  save: (root: string, name: string, spec: JoinSpec, by: string, version_id?: string | null) =>
    json<SavedJoin>(`/datasets/${e(root)}/joins`, 'POST', { name, spec, by, ...(version_id ? { version_id } : {}) }, 120_000),
  validate: (root: string, id: string, version_id?: string) => json<JoinValidation & { join_id: string }>(`/datasets/${e(root)}/joins/${e(id)}/validate`, 'POST', version_id ? { version_id } : {}, 120_000),
  /** Materialise (or reuse: 200 `cached`) the frame of a saved join on a version. */
  frame: (root: string, v: string, join_id: string, by: string) => json<JoinFrame>(`/datasets/${e(root)}/versions/${e(v)}/frames`, 'POST', { join_id, by }, 600_000),
  frames: (root: string, v: string) => request<{ version_id: string; frames: JoinFrame[] }>(`/datasets/${e(root)}/versions/${e(v)}/frames`).then((r) => r.frames),
  getFrame: (root: string, id: string, verify = false) => request<JoinFrame>(`/datasets/${e(root)}/frames/${e(id)}${verify ? '?verify=1' : ''}`, { timeoutMs: 120_000 }),
  /** U2: the first rows of a frame (masked like every row read). */
  rows: (root: string, id: string, limit = 20) => request<FrameRows>(`/datasets/${e(root)}/frames/${e(id)}/rows?limit=${limit}`, { timeoutMs: 60_000 }),
}

/** A run on one table of a version, or on a saved join's frame. P21: a version another team shared needs `project_id`
 *  (a project of yours), else 400 `project_required`. */
export type TrainBody = RunCreate & { table?: string; join_id?: string; project_id?: string }
export const trainOn = (body: TrainBody) =>
  request<Run>('/runs', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs: 120_000 })
