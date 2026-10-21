/**
 * What a build has written so far, from its live stream (`file`, `route` and `preview` events): every file written this
 * build with its latest content (module drafts then the saved source arrive as writes of the same path), the newest
 * write, the planned routes' status (stub, built, passing, failing) and how many times the app preview should reload.
 * The build itself (steps, logs) stays with `applyBuildEvent`; a late joiner gets none of these events and reads the
 * tree from the files API instead (the routes then come from the plan, see `liveRoutes`).
 */
import type { Build, BuildEvent, FileBuilder, RouteStatus } from './buildTypes'

export interface LiveFile {
  path: string
  builder: FileBuilder
  step_key: string | null
  op: 'write' | 'delete'
  content: string
  /** false while a module draft is still being generated */
  done: boolean
  truncated: boolean
  /** writes of this path this build */
  writes: number
  /** when the latest write arrived (ms) */
  at: number
}

export interface LiveRoute {
  /** "GET /runs" */
  key: string
  method: string
  path: string
  name: string
  status: RouteStatus
  step_key: string | null
}

export interface LiveState {
  files: Record<string, LiveFile>
  /** paths in the order they were first written */
  order: string[]
  /** the latest path written (deletes excluded) */
  newest: string | null
  routes: Record<string, LiveRoute>
  routeOrder: string[]
  /** the app preview: the latest app/version saved and how many saves (a reload each) */
  preview: { app_id: string; version_id: string | null; n: number } | null
}

export const EMPTY_LIVE: LiveState = { files: {}, order: [], newest: null, routes: {}, routeOrder: [], preview: null }

const latestWrite = (files: Record<string, LiveFile>): string | null =>
  Object.values(files).reduce<LiveFile | null>((a, f) => (f.op === 'write' && (!a || f.at >= a.at) ? f : a), null)?.path ?? null

/** The live state after one event; events it does not own return the same state. */
export function applyLiveEvent(s: LiveState, ev: BuildEvent, now: number = Date.now()): LiveState {
  if (ev.type === 'file') {
    const prev = s.files[ev.path]
    const f: LiveFile = {
      path: ev.path,
      builder: ev.builder,
      step_key: ev.step_key ?? null,
      op: ev.op,
      content: ev.op === 'delete' ? '' : ev.content,
      done: ev.done,
      truncated: !!ev.truncated,
      writes: (prev?.writes ?? 0) + 1,
      at: now,
    }
    const files = { ...s.files, [ev.path]: f }
    return {
      ...s,
      files,
      order: prev ? s.order : [...s.order, ev.path],
      newest: ev.op === 'write' ? ev.path : s.newest === ev.path ? latestWrite(files) : s.newest,
    }
  }
  if (ev.type === 'route') {
    const key = `${ev.method} ${ev.path}`
    const prev = s.routes[key]
    // a stub announcement never takes back what a module save already built
    const status = prev && ev.status === 'stub' ? prev.status : ev.status
    const r: LiveRoute = { key, method: ev.method, path: ev.path, name: ev.name, status, step_key: ev.step_key ?? prev?.step_key ?? null }
    return { ...s, routes: { ...s.routes, [key]: r }, routeOrder: prev ? s.routeOrder : [...s.routeOrder, key] }
  }
  if (ev.type === 'preview') {
    return { ...s, preview: { app_id: ev.app_id, version_id: ev.version_id, n: (s.preview?.n ?? 0) + 1 } }
  }
  return s
}

/** Paths written (not deleted) this build, in first-write order. */
export const writtenPaths = (s: LiveState): string[] => s.order.filter((p) => s.files[p]?.op === 'write')

const BUILT = new Set(['passed', 'partial'])

/**
 * The routes to show: what the stream said, then any planned route it has not mentioned (a late joiner missed the
 * announcements): built when its module's step is through, else stub.
 */
export function liveRoutes(s: LiveState, planned: Array<{ key: string; module: string }> = [], build?: Build | null): LiveRoute[] {
  const out = s.routeOrder.map((k) => s.routes[k])
  const steps = new Map((build?.steps ?? []).map((st) => [st.key, st.status]))
  for (const r of planned) {
    if (s.routes[r.key]) continue
    const [method, ...rest] = r.key.split(' ')
    const step_key = `module:${r.module}`
    const status: RouteStatus = BUILT.has(steps.get(step_key) ?? '') ? 'built' : 'stub'
    out.push({ key: r.key, method, path: rest.join(' '), name: '', status, step_key })
  }
  return out
}

export function routeCounts(routes: LiveRoute[]): Record<RouteStatus, number> & { total: number } {
  const c = { stub: 0, built: 0, passing: 0, failing: 0, total: routes.length }
  for (const r of routes) c[r.status] += 1
  return c
}

/**
 * The build's writes with the app chat's laid over them (LIVE-FILES §5: after the build the chat's builders stream the
 * same events): the chat's version of a file or route wins, its newest write leads, and preview reloads add up.
 */
export function mergeLive(build: LiveState, chat: LiveState): LiveState {
  if (!chat.order.length && !chat.routeOrder.length && !chat.preview) return build
  const add = (a: string[], b: string[]) => [...a, ...b.filter((k) => !a.includes(k))]
  return {
    files: { ...build.files, ...chat.files },
    order: add(build.order, chat.order),
    newest: chat.newest ?? build.newest,
    routes: { ...build.routes, ...chat.routes },
    routeOrder: add(build.routeOrder, chat.routeOrder),
    preview: chat.preview ? { ...chat.preview, n: chat.preview.n + (build.preview?.n ?? 0) } : build.preview,
  }
}
