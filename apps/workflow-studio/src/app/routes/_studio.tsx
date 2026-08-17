import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { StudioShell } from '../shell/StudioShell'

/**
 * Pathless layout route: contributes chrome, not a URL segment.
 *
 * `/data` stays `/data`. That matters beyond tidiness — `__root.tsx` decides
 * its wrapper with `useMatchRoute({ to: '/data' })`, and because this route
 * adds no segment those calls keep matching, so the scope-restricted file
 * needed no edit.
 *
 * New studio routes are added as children HERE and inherit the shell. That is
 * also why `__root`'s `isFullScreen` list no longer needs to grow with them.
 */
export const studioLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_studio',
  component: StudioShell,
})
