// P1: projects, the lineage tree, paired compare, tags and notes, champions, retention (backend/app/catalog).
import { request } from './client'

export type NodeKind = 'dataset' | 'run' | 'version' | 'project'
export type Project = { id: string; name: string; root_dataset_id: string; description: string; created_at: string; n_datasets: number; n_runs: number; last_run_at: string | null; champion: { version_id: string; run_id: string; decided_by: string; decided_at: string } | null }
export type ChampionRequest = { id: string; project_id: string; version_id: string; run_id: string; status: 'pending' | 'approved' | 'rejected' | 'superseded'; requested_by: string; requested_at: string; decided_by: string | null; decided_at: string | null; comment: string }
export type LNode = {
  kind: 'dataset' | 'run' | 'version'; id: string; depth: number; tags: string[]; notes: number
  name?: string; rows?: number; op?: string | null; parent_id?: string | null; sha256?: string | null
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
  metric?: string; score_a?: number; score_b?: number; delta?: number; ci?: [number, number]; level?: number; B?: number; rows?: number; same_rows?: boolean; verdict?: string; method?: string; sign?: string; warning?: string
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
  untag: (kind: NodeKind, id: string, tag: string, by: string) => request<Notes>(`/annotations/${kind}/${e(id)}/tags/${e(tag)}?by=${e(by)}`, { method: 'DELETE' }),
  requestChampion: (project: string, version_id: string, by: string, comment = '') => json<ChampionRequest>(`/projects/${e(project)}/champion`, 'POST', { version_id, by, comment }),
  decideChampion: (id: string, approve: boolean, by: string, comment = '') => json<ChampionRequest>(`/champions/${e(id)}/decide`, 'POST', { approve, by, comment }),
}

/** Runs of one project (filtered in SQL on the indexed project_id), every kind, the same shape as api.runs.list. */
export const projectRuns = (projectId: string | null) =>
  request<{ runs: import('./types').Run[] }>(`/runs?kind=all${projectId ? `&project_id=${e(projectId)}` : ''}`).then((r) => r.runs)
