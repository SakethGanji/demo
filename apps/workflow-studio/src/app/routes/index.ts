import { createRouter } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { editorRedirectRoute, builderRedirectRoute } from './legacy'
import { indexRoute } from './indexRoute'
import { projectsRoute } from './projects'
import { projectRouteTree } from './project'
import { designRoute } from './design'
import { interfaceRoute, interfaceRunRoute } from './public'
import { queryClient } from '../queryClient'

const routeTree = rootRoute.addChildren([
  indexRoute,
  editorRedirectRoute,
  builderRedirectRoute,
  projectsRoute,
  projectRouteTree,
  // public form / chat / custom surfaces: no studio shell (DESIGN-STAGE5-UIS §11.3)
  interfaceRoute,
  interfaceRunRoute,
  // dev-only component reference (IMPLEMENTATION-PLAN A7)
  ...(import.meta.env.DEV ? [designRoute] : []),
])

export const router = createRouter({ routeTree, context: { queryClient } })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
