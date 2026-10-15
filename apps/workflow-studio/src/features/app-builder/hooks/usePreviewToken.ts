/**
 * Preview token for the builder iframe (§5.2): `POST /api/projects/{P}/apps/{A}/preview-token`,
 * refetched at `expires_at − 5 min`; `refresh()` forces a new one (used once on 401 token_expired).
 */
import { useCallback, useEffect, useRef } from 'react'
import { appApiApi } from '@/shared/api/endpoints'
import { parseUtc } from '../lib/appModel'

const EARLY_MS = 5 * 60_000

export function usePreviewToken(pid: string, appId: string) {
  const tokenRef = useRef<{ token: string; exp: number } | null>(null)
  const inflight = useRef<Promise<string | null> | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refetch = useRef<() => Promise<string | null>>(() => Promise.resolve(null))

  const fetchToken = useCallback((): Promise<string | null> => {
    if (inflight.current) return inflight.current
    const p = appApiApi
      .previewToken(pid, appId)
      .then((t) => {
        const exp = parseUtc(t.expires_at)
        tokenRef.current = { token: t.token, exp }
        if (timer.current) clearTimeout(timer.current)
        const wait = Math.max(30_000, exp - EARLY_MS - Date.now())
        timer.current = setTimeout(() => void refetch.current(), Math.min(wait, 2 ** 31 - 1))
        return t.token
      })
      .catch(() => null)
      .finally(() => {
        inflight.current = null
      })
    inflight.current = p
    return p
  }, [pid, appId])

  useEffect(() => {
    refetch.current = fetchToken
  }, [fetchToken])

  useEffect(() => {
    tokenRef.current = null
    void fetchToken()
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [fetchToken])

  const get = useCallback(async () => {
    const t = tokenRef.current
    if (t && t.exp - EARLY_MS > Date.now()) return t.token
    return fetchToken()
  }, [fetchToken])

  const refresh = useCallback(() => {
    tokenRef.current = null
    return fetchToken()
  }, [fetchToken])

  return { get, refresh }
}
