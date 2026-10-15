/**
 * Where saved code references each secret (UI-DESIGN §3.5 "Referenced by", §4.5 client scan):
 * `studio.secret('K')` / `$vars.K` in each module's source, plus the engine's
 * `GET …/secrets/missing` `referenced_by` (which also covers workflows). Also collects the literal
 * `https?://host` strings per module, to prefill allowed hosts.
 */
import { useMemo } from 'react'
import { useQueries } from '@tanstack/react-query'
import { modulesApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { useModules, useRoutes } from '@/features/project/hooks/useProjectData'
import { moduleNameOf } from '@/features/project/lib/routes'
import type { MissingSecret, Route } from '@/shared/api/types'

export interface CodeRef {
  kind: 'module' | 'workflow'
  /** module name (no .py) or workflow name */
  name: string
  /** workflow id when kind=workflow */
  id?: string
  line?: number
  node?: string
  /** the route whose handler contains `line` */
  route?: Route
}

const SECRET_RE = /studio\.secret\(\s*['"]([A-Za-z_]\w*)['"]\s*\)|\$vars\.([A-Za-z_]\w*)/g
const HOST_RE = /https?:\/\/([A-Za-z0-9.-]+)/g

export function useSecretReferences(pid: string, missing: MissingSecret[] | undefined) {
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const sources = useQueries({
    queries: (modules.data ?? []).map((m) => ({
      queryKey: qk.module(pid, m.id),
      queryFn: () => modulesApi.get(pid, m.id),
      staleTime: 30_000,
    })),
  })
  const srcKey = sources.map((s) => s.data?.source_sha ?? '').join(',')

  return useMemo(() => {
    const refs = new Map<string, CodeRef[]>()
    const hosts = new Map<string, string[]>()
    const routeAt = (module: string, line: number): Route | undefined => {
      const inModule = (routes.data ?? []).filter(
        (r) => r.handler.type === 'python' && moduleNameOf(r, modules.data) === module && r.handler.line != null,
      )
      let best: Route | undefined
      for (const r of inModule) {
        const l = r.handler.type === 'python' ? r.handler.line ?? 0 : 0
        if (l <= line && (!best || l > (best.handler.type === 'python' ? best.handler.line ?? 0 : 0))) best = r
      }
      return best
    }
    const add = (key: string, ref: CodeRef) => {
      const list = refs.get(key) ?? []
      if (!list.some((x) => x.kind === ref.kind && x.name === ref.name && x.line === ref.line && x.node === ref.node))
        list.push(ref)
      refs.set(key, list)
    }
    for (const s of sources) {
      const mod = s.data
      if (!mod?.source) continue
      const lines = mod.source.split('\n')
      const hs = new Set<string>()
      lines.forEach((text, i) => {
        for (const m of text.matchAll(SECRET_RE)) add(m[1] ?? m[2], { kind: 'module', name: mod.name, line: i + 1, route: routeAt(mod.name, i + 1) })
        for (const m of text.matchAll(HOST_RE)) hs.add(m[1])
      })
      hosts.set(mod.name, [...hs])
    }
    for (const ms of missing ?? []) {
      for (const r of ms.referenced_by) {
        if (r.kind === 'module') add(ms.key, { kind: 'module', name: r.name, line: r.line, route: routeAt(r.name, r.line) })
        else add(ms.key, { kind: 'workflow', name: r.name, id: r.id, node: r.node })
      }
    }
    for (const list of refs.values()) list.sort((a, b) => a.name.localeCompare(b.name) || (a.line ?? 0) - (b.line ?? 0))
    const hostsFor = (key: string) => [
      ...new Set((refs.get(key) ?? []).filter((r) => r.kind === 'module').flatMap((r) => hosts.get(r.name) ?? [])),
    ]
    return { refs, hostsFor }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [srcKey, missing, routes.data, modules.data])
}
