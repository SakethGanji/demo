/**
 * Resolves `/p/$project` (slug or id) once and provides the project to every page under it.
 * `/p/{id}` is replaced with `/p/{slug}`; an unknown ref renders the full-page not-found (§2.3).
 * There is no sidebar: the Overview is the project's home screen, and everything opened from it (an app, a workflow,
 * a system page) takes the whole window, with the way home and the jump menu in its top bar (HomeNav). Overlays
 * addressed by search params (settings, the create-app dialog) are mounted here, so they open over any page,
 * editors included.
 */
import { useEffect, useMemo } from 'react'
import { Link, Outlet, useNavigate, useParams, useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { FolderX } from 'lucide-react'
import { projectsApi } from '@/shared/api/endpoints'
import { isApiError, errorMessage } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'
import { Button } from '@/shared/components/ui/button'
import { Spinner } from '@/shared/components/ui/spinner'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { ProjectProvider, type ProjectCtx } from '../ProjectContext'
import { useEnvProfilesStore } from '@/features/workflow-editor/stores/envProfilesStore'
import { WorkspaceOverlays } from './WorkspaceOverlays'

export function ProjectLayout() {
  const { project: ref } = useParams({ strict: false }) as { project: string }
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const searchStr = useRouterState({ select: (s) => s.location.searchStr })

  const q = useQuery({
    queryKey: qk.projectRef(ref),
    queryFn: () => projectsApi.get(ref),
    staleTime: 60_000,
  })
  const project = q.data

  // Canonicalise /p/{id}/… → /p/{slug}/… (replace, keeps the rest of the URL).
  useEffect(() => {
    // The path flips to the slug a render before `ref` does: only rewrite while the URL still names the id,
    // or the stale ref would cut the rest of the path off and land every deep link on the Overview.
    const prefix = `/p/${ref}`
    if (project && ref !== project.slug && (pathname === prefix || pathname.startsWith(`${prefix}/`))) {
      const rest = pathname.slice(prefix.length)
      navigate({ to: `/p/${project.slug}${rest}${searchStr}` as never, replace: true })
    }
  }, [project, ref, pathname, searchStr, navigate])

  const viewEnvStored = useEnvProfilesStore((s) => s.activeEnvironment)
  const storeProject = useEnvProfilesStore((s) => s.projectId)
  const setActiveEnvironment = useEnvProfilesStore((s) => s.setActiveEnvironment)
  const hydrate = useEnvProfilesStore((s) => s.hydrateForProject)
  useEffect(() => {
    if (project) {
      hydrate(project.id, project.default_environment, project.environments.map((e) => e.name))
    }
  }, [project, hydrate])

  const ctx = useMemo<ProjectCtx | null>(() => {
    if (!project) return null
    const envNames = project.environments.map((e) => e.name)
    const viewEnv =
      storeProject === project.id && envNames.includes(viewEnvStored) ? viewEnvStored : project.default_environment
    return {
      project,
      pid: project.id,
      slug: project.slug,
      viewEnv,
      setViewEnv: setActiveEnvironment,
    }
  }, [project, storeProject, viewEnvStored, setActiveEnvironment])

  if (q.isError) {
    if (isApiError(q.error, undefined, 404)) return <ProjectNotFound refName={ref} />
    return (
      <div className="flex h-full items-center justify-center" data-testid="project-load-error">
        <EmptyState
          title="Couldn't load this project"
          actions={
            <Button variant="outline" size="sm" onClick={() => q.refetch()}>
              Retry
            </Button>
          }
        >
          {errorMessage(q.error)}
        </EmptyState>
      </div>
    )
  }
  if (!ctx || ref !== ctx.slug) {
    return (
      <div className="flex h-full items-center justify-center" data-testid="project-loading">
        <Spinner />
      </div>
    )
  }
  return (
    <ProjectProvider value={ctx}>
      <div className="relative h-full w-full" data-testid="project-page">
        <Outlet />
      </div>
      <WorkspaceOverlays />
    </ProjectProvider>
  )
}

function ProjectNotFound({ refName }: { refName: string }) {
  return (
    <div className="flex h-full items-center justify-center" data-testid="project-not-found">
      <div className="tile px-10 py-8">
        <EmptyState
          icon={<FolderX />}
          title="Project not found"
          actions={
            <Button render={<Link to="/projects" />} data-testid="back-to-projects">
              Back to projects
            </Button>
          }
        >
          There is no project called <span className="font-mono text-foreground">{refName}</span>. It may have been
          renamed or deleted.
        </EmptyState>
      </div>
    </div>
  )
}
