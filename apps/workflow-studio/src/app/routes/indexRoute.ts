import { createRoute, lazyRouteComponent } from '@tanstack/react-router'
import { rootRoute } from './__root'

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  // lazy: studio users never download the landing (its shader, mocks and fixtures)
  component: lazyRouteComponent(() => import('./landing')),
})
