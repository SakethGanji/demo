/**
 * First-run guide state (UI-DESIGN §3.3).
 *
 * The guide shows for an empty project (no modules, routes or apps) or with `?setup=1`.
 * Once shown for an empty project it stays until all four steps are done (a per-project
 * localStorage flag), so the user can finish "test a route" and "create a UI" after the
 * first module exists. Completion clears the flag and shows a one-time toast.
 */
import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { runsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { useModules, useProjectApps, useRoutes, useSecrets } from './useProjectData'

const skipKey = (pid: string) => `luna:setup-skip:${pid}`
const activeKey = (pid: string) => `luna:setup-active:${pid}`

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}
function writeFlag(key: string, on: boolean) {
  try {
    if (on) localStorage.setItem(key, '1')
    else localStorage.removeItem(key)
  } catch {
    /* in-memory only */
  }
}

export interface SetupState {
  loading: boolean
  steps: { secret: boolean; module: boolean; test: boolean; app: boolean }
  done: number
  hasRoute: boolean
  firstRouteId: string | null
  /** whether the guide replaces the Overview */
  show: boolean
  skipSecret: () => void
}

export function useSetup(pid: string, forced: boolean): SetupState {
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const apps = useProjectApps(pid)
  const secrets = useSecrets(pid)
  const tested = useQuery({
    queryKey: qk.runs(pid, { setup: 'tested' }),
    queryFn: async () => {
      const [a, b] = await Promise.all([
        runsApi.list(pid, { limit: 1, trigger: 'route_test' }),
        runsApi.list(pid, { limit: 1, trigger: 'draft_test' }),
      ])
      return a.runs.length + b.runs.length > 0
    },
  })
  const [skipped, setSkipped] = useState(() => readFlag(skipKey(pid)))
  const [active, setActive] = useState(() => readFlag(activeKey(pid)))

  const loading = modules.isLoading || routes.isLoading || apps.isLoading || secrets.isLoading || tested.isLoading
  const steps = {
    secret: skipped || (secrets.data ?? []).some((s) => s.scope === 'project'),
    module: (modules.data?.length ?? 0) > 0,
    test: !!tested.data,
    app: (apps.data?.length ?? 0) > 0,
  }
  const done = Object.values(steps).filter(Boolean).length
  const empty = !loading && !steps.module && (routes.data?.length ?? 0) === 0 && !steps.app

  useEffect(() => {
    if (empty && !active) {
      writeFlag(activeKey(pid), true)
      setActive(true)
    }
  }, [empty, active, pid])

  const complete = done === 4
  useEffect(() => {
    if (!loading && complete && active) {
      writeFlag(activeKey(pid), false)
      setActive(false)
      toast.success('You’re set up', { description: 'Secret, backend, a tested route and a UI. The Overview takes it from here.' })
    }
  }, [loading, complete, active, pid])

  const skipSecret = useCallback(() => {
    writeFlag(skipKey(pid), true)
    setSkipped(true)
  }, [pid])

  const pyRoutes = routes.data ?? []
  return {
    loading,
    steps,
    done,
    hasRoute: pyRoutes.length > 0,
    firstRouteId: pyRoutes[0]?.id ?? null,
    show: forced || empty || (active && !complete),
    skipSecret,
  }
}

