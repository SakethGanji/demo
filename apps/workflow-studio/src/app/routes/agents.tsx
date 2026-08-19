import { createRoute } from '@tanstack/react-router'
import { studioLayoutRoute } from './_studio'
import { AgentsPage } from '@/features/agent-sdk/components/AgentsPage'

// The agent fleet: trigger runs, watch the runs table, drill into a run's
// event stream. Polling (after_seq) — live and replay are the same view.
export const agentsRoute = createRoute({
  getParentRoute: () => studioLayoutRoute,
  path: 'agents',
  component: AgentsPage,
})
