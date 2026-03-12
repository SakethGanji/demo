import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { AppBuilderShell } from '@/features/app-builder/components/AppBuilderShell'

export const builderRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'builder',
  validateSearch: (search: Record<string, unknown>): { appId?: string } => ({
    appId: search.appId as string | undefined,
  }),
  component: BuilderPage,
})

function BuilderPage() {
  const { appId } = builderRoute.useSearch()
  return <AppBuilderShell appId={appId} />
}
