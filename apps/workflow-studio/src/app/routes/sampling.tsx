import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { SamplingPage } from '@/features/datasets/components/SamplingPage'

export const samplingRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'sampling',
  // `?dataset=<id>` lets the catalog (or the data workspace) hand a specific
  // dataset straight to the sampler, so the source of a draw is addressable.
  validateSearch: (search: Record<string, unknown>): { dataset?: string } => ({
    dataset: typeof search.dataset === 'string' ? search.dataset : undefined,
  }),
  component: SamplingRoutePage,
})

function SamplingRoutePage() {
  const { dataset } = samplingRoute.useSearch()
  return <SamplingPage initialDatasetId={dataset ?? null} />
}
