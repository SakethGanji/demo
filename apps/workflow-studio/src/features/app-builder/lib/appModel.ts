/**
 * App-builder model helpers (UI-DESIGN §5): the studio block inside `definition`, the generated
 * client path, slugs, and route ↔ URL matching for the preview session.
 */
import type { ApiAppDetail, ApiAppFile } from '@/shared/lib/api'
import type { Route } from '@/shared/api/types'

/** Default definition for a new app (same as the projects page uses). */
export const BLANK_DEFINITION: Record<string, unknown> = { sourceCode: null }

/** The generated typed client; the server injects it, the studio never stores it (§5.2). */
export const CLIENT_PATH = 'api/client.ts'

export interface StudioBlock {
  route_ids?: string[]
  [k: string]: unknown
}

export function studioOf(app: Pick<ApiAppDetail, 'definition'> | null | undefined): StudioBlock {
  const s = (app?.definition as { studio?: unknown } | undefined)?.studio
  return s && typeof s === 'object' ? (s as StudioBlock) : {}
}

export function routeIdsOf(app: Pick<ApiAppDetail, 'definition'> | null | undefined): string[] {
  const ids = studioOf(app).route_ids
  return Array.isArray(ids) ? ids.filter((x): x is string => typeof x === 'string') : []
}

export function isClientPath(path: string): boolean {
  return path.replace(/^\.?\/?(src\/)?/, '') === CLIENT_PATH
}

export function stripClient<T extends { path: string }>(files: T[] | null | undefined): T[] {
  return (files ?? []).filter((f) => !isClientPath(f.path))
}

export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
}

export const SLUG_RE = /^[a-z0-9](?:-?[a-z0-9])*$/

export function slugError(slug: string): string | null {
  if (!slug) return null
  if (slug.length < 3) return 'At least 3 characters'
  if (!SLUG_RE.test(slug)) return 'Lowercase letters, numbers and single hyphens'
  return null
}

/** Entry file content (App.tsx), what the engine stores as `source_code`. */
export function entrySource(files: ApiAppFile[]): string {
  return (files.find((f) => /^(src\/)?[Aa]pp\.tsx$/.test(f.path)) ?? files[0])?.content ?? ''
}

/** `api.<op>(` calls in app files (client side, for the chat "Uses …" row). */
export function usedOperations(files: { path: string; content: string }[]): string[] {
  const out = new Set<string>()
  for (const f of stripClient(files)) {
    for (const m of f.content.matchAll(/\bapi\.([A-Za-z_$][\w$]*)\s*\(/g)) out.add(m[1])
  }
  return [...out]
}

/** Find the route a `/api/p/<slug>/…` request hit (method + path template). */
export function matchRoute(routes: Route[], method: string, path: string): Route | undefined {
  const m = method.toUpperCase()
  const clean = path.split('?')[0].replace(/\/+$/, '') || '/'
  return routes.find((r) => {
    if (r.method !== m) return false
    const tpl = r.path.replace(/\/+$/, '') || '/'
    const body = tpl
      .split(/\{[^}]+\}/)
      .map((part) => part.replace(/[.*+?^$()|[\]\\{}]/g, '\\$&'))
      .join('[^/]+')
    return new RegExp('^' + body + '$').test(clean)
  })
}

/** Parse an ISO timestamp as UTC even when the engine omitted the `Z` (§9 C13). */
export function parseUtc(iso: string): number {
  return Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
}
