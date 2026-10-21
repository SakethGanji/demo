/**
 * The build screen (`/p/$project/build/$build`): one build run in full, the detail view behind the app builder's
 * in-chat build card. The header has the plan version, the overall status, steps done of all, the elapsed time and
 * Cancel; a build waiting for input shows what it needs; a finished build leads with its report; the timeline follows
 * live over the build's event stream (polling when the stream is unavailable).
 */
import { Link, useParams } from '@tanstack/react-router'
import { Ban, ClipboardList, Hammer } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { errorMessage } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { Dot, Page, PageHeader, Progress, SectionHeader, Tile } from '@/shared/components/ui/surface'
import { buildCounts, formatElapsed, isActive, stepElapsed, waitingStep } from './buildLogic'
import { BuildReportCard, BuildStatusBadge, BuildTimeline } from './BuildTimeline'
import { BuildInputCard } from './PlanFlow'
import { useBuildMutations, useLiveBuild, useNow } from './useBuild'

export function BuildView() {
  const { build: id } = useParams({ strict: false }) as { build: string }
  return <BuildScreen key={id} id={id} />
}

function BuildScreen({ id }: { id: string }) {
  const { pid, slug } = useProjectCtx()
  const live = useLiveBuild(pid, id)
  const m = useBuildMutations(pid)
  const b = live.build
  const active = !!b && isActive(b.status)
  const now = useNow(active)
  const counts = b ? buildCounts(b) : { finished: 0, total: 0, failed: 0 }
  const ms = b ? stepElapsed(b, now) : null
  const waiting = waitingStep(b)

  const cancel = () =>
    m.cancel.mutate(id, {
      onSuccess: (r) => live.put(r),
      onError: (e) => toast.error('Not cancelled', { description: errorMessage(e) }),
    })

  return (
    <div className="h-full" data-testid="build-view">
      <Page className="@container/plan px-2 pt-5 pb-4 sm:px-8 sm:pt-7">
        <div className="mx-auto flex w-full max-w-[860px] flex-col gap-5">
          <PageHeader
            title="Build"
            icon={<Hammer />}
            hue="backend"
            meta={
              b ? (
                <>
                  <Link to="/p/$project/plan" params={{ project: slug }} className="hover:text-foreground hover:underline underline-offset-2">
                    Plan v{b.plan_version}
                  </Link>
                  {b.app_key ? (
                    <>
                      <Dot />
                      <span>{b.app_key}</span>
                    </>
                  ) : null}
                  <Dot />
                  <BuildStatusBadge status={b.status} />
                </>
              ) : null
            }
            actions={
              active ? (
                <Button size="sm" variant="outline" disabled={m.cancel.isPending} onClick={cancel} data-testid="build-cancel">
                  {m.cancel.isPending ? <Spinner /> : <Ban />} Cancel
                </Button>
              ) : (
                <Button size="sm" variant="ghost" render={<Link to="/p/$project/plan" params={{ project: slug }} />}>
                  <ClipboardList /> Plan
                </Button>
              )
            }
          />
          {!b ? (
            live.error ? (
              <Tile>
                <EmptyState title="Couldn't load this build" data-testid="build-error">{errorMessage(live.error)}</EmptyState>
              </Tile>
            ) : (
              <Tile className="p-4">
                <Skeleton className="h-5 w-48" />
                <Skeleton className="mt-3 h-24 w-full" />
              </Tile>
            )
          ) : (
            <>
              <Tile className="flex flex-col gap-2 p-4" data-testid="build-progress">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="text-[13.5px] font-semibold" data-testid="build-progress-count">
                    {counts.finished} of {counts.total} steps
                  </span>
                  {counts.failed ? <span className="text-[12.5px] text-destructive">{counts.failed} failed</span> : null}
                  <span className="ml-auto text-[12.5px] text-muted-foreground tabular-nums">{ms != null ? formatElapsed(ms) : 'not started'}</span>
                </div>
                <Progress value={counts.total ? (counts.finished / counts.total) * 100 : 0} />
                {live.polling && active ? <span className="text-[11.5px] text-muted-foreground">Live updates unavailable; refreshing every few seconds.</span> : null}
              </Tile>
              {waiting ? (
                <Tile className="p-4">
                  <BuildInputCard build={b} step={waiting} onContinued={live.put} />
                </Tile>
              ) : null}
              {!active ? (
                <Tile className="p-4 @min-[640px]/plan:p-5">
                  <BuildReportCard build={b} slug={slug} />
                </Tile>
              ) : null}
              <Tile className="p-4 @min-[640px]/plan:p-5">
                <SectionHeader title="Steps" sub="backend modules first, then the app, then the plan check" className="mb-3" />
                <BuildTimeline build={b} slug={slug} now={now} />
              </Tile>
            </>
          )}
        </div>
      </Page>
    </div>
  )
}
