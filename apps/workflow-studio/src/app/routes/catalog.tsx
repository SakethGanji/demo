import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { CatalogPage } from '@/features/datasets/components/CatalogPage'

export const catalogRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'catalog',
  component: CatalogRoutePage,
})

function CatalogRoutePage() {
  return <CatalogPage />
}
