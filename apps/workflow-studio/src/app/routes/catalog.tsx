import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { CatalogPage } from '@/features/datasets/components/CatalogPage'

export const catalogRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'catalog',
  component: CatalogRoutePage,
})

function CatalogRoutePage() {
  return <CatalogPage />
}
