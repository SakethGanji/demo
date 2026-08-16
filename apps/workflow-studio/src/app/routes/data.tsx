import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { DatasetsPage } from '@/features/datasets/components/DatasetsPage'

export const dataRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'data',
  // `?dataset=<id>` lets the catalog open a specific dataset in the workspace.
  validateSearch: (search: Record<string, unknown>): { dataset?: string } => ({
    dataset: typeof search.dataset === 'string' ? search.dataset : undefined,
  }),
  component: DataPage,
})

function DataPage() {
  const { dataset } = dataRoute.useSearch()
  return <DatasetsPage initialDatasetId={dataset ?? null} />
}
