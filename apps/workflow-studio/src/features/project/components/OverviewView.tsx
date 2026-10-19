/**
 * Overview: the project's story map (`story/`, design `overview-mockup-v3.html`), or the first-run guide.
 * The page is transparent; the map and the ship row are glass, the story and the river sit on the sky.
 * The scroller is a size container (`bt`), so the layout follows the sheet's width, not the viewport's.
 */
import { useSearch } from '@tanstack/react-router'
import { AlertTriangle } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useProjectSummary, useRoutes, useModules } from '../hooks/useProjectData'
import { useSetup } from '../hooks/useSetup'
import { MapSkeleton, StoryOverview } from '../story/components/StoryOverview'
import { NextSteps } from './NextSteps'
import { SetupGuide } from './SetupGuide'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Callout, Tile, Page } from '@/shared/components/ui/surface'
import { errorMessage } from '@/shared/api/http'

export { HealthBadge } from '../overview/HealthBadge'


export function OverviewView() {
  const { pid } = useProjectCtx()
  const search = useSearch({ strict: false }) as { setup?: 1 }
  const setup = useSetup(pid, search.setup === 1)
  if (setup.loading) return <OverviewSkeleton />
  if (setup.show) return <SetupGuide setup={setup} />
  return <Overview />
}

function OverviewSkeleton() {
  return (
    <Page className="@container/bt" data-testid="overview-loading">
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-4">
          <Skeleton className="size-[46px] shrink-0 rounded-panel" />
          <div className="min-w-0 flex-1">
            <Skeleton className="mt-1.5 mb-4 h-7 w-56" />
            <Skeleton className="mb-2.5 h-4 w-full max-w-[760px]" />
            <Skeleton className="h-4 w-2/3 max-w-[520px]" />
          </div>
        </div>
        <MapSkeleton />
      </div>
    </Page>
  )
}

function Overview() {
  const { pid } = useProjectCtx()
  const summary = useProjectSummary(pid)
  const modules = useModules(pid)
  const routes = useRoutes(pid)

  if (summary.isError)
    return (
      <Page className="@container/bt" data-testid="overview-panel">
        <Tile className="p-4">
          <Callout tone="error" icon={<AlertTriangle />} data-testid="overview-error">
            <span className="flex items-center gap-3">
              <span className="flex-1">Couldn't load project summary · {errorMessage(summary.error)}</span>
              <Button variant="outline" size="sm" onClick={() => summary.refetch()}>
                Retry
              </Button>
            </span>
          </Callout>
        </Tile>
      </Page>
    )
  if (!summary.data || !routes.data || !modules.data) return <OverviewSkeleton />

  return (
    <Page className="@container/bt" data-testid="overview-panel">
      <NextSteps className="mb-4" />
      <StoryOverview summary={summary.data} />
    </Page>
  )
}
