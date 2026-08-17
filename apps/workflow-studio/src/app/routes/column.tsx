import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { ColumnPage } from '@/features/datasets/components/ColumnPage'

export const columnRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'column',
  // `?dataset=<id>&column=<name>` addresses one column of one dataset. Both are
  // optional: with neither, the page opens on the first column of the first
  // dataset this seat can see rather than on an error.
  validateSearch: (search: Record<string, unknown>): { dataset?: string; column?: string } => ({
    dataset: typeof search.dataset === 'string' ? search.dataset : undefined,
    column: typeof search.column === 'string' ? search.column : undefined,
  }),
  component: ColumnRoutePage,
})

function ColumnRoutePage() {
  const { dataset, column } = columnRoute.useSearch()
  return <ColumnPage datasetId={dataset ?? null} columnName={column ?? null} />
}
