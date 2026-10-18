/**
 * `/p/$project/ai-build/$proposalId` is retired: the AI builds inside the workflow editor now (PLAN-MOTION-UI §2.3).
 * The route stays so old links and bookmarks still land somewhere useful:
 *  - `new` (optionally `?workflow=X`) opens the editor on that workflow (or a new one) with the Build tab open;
 *  - a proposal id opens the editor on the proposal's workflow (or a new one) showing that proposal.
 */
import { createRoute, redirect } from '@tanstack/react-router'
import { aiBuilderApi } from '@/features/ai-builder/api'
import { queryClient } from '@/app/queryClient'
import { projectRoute } from './project'

export interface AiBuildSearch {
  /** the workflow to change (only with `proposalId = new`) */
  workflow?: string
}

export const aiBuildRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: 'ai-build/$proposalId',
  validateSearch: (s: Record<string, unknown>): AiBuildSearch => ({
    workflow: typeof s.workflow === 'string' && s.workflow.length > 0 ? s.workflow : undefined,
  }),
  beforeLoad: async ({ params, search }) => {
    if (params.proposalId === 'new') {
      throw redirect({
        to: '/p/$project/workflows/$workflowId',
        params: { project: params.project, workflowId: search.workflow ?? 'new' },
        search: { ai: 1 },
        replace: true,
      })
    }
    const proposal = await queryClient.ensureQueryData({
      queryKey: ['ai-proposal', params.proposalId],
      queryFn: () => aiBuilderApi.get(params.proposalId),
    }).catch(() => null)
    throw redirect({
      to: '/p/$project/workflows/$workflowId',
      params: { project: params.project, workflowId: proposal?.workflow_id ?? 'new' },
      search: { proposal: params.proposalId },
      replace: true,
    })
  },
})
