import { createRouter } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { editorRoute } from './editor'
import { builderRoute } from './builder'
import { indexRoute } from './indexRoute'
import { projectsRoute } from './projects'
import { dataRoute } from './data'
import { catalogRoute } from './catalog'

const routeTree = rootRoute.addChildren([
  indexRoute,
  editorRoute,
  builderRoute,
  projectsRoute,
  dataRoute,
  catalogRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
