/**
 * Overview: the project's home screen (`overview/home/`: widgets, app and workflow icons, the system dock), or the
 * first-run guide while the project is new. The page is transparent: the widgets are glass on the sky wallpaper.
 * The scroller is a size container (`bt`), so the layout follows the sheet's width, not the viewport's.
 */
import { useSearch } from '@tanstack/react-router'
import { AlertTriangle } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useModules, useProjectSummary } from '../hooks/useProjectData'
import { useSetup } from '../hooks/useSetup'
import { NextSteps } from './NextSteps'
import { SetupGuide } from './SetupGuide'
import { HomeScreen } from '../overview/home/HomeScreen'
import { useProjectPulse } from '@/features/project/hooks/useProjectPulse'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Callout, Tile, Page } from '@/shared/components/ui/surface'
import { errorMessage } from '@/shared/api/http'

export { HealthBadge } from '../overview/HealthBadge'

/** 8px page gutter on phones (the sheet adds 8px: 16px from the screen edge), the standard inset from 640px. */
const pageCls = '@container/bt px-2 sm:px-8 pt-5 sm:pt-7 pb-4'

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
    <Page className={pageCls} data-testid="overview-loading">
      <div className="flex flex-col gap-6">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-10 w-full max-w-[520px]" />
        <div className="flex gap-12">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12 w-24" />
          ))}
        </div>
        <Skeleton className="h-[320px] w-full rounded-panel" />
      </div>
    </Page>
  )
}

function Overview() {
  const { pid } = useProjectCtx()
  const summary = useProjectSummary(pid)
  const pulse = useProjectPulse(pid)
  const modules = useModules(pid)

  if (summary.isError || pulse.error)
    return (
      <Page className={pageCls} data-testid="overview-panel">
        <Tile className="p-4">
          <Callout tone="error" icon={<AlertTriangle />} data-testid="overview-error">
            <span className="flex items-center gap-3">
              <span className="flex-1">Couldn't load the project overview · {errorMessage(summary.error ?? pulse.error)}</span>
              <Button variant="outline" size="sm" onClick={() => summary.refetch()}>
                Retry
              </Button>
            </span>
          </Callout>
        </Tile>
      </Page>
    )
  if (!summary.data || !pulse.listsReady || !pulse.runsReady) return <OverviewSkeleton />

  return (
    <Page className={pageCls} data-testid="overview-panel">
      <NextSteps className="mx-auto mb-6 max-w-[1180px]" />
      <HomeScreen
        summary={summary.data}
        pulse={pulse}
        firstModule={modules.data?.[0]?.name}
      />
    </Page>
  )
}
