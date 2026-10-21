/**
 * Build queries and mutations. `useLiveBuild` follows one build over its SSE feed (snapshot, step changes, progress
 * lines, done; and the files, routes and previews the builders produce, as `files`) and falls back to polling GET every 3 s when the stream cannot be opened or drops before `done`. When a
 * build finishes, the builds list, the plan and its conformance check are refetched (the build changed what they show).
 */
import { useEffect, useReducer, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isApiError } from '@/shared/api/http'
import { buildApi } from './buildApi'
import { activeBuild, applyBuildEvent, isActive } from './buildLogic'
import { applyLiveEvent, EMPTY_LIVE } from './liveBuild'
import type { Build, BuildEvent, StartBuild } from './buildTypes'
import { planKeys } from './usePlan'

export const buildKeys = {
  list: (p: string) => ['project', p, 'builds'] as const,
  one: (p: string, id: string) => ['project', p, 'builds', id] as const,
}

const POLL_MS = 3000

/** The project's builds, newest first; refetched every few seconds while one is active. */
export function useBuilds(pid: string) {
  return useQuery({
    queryKey: buildKeys.list(pid),
    queryFn: () => buildApi.list(pid),
    refetchInterval: (q) => (activeBuild(q.state.data) ? POLL_MS : false),
  })
}

function useAfterBuild(pid: string) {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: buildKeys.list(pid), exact: true })
    void qc.invalidateQueries({ queryKey: planKeys.plan(pid) })
    void qc.invalidateQueries({ queryKey: ['project', pid, 'apps'] })
  }
}

/** One build, live. A new id starts from a clean state (`null` clears both reducers when the stream changes). */
export function useLiveBuild(pid: string, id: string | null) {
  const [live, dispatch] = useReducer((s: Build | null, ev: BuildEvent | null) => (ev ? applyBuildEvent(s, ev) : null), null)
  const [files, dispatchFile] = useReducer((s: typeof EMPTY_LIVE, ev: BuildEvent | null) => (ev ? applyLiveEvent(s, ev) : EMPTY_LIVE), EMPTY_LIVE)
  const [polling, setPolling] = useState(false)
  const after = useAfterBuild(pid)
  const query = useQuery({
    queryKey: buildKeys.one(pid, id ?? ''),
    queryFn: async () => {
      const b = await buildApi.get(pid, id as string)
      if (polling && !isActive(b.status)) after()
      return b
    },
    enabled: !!id,
    refetchInterval: (q) => (polling && (!q.state.data || isActive(q.state.data.status)) ? POLL_MS : false),
  })

  useEffect(() => {
    dispatch(null)
    dispatchFile(null)
    setPolling(false)
    if (!id) return
    const ctl = new AbortController()
    let done = false
    const onEvent = (ev: BuildEvent) => {
      if (ev.type === 'done') {
        done = true
        after()
      }
      if (ev.type === 'file' || ev.type === 'route' || ev.type === 'preview') dispatchFile(ev)
      else dispatch(ev)
    }
    buildApi
      .events(pid, id, onEvent, ctl.signal)
      .then(() => {
        if (!done && !ctl.signal.aborted) setPolling(true)
      })
      .catch(() => {
        if (!ctl.signal.aborted) setPolling(true)
      })
    return () => ctl.abort()
    // `after` only invalidates queries; following it would reopen the stream on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, id])

  const build: Build | null = polling ? (query.data ?? live) : (live ?? query.data ?? null)
  /** Put a build returned by a write (cancel, continue) on screen straight away. */
  const put = (b: Build) => dispatch({ type: 'snapshot', build: b })
  return { build, files, isPending: !build && (query.isPending || !polling), error: query.error, polling, put }
}

export type LiveBuild = ReturnType<typeof useLiveBuild>

export function useBuildMutations(pid: string) {
  const qc = useQueryClient()
  const refresh = () => void qc.invalidateQueries({ queryKey: buildKeys.list(pid), exact: true })
  // A build already running (409 build_in_progress) is the one to follow: there is one build per project at a time.
  const start = useMutation({
    mutationFn: async (opts: StartBuild = {}) => {
      try {
        return await buildApi.start(pid, opts)
      } catch (e) {
        if (isApiError(e, 'build_in_progress')) {
          const running = activeBuild(await buildApi.list(pid))
          if (running) return running
        }
        throw e
      }
    },
    onSuccess: refresh,
  })
  const cancel = useMutation({ mutationFn: (id: string) => buildApi.cancel(pid, id), onSuccess: refresh })
  const proceed = useMutation({ mutationFn: (id: string) => buildApi.continue(pid, id), onSuccess: refresh })
  return { start, cancel, proceed }
}

/** The current time, ticking every second while `on` (elapsed times of running steps). */
export function useNow(on: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!on) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [on])
  return now
}
