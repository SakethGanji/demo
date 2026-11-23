/**
 * Public interface pages (DESIGN-STAGE5-UIS §7.3, §11.3). They sit at the router root, outside `p/$project`, so
 * they render without the studio shell: no top bar, no outline, no project context.
 */
import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { PublicInterfacePage } from '@/features/interfaces/public/PublicInterfacePage'
import { PublicRunPage } from '@/features/interfaces/public/PublicRunPage'

export const interfaceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'i/$project/$slug',
  component: PublicInterfacePage,
})

export const interfaceRunRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'i/$project/$slug/runs/$executionId',
  validateSearch: (s: Record<string, unknown>): { t?: string } => ({ t: typeof s.t === 'string' && s.t ? s.t : undefined }),
  component: PublicRunPage,
})
