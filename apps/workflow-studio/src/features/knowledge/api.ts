/** Typed client for `/api/knowledge/*` over the shared HTTP core. */
import { request } from '@/shared/api/http'
import { getActorName } from '@/features/ai-builder/api'
import type { CapabilitiesResponse, GraphResponse, MemoryResponse, NodeDetail, NodeView, RebuildResponse } from './types'

const enc = encodeURIComponent

export const knowledgeApi = {
  graph: (projectId: string, q: { learnedOnly?: boolean; focus?: string; depth?: 1 | 2 } = {}): Promise<GraphResponse> =>
    request('/knowledge/graph', {
      query: { project_id: projectId, learned_only: q.learnedOnly ? true : undefined, focus: q.focus, depth: q.focus ? q.depth : undefined },
    }),
  node: (projectId: string, id: string): Promise<NodeDetail> => request(`/knowledge/nodes/${enc(id)}`, { query: { project_id: projectId } }),
  capabilities: (projectId: string): Promise<CapabilitiesResponse> => request('/knowledge/capabilities', { query: { project_id: projectId } }),
  memory: (projectId: string, limit = 30, cursor?: string | null): Promise<MemoryResponse> =>
    request('/knowledge/memory', { query: { project_id: projectId, limit, cursor } }),
  rebuild: (projectId: string, llmTagging: boolean): Promise<RebuildResponse> =>
    request('/knowledge/rebuild', { method: 'POST', body: { project_id: projectId, llm_tagging: llmTagging } }),
  decide: (projectId: string, id: string, to: 'accept' | 'reject'): Promise<NodeView> =>
    request(`/knowledge/nodes/${enc(id)}/${to}`, { method: 'POST', query: { project_id: projectId }, body: { actor: getActorName() || null } }),
}
