import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { BuildPage } from '@/features/agent-sdk/components/BuildPage'

// Script → Workflow: the human door onto the same sandboxed SDK core the
// agent's build_workflow tool uses. One path for both keeps them honest.
export const buildRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'build',
  component: BuildPage,
})
