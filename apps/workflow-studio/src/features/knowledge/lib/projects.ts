/**
 * Project attribution for the team-wide Knowledge page: which project a node belongs to, its stable colour, the
 * Projects filter's chips and counts, which projects touch a concept, and the workflow a node points at. Pure.
 *
 * A project's colour is one of the studio's entity hues, picked by hashing the project id, so a project looks the
 * same on every visit and on every page that shows it. The kind colour (`--viz-*`) stays the node's main colour;
 * the project colour is only its orbit dot and chip.
 */
import type { GraphModel } from './graph'
import { hashString } from './layout'
import type { KgNeighbour, KgNode, KgProjectRef } from './types'

/** The filter key of a node that belongs to no project (global or team-wide knowledge). */
export const SHARED = 'shared'

/** Muted entity hues: distinct from the kind colours, readable in both themes. */
export const PROJECT_PALETTE = [
  'var(--hue-backend)',
  'var(--hue-data)',
  'var(--hue-apps)',
  'var(--hue-flow)',
  'var(--hue-secrets)',
  'var(--hue-runs)',
  'var(--hue-release)',
  'var(--hue-ai)',
] as const

/** Shared knowledge is neutral. */
export const SHARED_COLOR = 'var(--hue-home)'

/** The stable colour of a project, from its id alone (null or empty: the shared colour). */
export function projectColor(projectId: string | null | undefined): string {
  if (!projectId) return SHARED_COLOR
  return PROJECT_PALETTE[hashString(projectId) % PROJECT_PALETTE.length]
}

/** The project a node is filed under: its `project_id`, or `SHARED`. */
export const projectKey = (n: Pick<KgNode, 'project_id'>): string => n.project_id || SHARED

/** How many nodes each project (and `SHARED`) owns. */
export function projectCounts(nodes: Pick<KgNode, 'project_id'>[]): Map<string, number> {
  const m = new Map<string, number>()
  for (const n of nodes) {
    const k = projectKey(n)
    m.set(k, (m.get(k) ?? 0) + 1)
  }
  return m
}

export interface ProjectChipModel {
  /** a project id, or `SHARED` */
  key: string
  label: string
  slug: string | null
  color: string
  count: number
}

/**
 * The Projects filter: "Shared" first (when anything is shared), then each project that owns a node, the biggest
 * first (ties by name). A project id the graph names but `projects` does not still gets a chip, labelled by its id.
 */
export function projectChips(projects: KgProjectRef[] | undefined, nodes: Pick<KgNode, 'project_id'>[]): ProjectChipModel[] {
  const counts = projectCounts(nodes)
  const refs = new Map((projects ?? []).map((p) => [p.id, p]))
  const out: ProjectChipModel[] = []
  for (const [key, count] of counts) {
    if (key === SHARED) continue
    const p = refs.get(key)
    out.push({ key, label: p?.name || p?.slug || key, slug: p?.slug ?? null, color: projectColor(key), count })
  }
  out.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
  const shared = counts.get(SHARED) ?? 0
  return shared ? [{ key: SHARED, label: 'Shared', slug: null, color: SHARED_COLOR, count: shared }, ...out] : out
}

/** Projects whose nodes link to `conceptId` (by any relation), in name order. */
export function conceptProjects(model: GraphModel, conceptId: string, projects: ReadonlyMap<string, KgProjectRef>): string[] {
  const seen = new Set<string>()
  for (const e of model.incident.get(conceptId) ?? []) {
    const other = e.src_id === conceptId ? e.dst_id : e.src_id
    const pid = model.byId.get(other)?.project_id
    if (pid) seen.add(pid)
  }
  const name = (id: string) => projects.get(id)?.name ?? id
  return [...seen].sort((a, b) => name(a).localeCompare(name(b)))
}

/** `wf:<id>` -> `<id>`. */
const wfId = (key: string) => (key.startsWith('wf:') && key.length > 3 ? key.slice(3) : null)

/**
 * The workflow a node is about: a workflow capability (`wf:<id>`) is its own; a pattern points at the workflow
 * that validated it (a `wf:` neighbour). Null when there is none.
 */
export function workflowIdFor(node: Pick<KgNode, 'key' | 'kind'>, neighbours?: Record<string, KgNeighbour[]>): string | null {
  const own = wfId(node.key)
  if (own) return own
  if (node.kind !== 'pattern' || !neighbours) return null
  const rels = Object.keys(neighbours).sort((a, b) => Number(b === 'validated_by') - Number(a === 'validated_by') || a.localeCompare(b))
  for (const rel of rels) for (const nb of neighbours[rel]) {
    const id = wfId(nb.node.key)
    if (id) return id
  }
  return null
}
