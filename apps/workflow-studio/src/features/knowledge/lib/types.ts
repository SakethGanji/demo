/**
 * The Knowledge API (engine `/api/knowledge/*`, `schemas/knowledge.py`): the capability graph the AI builder reads.
 * A project-scoped call takes `project_id` (the resolved id, not the slug); a node outside the project's slice answers
 * 404. A team-scoped call omits `project_id` (and `team_id`, so the engine uses the default team) and reads every
 * project's slice at once.
 */

/** Which slice of the graph a page reads: one project's, or the whole team's (every project plus the shared nodes). */
export type KnowledgeScope = { kind: 'project'; projectId: string } | { kind: 'team' }

export const TEAM_SCOPE: KnowledgeScope = { kind: 'team' }

/** A project that owns at least one node of a team-scoped graph. */
export interface KgProjectRef {
  id: string
  slug: string
  name: string
}

export type KgSource = 'seed' | 'extracted' | 'learned' | 'human' | (string & {})
export type KgStatus = 'active' | 'stale' | 'proposed' | 'rejected' | (string & {})

export interface KgNode {
  id: string
  /** `concept:approval`, `node:Profile`, `tpl:dataset-report-email`, `pattern:<hash>`, `role:<slug>` */
  key: string
  /** concept · capability · pattern · role · app (the engine may add more) */
  kind: string
  subkind: string | null
  scope: string
  visibility: string
  team_id?: string | null
  project_id?: string | null
  label: string
  summary: string
  aliases: string[]
  /** the listing drops the heavy pattern props (`skeleton`, `reference_bundle`); the detail has them */
  props: Record<string, unknown>
  source: KgSource
  status: KgStatus
  validated_count: number
  last_validated_at: string | null
  updated_at: string | null
}

export interface KgEdge {
  id: number
  src_id: string
  rel: string
  dst_id: string
  props: Record<string, unknown>
  source: string
}

export interface KgGraph {
  nodes: KgNode[]
  edges: KgEdge[]
  /** the engine caps a graph at 400 nodes */
  truncated: boolean
  /** team scope: the projects owning at least one returned node; `[]` (or absent) in project scope */
  projects?: KgProjectRef[]
}

export interface KgNodeRef {
  id: string
  key: string
  label: string
  kind: string
  subkind: string | null
}

export interface KgNeighbour {
  /** `out`: this node is the source; `in`: this node is the target */
  direction: 'in' | 'out'
  node: KgNodeRef
  props: Record<string, unknown>
  source: string
}

export interface KgLearnEvent {
  id: number
  at: string
  project_id: string | null
  team_id: string | null
  actor: string | null
  trigger: string
  proposal_id: string | null
  summary: string
  payload: Record<string, unknown>
}

export interface KgNodeDetail {
  node: KgNode
  /** relation -> neighbours in both directions */
  neighbours: Record<string, KgNeighbour[]>
  evidence: Record<string, unknown>[]
  learn_events: KgLearnEvent[]
}

export interface KgMatrixRow {
  concept: KgNodeRef
  /** capability subkind (node_type, template, ...) -> how many implement the concept */
  implemented_by: Record<string, number>
  /** every implementer, patterns and apps included (so it can exceed the sum of `implemented_by`) */
  implemented_total: number
  patterns: number
  validated: number
  apps: number
  teams: string[]
  roles: KgNodeRef[]
  gap: boolean
}

export interface KgMatrixTotals {
  concepts: number
  implemented: number
  gaps: number
  patterns: number
  validated: number
  apps: number
  subworkflows: number
}

export interface KgCapabilities {
  rows: KgMatrixRow[]
  totals: KgMatrixTotals
}

export interface KgMemoryPage {
  items: KgLearnEvent[]
  next_cursor: string | null
}

export interface KgRebuildReport {
  added: number
  updated: number
  staled: number
  tagged: number
  proposed: number
  warnings: string[]
  /** team scope: what the rebuild did in each project (the totals above are their sum) */
  projects?: KgRebuildProject[]
}

export interface KgRebuildProject {
  project_id: string
  added: number
  updated: number
  staled: number
}
