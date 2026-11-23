import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { TeamKnowledgePage } from '@/features/knowledge/components/TeamKnowledgePage'
import { KNOWLEDGE_VIEWS, type KnowledgeSearch } from '@/features/knowledge/lib/search'

const str = (v: unknown) => (typeof v === 'string' && v.length > 0 ? v : undefined)

/**
 * `/knowledge`: the capability graph across every project. `view` picks the lens, `node` the selection and `group`
 * the "Group by project" toggle (all in the URL, as on a project's Knowledge page, so a link reopens them).
 */
export const knowledgeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'knowledge',
  validateSearch: (s: Record<string, unknown>): KnowledgeSearch => ({
    view: (KNOWLEDGE_VIEWS as readonly unknown[]).includes(s.view) ? (s.view as KnowledgeSearch['view']) : undefined,
    node: str(s.node),
    group: s.group === true || s.group === 'true' ? true : s.group === false || s.group === 'false' ? false : undefined,
  }),
  component: TeamKnowledgePage,
})
