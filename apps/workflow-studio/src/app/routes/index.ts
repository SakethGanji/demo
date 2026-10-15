import { createRouter } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { editorRedirectRoute, builderRedirectRoute } from './legacy'
import { indexRoute } from './indexRoute'
import { projectsRoute } from './projects'
import { projectRouteTree } from './project'
import { queryClient } from '../queryClient'

const routeTree = rootRoute.addChildren([
  indexRoute,
  editorRedirectRoute,
  builderRedirectRoute,
  projectsRoute,
  projectRouteTree,
])

export const router = createRouter({ routeTree, context: { queryClient } })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
