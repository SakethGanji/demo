/**
 * The project Overview as a story map (design `overview-mockup-v3.html`): the story sentence, the map of
 * what the project has (with the focus panel for whatever is picked), the run river and the ship row. It
 * adapts to the project: only the kinds it has get a lane, and sections with nothing to say stay out.
 */
import { useEffect, useRef, useState } from 'react'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Tile } from '@/shared/components/ui/surface'
import { useMotionOff } from '@/features/knowledge/hooks/useMotionOff'
import type { ProjectSummary } from '@/shared/api/types'
import { useProjectCtx } from '../../ProjectContext'
import { useStoryActions } from '../hooks/useStoryActions'
import { useStoryData } from '../hooks/useStoryData'
import { median } from '../lib/model'
import { buildSentence, type TokTarget } from '../lib/sentence'
import { StoryFocus } from './StoryFocus'
import { StoryHeader } from './StoryHeader'
import { StoryMap } from './StoryMap'
import { StoryRiver } from './StoryRiver'
import { StoryShip } from './StoryShip'
import '../story.css'

export function StoryOverview({ summary }: { summary: ProjectSummary }) {
  const { pid, project } = useProjectCtx()
  const data = useStoryData(pid)
  const actions = useStoryActions(data.modules)
  const motionOff = useMotionOff()
  const [selected, setSelected] = useState<string | null>(null)
  const [closing, setClosing] = useState(false)
  const [since] = useState(() => Date.now())
  const root = useRef<HTMLDivElement>(null)

  // CSS motion pauses while the tab is hidden
  useEffect(() => {
    const el = root.current
    if (!el) return
    const sync = () => el.setAttribute('data-paused', String(document.hidden))
    sync()
    document.addEventListener('visibilitychange', sync)
    return () => document.removeEventListener('visibilitychange', sync)
  }, [])

  const { model } = data
  const hasRelease = project.environments.some((e) => e.kind === 'release')
  const live = project.live_environment
  const p50 =
    summary.health.p50_ms ??
    median(
      data.runs
        .filter((r) => r.duration_ms != null && (Date.parse(r.start_time) || 0) >= data.now - 24 * 3600 * 1000)
        .map((r) => r.duration_ms as number),
    )
  const sentence = buildSentence({
    model,
    runs24h: summary.health.runs_24h,
    failed24h: summary.health.failed_24h,
    p50,
    missing: data.missing,
    release: hasRelease ? { live, number: summary.active_releases?.[live]?.number ?? null } : null,
  })

  const focusObj = selected ? model.byKey.get(selected) : undefined

  const close = () => {
    if (!selected) return
    if (motionOff) return setSelected(null)
    setClosing(true)
  }
  const closed = () => {
    setClosing(false)
    setSelected(null)
  }
  const toggle = (k: string) => {
    if (selected === k && !closing) return close()
    setClosing(false)
    setSelected(k)
  }

  // Escape closes the focus panel and gives focus back to the object it was about
  useEffect(() => {
    if (!selected) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      const t = e.target as HTMLElement | null
      if (t?.closest('input, textarea, [contenteditable="true"], [role="dialog"], [role="menu"]')) return
      document.querySelector<HTMLElement>(`[data-obj="${CSS.escape(selected)}"]`)?.focus({ preventScroll: true })
      close()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })

  const reveal = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: motionOff ? 'auto' : 'smooth', block: 'center' })
  const pick = (key: string) => {
    setClosing(false)
    setSelected(key)
    requestAnimationFrame(() => document.querySelector(`[data-obj="${CSS.escape(key)}"]`)?.scrollIntoView({ behavior: motionOff ? 'auto' : 'smooth', block: 'center' }))
  }
  const firstRoute = data.routes.find((r) => r.handler.type !== 'stub') ?? data.routes[0]
  const firstWorkflow = model.lanes.find((l) => l.key === 'workflows')?.objs[0]
  const onToken = (t: TokTarget) => {
    if (t.type === 'obj') return pick(t.key)
    if (t.type === 'lane') {
      const first = model.lanes.find((l) => l.key === t.lane)?.objs[0]
      return first && pick(first.key)
    }
    if (t.type === 'section') return reveal(`story-${t.id}`)
    if (t.to === 'new-route') return actions.newRoute()
    if (t.to === 'new-workflow') return actions.newWorkflow()
    if (t.to === 'test-route') return firstRoute && actions.testRoute(firstRoute)
    if (t.to === 'run-workflow') return firstWorkflow ? pick(firstWorkflow.key) : actions.newWorkflow()
  }

  const hasLane = (k: string) => model.lanes.some((l) => l.key === k)
  const emptyHint = hasLane('workflows')
    ? 'Run a workflow and it lands here.'
    : hasLane('routes')
      ? 'Test a route and the call lands here.'
      : 'Every route call and workflow run lands here.'
  const emptyAction = hasLane('workflows')
    ? { label: 'Run a workflow', run: () => onToken({ type: 'go', to: 'run-workflow' }) }
    : hasLane('routes')
      ? { label: 'Test a route', run: () => onToken({ type: 'go', to: 'test-route' }) }
      : undefined

  return (
    <div ref={root} className="flex flex-col gap-4" data-story data-motion={motionOff ? 'off' : undefined}>
      <StoryHeader summary={summary} sentence={sentence} routes={data.routes} onToken={onToken} onCreateUi={actions.createUi} />
      {!data.ready ? (
        <MapSkeleton />
      ) : (
      <StoryMap
        model={model}
        runs={data.runs}
        runsLoaded={data.runsLoaded}
        pid={pid}
        selected={focusObj ? selected : null}
        onSelect={toggle}
        add={actions.add}
        onUploadDataset={actions.openDatasets}
        motionOff={motionOff}
        failed={data.failed}
        onRetry={data.retry}
      >
        {focusObj && <StoryFocus obj={focusObj} data={data} actions={actions} closing={closing} onClose={close} onClosed={closed} />}
      </StoryMap>
      )}
      <StoryRiver
        runs={data.runs}
        now={data.now}
        runs24h={summary.health.runs_24h}
        failed24h={summary.health.failed_24h}
        p50={p50}
        since={since}
        emptyHint={emptyHint}
        onEmptyAction={data.ready ? emptyAction : undefined}
      />
      <StoryShip summary={summary} secrets={data.secrets} missing={data.missing} />
    </div>
  )
}

/** The map's shape while its lists load: lanes of plates, so the page doesn't jump when they arrive. */
export function MapSkeleton() {
  return (
    <Tile className="px-[22px] pt-[22px] pb-5" data-testid="story-map-loading" aria-busy>
      <div className="grid grid-cols-1 gap-x-14 gap-y-6 @min-[761px]/bt:grid-cols-4">
        {[2, 3, 1, 2].map((n, i) => (
          <div key={i} className="flex flex-col gap-4">
            <Skeleton className="h-3.5 w-24" />
            {Array.from({ length: n }, (_, j) => (
              <div key={j} className="flex items-start gap-[11px]">
                <Skeleton className="size-[38px] shrink-0 rounded-panel" />
                <div className="flex-1 pt-0.5">
                  <Skeleton className="mb-2 h-3.5 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Tile>
  )
}
