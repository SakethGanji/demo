import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { IngestPage } from '@/features/datasets/components/IngestPage'

export const ingestRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'ingest',
  // `?dataset=<id>` scopes the upload to an existing dataset, which turns it
  // into a new immutable VERSION of that dataset rather than a new one.
  validateSearch: (search: Record<string, unknown>): { dataset?: string } => ({
    dataset: typeof search.dataset === 'string' ? search.dataset : undefined,
  }),
  component: IngestRoutePage,
})

function IngestRoutePage() {
  const { dataset } = ingestRoute.useSearch()
  return <IngestPage targetDatasetId={dataset ?? null} />
}
