import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { ProjectsHome } from '@/features/project/components/ProjectsHome'

export const projectsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'projects',
  component: ProjectsHome,
})
