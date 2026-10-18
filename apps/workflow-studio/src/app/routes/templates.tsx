import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { TemplatesHome } from '@/features/templates/TemplatesPage'

export interface TemplatesSearch {
  /** the project to install into (slug or id), preselected in the install picker */
  project?: string
}

/** `/templates`: the template catalog, outside any project; install asks which project to use. */
export const templatesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'templates',
  validateSearch: (s: Record<string, unknown>): TemplatesSearch => ({
    project: typeof s.project === 'string' && s.project.length > 0 ? s.project : undefined,
  }),
  component: TemplatesHome,
})
