/**
 * Table lineage (DESIGN §6 "table lineage"): which routes write the table and which read it, and which
 * apps use one of those routes. There is no route→table field on the engine, so this scans each module's
 * source for `studio.db.table("<name>")` (the same client-side scan `useSecretReferences` does for secrets):
 * a route's handler spans from its `line` to the next handler in that module; a handler uses the table
 * when it calls `db.table("<name>")` directly or through an alias assigned from it, and it writes when it
 * calls `.insert / .update / .delete / .upsert` on that table.
 */
import { useMemo } from 'react'
import { useQueries } from '@tanstack/react-query'
import { modulesApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { appsApi } from '@/shared/lib/api'
import { useModules, useProjectApps, useRoutes } from '@/features/project/hooks/useProjectData'
import { moduleNameOf } from '@/features/project/lib/routes'
import type { Route } from '@/shared/api/types'

export interface TableUse {
  write: boolean
  read: boolean
}

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const WRITE_OPS = '(?:insert|insert_many|update|update_many|delete|delete_many|upsert|remove)'
const READ_OPS = '(?:list|get|find|query|count|select|first|all|sum|aggregate|search)'

/** How one handler's source touches `table` (null when it doesn't). */
export function scanHandler(body: string, aliases: string[], table: string): TableUse | null {
  const direct = `db\\.table\\(\\s*["']${esc(table)}["']\\s*\\)`
  const heads = [direct, ...aliases.map((a) => `\\b${esc(a)}\\b`)]
  const head = `(?:${heads.join('|')})`
  if (!new RegExp(head).test(body)) return null
  const write = new RegExp(`${head}\\s*\\.\\s*${WRITE_OPS}\\s*\\(`).test(body)
  const readOp = new RegExp(`${head}\\s*\\.\\s*${READ_OPS}\\s*\\(`).test(body)
  return { write, read: readOp || !write }
}

/** Per route id: how its handler uses `table`, scanning one module's source. */
export function scanModule(source: string, table: string, handlers: { id: string; line: number }[]): Map<string, TableUse> {
  const out = new Map<string, TableUse>()
  const lines = source.split('\n')
  const aliasRe = new RegExp(`([A-Za-z_]\\w*)\\s*=\\s*[\\w.]*db\\.table\\(\\s*["']${esc(table)}["']\\s*\\)`, 'g')
  const aliases = [...source.matchAll(aliasRe)].map((m) => m[1])
  const sorted = [...handlers].sort((a, b) => a.line - b.line)
  sorted.forEach((h, i) => {
    const end = sorted[i + 1]?.line ?? lines.length + 1
    const use = scanHandler(lines.slice(Math.max(0, h.line - 1), end - 1).join('\n'), aliases, table)
    if (use) out.set(h.id, use)
  })
  return out
}

export interface TableLineage {
  writers: Route[]
  readers: Route[]
  apps: { id: string; name: string }[]
  /** false while module sources are still loading */
  ready: boolean
}

export function useTableLineage(pid: string, table: string): TableLineage {
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const apps = useProjectApps(pid)
  const sources = useQueries({
    queries: (modules.data ?? []).map((m) => ({
      queryKey: qk.module(pid, m.id),
      queryFn: () => modulesApi.get(pid, m.id),
      staleTime: 30_000,
    })),
  })
  const details = useQueries({
    queries: (apps.data ?? []).map((a) => ({ queryKey: qk.app(a.id), queryFn: () => appsApi.get(a.id), staleTime: 30_000 })),
  })
  const srcKey = sources.map((s) => s.data?.source_sha ?? '').join(',')
  const appKey = details.map((d) => `${d.data?.id}:${d.data?.updated_at}`).join(',')
  const ready = !modules.isPending && !routes.isPending && sources.every((s) => !s.isPending)

  return useMemo(() => {
    const uses = new Map<string, TableUse>()
    for (const s of sources) {
      const mod = s.data
      if (!mod?.source) continue
      const handlers = (routes.data ?? [])
        .filter((r) => r.handler.type === 'python' && moduleNameOf(r, modules.data) === mod.name && r.handler.line != null)
        .map((r) => ({ id: r.id, line: r.handler.type === 'python' ? (r.handler.line ?? 1) : 1 }))
      for (const [id, u] of scanModule(mod.source, table, handlers)) uses.set(id, u)
    }
    const byId = new Map((routes.data ?? []).map((r) => [r.id, r]))
    const pick = (f: (u: TableUse) => boolean) => [...uses].filter(([, u]) => f(u)).map(([id]) => byId.get(id)!).filter(Boolean)
    const readIds = new Set([...uses].filter(([, u]) => u.read).map(([id]) => id))
    const appList: { id: string; name: string }[] = []
    for (const d of details) {
      const a = d.data
      if (!a) continue
      const ids = ((a.definition as { studio?: { route_ids?: string[] } })?.studio?.route_ids ?? []) as string[]
      if (ids.some((id) => readIds.has(id))) appList.push({ id: a.id, name: a.name })
    }
    return { writers: pick((u) => u.write), readers: pick((u) => u.read), apps: appList, ready }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [srcKey, appKey, routes.data, modules.data, table, ready])
}
