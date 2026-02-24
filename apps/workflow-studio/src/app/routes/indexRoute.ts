import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import LandingPage from './landing'

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: LandingPage,
})
