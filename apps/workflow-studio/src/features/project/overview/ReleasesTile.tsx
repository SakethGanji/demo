import { useNavigate } from '@tanstack/react-router'
import { Rocket } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useMissingSecrets } from '../hooks/useProjectData'
import { timeAgo } from '../lib/format'
import { ReleasePath } from './ReleasePath'
import { span, TileHead } from './tiles'
import { Button } from '@/shared/components/ui/button'
import { Tile, Well } from '@/shared/components/ui/surface'
import type { ProjectSummary } from '@/shared/api/types'
import { cn } from '@/shared/lib/utils'

export function ReleasesTile({ summary }: { summary: ProjectSummary }) {
  const { project, pid } = useProjectCtx()
  const navigate = useNavigate()
  const missing = useMissingSecrets(pid)
  const live = project.live_environment
  const draft = project.environments.find((e) => e.kind === 'draft')?.name ?? project.default_environment
  const rel = summary.active_releases?.[live]
  const blockers = (missing.data ?? []).filter((m) => m.missing_in.includes(live)).length
  return (
    <Tile as="section" className={cn('flex flex-col', span.half)} data-testid="releases-card">
      <TileHead
        icon={<Rocket />}
        hue="release"
        title="Releases"
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, sheet: 'release' }) } as never)}
            data-testid="releases-create"
          >
            Create release
          </Button>
        }
      />
      <Well className="mx-2 mb-2 grid flex-1 content-center px-4 pt-3 pb-1">
        <ReleasePath draft={draft} live={live} releaseNumber={rel?.number ?? null} blockers={blockers} />
        <ul className="sr-only">
          {project.environments.map((e) => {
            const r = summary.active_releases?.[e.name]
            return (
              <li key={e.name} data-testid={`release-env-${e.name}`}>
                {e.name}:{' '}
                {e.kind === 'draft' ? 'always your latest saved code' : r ? `release #${r.number} · ${timeAgo(r.created_at)}` : 'no release yet, serves latest saved code'}
              </li>
            )
          })}
        </ul>
      </Well>
    </Tile>
  )
}
