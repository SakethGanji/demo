import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { QueryPage } from '@/features/datasets/components/QueryPage'

export const queryRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'query',
  // `?dataset=<id>` lets the catalog (or the data workspace) open the builder
  // already scoped to a dataset, the same contract `/data` publishes.
  validateSearch: (search: Record<string, unknown>): { dataset?: string } => ({
    dataset: typeof search.dataset === 'string' ? search.dataset : undefined,
  }),
  component: QueryRoutePage,
})

function QueryRoutePage() {
  const { dataset } = queryRoute.useSearch()
  return <QueryPage initialDatasetId={dataset ?? null} />
}
