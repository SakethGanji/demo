export interface PreviewRoute { method: string; path: string }

/**
 * Only the selected project's application routes may cross the parent fetch bridge. `scope: 'preview'` (the Studio
 * preview) opens every route of the app's own project, since the scan that fills `route_ids` can miss a call;
 * `'published'` keeps the app's declared routes only. Other projects and hosts stay blocked in both.
 */
export function previewRequestUrl(url: string, method: string, origin: string, slug: string, routes: PreviewRoute[], workflowPaths: string[] = [], scope: 'preview' | 'published' = 'published'): URL {
  const target = new URL(url, origin)
  const prefix = `/api/p/${encodeURIComponent(slug)}`
  if (target.origin !== origin || target.username || target.password || /%2f|%5c|%2e/i.test(target.pathname)) {
    throw new Error('Preview request blocked: use the selected project API')
  }
  if (method === 'POST' && workflowPaths.includes(target.pathname)) return target
  const path = target.pathname.slice(prefix.length)
  const inProject = target.pathname.startsWith(prefix + '/')
  const allowed = inProject && (scope === 'preview' || routes.some((route) => {
    const pattern = route.path.split(/\{[^}]+\}/).map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^/]+')
    return route.method.toUpperCase() === method && new RegExp('^' + pattern + '/?$').test(path)
  }))
  if (!allowed) throw new Error('Preview request blocked: route is outside this app’s API access')
  return target
}

export interface PreviewUser { id: string; name: string; roles: string[] }

/** The "view as" headers a preview request carries; an empty user means the app itself (no headers). */
export function previewUserHeaders(user: PreviewUser | null | undefined): Record<string, string> {
  const id = user?.id.trim()
  if (!user || !id) return {}
  const out: Record<string, string> = { 'X-Preview-User': id }
  if (user.name.trim()) out['X-Preview-User-Name'] = user.name.trim()
  const roles = user.roles.map((r) => r.trim()).filter(Boolean)
  if (roles.length) out['X-Preview-User-Roles'] = roles.join(',')
  return out
}

/** The app cannot set the view-as headers itself; the toolbar's chosen user (if any) replaces them. */
export function withViewAs(headers: Record<string, string>, viewAs: PreviewUser | null | undefined): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(headers)) if (!k.toLowerCase().startsWith('x-preview-user')) out[k] = v
  return { ...out, ...previewUserHeaders(viewAs) }
}
