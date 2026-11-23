/**
 * The state behind the app builder's plan-and-build conversation (`PlanFlow.tsx` renders it): the open draft, the
 * approved plan, the build it started, the phase they add up to, and the actions the cards and the composer call.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import type { PlanSource } from '@/features/connections/connectionsTypes'
import { ApiError, errorMessage } from '@/shared/api/http'
import { activeBuild, flowPhase, isActive, newAppTarget, pickAppKey, type FlowPhase } from './buildLogic'
import type { Build } from './buildTypes'
import { errorsOf } from './planLogic'
import type { PlanDiffResult, PlanVersion } from './planTypes'
import { usePlanFlowStore } from './planFlowStore'
import { useBuildMutations, useBuilds } from './useBuild'
import { usePlan, usePlanMutations } from './usePlan'

/**
 * The conversation for a project. Without `appId` it is New app (`apps/new`): the open draft and the active build
 * belong to it. With `appId` it shows only when this app came out of the conversation (or a build is making it).
 */
export function usePlanFlow(pid: string, appId?: string) {
  const plan = usePlan(pid)
  const pm = usePlanMutations(pid)
  const builds = useBuilds(pid)
  const bm = useBuildMutations(pid)
  const flow = usePlanFlowStore((s) => s.flows[pid])
  const store = usePlanFlowStore.getState
  const [breaking, setBreaking] = useState<PlanDiffResult | null>(null)
  const [buildError, setBuildError] = useState<string | null>(null)

  const draft = appId ? null : (plan.data?.draft ?? null)
  const approved = plan.data?.approved ?? null
  const making = appId ? (builds.data ?? []).find((b) => isActive(b.status) && b.steps.some((s) => s.app_id === appId)) : activeBuild(builds.data)
  const buildId = (appId && flow?.appId !== appId ? null : flow?.buildId) ?? making?.id ?? null
  const listed = (builds.data ?? []).find((b) => b.id === buildId)
  const visible = appId ? flow?.appId === appId || !!making : true
  const target = newAppTarget({ draft, approved })
  const phase: FlowPhase = flowPhase({
    started: !!flow || pm.draft.isPending || pm.revise.isPending,
    draft,
    // the list lags a just-started build: until it shows up, the build counts as queued
    build: buildId ? (listed ?? ({ status: 'queued' } as Build)) : null,
  })

  const fail = (title: string) => (e: unknown) => toast.error(title, { description: errorMessage(e) })

  const describe = (prompt: string, sources?: PlanSource[]) => {
    if (target === 'draft') return
    store().start(pid, prompt)
    const onError = (e: unknown) => {
      store().end(pid)
      fail('Plan not drafted')(e)
    }
    if (target === 'revise') pm.revise.mutate({ prompt, sources }, { onError })
    else pm.draft.mutate({ prompt, sources }, { onError })
  }

  const startBuild = (appKey?: string) => {
    setBuildError(null)
    bm.start.mutate(appKey ? { app_key: appKey } : {}, {
      onSuccess: (b) => store().setBuild(pid, b.id),
      onError: (e) => setBuildError(errorMessage(e)),
    })
  }

  const answer = (answers: Record<string, string>) => {
    if (draft) pm.answer.mutate({ version: draft.version, answers }, { onError: fail('Answers not sent') })
  }

  /** "Accept all suggestions": the server takes every suggested pick; when that leaves a clean plan, approve it and build. */
  const acceptAll = () => {
    if (!draft) return
    pm.accept.mutate(
      { version: draft.version },
      {
        onSuccess: (r) => {
          const next = r.plan as PlanVersion
          if (!next.questions.length && !errorsOf(next.issues).length) approve(false)
        },
        onError: fail('Suggestions not applied'),
      },
    )
  }

  const approve = (acceptBreaking = false) => {
    if (!draft) return
    if (!flow) store().start(pid, '')
    pm.approve.mutate(
      { version: draft.version, acceptBreaking },
      {
        onSuccess: (r) => {
          setBreaking(null)
          startBuild(pickAppKey(r.body.apps))
        },
        onError: (e) => {
          if (e instanceof ApiError && e.code === 'breaking_change') setBreaking((e.details as { diff: PlanDiffResult }).diff)
          else fail('Not approved')(e)
        },
      },
    )
  }

  return {
    pid,
    appId,
    loading: plan.isPending,
    flow,
    draft,
    approved,
    buildId,
    phase,
    target,
    visible,
    breaking,
    buildError,
    pending: { draft: pm.draft.isPending || pm.revise.isPending, answer: pm.answer.isPending || pm.accept.isPending, approve: pm.approve.isPending, start: bm.start.isPending },
    describe,
    answer,
    acceptAll,
    approve,
    startBuild,
    dismissBreaking: () => setBreaking(null),
    /** The composer is for the description (and a new one after a build that made no app); otherwise the cards lead. */
    canDescribe: phase === 'describe' || (phase === 'finished' && !appId),
  }
}

export type PlanFlowState = ReturnType<typeof usePlanFlow>

/** The composer's hint for each phase of New app. */
export function composerHint(f: Pick<PlanFlowState, 'phase' | 'target' | 'approved'>): string {
  switch (f.phase) {
    case 'describe':
      return f.target === 'revise' ? 'Describe the app; it is added to your plan…' : 'Describe the app you need…'
    case 'drafting':
      return 'Drafting the plan…'
    case 'questions':
      return 'Answer the questions above to go on'
    case 'review':
      return 'Approve the plan above to build it'
    case 'input':
      return 'The build needs your input above'
    case 'building':
      return 'Building… the app opens here when its UI starts'
    default:
      return 'Describe another app…'
  }
}
