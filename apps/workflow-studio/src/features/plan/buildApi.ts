/**
 * Calls for build runs. `list` answers `[]` while the engine has no builds endpoint (404/405), like the plan check, so
 * the Plan page and the Overview simply show no builds. `events` streams the SSE feed over fetch (it carries the same
 * credentials as every other call) and resolves when the stream closes.
 */
import { apiUrl, isApiError, parseError, request } from '@/shared/api/http'
import { consumeSSEStream } from '@/shared/lib/sseParser'
import type { Build, BuildEvent, StartBuild } from './buildTypes'

const enc = encodeURIComponent
const B = (p: string) => `/api/projects/${enc(p)}/builds`

export const buildApi = {
  start: (p: string, opts: StartBuild = {}) => request<Build>(B(p), { method: 'POST', body: opts }),
  list: async (p: string): Promise<Build[]> => {
    try {
      return await request<Build[]>(B(p))
    } catch (e) {
      if (isApiError(e, undefined, 404) || isApiError(e, undefined, 405)) return []
      throw e
    }
  },
  get: (p: string, id: string) => request<Build>(`${B(p)}/${enc(id)}`),
  cancel: (p: string, id: string) => request<Build>(`${B(p)}/${enc(id)}/cancel`, { method: 'POST', body: {} }),
  /** Go on past a `waiting_for_input` step (with whatever was provided; anything left out is skipped). */
  continue: (p: string, id: string) => request<Build>(`${B(p)}/${enc(id)}/continue`, { method: 'POST', body: {} }),
  /** Streams `BuildEvent`s to `onEvent`; throws when the stream cannot be opened. */
  events: async (p: string, id: string, onEvent: (e: BuildEvent) => void, signal?: AbortSignal): Promise<void> => {
    const res = await fetch(apiUrl(`${B(p)}/${enc(id)}/events`), { headers: { Accept: 'text/event-stream' }, signal })
    if (!res.ok) {
      let body: unknown
      try {
        body = await res.json()
      } catch {
        body = undefined
      }
      throw parseError(res.status, body)
    }
    if (!res.body) throw new Error('Empty event stream')
    await consumeSSEStream(res.body.getReader(), (data) => {
      try {
        onEvent(JSON.parse(data) as BuildEvent)
      } catch {
        /* a line that is not JSON (a keep-alive) */
      }
    })
  },
}
