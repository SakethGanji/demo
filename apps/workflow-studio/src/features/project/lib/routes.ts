import type { CodeModule, Route } from '@/shared/api/types'

/** The module that owns a python route, if any. */
export function moduleNameOf(r: Route, modules: CodeModule[] | undefined): string | null {
  if (r.handler.type !== 'python') return null
  const h = r.handler
  if (h.module_name) return h.module_name
  return modules?.find((m) => m.id === h.module_id)?.name ?? null
}

export interface RouteGroup {
  key: string
  label: string
  module: CodeModule | null
  routes: Route[]
}

/** Routes grouped by owning module (modules in name order), then workflow/stub routes. */
export function groupRoutes(routes: Route[], modules: CodeModule[] | undefined): RouteGroup[] {
  const byModule = new Map<string, Route[]>()
  const other: Route[] = []
  for (const r of routes) {
    if (r.handler.type === 'python') {
      const id = r.handler.module_id
      byModule.set(id, [...(byModule.get(id) ?? []), r])
    } else other.push(r)
  }
  const groups: RouteGroup[] = []
  for (const m of [...(modules ?? [])].sort((a, b) => a.name.localeCompare(b.name))) {
    const rs = byModule.get(m.id)
    if (rs?.length) groups.push({ key: m.id, label: `${m.name}.py`, module: m, routes: sortRoutes(rs) })
  }
  if (other.length) groups.push({ key: 'other', label: 'workflows & stubs', module: null, routes: sortRoutes(other) })
  return groups
}

const ORDER = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
export function sortRoutes(rs: Route[]): Route[] {
  return [...rs].sort((a, b) => a.path.localeCompare(b.path) || ORDER.indexOf(a.method) - ORDER.indexOf(b.method))
}

export const AUTH_LABEL: Record<string, string> = { none: 'public', app_token: 'app token', api_key: 'api key' }
