/**
 * Plan queries and mutations. Every write puts the returned version straight into the plan query (no refetch flash) and
 * then invalidates it; approval also invalidates the conformance check and project knowledge, which mirror the plan.
 */
import type { PlanSource } from '@/features/connections/connectionsTypes'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { planApi } from './planApi'
import type { PlanBody, PlanState, PlanVersion } from './planTypes'

export const planKeys = {
  plan: (p: string) => ['project', p, 'plan'] as const,
  check: (p: string) => ['project', p, 'plan', 'check'] as const,
  diff: (p: string, v: number) => ['project', p, 'plan', 'diff', v] as const,
}

export function usePlan(pid: string) {
  return useQuery({ queryKey: planKeys.plan(pid), queryFn: () => planApi.get(pid) })
}

export function usePlanCheck(pid: string, enabled: boolean) {
  return useQuery({ queryKey: planKeys.check(pid), queryFn: () => planApi.check(pid), enabled, retry: false })
}

export function usePlanDiff(pid: string, version: number | null) {
  return useQuery({
    queryKey: planKeys.diff(pid, version ?? 0),
    queryFn: () => planApi.diff(pid, version as number),
    enabled: version != null,
  })
}

export function usePlanMutations(pid: string) {
  const qc = useQueryClient()
  const putDraft = (v: PlanVersion) =>
    qc.setQueryData<PlanState>(planKeys.plan(pid), (s) => (s ? { ...s, draft: v.status === 'draft' ? v : s.draft } : s))
  const refresh = () => qc.invalidateQueries({ queryKey: planKeys.plan(pid) })

  const draft = useMutation({
    mutationFn: (a: { prompt: string; sources?: PlanSource[] }) => planApi.draftFromPrompt(pid, a.prompt, a.sources),
    onSuccess: (r) => {
      putDraft(r.plan)
      void refresh()
    },
  })
  const answer = useMutation({
    mutationFn: (a: { version: number; answers: Record<string, string> }) => planApi.answer(pid, a.version, a.answers),
    onSuccess: (r) => {
      putDraft(r.plan)
      void refresh()
    },
  })
  const accept = useMutation({
    mutationFn: (a: { version: number }) => planApi.acceptSuggestions(pid, a.version),
    onSuccess: (r) => {
      putDraft(r.plan)
      void refresh()
    },
  })
  const save = useMutation({
    mutationFn: (a: { version: number; body: PlanBody; baseUpdatedAt: string | null }) =>
      planApi.save(pid, a.version, a.body, a.baseUpdatedAt),
    onSuccess: (v) => {
      putDraft(v)
      void refresh()
    },
  })
  const approve = useMutation({
    mutationFn: (a: { version: number; acceptBreaking?: boolean }) => planApi.approve(pid, a.version, a.acceptBreaking),
    onSuccess: () => {
      void refresh()
      void qc.invalidateQueries({ queryKey: planKeys.check(pid) })
      void qc.invalidateQueries({ queryKey: ['project', pid, 'knowledge'] })
    },
  })
  const revise = useMutation({
    mutationFn: (a: { prompt?: string; sources?: PlanSource[] }) => planApi.revise(pid, a.prompt, a.sources),
    onSuccess: (v) => {
      putDraft(v)
      void refresh()
    },
  })
  return { draft, answer, accept, save, approve, revise }
}
