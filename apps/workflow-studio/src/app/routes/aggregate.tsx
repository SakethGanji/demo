import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { AggregatePage } from '@/features/datasets/components/AggregatePage'

export const aggregateRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'aggregate',
  // `?dataset=<id>` lets the catalog or the data grid open the builder already
  // pointed at a dataset, the same contract `/data` uses.
  validateSearch: (search: Record<string, unknown>): { dataset?: string } => ({
    dataset: typeof search.dataset === 'string' ? search.dataset : undefined,
  }),
  component: AggregateRoutePage,
})

function AggregateRoutePage() {
  const { dataset } = aggregateRoute.useSearch()
  return <AggregatePage initialDatasetId={dataset ?? null} />
}
