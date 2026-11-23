/**
 * The project's home screen (the Overview), laid out like an iPad home screen on the sky wallpaper:
 *   1. The project's name, its health and the live environment.
 *   2. What it builds, as preview cards in two rows (Builds.tsx): the app (a project is one app plus any workflows
 *      around it) and the workflows, each row ending in its add card. Short rows share one line. The plan lives with
 *      the app, in the app builder's Plan tab.
 *   3. Usage (Widgets.tsx): a KPI strip of six numbers, then the Activity chart beside Attention, Up next and the
 *      busiest routes.
 *   4. Data (DataSection.tsx): the project's tables and connected databases.
 * The supporting pages live in the top bar (HomeNav: System menu, ⌘J). Opening anything takes it full screen; the
 * top bar's Home button (or `g h`) comes back here.
 */
import { useNavigate } from '@tanstack/react-router'
import { BarChart3 } from 'lucide-react'
import { SectionHeader } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { useQuery } from '@tanstack/react-query'
import { schedulesApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { ProjectSummary } from '@/shared/api/types'
import type { ProjectPulse } from '@/features/project/hooks/useProjectPulse'
import { useProjectCtx } from '../../ProjectContext'
import { HealthBadge } from '../HealthBadge'
import { attentionItems } from '../lib/pulse'
import { recentWindow } from '../lib/stats'
import { BuildRow } from './Builds'
import { DataSection } from './DataSection'
import { ActivityPanel, AttentionWidget, KpiStrip, RoutesPanel, UpNextWidget } from './Widgets'

function Header({ summary, now }: { summary: ProjectSummary; now: number }) {
  const date = new Date(now).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
  return (
    <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5" data-testid="pulse-hero">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[12.5px] font-medium text-muted-foreground">{date}</span>
        <h1 className="truncate text-[32px] leading-tight font-[700] tracking-[-0.04em] text-foreground" data-testid="overview-project-name">
          {summary.project.name}
        </h1>
      </div>
      <span className="mb-1.5 flex items-center gap-2 text-[12.5px] text-muted-foreground">
        <HealthBadge summary={summary} />
        <span>live in {summary.project.live_environment}</span>
      </span>
    </div>
  )
}

/* ── the screen ────────────────────────────────────────────────────────────────────────────────────────────────── */

export function HomeScreen({
  summary,
  pulse,
  firstModule,
}: {
  summary: ProjectSummary
  pulse: ProjectPulse
  firstModule?: string
}) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const schedules = useQuery({ queryKey: qk.schedules(pid), queryFn: () => schedulesApi.list(pid), refetchInterval: 15_000 })
  const sch = schedules.data ?? []
  const w = recentWindow(pulse.runs, pulse.now)
  const items = attentionItems({
    entities: pulse.entities,
    status: pulse.status,
    openTasks: pulse.openTasks,
    missingSecrets: summary.missing_secrets,
    stubRoutes: summary.stub_routes ?? 0,
    liveEnv: summary.project.live_environment,
  })
  // short rows share one line (each card keeps a quarter of it) instead of leaving two lines half empty
  const appCols = pulse.appEntities.length + 1
  const wfCols = pulse.wfEntities.length + 1
  const pair = appCols + wfCols <= 4
  const appMeta = new Map(pulse.apps.map((a) => [a.id, a.published_at ? `Live · v${a.current_version_number ?? 1}` : 'Draft']))
  const wfMeta = new Map(pulse.workflows.map((wf) => [wf.id, `${wf.node_count} step${wf.node_count === 1 ? '' : 's'}`]))

  return (
    <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-7" data-testid="home-screen">
      <Header summary={summary} now={pulse.now} />

      <div
        className={cn('flex flex-col gap-7', pair && '@[880px]/bt:grid @[880px]/bt:grid-cols-(--cols) @[880px]/bt:gap-3')}
        style={pair ? ({ '--cols': `repeat(${appCols + wfCols}, minmax(0, 1fr))` } as React.CSSProperties) : undefined}
        data-testid="launcher"
      >
        <BuildRow
          kind="app"
          entities={pulse.appEntities}
          status={pulse.status}
          now={pulse.now}
          meta={appMeta}
          span={pair ? appCols : undefined}
          onNew={() => navigate({ to: '/p/$project/apps/$appId', params: { project: slug, appId: 'new' } })}
        />
        <BuildRow
          kind="workflow"
          entities={pulse.wfEntities}
          status={pulse.status}
          now={pulse.now}
          meta={wfMeta}
          span={pair ? wfCols : undefined}
          onNew={() => navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: 'new' } })}
        />
      </div>

      <div className="flex flex-col gap-3" data-testid="stats-band">
        <SectionHeader icon={<BarChart3 />} hue="backend" title={<h2 className="inline">Usage</h2>} sub={w.truncated ? `newest ${pulse.runs.length} runs` : 'last 24 hours'} />
        <KpiStrip summary={summary} pulse={pulse} window={w} />
        <div className="grid gap-4 @[760px]/bt:grid-cols-3">
          <ActivityPanel pulse={pulse} window={w} className="min-h-[260px] @[760px]/bt:col-span-2" />
          <div className="flex min-w-0 flex-col gap-4">
            <AttentionWidget items={items} firstModule={firstModule} />
            <UpNextWidget pulse={pulse} schedules={sch} window={w} />
            <RoutesPanel pulse={pulse} window={w} />
          </div>
        </div>
      </div>

      <DataSection pid={pid} slug={slug} />
    </div>
  )
}
