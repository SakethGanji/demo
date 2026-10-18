/** Response shapes of the Knowledge API, copied from `apps/workflow-engine/src/schemas/knowledge.py` (frozen as built). */

export interface NodeView {
  id: string
  key: string
  kind: string
  subkind?: string | null
  scope: string
  visibility: string
  team_id?: string | null
  project_id?: string | null
  label: string
  summary: string
  aliases: string[]
  props: Record<string, unknown>
  source: string
  status: string
  validated_count: number
  last_validated_at?: string | null
  updated_at?: string | null
}

export interface EdgeView {
  id: number
  src_id: string
  rel: string
  dst_id: string
  props: Record<string, unknown>
  source: string
}

export interface GraphResponse {
  nodes: NodeView[]
  edges: EdgeView[]
  truncated: boolean
}

export interface NodeRef {
  id: string
  key: string
  label: string
  kind: string
  subkind?: string | null
}

export interface Neighbour {
  /** "out": this node is the source; "in": this node is the target */
  direction: 'out' | 'in' | string
  node: NodeRef
  props: Record<string, unknown>
  source: string
}

export interface LearnEvent {
  id: number
  at: string
  project_id?: string | null
  team_id?: string | null
  actor?: string | null
  trigger: string
  proposal_id?: string | null
  summary: string
  payload: Record<string, unknown>
}

export interface NodeDetail {
  node: NodeView
  neighbours: Record<string, Neighbour[]>
  evidence: Record<string, unknown>[]
  learn_events: LearnEvent[]
}

export interface MatrixRow {
  concept: NodeRef
  implemented_by: Record<string, number>
  implemented_total: number
  patterns: number
  validated: number
  apps: number
  teams: string[]
  roles: NodeRef[]
  gap: boolean
}

export interface MatrixTotals {
  concepts: number
  implemented: number
  gaps: number
  patterns: number
  validated: number
  apps: number
  subworkflows: number
}

export interface CapabilitiesResponse {
  rows: MatrixRow[]
  totals: MatrixTotals
}

export interface MemoryResponse {
  items: LearnEvent[]
  next_cursor?: string | null
}

export interface RebuildResponse {
  added: number
  updated: number
  staled: number
  tagged: number
  proposed: number
  warnings: string[]
}
