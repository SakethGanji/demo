/**
 * The way home, the same on every project page (workspace views and both editors), at the left of the slim top bar:
 *   [⌂]  Home: the project's home screen (the Overview), `g h` from any workspace page.
 *   [⊞]  Jump: a searchable menu of every app, workflow and system page, ⌘J from anywhere, so you can go straight
 *        from one thing to another without going home first.
 *   System  the supporting pages (Runs, Approvals, Data, Backend, Secrets, Knowledge, Datasets, APIs, Settings) as a
 *        quiet grid menu, with badges for what needs a look (failures today, approvals open, secrets missing); the
 *        trigger carries a dot when any badge is up.
 */
import { useEffect, useState } from 'react'
import { Link, useMatchRoute, useNavigate } from '@tanstack/react-router'
import { ChevronDown, House, LayoutGrid, Plus } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { SearchMenuList, type SearchMenuItem } from '@/shared/components/ui/search-menu'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { useProjectCtx } from '../ProjectContext'
import { useProjectApps, useProjectSummary, useProjectWorkflows } from '../hooks/useProjectData'
import { useTaskCounts } from '@/features/approvals/hooks/useTasks'
import { initials } from '../lib/entities'
import { PLACES, type Place } from '../lib/places'

const btn =
  'inline-flex size-8 shrink-0 items-center justify-center rounded-ctl text-foreground/80 outline-none hover:bg-(--hover-fill) hover:text-foreground focus-visible:shadow-focus aria-expanded:bg-(--hover-fill) data-[on]:bg-foreground/8 data-[on]:text-foreground [&>svg]:size-[17px] [&>svg]:stroke-[1.75]'

const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true })

function Mark({ text, tint, round, children }: { text: string; tint: string; round?: boolean; children: React.ReactNode }) {
  return (
    <span className={cn('flex size-5 shrink-0 items-center justify-center text-[9px] font-semibold [&>svg]:size-3', round ? 'rounded-full' : 'rounded-chip', text, tint)} aria-hidden>
      {children}
    </span>
  )
}

export function HomeNav({ className }: { className?: string }) {
  const { slug, pid, project } = useProjectCtx()
  const navigate = useNavigate()
  const matchRoute = useMatchRoute()
  const apps = useProjectApps(pid)
  const workflows = useProjectWorkflows(pid)
  const [open, setOpen] = useState(false)
  const atHome = !!matchRoute({ to: '/p/$project', params: { project: slug } })
  const P = { project: slug }

  // ⌘J opens the jump menu from anywhere (⌘K stays with each page's own palette)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'j') {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const go = (fn: () => void) => () => {
    setOpen(false)
    fn()
  }
  const appItems: SearchMenuItem[] = [...(apps.data ?? [])].sort(byName).map((a) => ({
    key: `app:${a.id}`,
    keywords: `app ${a.name}`,
    icon: <Mark text="text-hue-apps" tint="bg-hue-apps/16">{initials(a.name)}</Mark>,
    label: a.name,
    testId: `jump-app-${a.id}`,
    onSelect: go(() => navigate({ to: '/p/$project/apps/$appId', params: { ...P, appId: a.id } })),
  }))
  const wfItems: SearchMenuItem[] = [...(workflows.data ?? [])].sort(byName).map((w) => ({
    key: `wf:${w.id}`,
    keywords: `workflow ${w.name}`,
    icon: <Mark text="text-hue-flow" tint="bg-hue-flow/16" round>{initials(w.name)}</Mark>,
    label: w.name,
    testId: `jump-wf-${w.id}`,
    onSelect: go(() => navigate({ to: '/p/$project/workflows/$workflowId', params: { ...P, workflowId: w.id } })),
  }))
  const placeItems: SearchMenuItem[] = [
    { key: 'home', keywords: 'home overview', icon: <Mark text="text-foreground/70" tint="bg-foreground/8"><House /></Mark>, label: 'Home', testId: 'jump-home', onSelect: go(() => navigate({ to: '/p/$project', params: P })) },
    ...PLACES.map((p) => ({
      key: p.key,
      keywords: `system ${p.label}`,
      icon: (
        <Mark text={p.text} tint={p.tint}>
          <p.Icon />
        </Mark>
      ),
      label: p.label,
      testId: `jump-${p.key}`,
      onSelect: go(() =>
        p.overlay
          ? navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, ...p.overlay }) } as never)
          : navigate({ to: p.to as '/p/$project', params: P }),
      ),
    })),
  ]
  const newItems: SearchMenuItem[] = [
    { key: 'new-app', keywords: 'new app create', icon: <Plus />, label: 'New app', testId: 'jump-new-app', onSelect: go(() => navigate({ to: '/p/$project/apps/$appId', params: { ...P, appId: 'new' } })) },
    { key: 'new-wf', keywords: 'new workflow create', icon: <Plus />, label: 'New workflow', testId: 'jump-new-workflow', onSelect: go(() => navigate({ to: '/p/$project/workflows/$workflowId', params: { ...P, workflowId: 'new' } })) },
  ]

  return (
    <div className={cn('flex shrink-0 items-center gap-0.5', className)} data-testid="home-nav">
      <Tip label={`Home · ${project.name}`} kbd="G H" side="bottom">
        <Link to="/p/$project" params={P} className={btn} data-on={atHome ? '' : undefined} aria-label="Home" data-testid="home-button">
          <House />
        </Link>
      </Tip>
      <Popover open={open} onOpenChange={setOpen}>
        <Tip label="Jump to an app, workflow or page" kbd="⌘J" side="bottom">
          <PopoverTrigger className={btn} aria-label="Jump to" data-testid="jump-button">
            <LayoutGrid />
          </PopoverTrigger>
        </Tip>
        <PopoverContent side="bottom" align="start" className="w-80 p-0">
          <SearchMenuList
            placeholder="Jump to an app, workflow or page…"
            testId="jump-menu"
            groups={[
              { label: 'Apps', items: appItems },
              { label: 'Workflows', items: wfItems },
              { label: 'System', items: placeItems },
              { items: newItems },
            ]}
          />
        </PopoverContent>
      </Popover>
      <SystemMenu />
    </div>
  )
}

function SystemMenu() {
  const { slug, pid } = useProjectCtx()
  const navigate = useNavigate()
  const matchRoute = useMatchRoute()
  const summary = useProjectSummary(pid)
  const tasks = useTaskCounts(pid)
  const [open, setOpen] = useState(false)
  const badges: Partial<Record<Place['key'], { n: number; tone: 'err' | 'warn' }>> = {
    runs: { n: summary.data?.health.failed_24h ?? 0, tone: 'err' },
    approvals: { n: tasks.data?.open ?? 0, tone: 'warn' },
    secrets: { n: summary.data?.missing_secrets ?? 0, tone: 'warn' },
  }
  const up = Object.values(badges).filter((b) => b && b.n > 0)
  const dot = up.some((b) => b!.tone === 'err') ? 'bg-destructive' : up.length ? 'bg-warning' : null
  const P = { project: slug }
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="ml-0.5 inline-flex h-8 shrink-0 items-center gap-1 rounded-ctl px-2 text-[12.5px] font-medium text-foreground/75 outline-none hover:bg-(--hover-fill) hover:text-foreground focus-visible:shadow-focus aria-expanded:bg-(--hover-fill)"
        data-testid="system-button"
      >
        System
        {dot && <span className={cn('size-1.5 rounded-full', dot)} data-testid="system-dot" />}
        <ChevronDown className="size-3.5 text-faint" />
      </PopoverTrigger>
      <PopoverContent side="bottom" align="start" className="w-[300px] p-2" data-testid="system-menu">
        <div className="grid grid-cols-3 gap-1">
          {PLACES.map((p) => {
            const b = badges[p.key]
            const on = !!p.to && !!matchRoute({ to: p.to, params: P, fuzzy: true })
            return (
              <button
                key={p.key}
                type="button"
                className={cn('flex flex-col items-center gap-1.5 rounded-well px-1 pt-2.5 pb-2 outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus', on && 'bg-foreground/6')}
                onClick={() => {
                  setOpen(false)
                  if (p.overlay) navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, ...p.overlay }) } as never)
                  else navigate({ to: p.to as '/p/$project', params: P })
                }}
                data-testid={`system-${p.key}`}
              >
                <span className={cn('relative flex size-9 items-center justify-center rounded-well [&>svg]:size-[18px] [&>svg]:stroke-[1.75]', p.tint, p.text)} aria-hidden>
                  <p.Icon />
                  {b && b.n > 0 && (
                    <span
                      className={cn(
                        'absolute -top-1.5 -right-1.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums shadow-[0_0_0_2px_var(--surface)]',
                        b.tone === 'err' ? 'bg-destructive text-destructive-foreground' : 'bg-warning-soft text-warning-text',
                      )}
                      data-testid={`system-badge-${p.key}`}
                    >
                      {b.n > 99 ? '99+' : b.n}
                    </span>
                  )}
                </span>
                <span className="text-[11.5px] text-foreground/85">{p.label}</span>
              </button>
            )
          })}
        </div>
      </PopoverContent>
    </Popover>
  )
}
