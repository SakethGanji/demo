import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { AdminPage } from '@/features/datasets/components/AdminPage'

export const adminRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'admin',
  component: AdminRoutePage,
})

function AdminRoutePage() {
  return <AdminPage />
}
