/**
 * What each start and each next step does inside a project. The guide's panels and step rows and the slim
 * NextSteps strip call these, so a step's button does the same thing wherever it shows.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { errorMessage } from '@/shared/api/http'
import type { DatasetItem } from '@/features/datasets/lib/types'
import { useProjectCtx } from '../ProjectContext'
import { moduleNameOf } from '../lib/routes'
import type { NextStep } from '../lib/nextSteps'
import { createInterfaceApp, installForDataset, prefillAiRequest } from '../home/startActions'
import { PROFILE_TEMPLATE, REPORT_TEMPLATE, type SetupState } from './useSetup'
import { useModules, useRoutes } from './useProjectData'

/** "Profile it" / "Email me a report" for a dataset: install the template, pre-select the dataset, open the workflow. */
export function useDatasetStarters() {
  const { pid, slug } = useProjectCtx()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<'profile' | 'report' | null>(null)
  const go = async (which: 'profile' | 'report', dataset: DatasetItem | null) => {
    if (busy) return
    setBusy(which)
    try {
      const res = await installForDataset(qc, pid, which === 'profile' ? PROFILE_TEMPLATE : REPORT_TEMPLATE, dataset)
      if (dataset && !res.preselected) toast.message(`Pick ${dataset.name} on the Profile step`, { description: 'The workflow is installed; choose the dataset there.' })
      if (res.workflowId) navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: res.workflowId } })
    } catch (e) {
      toast.error(`Couldn't install it: ${errorMessage(e)}`)
    } finally {
      setBusy(null)
    }
  }
  return { busy, profile: (d: DatasetItem | null) => go('profile', d), report: (d: DatasetItem | null) => go('report', d) }
}

/** Creates a blank interface (no backend needed) and opens the builder on it. */
export function useCreateInterface() {
  const { pid, slug } = useProjectCtx()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const create = async (prompt: string) => {
    if (busy) return
    setBusy(true)
    try {
      const app = await createInterfaceApp(qc, pid, prompt)
      navigate({ to: '/p/$project/apps/$appId', params: { project: slug, appId: app.appId }, search: { ...(app.prompt ? { prompt: app.prompt } : {}), slug: app.slug } })
    } catch (e) {
      toast.error(`Couldn't create the interface: ${errorMessage(e)}`)
    } finally {
      setBusy(false)
    }
  }
  return { busy, create }
}

/** Opens the workflow editor on a new workflow, with the AI Build tab (and the prompt typed in) when asked. */
export function useOpenWorkflow() {
  const { slug } = useProjectCtx()
  const navigate = useNavigate()
  return (opts: { ai?: boolean; prompt?: string; id?: string | null } = {}) => {
    if (opts.prompt) prefillAiRequest(opts.prompt)
    navigate({
      to: '/p/$project/workflows/$workflowId',
      params: { project: slug, workflowId: opts.id ?? 'new' },
      search: opts.ai || opts.prompt ? { ai: 1 } : {},
    })
  }
}

export interface StepAction {
  label: string
  run: () => void
  testId?: string
  busy?: boolean
  /** a quieter button (secondary actions) */
  quiet?: boolean
}

/** The button for a next step. Secret steps return null: the guide shows the secret form inline, the strip links to Secrets. */
export function useStepAction(setup: SetupState): (step: NextStep) => StepAction | null {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const routes = useRoutes(pid)
  const modules = useModules(pid)
  const openWorkflow = useOpenWorkflow()
  const ds = useDatasetStarters()
  const P = { project: slug }
  const wf = setup.firstWorkflowId

  return (step) => {
    switch (step.id) {
      case 'run-workflow':
      case 'run-profile':
        return { label: wf ? 'Open and run' : 'New workflow', run: () => openWorkflow({ id: wf }) }
      case 'add-trigger':
      case 'schedule':
        return { label: step.id === 'schedule' ? 'Add a schedule' : 'Add a trigger', run: () => openWorkflow({ id: wf }) }
      case 'add-interface':
        return { label: 'New interface', testId: 'setup-create-ui', run: () => navigate({ to: '/p/$project', params: P, search: { dialog: 'create-ui' } }) }
      case 'release':
        return { label: 'Release', run: () => navigate({ to: '/p/$project', params: P, search: { sheet: 'release' } }) }
      case 'share-report':
        return { label: 'Email me a report', busy: ds.busy === 'report', run: () => void ds.report(setup.latestDataset) }
      case 'test-route': {
        const first = setup.firstRoute
        if (!first) return { label: 'Write a route', run: () => navigate({ to: '/p/$project/backend/$module', params: { ...P, module: '~new' } }) }
        const m = moduleNameOf(first, modules.data)
        return {
          label: `Test ${first.method} ${first.path}`,
          testId: 'setup-test-route',
          run: () =>
            m
              ? navigate({ to: '/p/$project/backend/$module', params: { ...P, module: m }, search: { route: first.name } })
              : navigate({ to: '/p/$project/runs', params: P }),
        }
      }
      case 'wire-route': {
        const stubs = (routes.data ?? []).filter((r) => r.handler.type === 'stub')
        const prompt = stubs.length ? `Implement these routes: ${stubs.map((r) => `${r.method} ${r.path}`).join(', ')}.` : undefined
        return {
          label: stubs.length ? `Write ${stubs.length === 1 ? 'the stub' : `${stubs.length} stubs`}` : 'Write a route',
          run: () => navigate({ to: '/p/$project/backend/$module', params: { ...P, module: '~new' }, search: prompt ? { prompt } : {} }),
        }
      }
      case 'add-workflow':
        return { label: 'Build a workflow', run: () => openWorkflow({ ai: true }) }
      default:
        return null
    }
  }
}
