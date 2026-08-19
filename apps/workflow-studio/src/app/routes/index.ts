import { createRouter } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { editorRoute } from './editor'
import { builderRoute } from './builder'
import { indexRoute } from './indexRoute'
import { projectsRoute } from './projects'
import { studioLayoutRoute } from './_studio'
import { dataRoute } from './data'
import { catalogRoute } from './catalog'
import { queryRoute } from './query'
import { aggregateRoute } from './aggregate'
import { columnRoute } from './column'
import { pivotRoute } from './pivot'
import { samplingRoute } from './sampling'
import { runsRoute } from './runs'
import { adminRoute } from './admin'
import { ingestRoute } from './ingest'

const routeTree = rootRoute.addChildren([
  indexRoute,
  editorRoute,
  builderRoute,
  projectsRoute,
  // agent-sdk-demo.tsx (the SDK feasibility spike) is deliberately NOT
  // registered: it was an unguarded production route reachable by URL.
  // Re-add its import here if the spike needs to be shown live again.
  // Everything below shares the studio shell. New studio routes are added HERE
  // rather than to the root, which is why `__root.tsx` has needed no edit as
  // the surface grew from two routes to ten.
  studioLayoutRoute.addChildren([
    dataRoute,
    catalogRoute,
    queryRoute,
    aggregateRoute,
    columnRoute,
    pivotRoute,
    samplingRoute,
    runsRoute,
    adminRoute,
    ingestRoute,
  ]),
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
