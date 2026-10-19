/** Project attribution chips for the team-wide Knowledge page (the scope itself lives in `useKnowledgeScope`). */
import { cn } from '@/shared/lib/utils'
import { useKnowledgeScope } from '../hooks/useKnowledgeScope'
import { projectColor, SHARED, SHARED_COLOR } from '../lib/projects'

/** A project's colour dot. */
export function ProjectDot({ projectId, className }: { projectId: string | null | undefined; className?: string }) {
  return (
    <span
      className={cn('inline-block size-2 shrink-0 rounded-full', className)}
      style={{ backgroundColor: projectId && projectId !== SHARED ? projectColor(projectId) : SHARED_COLOR }}
      aria-hidden
    />
  )
}

/** A project's name on its colour dot (or "Shared" for knowledge no project owns). */
export function ProjectChip({ projectId, className }: { projectId: string | null | undefined; className?: string }) {
  const { projects } = useKnowledgeScope()
  const p = projectId && projectId !== SHARED ? projects.get(projectId) : undefined
  const label = !projectId || projectId === SHARED ? 'Shared' : (p?.name ?? p?.slug ?? 'Another project')
  return (
    <span
      className={cn('inline-flex h-5 max-w-40 min-w-0 items-center gap-1.5 rounded-full bg-recess px-2 text-[11px] font-medium text-muted-foreground', className)}
      data-testid="knowledge-project-chip"
      data-project={projectId || SHARED}
    >
      <ProjectDot projectId={projectId} />
      <span className="truncate">{label}</span>
    </span>
  )
}
