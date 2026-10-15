/**
 * Old URLs (UI-DESIGN §2.3). `beforeLoad` resolves the owning project through the hoisted
 * QueryClient and redirects; unknown ids land on /projects with a toast.
 */
import { createRoute, redirect } from '@tanstack/react-router'
import { toast } from 'sonner'
import { rootRoute } from './__root'
import { workflowsApi, appsApi } from '@/shared/lib/api'
import { projectsApi } from '@/shared/api/endpoints'
import { isApiError } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'

async function slugFor(queryClient: import('@tanstack/react-query').QueryClient, projectId: string | null | undefined) {
  const id = projectId || 'proj_default'
  const project = await queryClient.fetchQuery({ queryKey: qk.projectRef(id), queryFn: () => projectsApi.get(id) })
  return project.slug
}

export const editorRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'editor',
  validateSearch: (search: Record<string, unknown>): { workflowId?: string } => ({
    workflowId: typeof search.workflowId === 'string' ? search.workflowId : undefined,
  }),
  beforeLoad: async ({ search, context }) => {
    if (!search.workflowId) {
      throw redirect({ to: '/p/$project/workflows/$workflowId', params: { project: 'default', workflowId: 'new' }, replace: true })
    }
    let slug: string
    try {
      const wf = (await workflowsApi.get(search.workflowId)) as { project_id?: string | null }
      slug = await slugFor(context.queryClient, wf.project_id)
    } catch (e) {
      if (isApiError(e, undefined, 404)) {
        toast.error('Workflow not found')
        throw redirect({ to: '/projects', replace: true })
      }
      throw e
    }
    throw redirect({
      to: '/p/$project/workflows/$workflowId',
      params: { project: slug, workflowId: search.workflowId },
      replace: true,
    })
  },
})

export const builderRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'builder',
  validateSearch: (search: Record<string, unknown>): { appId?: string } => ({
    appId: typeof search.appId === 'string' ? search.appId : undefined,
  }),
  beforeLoad: async ({ search, context }) => {
    if (!search.appId) {
      throw redirect({ to: '/p/$project', params: { project: 'default' }, search: { dialog: 'create-ui' }, replace: true })
    }
    let slug: string
    try {
      const app = (await appsApi.get(search.appId)) as { project_id?: string | null }
      slug = await slugFor(context.queryClient, app.project_id)
    } catch (e) {
      if (isApiError(e, undefined, 404)) {
        toast.error('App not found')
        throw redirect({ to: '/projects', replace: true })
      }
      throw e
    }
    throw redirect({ to: '/p/$project/apps/$appId', params: { project: slug, appId: search.appId }, replace: true })
  },
})
