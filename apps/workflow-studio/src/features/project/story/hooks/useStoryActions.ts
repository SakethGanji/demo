/**
 * Where the Overview's buttons go: the existing create flows (create-ui / new-module dialogs, a new workflow,
 * Data, Datasets), a route's test (its module editor on that route, as the old Backend tile did) and a
 * workflow's Run now.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { errorMessage } from '@/shared/api/http'
import { invalidate } from '@/shared/api/queryKeys'
import type { CodeModule, Route } from '@/shared/api/types'
import { workflowsApi } from '@/shared/lib/api'
import { useProjectCtx } from '../../ProjectContext'
import { moduleNameOf } from '../../lib/routes'
import type { LaneKey } from '../lib/model'

export function useStoryActions(modules: CodeModule[]) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [running, setRunning] = useState<string | null>(null)
  const P = { project: slug }

  const createUi = () => navigate({ to: '/p/$project', params: P, search: { dialog: 'create-ui' } })
  const newModule = () => navigate({ to: '/p/$project', params: P, search: { dialog: 'new-module' } })
  const newWorkflow = () => navigate({ to: '/p/$project/workflows/$workflowId', params: { ...P, workflowId: 'new' } })
  const openData = () => navigate({ to: '/p/$project/data', params: P })
  const openDatasets = () => navigate({ to: '/p/$project/datasets', params: P })
  const newRoute = () => (modules[0] ? navigate({ to: '/p/$project/backend/$module', params: { ...P, module: modules[0].name } }) : newModule())

  const add: Record<LaneKey, () => void> = { apps: createUi, routes: newRoute, modules: newModule, workflows: newWorkflow, data: openData }

  const testRoute = (r: Route) => {
    const m = moduleNameOf(r, modules)
    return m
      ? navigate({ to: '/p/$project/backend/$module', params: { ...P, module: m }, search: { route: r.name } })
      : navigate({ to: '/p/$project/runs', params: P, search: { route: r.id } })
  }

  const runWorkflow = async (id: string, name: string) => {
    if (running) return
    setRunning(id)
    try {
      await workflowsApi.run(id)
      toast.success(`Started ${name}`)
      void invalidate.runsChanged(qc, pid)
    } catch (e) {
      toast.error(`Couldn't run ${name}: ${errorMessage(e)}`)
    } finally {
      setRunning(null)
    }
  }

  return { createUi, newModule, newWorkflow, newRoute, openData, openDatasets, add, testRoute, runWorkflow, running }
}
