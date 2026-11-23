/**
 * Server state for the Knowledge pages. Every query lives under `qk.knowledge(scope)` (one project, or the whole
 * team), and a rebuild or a decision in either scope refreshes every knowledge query in both, because a project
 * rebuild changes the team view and a team rebuild rebuilds every project.
 */
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { knowledgeApi } from '@/shared/api/endpoints'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { KnowledgeScope } from '../lib/types'

export function useKgGraph(scope: KnowledgeScope, learnedOnly: boolean, enabled = true) {
  return useQuery({
    queryKey: qk.knowledgeGraph(scope, learnedOnly),
    queryFn: ({ signal }) => knowledgeApi.graph(scope, { learnedOnly }, signal),
    // flipping "learned only" keeps the old map on screen until the new one lands
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useKgNode(scope: KnowledgeScope, id: string | null | undefined) {
  return useQuery({
    queryKey: qk.knowledgeNode(scope, id ?? ''),
    queryFn: ({ signal }) => knowledgeApi.node(scope, id as string, signal),
    enabled: !!id,
  })
}

export function useKgCapabilities(scope: KnowledgeScope, enabled = true) {
  return useQuery({
    queryKey: qk.knowledgeCapabilities(scope),
    queryFn: ({ signal }) => knowledgeApi.capabilities(scope, signal),
    enabled,
  })
}

export function useKgMemory(scope: KnowledgeScope, enabled = true) {
  return useInfiniteQuery({
    queryKey: qk.knowledgeMemory(scope),
    queryFn: ({ pageParam, signal }) => knowledgeApi.memory(scope, pageParam, 50, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
  })
}

export function useRebuildKnowledge(scope: KnowledgeScope) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => knowledgeApi.rebuild(scope),
    onSettled: () => invalidate.knowledgeChanged(qc),
  })
}

export function useDecideNode(scope: KnowledgeScope) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decision, actor }: { id: string; decision: 'accept' | 'reject'; actor: string | null }) =>
      decision === 'accept' ? knowledgeApi.accept(scope, id, actor) : knowledgeApi.reject(scope, id, actor),
    onSettled: () => invalidate.knowledgeChanged(qc),
  })
}
