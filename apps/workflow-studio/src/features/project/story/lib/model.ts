/**
 * The Overview story map's model (design `overview-mockup-v3.html`): the objects a project has, grouped into
 * lanes in flow order (Interface → Routes → Backend → Workflows → Data), the facts shown beside each one, and
 * the wires between them from real relationships. Pure: the hooks gather the API data, the components draw it.
 */
import type { ApiUsage, CodeModule, DataTable, MissingSecret, Route, RunListItem } from '@/shared/api/types'
import type { ApiAppListItem } from '@/shared/lib/api'
import type { ApiWorkflowDetail } from '@/shared/lib/backendTypes'
import type { DatasetItem } from '@/features/datasets/lib/types'
import type { WorkflowSummary } from '@/features/workflows-list/workflowMeta'
import { runTone } from '@/shared/components/ui/status-dot'
import { formatMs, parseTs, pluralize, timeAgo } from '../../lib/format'
import { sortRoutes } from '../../lib/routes'

export type ObjKind = 'app' | 'route' | 'module' | 'workflow' | 'table' | 'dataset'
export type LaneKey = 'apps' | 'routes' | 'modules' | 'workflows' | 'data'
export type Tone = 'ok' | 'bad' | 'warn'

export interface Fact {
  text: string
  tone?: Tone
}

export interface StoryObj {
  kind: ObjKind
  id: string
  /** `kind-id`: the DOM key wires and packets address */
  key: string
  name: string
  /** route method (the name is then the path) */
  method?: string
  mono?: boolean
  facts: Fact[]
  /** failing objects draw in the error colour */
  tone?: Tone
  /** a run is in flight on it right now (the only looping motion, like `animate-live-ring`) */
  busy: boolean
  /** the first missing secret this object uses */
  lock?: string
  calls24h: number
}

export interface Lane {
  key: LaneKey
  title: string
  objs: StoryObj[]
}

export interface Edge {
  from: string
  to: string
  /** calls in the last 24h; sets the wire's thickness (0 draws dashed) */
  volume: number
}

export interface RunStat {
  last: RunListItem
  calls24h: number
  p50: number | null
  failed: boolean
}

export interface WorkflowInfo {
  summary: WorkflowSummary
  detail?: ApiWorkflowDetail
  trigger: string
}

export interface StoryInput {
  apps: ApiAppListItem[]
  usage: Map<string, ApiUsage[]>
  routes: Route[]
  modules: CodeModule[]
  workflows: WorkflowInfo[]
  tables: DataTable[]
  datasets: DatasetItem[]
  missing: MissingSecret[]
  runs: RunListItem[]
  now: number
}

export const LANE_ORDER: LaneKey[] = ['apps', 'routes', 'modules', 'workflows', 'data']
export const LANE_TITLE: Record<LaneKey, string> = { apps: 'Interface', routes: 'Routes', modules: 'Backend', workflows: 'Workflows', data: 'Data' }

export const objKey = (kind: ObjKind, id: string) => `${kind}-${id}`

const DAY = 24 * 3600 * 1000

export function median(xs: number[]): number | null {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const m = Math.floor(s.length / 2)
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

/** Last run, 24h count and p50 per id (runs arrive newest first). */
export function statsBy(runs: RunListItem[], now: number, idOf: (r: RunListItem) => string | null): Map<string, RunStat> {
  const groups = new Map<string, RunListItem[]>()
  for (const r of runs) {
    const id = idOf(r)
    if (id) groups.set(id, [...(groups.get(id) ?? []), r])
  }
  const out = new Map<string, RunStat>()
  for (const [id, rs] of groups) {
    const recent = rs.filter((r) => (parseTs(r.start_time)?.getTime() ?? 0) >= now - DAY)
    out.set(id, {
      last: rs[0],
      calls24h: recent.length,
      p50: median(recent.map((r) => r.duration_ms).filter((d): d is number => d != null)),
      failed: runTone(rs[0].status) === 'err',
    })
  }
  return out
}

/** in flight: its newest run is still running */
const isBusy = (st: RunStat | undefined) => !!st && runTone(st.last.status) === 'running'

/** a route's last answer: 5xx or a failed run is bad, 4xx a warning (the caller's fault), else ok */
export function codeTone(st: RunStat): Tone {
  const c = st.last.status_code
  if (st.failed || (c != null && c >= 500)) return 'bad'
  if (c != null && c >= 400) return 'warn'
  return 'ok'
}

/** The dataset ids a workflow's Profile / Sample / Aggregate steps read. */
export function datasetRefs(def: ApiWorkflowDetail['definition'] | undefined): string[] {
  if (!def) return []
  const out = new Set<string>()
  for (const n of def.nodes) {
    if (n.type !== 'Profile' && n.type !== 'Sample' && n.type !== 'Aggregate') continue
    const v = n.parameters?.datasetId
    if (typeof v === 'string' && v) out.add(v)
  }
  return [...out]
}

export function findDataset(datasets: DatasetItem[], ref: string): DatasetItem | undefined {
  return datasets.find((d) => d.id === ref || d.dataset_id === ref || d.name === ref)
}

/** The route ids a workflow serves: its `handler_for` plus routes whose handler points at it. */
export function routesOf(w: WorkflowSummary, routes: Route[]): string[] {
  const ids = new Set((w.handler_for ?? []).map((h) => h.route_id))
  for (const r of routes) if (r.handler.type === 'workflow' && r.handler.workflow_id === w.id) ids.add(r.id)
  return [...ids]
}

/** The route ids an app calls, from its API usage (matched by route id, else by operation name). */
export function routesCalledBy(usage: ApiUsage[] | undefined, routes: Route[]): string[] {
  const ids = new Set<string>()
  for (const u of usage ?? []) {
    const r = routes.find((x) => (u.route_id && x.id === u.route_id) || x.name === u.operation)
    if (r) ids.add(r.id)
  }
  return [...ids]
}

/** Missing secret keys per referencing workflow / module id. */
export function locksBy(missing: MissingSecret[]): Map<string, string[]> {
  const out = new Map<string, string[]>()
  for (const m of missing)
    for (const ref of m.referenced_by) {
      const id = `${ref.kind}-${ref.id}`
      out.set(id, [...new Set([...(out.get(id) ?? []), m.key])])
    }
  return out
}

export function formatBytes(n: number | null | undefined): string | null {
  if (n == null) return null
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function formatCount(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`
  return n.toLocaleString()
}

export interface StoryModel {
  lanes: Lane[]
  absent: LaneKey[]
  edges: Edge[]
  byKey: Map<string, StoryObj>
  routeStats: Map<string, RunStat>
  workflowStats: Map<string, RunStat>
  locks: Map<string, string[]>
}

export function buildModel(input: StoryInput): StoryModel {
  const { apps, usage, routes, modules, workflows, tables, datasets, missing, runs, now } = input
  const routeStats = statsBy(runs, now, (r) => r.route_id)
  const workflowStats = statsBy(runs, now, (r) => r.workflow_id)
  const locks = locksBy(missing)
  const sorted = sortRoutes(routes)
  const edges: Edge[] = []
  const vol = (routeId: string) => routeStats.get(routeId)?.calls24h ?? 0

  const appObjs: StoryObj[] = apps.map((a) => {
    const calls = routesCalledBy(usage.get(a.id), routes)
    for (const r of calls) edges.push({ from: objKey('app', a.id), to: objKey('route', r), volume: vol(r) })
    const published = !!a.published_at
    return {
      kind: 'app',
      id: a.id,
      key: objKey('app', a.id),
      name: a.name,
      facts: [published ? { text: 'published', tone: 'ok' } : { text: 'draft' }, { text: calls.length ? `calls ${pluralize(calls.length, 'route')}` : 'no routes yet' }],
      busy: false,
      calls24h: calls.reduce((n, r) => n + vol(r), 0),
    }
  })

  const routeObjs: StoryObj[] = sorted.map((r) => {
    const st = routeStats.get(r.id)
    if (r.handler.type === 'python') edges.push({ from: objKey('route', r.id), to: objKey('module', r.handler.module_id), volume: vol(r.id) })
    if (r.handler.type === 'workflow') edges.push({ from: objKey('route', r.id), to: objKey('workflow', r.handler.workflow_id), volume: vol(r.id) })
    const stub = r.handler.type === 'stub'
    const facts: Fact[] = stub
      ? [{ text: 'stub', tone: 'warn' }, { text: 'no calls' }]
      : st
        ? [
            { text: String(st.last.status_code ?? st.last.status), tone: codeTone(st) },
            { text: timeAgo(st.last.start_time, now) },
            { text: `${st.calls24h}/24h` },
            ...(st.p50 != null ? [{ text: `p50 ${formatMs(st.p50)}` }] : []),
          ]
        : [{ text: 'no calls yet' }]
    return {
      kind: 'route',
      id: r.id,
      key: objKey('route', r.id),
      name: r.path,
      method: r.method,
      mono: true,
      facts,
      tone: st?.failed ? 'bad' : undefined,
      busy: isBusy(st),
      calls24h: st?.calls24h ?? 0,
    }
  })

  const moduleObjs: StoryObj[] = [...modules]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((m) => {
      const n = routes.filter((r) => r.handler.type === 'python' && r.handler.module_id === m.id)
      const calls = n.reduce((s, r) => s + vol(r.id), 0)
      const lock = locks.get(`module-${m.id}`)?.[0]
      return {
        kind: 'module',
        id: m.id,
        key: objKey('module', m.id),
        name: `${m.name}.py`,
        mono: true,
        facts: [{ text: pluralize(m.route_count ?? n.length, 'API') }, { text: `edited ${timeAgo(m.updated_at, now)}` }, ...(lock ? [{ text: `needs ${lock}`, tone: 'warn' as const }] : [])],
        busy: false,
        lock,
        calls24h: calls,
      }
    })

  const workflowObjs: StoryObj[] = workflows.map(({ summary: w, detail, trigger }) => {
    const st = workflowStats.get(w.id)
    const lock = locks.get(`workflow-${w.id}`)?.[0]
    for (const ref of datasetRefs(detail?.definition)) {
      const d = findDataset(datasets, ref)
      if (d) edges.push({ from: objKey('workflow', w.id), to: objKey('dataset', d.id), volume: st?.calls24h ?? 0 })
    }
    const facts: Fact[] = [
      st ? (st.failed ? { text: 'failing', tone: 'bad' } : { text: 'ok', tone: 'ok' }) : { text: 'no runs yet' },
      { text: trigger },
      ...(st ? [{ text: timeAgo(st.last.start_time, now) }, { text: `${st.calls24h}/24h` }] : []),
      ...(lock ? [{ text: `needs ${lock}`, tone: 'warn' as const }] : []),
    ]
    return {
      kind: 'workflow',
      id: w.id,
      key: objKey('workflow', w.id),
      name: w.name,
      facts,
      tone: st?.failed ? 'bad' : undefined,
      busy: isBusy(st),
      lock,
      calls24h: st?.calls24h ?? 0,
    }
  })

  const dataObjs: StoryObj[] = [
    ...tables.map(
      (t): StoryObj => ({
        kind: 'table',
        id: t.id,
        key: objKey('table', t.id),
        name: t.name,
        mono: true,
        facts: [{ text: 'table' }, { text: `${formatCount(t.row_count)} rows` }],
        busy: false,
        calls24h: 0,
      }),
    ),
    ...datasets.map(
      (d): StoryObj => ({
        kind: 'dataset',
        id: d.id,
        key: objKey('dataset', d.id),
        name: d.name,
        mono: true,
        facts: [{ text: 'dataset' }, ...[formatBytes(d.size_bytes) ?? `${formatCount(d.row_count)} rows`].map((text) => ({ text })), ...(d.missing ? [{ text: 'file missing', tone: 'bad' as const }] : [])],
        tone: d.missing ? 'bad' : undefined,
        busy: false,
        calls24h: 0,
      }),
    ),
  ]

  const all: Record<LaneKey, StoryObj[]> = { apps: appObjs, routes: routeObjs, modules: moduleObjs, workflows: workflowObjs, data: dataObjs }
  const lanes = LANE_ORDER.filter((k) => all[k].length).map((k) => ({ key: k, title: LANE_TITLE[k], objs: all[k] }))
  const byKey = new Map(lanes.flatMap((l) => l.objs.map((o) => [o.key, o] as const)))
  return {
    lanes,
    absent: LANE_ORDER.filter((k) => !all[k].length),
    edges: edges.filter((e) => byKey.has(e.from) && byKey.has(e.to)),
    byKey,
    routeStats,
    workflowStats,
    locks,
  }
}

/** The wires a run travels, in order: (app →) route → handler, or workflow → datasets. */
export function runPath(run: Pick<RunListItem, 'route_id' | 'workflow_id'>, edges: Edge[]): Edge[] {
  const out: Edge[] = []
  if (run.route_id) {
    const r = objKey('route', run.route_id)
    const inbound = edges.find((e) => e.to === r && e.from.startsWith('app-'))
    if (inbound) out.push(inbound)
    const handler = edges.find((e) => e.from === r)
    if (handler) {
      out.push(handler)
      if (handler.to.startsWith('workflow-')) out.push(...edges.filter((e) => e.from === handler.to).slice(0, 1))
    }
    return out
  }
  if (run.workflow_id) out.push(...edges.filter((e) => e.from === objKey('workflow', run.workflow_id!)).slice(0, 1))
  return out
}

/** Stroke width for a wire carrying `n` calls a day. */
export const wireWidth = (n: number) => 1 + Math.log10(n + 1) * 1.3

/** the plate colour per kind (entity hues) */
export const KIND_HUE: Record<ObjKind, string> = {
  app: 'var(--hue-apps)',
  route: 'var(--hue-runs)',
  module: 'var(--hue-backend)',
  workflow: 'var(--hue-flow)',
  table: 'var(--hue-data)',
  dataset: 'var(--hue-data)',
}
export const LANE_HUE: Record<LaneKey, string> = {
  apps: KIND_HUE.app,
  routes: KIND_HUE.route,
  modules: KIND_HUE.module,
  workflows: KIND_HUE.workflow,
  data: KIND_HUE.table,
}
export const KIND_LABEL: Record<ObjKind, string> = { app: 'Interface', route: 'Route', module: 'Module', workflow: 'Workflow', table: 'Table', dataset: 'Dataset' }
