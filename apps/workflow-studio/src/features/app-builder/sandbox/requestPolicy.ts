export interface PreviewRoute { method: string; path: string }

/** Only the selected project's application routes may cross the parent fetch bridge. */
export function previewRequestUrl(url: string, method: string, origin: string, slug: string, routes: PreviewRoute[], workflowPaths: string[] = []): URL {
  const target = new URL(url, origin)
  const prefix = `/api/p/${encodeURIComponent(slug)}`
  if (target.origin !== origin || target.username || target.password || /%2f|%5c|%2e/i.test(target.pathname)) {
    throw new Error('Preview request blocked: use the selected project API')
  }
  if (method === 'POST' && workflowPaths.includes(target.pathname)) return target
  const path = target.pathname.slice(prefix.length)
  const allowed = target.pathname.startsWith(prefix + '/') && routes.some((route) => {
    const pattern = route.path.split(/\{[^}]+\}/).map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^/]+')
    return route.method.toUpperCase() === method && new RegExp('^' + pattern + '/?$').test(path)
  })
  if (!allowed) throw new Error('Preview request blocked: route is outside this app’s API access')
  return target
}
