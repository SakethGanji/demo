/**
 * Preview token for the builder iframe (§5.2): `POST /api/projects/{P}/apps/{A}/preview-token`,
 * refetched at `expires_at − 5 min`; `refresh()` forces a new one (used once on 401 token_expired).
 */
import { useCallback, useEffect, useRef } from 'react'
import { appApiApi } from '@/shared/api/endpoints'
import { parseUtc } from '../lib/appModel'

const EARLY_MS = 5 * 60_000

export function usePreviewToken(pid: string, appId: string) {
  const identity = `${pid}:${appId}`
  const currentIdentity = useRef(identity)
  currentIdentity.current = identity
  const tokenRef = useRef<{ token: string; exp: number; identity: string } | null>(null)
  const inflight = useRef<{ identity: string; promise: Promise<string | null> } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refetch = useRef<() => Promise<string | null>>(() => Promise.resolve(null))

  const fetchToken = useCallback((): Promise<string | null> => {
    if (currentIdentity.current !== identity) return Promise.resolve(null)
    if (inflight.current?.identity === identity) return inflight.current.promise
    const p = appApiApi
      .previewToken(pid, appId)
      .then((t) => {
        if (currentIdentity.current !== identity) return null
        const exp = parseUtc(t.expires_at)
        tokenRef.current = { token: t.token, exp, identity }
        if (timer.current) clearTimeout(timer.current)
        const wait = Math.max(30_000, exp - EARLY_MS - Date.now())
        timer.current = setTimeout(() => void refetch.current(), Math.min(wait, 2 ** 31 - 1))
        return t.token
      })
      .catch(() => null)
      .finally(() => {
        if (inflight.current?.promise === p) inflight.current = null
      })
    inflight.current = { identity, promise: p }
    return p
  }, [pid, appId, identity])

  useEffect(() => {
    refetch.current = fetchToken
  }, [fetchToken])

  useEffect(() => {
    currentIdentity.current = identity
    tokenRef.current = null
    void fetchToken()
    return () => {
      if (timer.current) clearTimeout(timer.current)
      currentIdentity.current = ''
    }
  }, [fetchToken, identity])

  const get = useCallback(async () => {
    const t = tokenRef.current
    if (t?.identity === identity && t.exp - EARLY_MS > Date.now()) return t.token
    return fetchToken()
  }, [fetchToken, identity])

  const refresh = useCallback(() => {
    tokenRef.current = null
    return fetchToken()
  }, [fetchToken])

  return { get, refresh }
}
