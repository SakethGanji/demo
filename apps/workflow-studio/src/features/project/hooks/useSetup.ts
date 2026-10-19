/**
 * Project onboarding state (UI-DESIGN §3.3, reworked so a project can start from anything).
 *
 * The full guide (`SetupGuide`) replaces the Overview while the project is empty (no workflow, dataset, table,
 * module, route or app), and, for a project started through the picker, until its first successful run of any
 * kind or until the user leaves it with "Go to the Overview". `?setup=1` always opens it.
 * After that the Overview shows the slim `NextSteps` strip (`../components/NextSteps`) while steps are open.
 *
 * Steps come from the pure `nextSteps(facts)` (`../lib/nextSteps`); the state lives in
 * `project.settings.onboarding` (`useOnboarding`).
 */
import { useCallback, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { runsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { Route, RunListItem } from '@/shared/api/types'
import { useDatasets } from '@/features/datasets/useDatasets'
import type { DatasetItem } from '@/features/datasets/lib/types'
import { useTemplates } from '@/features/templates/hooks'
import {
  EMPTY_ONBOARDING,
  isProjectEmpty,
  nextSteps,
  originOf,
  successKind,
  successMessage,
  type AiTarget,
  type NextStep,
  type Onboarding,
  type Origin,
  type ProjectFacts,
  type StartKind,
} from '../lib/nextSteps'
import { useOnboarding } from './useOnboarding'
import {
  useMissingSecrets,
  useModules,
  useProjectApps,
  useProjectSummary,
  useProjectWorkflows,
  useRoutes,
  useSecrets,
  useTables,
} from './useProjectData'

export const REPORT_TEMPLATE = 'dataset-report-email'
export const PROFILE_TEMPLATE = 'profile-dataset'

export interface SetupState {
  loading: boolean
  /** whether the full guide replaces the Overview */
  show: boolean
  /** no artifact of any kind yet */
  empty: boolean
  /** the start chosen in the picker (null: none chosen) */
  start: StartKind | null
  /** the shape the steps follow */
  origin: Origin
  /** the project was started through the picker (or the old guide), so steps are tracked */
  tracked: boolean
  onboarding: Onboarding
  /** the next steps (open, done and deferred; dismissed ones are gone) */
  steps: NextStep[]
  /** done items out of `total`; step 0 ("where do you want to start?") counts as one */
  done: number
  total: number
  /** open steps: the outline's "Next steps · n" (hidden at zero) */
  openCount: number
  firstSuccess: boolean
  firstRoute: Route | null
  firstWorkflowId: string | null
  latestDataset: DatasetItem | null
  /** pick a start (and, for the AI card, its target) */
  choose: (start: StartKind, extra?: { target?: AiTarget | null; template?: string | null }) => void
  /** Later (one step, or several in one save) */
  defer: (id: string | string[]) => void
  dismiss: (id: string) => void
  /** undo Later / Not for this project (one step, or several in one save) */
  restore: (id: string | string[]) => void
  /** "Go to the Overview": stop taking over before the first success */
  closeGuide: () => void
}

// one first-success write + toast per project per page load (the outline and the Overview both run this hook)
const celebrated = new Set<string>()
const FRESH_MS = 60 * 60 * 1000

const ids = (id: string | string[]) => (Array.isArray(id) ? id : [id])
const add = (xs: string[], id: string | string[]) => [...new Set([...xs, ...ids(id)])]
const drop = (xs: string[], id: string | string[]) => xs.filter((x) => !ids(id).includes(x))

export function useSetup(pid: string, forced: boolean): SetupState {
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const apps = useProjectApps(pid)
  const workflows = useProjectWorkflows(pid)
  const tables = useTables(pid)
  const datasets = useDatasets(pid)
  const secrets = useSecrets(pid)
  const missing = useMissingSecrets(pid)
  const summary = useProjectSummary(pid)
  const templates = useTemplates(pid)
  const ob = useOnboarding()
  const successes = useQuery({
    queryKey: qk.runs(pid, { setup: 'success' }),
    queryFn: async () => (await runsApi.list(pid, { limit: 25, status: 'success' })).runs,
  })

  const loading =
    modules.isLoading || routes.isLoading || apps.isLoading || workflows.isLoading || tables.isLoading || datasets.isLoading || successes.isLoading
  const onboarding = ob.value ?? EMPTY_ONBOARDING
  const tracked = !!ob.value?.start

  const routeList = useMemo(() => routes.data ?? [], [routes.data])
  const wfList = useMemo(() => workflows.data ?? [], [workflows.data])
  const runs: RunListItem[] = useMemo(() => successes.data ?? [], [successes.data])

  const counts = {
    workflows: wfList.length,
    datasets: datasets.data?.length ?? 0,
    tables: tables.data?.length ?? 0,
    modules: modules.data?.length ?? 0,
    routes: routeList.length,
    apps: apps.data?.length ?? 0,
  }
  const empty = !loading && isProjectEmpty(counts)

  const missingSecrets = useMemo(() => {
    const have = new Set((secrets.data ?? []).map((s) => s.key))
    const keys = (missing.data ?? []).map((m) => m.key)
    for (const t of templates.data ?? []) if (t.installed_in?.includes(pid)) for (const s of t.requires.secrets) if (!have.has(s.name)) keys.push(s.name)
    return [...new Set(keys)]
  }, [secrets.data, missing.data, templates.data, pid])

  const facts: ProjectFacts = {
    ...counts,
    start: onboarding.start,
    target: onboarding.target ?? null,
    realRoutes: routeList.filter((r) => r.handler.type !== 'stub').length,
    workflowRoutes: routeList.filter((r) => r.handler.type === 'workflow').length,
    activeWorkflows: wfList.filter((w) => w.active).length,
    successes: [...new Set(runs.map(successKind))],
    released: Object.values(summary.data?.active_releases ?? {}).some(Boolean),
    reportInstalled: (templates.data ?? []).some((t) => t.slug === REPORT_TEMPLATE && t.installed_in?.includes(pid)),
    missingSecrets,
  }
  const origin = originOf(facts)
  // before a start is chosen in an empty project there is nothing to propose yet
  const steps = empty && !onboarding.start ? [] : nextSteps(facts, onboarding)
  const firstSuccess = !!onboarding.first_success_at || runs.length > 0
  const done = (empty ? 0 : 1) + steps.filter((s) => s.state === 'done').length
  const total = 1 + steps.length
  const openCount = tracked ? steps.filter((s) => s.state === 'open').length : 0

  // the first success: record it and say so, once
  const firstRun = runs.length ? runs[runs.length - 1] : null
  const { save } = ob
  useEffect(() => {
    if (loading || !tracked || onboarding.first_success_at || !firstRun || celebrated.has(pid)) return
    celebrated.add(pid)
    save({ ...onboarding, first_success_at: firstRun.start_time })
    if (Date.now() - new Date(firstRun.start_time).getTime() < FRESH_MS)
      // one id: the outline and the Overview each run this hook, so without it the toast appears twice
      toast.success(successMessage(successKind(firstRun)), { id: 'setup-complete', description: 'The Overview takes it from here.' })
  }, [loading, tracked, onboarding, firstRun, pid, save])

  const { update } = ob
  const choose = useCallback<SetupState['choose']>(
    (start, extra = {}) => update((cur) => ({ ...cur, start, closed_at: null, ...extra })),
    [update],
  )
  const defer = useCallback((id: string | string[]) => update((cur) => ({ ...cur, deferred: add(cur.deferred, id) })), [update])
  const dismiss = useCallback((id: string) => update((cur) => ({ ...cur, dismissed: add(cur.dismissed, id), deferred: drop(cur.deferred, id) })), [update])
  const restore = useCallback((id: string | string[]) => update((cur) => ({ ...cur, dismissed: drop(cur.dismissed, id), deferred: drop(cur.deferred, id) })), [update])
  const closeGuide = useCallback(() => update((cur) => ({ ...cur, closed_at: new Date().toISOString() })), [update])

  const datasetList = datasets.data ?? []
  const latestDataset = datasetList.reduce<DatasetItem | null>((a, d) => (!a || d.created_at > a.created_at ? d : a), null)
  const latestWorkflow = wfList.reduce<(typeof wfList)[number] | null>((a, w) => (!a || w.updated_at > a.updated_at ? w : a), null)

  return {
    loading,
    show: forced || empty || (tracked && !firstSuccess && !onboarding.closed_at),
    empty,
    start: onboarding.start,
    origin,
    tracked,
    onboarding,
    steps,
    done,
    total,
    openCount,
    firstSuccess,
    firstRoute: routeList[0] ?? null,
    firstWorkflowId: latestWorkflow?.id ?? null,
    latestDataset,
    choose,
    defer,
    dismiss,
    restore,
    closeGuide,
  }
}
