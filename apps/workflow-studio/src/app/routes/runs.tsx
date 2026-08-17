import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { RunsPage } from '@/features/datasets/components/RunsPage'

export const runsRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'runs',
  component: RunsRoutePage,
})

function RunsRoutePage() {
  return <RunsPage />
}
