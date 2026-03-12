import { createRouter } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { editorRoute } from './editor'
import { builderRoute } from './builder'
import { indexRoute } from './indexRoute'
import { workflowsRoute } from './workflows'
import { appsRoute } from './apps'

const routeTree = rootRoute.addChildren([indexRoute, editorRoute, builderRoute, workflowsRoute, appsRoute])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
