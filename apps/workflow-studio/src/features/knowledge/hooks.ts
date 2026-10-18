/** Queries and mutations of the Knowledge page (PLAN-CAPGRAPH-UI §2.5). Keys: ['kg', <part>, projectId, …]. */
import { useNavigate } from '@tanstack/react-router'
import { infiniteQueryOptions, keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { aiBuilderApi } from '@/features/ai-builder/api'
import { useAiProposalStore } from '@/features/ai-builder/aiProposalStore'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { knowledgeApi } from './api'
import { buildPrompt } from './lib/labels'

export const kgKeys = {
  all: (pid: string) => ['kg', 'all', pid] as const,
  graph: (pid: string, learnedOnly: boolean) => ['kg', 'graph', pid, learnedOnly] as const,
  node: (pid: string, id: string) => ['kg', 'node', pid, id] as const,
  caps: (pid: string) => ['kg', 'caps', pid] as const,
  memory: (pid: string) => ['kg', 'memory', pid] as const,
}

const visible = () => typeof document === 'undefined' || document.visibilityState === 'visible'

export function useKgGraph(pid: string, learnedOnly: boolean) {
  return useQuery({
    queryKey: kgKeys.graph(pid, learnedOnly),
    queryFn: () => knowledgeApi.graph(pid, { learnedOnly }),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  })
}

export function useKgNode(pid: string, id: string | null) {
  return useQuery({
    queryKey: kgKeys.node(pid, id ?? ''),
    enabled: !!id,
    queryFn: () => knowledgeApi.node(pid, id as string),
    staleTime: 30_000,
  })
}

export function usePrefetchKgNode(pid: string) {
  const qc = useQueryClient()
  return (id: string) => void qc.prefetchQuery({ queryKey: kgKeys.node(pid, id), queryFn: () => knowledgeApi.node(pid, id), staleTime: 30_000 })
}

export function useKgCaps(pid: string) {
  return useQuery({ queryKey: kgKeys.caps(pid), queryFn: () => knowledgeApi.capabilities(pid), staleTime: 15_000 })
}

/** Newest first, keyset pages of 30. Polls every 15 s while the tab is visible. */
export function kgMemoryOptions(pid: string) {
  return infiniteQueryOptions({
    queryKey: kgKeys.memory(pid),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => knowledgeApi.memory(pid, 30, pageParam),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: () => (visible() ? 15_000 : false),
    refetchOnWindowFocus: true,
    staleTime: 0, // the global 30 s staleTime would swallow the refetch on focus
  })
}

export function useKgMemory(pid: string) {
  return useInfiniteQuery(kgMemoryOptions(pid))
}

export function useBuilderConfigured() {
  return useQuery({ queryKey: ['ai-builder-config'], queryFn: aiBuilderApi.config, staleTime: 60_000 })
}

/** Everything the page reads for a project goes stale together. */
export function useInvalidateKg(pid: string) {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'kg' && q.queryKey[2] === pid })
}

export function useRebuild(pid: string) {
  const invalidate = useInvalidateKg(pid)
  return useMutation({ mutationFn: (llm: boolean) => knowledgeApi.rebuild(pid, llm), onSuccess: () => invalidate() })
}

export function useDecide(pid: string) {
  const invalidate = useInvalidateKg(pid)
  return useMutation({ mutationFn: (v: { id: string; to: 'accept' | 'reject' }) => knowledgeApi.decide(pid, v.id, v.to), onSuccess: () => invalidate() })
}

/** "Build with this": opens the new-workflow editor on the Build tab with the request filled in. */
export function useBuildWith() {
  const navigate = useNavigate()
  const { slug } = useProjectCtx()
  return (concept: { label: string; definition?: string | null }) => {
    const store = useAiProposalStore.getState()
    store.reset()
    store.setPendingRequest(buildPrompt(concept))
    void navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: 'new' }, search: { ai: 1 } })
  }
}
