// Live or mock: the app talks to the API when /api/health answers, and falls back to its in-browser simulation when it doesn't.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from './client'
import type { ApiStatus, Health } from './types'

export type ApiMode = 'checking' | 'live' | 'mock'

/** `status` (live only): the machine, versions, the active run and the queue, polled every 3 s. */
type ApiState = { mode: ApiMode; health: Health | null; status: ApiStatus | null; recheck: () => void }

const ApiContext = createContext<ApiState>({ mode: 'mock', health: null, status: null, recheck: () => {} })

/** Polls the health endpoint: every 5 s while in mock mode (so starting the API flips the UI to live), every 20 s while live. */
export function ApiProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [mode, setMode] = useState<ApiMode>('checking')
  const [tick, setTick] = useState(0)
  const [status, setStatus] = useState<ApiStatus | null>(null)
  const recheck = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    let alive = true
    api.health()
      .then((h) => { if (alive) { setHealth(h); setMode('live') } })
      .catch(() => { if (alive) { setHealth(null); setMode('mock') } })
    return () => { alive = false }
  }, [tick])

  useEffect(() => {
    if (mode === 'checking') return
    const t = setTimeout(recheck, mode === 'live' ? 20_000 : 5_000)
    return () => clearTimeout(t)
  }, [mode, tick, recheck])

  useEffect(() => {
    if (mode !== 'live') { setStatus(null); return }
    let alive = true
    const poll = () => api.status().then((st) => alive && setStatus(st)).catch(() => {})
    poll()
    const t = setInterval(poll, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [mode])

  return <ApiContext.Provider value={{ mode, health, status, recheck }}>{children}</ApiContext.Provider>
}

export const useApi = () => useContext(ApiContext)
