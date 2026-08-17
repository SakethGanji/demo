import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { PivotPage } from '@/features/datasets/components/PivotPage'

export const pivotRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'pivot',
  // `?dataset=<id>` lets the catalog or the data grid open a cross-tab over a
  // specific dataset. Same contract as `/data`, so the two are interchangeable
  // in a link.
  validateSearch: (search: Record<string, unknown>): { dataset?: string } => ({
    dataset: typeof search.dataset === 'string' ? search.dataset : undefined,
  }),
  component: PivotRoute,
})

function PivotRoute() {
  const { dataset } = pivotRoute.useSearch()
  return <PivotPage initialDatasetId={dataset ?? null} />
}
