// U3 (P24, plan §11 item 3): retention per team, GC and purge. Nothing here runs on its own: a person starts every
// step (plan → dry run → run), every step is audited, and there is no default period anywhere.
import { request } from './client'

const e = encodeURIComponent
const json = <T,>(path: string, method: string, body: unknown, timeoutMs = 120_000) =>
  request<T>(path, { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs })

export type HoldRef = { id: string; case_ref: string; scope_kind: string; scope_id: string; placed_by?: string; placed_at?: string }

// -- retention ------------------------------------------------------------------------------------------------------
export const RT_CATEGORIES = ['versions', 'sources', 'join_caches', 'runs', 'staging'] as const
export type RtCategory = typeof RT_CATEGORIES[number]
/** The day keys per category (none has a default). versions also takes keep_min (a floor, 1 when unset) and keep_certified. */
export const RT_KEYS: Record<RtCategory, string[]> = { versions: ['keep_days'], sources: ['keep_days'], join_caches: ['evict_unused_days'], runs: ['slim_days', 'evict_days'], staging: ['days'] }
export const RT_WHAT: Record<RtCategory, { name: string; act: string }> = {
  versions: { name: 'Dataset versions', act: 'asks to delete the version (a second person decides; GC takes the bytes later)' },
  sources: { name: 'Upload sources', act: "drops the upload's original file ref (GC deletes the bytes once nothing else holds them)" },
  join_caches: { name: 'Join caches', act: "drops a join frame's cache ref when no run uses it (it can be rebuilt)" },
  runs: { name: 'Run predictors', act: 'slims or evicts the predictor files (the run record stays)' },
  staging: { name: 'Staged uploads', act: 'expires an upload that was never committed' },
}
export type RtItem = { id: string; kind?: string; action?: string; reason?: string; why?: string; holds?: (string | HoldRef)[]; age_days?: number | null
  version_id?: string; root_id?: string; v?: number; name?: string; upload_id?: string; blob_sha256?: string; bytes?: number; join_frame_id?: string
  run_id?: string; state?: string; would_free_bytes?: number; status?: string; created_by?: string }
export type RtCounts = Record<RtCategory, { candidates: number; kept: number; held: number }>
export type RtPlan = { team: string; dry_run: true; at: string; periods: Record<RtCategory, Record<string, number | boolean | null>>; periods_from: Record<string, string>
  no_period_set: RtCategory[]; categories: Record<RtCategory, { candidates: RtItem[]; kept: RtItem[]; held: RtItem[] }>; counts: RtCounts
  n_candidates: number; n_held: number; note: string; plan_id?: string; by?: string }
export type RtRun = { dry_run: false; plan_id: string; team_id: string; acted: number; failed: number; skipped: number; by_action: Record<string, number>
  actions: (RtItem & { ok: boolean; request_id?: string; status?: string; note?: string; error?: unknown; refs_left?: number })[]; skipped_items: { id: string; why: string }[] }
export type Periods = Partial<Record<RtCategory, Record<string, number | boolean>>>

export const retention = {
  /** Read-only: what a run would touch now. */
  plan: (team: string, periods?: Periods) => request<RtPlan>(`/retention/plan?team=${e(team)}${periods ? `&periods=${e(JSON.stringify(periods))}` : ''}`, { timeoutMs: 120_000 }),
  /** The same plan, stored under plan_id (24 h) and audited: what a run may act on. */
  dryRun: (team: string, by: string, periods?: Periods) => json<RtPlan & { plan_id: string }>('/retention/dry-run', 'POST', { team, by, ...(periods ? { periods } : {}) }),
  /** Acts on the dry run's items that are still candidates (an owner of the team or an admin). A plan runs once. */
  run: (team: string, plan_id: string, by: string, items?: string[]) => json<RtRun>('/retention/run', 'POST', { team, plan_id, by, confirm: 'retention', ...(items ? { items } : {}) }, 300_000),
}

// -- GC --------------------------------------------------------------------------------------------------------------
export type GcBlob = { sha256: string; key: string; kind: string; bytes: number; state: string; created_at: string; eligible_after?: string; holds?: HoldRef[]; purge_reason?: string }
export type GcArmed = { armed: boolean; armed_at: string | null; verify_all_passes: string[]; how: string }
export type GcPlan = { dry_run: true; armed: boolean; at: string; grace: { pending_hours: number; live_days: number; orphan_hours: number }
  blobs: Record<string, number>; blob_refs: number; candidates: GcBlob[]; candidate_bytes: number; resume_purging: GcBlob[]; held: GcBlob[]
  deleted_versions_with_refs: string[]; unreferenced_in_grace: GcBlob[]; orphans: { key: string; bytes: number; why?: string }[]; orphans_in_grace: number
  stale_tmp: { dir: string; bytes: number }[]; integrity: { key: string; finding: string }[]; note: string; plan_id: string; by: string }
export type GcRun = { dry_run: false; plan_id: string; deleted: number; bytes: number; skipped: number; failed: number; stale_tmp_removed: number
  aborted_multipart: unknown; deleted_versions_refs_dropped: number; store: string; deleted_blobs: { sha256: string; key: string; kind: string; bytes: number }[]
  skipped_blobs: { sha256: string; why: string }[]; failed_blobs: { sha256: string; error: string }[]; orphans_reported_only: number }
export type VerifyAll = { ok: boolean; checked: number; legacy_json?: { checked: number; failed: unknown[] }; gc_armed?: unknown; problems?: unknown[] }
export const gc = {
  armed: () => request<GcArmed>('/admin/lake/gc/armed'),
  dryRun: (by: string) => json<GcPlan>(`/admin/lake/gc?dry_run=1&by=${e(by)}`, 'POST', { by }),
  run: (plan_id: string, by: string) => json<GcRun>('/admin/lake/gc?dry_run=0', 'POST', { plan_id, by, confirm: 'gc' }, 600_000),
  /** Re-hash every live version (and legacy_json blob): the first pass that passes arms GC. */
  verifyAll: () => request<VerifyAll>('/admin/lake/verify-all', { timeoutMs: 600_000 }),
}

// -- purge -----------------------------------------------------------------------------------------------------------
export type PurgeImpact = { root_id: string; owner_team: string; table: string; columns: string[]; n_row_ids: number
  versions_with_values: { id: string; v: number; state: string }[]
  blobs: { sha256: string; why: string[]; state: string; bytes?: number; kind?: string; key?: string }[]
  refs: { blob_sha256: string; owner_kind: string; owner_id: string; role: string; team_id: string }[]
  versions: { id: string; root: string; name: string; version_no: number; state: string; project_id: string | null; team: string; other_dataset: boolean }[]
  runs: { id: string; status: string; dataset_id: string; project_id: string | null; team: string; via: string }[]
  join_frames: string[]; models: { id: string; model_name: string; version: number; approval: string; stage: string; run_id: string }[]
  champions: { id: string; status: string; project_id: string; run_id: string; version_id: string }[]
  shares: { share_id: string; grantee_team: string; scope: string; pii: string; grantee_runs: string[] }[]
  teams: string[]; by_team: Record<string, { versions: string[]; runs: string[]; refs: number; shares: string[] }>; holds: HoldRef[]; bytes: number; note: string }
export type Purge = { id: string; action: string; object_id: string; team_id: string; status: 'pending' | 'approved' | 'used' | 'rejected' | 'withdrawn'
  requested_by: string; requested_at: string; reason: string; decided_by: string | null; decided_at: string | null; comment?: string | null
  version_id: string; root_id: string; table: string; columns: string[]; new_version_id: string; teams_needed: string[]
  approvals: Record<string, { by: string; at: string; comment?: string }>; n_row_ids: number; waiting_for: string[]; impact: PurgeImpact
  executed?: { at: string; by: string; versions_deleted: string[]; run_pins_marked: number; refs_dropped: number; blobs: string[]; default_moved: { from: string; to: string } | null
    bytes_deleted?: number; bytes_failed?: unknown[] }
  blobs_deleted?: unknown[]; blobs_failed?: unknown[] }
export type PurgeRow = { id: string; status: Purge['status']; root_id: string; team_id: string; requested_by: string; requested_at: string; reason: string
  table: string; columns: string[]; teams_needed: string[]; approvals: Record<string, string> }
export type RunVerify = { ok: boolean | null; status?: string; run_id?: string; data?: { ok: boolean | null; [k: string]: unknown }; split?: { ok: boolean | null; [k: string]: unknown }
  purged?: { purge_ids: string[]; purged_at: string | null; blobs: string[]; pins: string[] }; [k: string]: unknown }
export const purges = {
  request: (version: string, b: { table?: string; columns?: string[]; row_ids?: number[]; reason: string }, by: string) => json<Purge>(`/datasets/${e(version)}/purge`, 'POST', { ...b, by }, 300_000),
  list: (team?: string) => request<{ purges: PurgeRow[] }>(`/purges${team ? `?team=${e(team)}` : ''}`).then((r) => r.purges),
  get: (id: string) => request<Purge>(`/purges/${e(id)}`),
  /** An owner of `team` (not the requester; one person per team). The last approval executes it, all-or-nothing. */
  decide: (id: string, team: string, approve: boolean, by: string, comment = '') => json<Purge>(`/purges/${e(id)}/decide`, 'POST', { team, approve, comment, by }, 600_000),
  withdraw: (id: string, by: string) => json<Purge>(`/purges/${e(id)}/decide`, 'POST', { withdraw: true, by }),
  resume: (id: string, by: string) => json<Purge>(`/purges/${e(id)}/resume`, 'POST', { by }, 600_000),
  /** Admin: re-hash what a run was trained on. A purged input answers status 'purged', data.ok null (not a mismatch). */
  verifyRun: (run: string) => request<RunVerify>(`/admin/lake/runs/${e(run)}/verify`, { timeoutMs: 300_000 }),
}

/** The P24 refusals as sentences (the API's own message follows). */
export const LIFECYCLE_CODES: Record<string, string> = {
  legal_hold: 'A legal hold covers this, so nothing in it can be deleted, retained away, collected or purged until a second admin releases the hold.',
  gc_disarmed: 'GC is not armed yet. Its deletes stay off until a verify-all pass has re-hashed every version on this deployment.',
  plan_used: 'This plan has already been run once. Make a new dry run.',
  plan_stale: 'This plan is older than 24 hours. Make a new dry run.',
  plan_not_found: 'The API has no such plan. Make a dry run first.',
  four_eyes: 'Four eyes: the person who asked cannot also decide.',
  owner_required: 'Only an owner of that team approves for it.',
  impact_grew: 'Since the request, the purge reaches more teams. An owner of each new team must approve too.',
  purge_pending: 'A purge of this dataset is already waiting. Decide or withdraw it first.',
  not_parquet: 'A version of this dataset is still CSV-era. Migrate it (P10) before a purge.',
  reason_required: 'A reason of at least 10 characters is required.',
  all_columns: 'A purge keeps at least one column. To remove everything, delete the dataset instead.',
  nothing_to_purge: 'Choose columns or row ids to purge.',
  already_approved: 'That team has already approved.',
  team_not_involved: 'That team is not involved in this purge.',
  not_pending: 'This purge has already been decided.',
  forbidden: 'You do not have permission for this.',
}
