/**
 * Overview: the 12-column bento (DESIGN §1; `design/overview.html`), or the first-run guide.
 * The page is transparent; every box is a glass `Tile`, text sits in `Well`s. The scroller is a size
 * container (`bt`), so the grid reflows to 6 columns on the sheet's width, not the viewport's.
 */
import { useSearch } from '@tanstack/react-router'
import { AlertTriangle } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useProjectSummary, useRoutes, useModules } from '../hooks/useProjectData'
import { useSetup } from '../hooks/useSetup'
import { ActivityTile } from '../overview/ActivityTile'
import { BackendTile } from '../overview/BackendTile'
import { BuildingBlocks } from '../overview/BuildingBlocks'
import { HeroTile } from '../overview/HeroTile'
import { RecentRunsTile } from '../overview/RecentRunsTile'
import { ReleasesTile } from '../overview/ReleasesTile'
import { SecretsTile } from '../overview/SecretsTile'
import { StatTiles } from '../overview/StatTiles'
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
      <div className="grid grid-cols-12 gap-4">
        <Tile className="col-span-8 h-[445px] p-5">
          <Skeleton className="mb-4 h-8 w-56" />
          <Skeleton className="mb-3 h-10 w-full" />
          <Skeleton className="h-[260px] w-full" />
        </Tile>
        <Tile className="col-span-4 h-[445px] p-5">
          <Skeleton className="mb-4 h-6 w-32" />
          <Skeleton className="h-16 w-40" />
        </Tile>
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

  const s = summary.data
  const hasReleaseEnv = s.project.environments.some((e) => e.kind === 'release')

  return (
    <Page className="@container/bt" data-testid="overview-panel">
      <NextSteps className="mb-4" />
      <div className="grid grid-cols-6 gap-4 @min-[981px]/bt:grid-cols-12">
        <BuildingBlocks />
        <HeroTile summary={s} />
        <ActivityTile runs24h={s.health.runs_24h} />
        <StatTiles failed24h={s.health.failed_24h} />
        <BackendTile />
        <RecentRunsTile />
        <SecretsTile />
        {hasReleaseEnv && <ReleasesTile summary={s} />}
      </div>
    </Page>
  )
}

