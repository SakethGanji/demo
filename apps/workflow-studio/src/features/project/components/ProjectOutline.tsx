/**
 * ProjectOutline (DESIGN §1, §3, UI-DESIGN §3.1) and its 1024–1279px icon rail (review H5).
 * A project is built from three optional blocks: Interface (screens; "apps" in code), Backend (modules and the routes
 * they serve) and Workflows. Bands: Build (the blocks) · Data & connections (tables, external APIs, secrets) · Operate (runs, approvals), with
 * settings pinned at the foot. It sits on the canvas: items are 30px, icons take their entity hue (60% when
 * inactive), group heads are sentence case with a `+` that appears on hover, and one soft fill slides to the active item.
 */
import { useLayoutEffect, useRef, useState } from 'react'
import { Link, useMatchRoute, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import {
  ArrowUpRight,
  AppWindow,
  CheckCheck,
  Database,
  FileCode2,
  History,
  KeyRound,
  LayoutGrid,
  LayoutList,
  ListChecks,
  Plug,
  Plus,
  Settings2,
  Sparkles,
  Table2,
  KeySquare,
  Workflow,
} from 'lucide-react'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/shared/components/ui/hover-card'
import { cn } from '@/shared/lib/utils'
import { liquidGlassSupported, useLiquidGlass } from '@/shared/lib/liquid-glass'
import { useProjectCtx } from '../ProjectContext'
import { useModules, useProjectApps, useProjectSummary, useProjectWorkflows, useRoutes, useTables } from '../hooks/useProjectData'
import { useSetup } from '../hooks/useSetup'
import { useTaskCounts } from '@/features/approvals/hooks/useTasks'
import { sortRoutes } from '../lib/routes'
import { byRecent, pluralize } from '../lib/format'
import { useOutlineDraftStore } from '@/features/backend/outlineDraftStore'
import type { Route } from '@/shared/api/types'
import { useApiSpecs } from '@/features/api-catalog/useApiSpecs'

const itemCls =
  'relative z-[1] flex h-[30px] items-center gap-2 rounded-ctl px-2 text-[13px] text-sidebar-foreground hover:bg-foreground/6 [&>svg]:size-[15px] [&>svg]:shrink-0 [&>svg]:stroke-[1.75] [&>svg]:opacity-60'
/** active: the sliding fill draws the background; the item only lifts its text and icon */
const onCls = 'font-medium text-foreground hover:bg-transparent [&>svg]:opacity-100'
const hue = {
  home: '[&>svg]:text-hue-home',
  backend: '[&>svg]:text-hue-backend',
  ai: '[&>svg]:text-hue-ai',
  data: '[&>svg]:text-hue-data',
  apps: '[&>svg]:text-hue-apps',
  secrets: '[&>svg]:text-hue-secrets',
  runs: '[&>svg]:text-hue-runs',
  flow: '[&>svg]:text-hue-flow',
}

/** One fill that slides to the item marked `data-active` inside `host` (DESIGN §7: navigation motion). */
function ActiveFill({ host, dep }: { host: React.RefObject<HTMLElement | null>; dep: unknown }) {
  type Box = { top: number; height: number; left: number; width: number }
  const [box, setBox] = useState<(Box & { animate: boolean }) | null>(null)
  const last = useRef<Box | null>(null)
  const key = JSON.stringify(dep)
  useLayoutEffect(() => {
    const el = host.current
    if (!el) return
    const measure = () => {
      const on = el.querySelector<HTMLElement>('[data-active]')
      const next = on ? { top: on.offsetTop, height: on.offsetHeight, left: on.offsetLeft, width: on.offsetWidth } : null
      const prev = last.current
      if (prev === next || (prev && next && prev.top === next.top && prev.height === next.height && prev.left === next.left && prev.width === next.width)) return
      last.current = next
      setBox(next ? { ...next, animate: !!prev } : null)
    }
    measure()
    let raf = 0
    const mo = new MutationObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(measure)
    })
    mo.observe(el, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-active'] })
    return () => {
      mo.disconnect()
      cancelAnimationFrame(raf)
    }
  }, [host, key])
  // where supported (Chromium, motion allowed) the fill is a liquid-glass lens over the active item
  const lensRef = useRef<HTMLSpanElement>(null)
  const liquid = liquidGlassSupported()
  useLiquidGlass(lensRef, { radius: 6, bezel: 6, strength: 8, extra: 'saturate(160%) brightness(1.05)' }, liquid && !!box)
  if (!box) return null
  return (
    <span
      ref={lensRef}
      aria-hidden
      data-slot="active-fill"
      className={cn(
        'pointer-events-none absolute rounded-ctl',
        'bg-brand-soft',
        liquid ? 'z-[2] shadow-(--thumb-glass-shadow)' : 'z-0 shadow-raise-sm',
        box.animate && 'transition-[top,height] duration-(--d-base) ease-(--ease-io)',
      )}
      style={{ top: box.top, height: box.height, left: box.left, width: box.width }}
    />
  )
}

function useSearchSetter() {
  const navigate = useNavigate()
  return (patch: Record<string, unknown>) =>
    navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, ...patch }) } as never)
}

/**
 * A heading in the outline. `section` is a top-level band (Build, Data, Operate); `group` is a building block inside it
 * (Interface, Backend, Workflows, Tables) with a `+` that appears on hover.
 */
function GroupHeader({
  label,
  onAdd,
  addTestId,
  addLabel,
  variant = 'group',
}: {
  label: string
  onAdd?: () => void
  addTestId?: string
  addLabel?: string
  variant?: 'section' | 'group'
}) {
  return (
    <div className={cn('group/gh flex items-center justify-between px-2', variant === 'section' ? 'h-[34px] pt-4 pb-0.5' : 'h-[28px] pt-1.5')}>
      <span className={variant === 'section' ? 'text-[11.5px] font-semibold text-faint' : 'text-[12px] font-medium text-muted-foreground'}>{label}</span>
      {onAdd && (
        <button
          type="button"
          onClick={onAdd}
          data-testid={addTestId}
          aria-label={addLabel}
          className="inline-flex size-5 items-center justify-center rounded-chip text-muted-foreground opacity-0 group-hover/gh:opacity-100 hover:bg-(--hover-fill) hover:text-foreground focus-visible:opacity-100"
        >
          <Plus className="size-3.5" />
        </button>
      )}
    </div>
  )
}

/** The muted line under an empty group. */
function Hint({ children }: { children: React.ReactNode }) {
  return <div className="pb-1.5 pl-[31px] pr-2 pt-0.5 text-xs text-muted-foreground">{children}</div>
}

/** The first `max` items, plus the active one when it sits past the cap; `more` is how many stay hidden. */
function capList<T>(xs: T[], max: number, isOn: (x: T) => boolean = () => false): { shown: T[]; more: number } {
  if (xs.length <= max) return { shown: xs, more: 0 }
  const head = xs.slice(0, max)
  const active = xs.slice(max).find(isOn)
  const shown = active ? [...head, active] : head
  return { shown, more: xs.length - shown.length }
}

const moreCls = 'relative z-[1] flex h-[26px] w-full items-center rounded-ctl pl-[31px] pr-2 text-left text-xs text-muted-foreground hover:bg-foreground/6 hover:text-foreground'

/** "Show all (n)" under a capped group that has no page of its own: expands in place. */
function ShowAll({ more, onClick, testId }: { more: number; onClick: () => void; testId: string }) {
  return (
    <button type="button" className={moreCls} onClick={onClick} data-testid={testId}>
      Show all ({more} more)
    </button>
  )
}

const MAX_APPS = 5
const MAX_MODULES = 8
const MAX_WORKFLOWS = 5

/** Counts of draft-only missing secret keys (client scan, §4.5) not already in summary. */
function useDraftState(pid: string) {
  const drafts = useOutlineDraftStore((s) => s.drafts)
  return Object.values(drafts).filter((d) => d.projectId === pid)
}

export function useMissingCount(pid: string) {
  const summary = useProjectSummary(pid)
  const drafts = useDraftState(pid)
  const extra = new Set<string>()
  for (const d of drafts) if (d.dirty) d.missingSecretKeys.forEach((k) => extra.add(k))
  return (summary.data?.missing_secrets ?? 0) + extra.size
}

export function ProjectOutline() {
  const { pid, slug } = useProjectCtx()
  const params = useParams({ strict: false }) as { module?: string; table?: string }
  const matchRoute = useMatchRoute()
  const setSearch = useSearchSetter()
  const navigate = useNavigate()
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const apiSpecs = useApiSpecs(pid)
  const tables = useTables(pid)
  const apps = useProjectApps(pid)
  const workflows = useProjectWorkflows(pid)
  const summary = useProjectSummary(pid)
  const drafts = useDraftState(pid)
  const setup = useSetup(pid, false)
  const missingCount = useMissingCount(pid)
  const [allApps, setAllApps] = useState(false)
  const [allModules, setAllModules] = useState(false)
  const P = { project: slug }

  const onOverview = !!matchRoute({ to: '/p/$project', params: P })
  const onSecrets = !!matchRoute({ to: '/p/$project/secrets', params: P })
  const onApis = !!matchRoute({ to: '/p/$project/apis', params: P })
  const onWorkflows = !!matchRoute({ to: '/p/$project/workflows', params: P })
  const onRuns = !!matchRoute({ to: '/p/$project/runs', params: P, fuzzy: true })
  const onApprovals = !!matchRoute({ to: '/p/$project/approvals', params: P, fuzzy: true })
  const openTasks = useTaskCounts(pid).data?.open ?? 0
  const activeModule = matchRoute({ to: '/p/$project/backend/$module', params: P, fuzzy: true }) ? params.module : undefined
  const activeTable = params.table
  const routeParam = (useSearch({ strict: false }) as { route?: string }).route

  const routesByModule = new Map<string, Route[]>()
  for (const r of routes.data ?? []) {
    if (r.handler.type === 'python') routesByModule.set(r.handler.module_id, [...(routesByModule.get(r.handler.module_id) ?? []), r])
  }
  const savedNames = new Set((modules.data ?? []).map((m) => m.name))
  const draftModules = drafts.filter((d) => d.isNew && !savedNames.has(d.module))
  const draftTables = new Set(drafts.filter((d) => d.dirty).flatMap((d) => d.tablesOnSave))
  const existingTables = new Set((tables.data ?? []).map((t) => t.name))
  const runs24 = summary.data?.health.runs_24h ?? 0
  const appList = capList(apps.data ?? [], allApps ? Infinity : MAX_APPS)
  const moduleList = capList(modules.data ?? [], allModules ? Infinity : MAX_MODULES, (m) => m.name === activeModule)
  const recentWorkflows = byRecent(workflows.data ?? [])
  const workflowList = capList(recentWorkflows, MAX_WORKFLOWS)
  const listRef = useRef<HTMLDivElement>(null)
  const act = (on: boolean) => (on ? { 'data-active': '' } : {})

  return (
    <nav className="flex min-h-0 flex-1 flex-col" aria-label="Project outline" data-testid="outline">
      <div ref={listRef} className="relative min-h-0 flex-1 overflow-y-auto px-2.5 pt-3.5">
        <ActiveFill host={listRef} dep={[onOverview, onSecrets, onApis, onWorkflows, onRuns, onApprovals, activeModule, activeTable, routeParam]} />
        <Link to="/p/$project" params={P} className={cn(itemCls, hue.home, onOverview && onCls)} {...act(onOverview)} data-testid="outline-overview">
          {setup.show ? <ListChecks /> : <LayoutGrid />}
          {setup.show ? (
            <>
              Get started <span className="ml-auto text-[11.5px] text-muted-foreground">{setup.done} / 4</span>
            </>
          ) : (
            'Overview'
          )}
        </Link>

        <GroupHeader variant="section" label="Build" />
        <GroupHeader label="Interface" onAdd={() => setSearch({ dialog: 'create-ui' })} addTestId="outline-add-app" addLabel="New interface" />
        {appList.shown.map((a) => (
          <Link
            key={a.id}
            to="/p/$project/apps/$appId"
            params={{ ...P, appId: a.id }}
            className={cn(itemCls, hue.apps, 'group')}
            data-testid={`outline-app-${a.id}`}
          >
            <AppWindow />
            <span className="truncate">{a.name}</span>
            <ArrowUpRight className="ml-auto !size-3.5 opacity-60 group-hover:opacity-100" />
          </Link>
        ))}
        {appList.more > 0 && <ShowAll more={appList.more} onClick={() => setAllApps(true)} testId="outline-apps-more" />}
        {(apps.data?.length ?? 0) === 0 && <Hint>No interface yet</Hint>}

        <GroupHeader label="Backend" onAdd={() => setSearch({ dialog: 'new-module' })} addTestId="outline-add-module" addLabel="New module" />
        {moduleList.shown.map((m) => {
          const on = activeModule === m.name
          const draft = drafts.find((d) => d.module === m.name && d.dirty)
          const rs = sortRoutes(routesByModule.get(m.id) ?? [])
          const expanded = on || (!activeModule && modules.data![0]?.id === m.id && onOverview)
          return (
            <div key={m.id}>
              <Link
                to="/p/$project/backend/$module"
                params={{ ...P, module: m.name }}
                className={cn(itemCls, hue.backend, on && !routeParam && onCls)}
                {...act(on && !routeParam)}
                data-testid={`outline-module-${m.name}`}
              >
                <FileCode2 />
                <span className="truncate font-mono text-[12.5px]">{m.name}.py</span>
                {draft ? (
                  <span className="ml-auto text-[11.5px] text-ai-text">draft</span>
                ) : (
                  <span className="ml-auto text-[11.5px] text-muted-foreground">{rs.length}</span>
                )}
              </Link>
              {expanded && (
                <>
                  {rs.map((r) => (
                    <OutlineRoute key={r.id} route={r} module={m.name} on={on && routeParam === r.name} />
                  ))}
                  {draft?.newRoutes.map((r) => <NewRouteRow key={`new-${r.method}-${r.path}`} method={r.method} path={r.path} />)}
                </>
              )}
            </div>
          )
        })}
        {moduleList.more > 0 && <ShowAll more={moduleList.more} onClick={() => setAllModules(true)} testId="outline-modules-more" />}
        {draftModules.map((d) => (
          <div key={`draft-${d.module}`}>
            <Link
              to="/p/$project/backend/$module"
              params={{ ...P, module: d.module }}
              className={cn(itemCls, hue.backend, activeModule === d.module && onCls)}
              {...act(activeModule === d.module)}
              data-testid={`outline-module-${d.module}`}
            >
              <FileCode2 />
              <span className="truncate font-mono text-[12.5px]">{d.module === '~new' ? 'untitled' : `${d.module}.py`}</span>
              <span className="ml-auto text-[11.5px] text-ai-text">draft</span>
            </Link>
            {d.newRoutes.map((r) => (
              <NewRouteRow key={`new-${r.method}-${r.path}`} method={r.method} path={r.path} />
            ))}
          </div>
        ))}
        {(modules.data?.length ?? 0) === 0 && draftModules.length === 0 && <Hint>No modules yet</Hint>}
        <Link
          to="/p/$project/backend/$module"
          params={{ ...P, module: '~new' }}
          search={{ generate: 1 }}
          className={cn(itemCls, hue.ai, 'text-ai-text [&>svg]:opacity-100')}
          data-testid="outline-generate"
        >
          <Sparkles /> Generate with AI
        </Link>

        <GroupHeader
          label="Workflows"
          onAdd={() => navigate({ to: '/p/$project/workflows/$workflowId', params: { ...P, workflowId: 'new' } })}
          addTestId="outline-add-workflow"
          addLabel="New workflow"
        />
        {workflowList.shown.map((w) => (
          <Link
            key={w.id}
            to="/p/$project/workflows/$workflowId"
            params={{ ...P, workflowId: w.id }}
            className={cn(itemCls, hue.flow, 'group')}
            data-testid={`outline-workflow-${w.id}`}
          >
            <Workflow />
            <span className="truncate">{w.name}</span>
            <ArrowUpRight className="ml-auto !size-3.5 opacity-60 group-hover:opacity-100" />
          </Link>
        ))}
        {(workflows.data?.length ?? 0) === 0 && <Hint>No workflows yet</Hint>}
        <Link to="/p/$project/workflows" params={P} className={cn(itemCls, hue.flow, onWorkflows && onCls)} {...act(onWorkflows)} data-testid="outline-workflows">
          <LayoutList /> {workflowList.more > 0 ? 'View all' : 'All workflows'}
          <span className="ml-auto text-[11.5px] tabular-nums text-muted-foreground">{workflows.data?.length ?? 0}</span>
        </Link>
        <Link
          to="/p/$project/workflows/$workflowId"
          params={{ ...P, workflowId: 'new' }}
          search={{ ai: 1 }}
          className={cn(itemCls, hue.ai, 'text-ai-text [&>svg]:opacity-100')}
          data-testid="ai-build-open"
        >
          <Sparkles /> Build a workflow with AI
        </Link>

        <GroupHeader variant="section" label="Data & connections" />
        <GroupHeader label="Tables" onAdd={() => setSearch({ dialog: 'new-table' })} addTestId="outline-add-table" addLabel="New table" />
        {(tables.data ?? []).map((t) => (
          <Link
            key={t.id}
            to="/p/$project/data/$table"
            params={{ ...P, table: t.name }}
            className={cn(itemCls, hue.data, activeTable === t.name && onCls)}
            {...act(activeTable === t.name)}
            data-testid={`outline-table-${t.name}`}
          >
            <Table2 />
            <span className="truncate">{t.name}</span>
            <span className="ml-auto text-[11.5px] text-muted-foreground">{pluralize(t.row_count ?? 0, "row")}</span>
          </Link>
        ))}
        {[...draftTables].filter((t) => !existingTables.has(t)).map((t) => (
          <div key={`dt-${t}`} className={cn(itemCls, hue.data, 'text-ai-text')} data-testid={`outline-draft-table-${t}`}>
            <Table2 />
            <span className="truncate">{t}</span>
            <span className="ml-auto text-[11.5px]">created on save</span>
          </div>
        ))}
        {(tables.data?.length ?? 0) === 0 && draftTables.size === 0 && <Hint>No tables yet</Hint>}
        <Link to="/p/$project/apis" params={P} className={cn(itemCls, hue.data, onApis && onCls)} {...act(onApis)} data-testid="outline-apis">
          <Plug /> External APIs <span className="ml-auto text-[11.5px] tabular-nums text-muted-foreground">{apiSpecs.data?.length ?? 0}</span>
        </Link>
        <Link to="/p/$project/secrets" params={P} className={cn(itemCls, hue.secrets, onSecrets && onCls)} {...act(onSecrets)} data-testid="outline-secrets">
          <KeyRound /> Secrets
          {missingCount > 0 ? (
            <span className="ml-auto inline-flex items-center gap-1.5 text-[11.5px] text-warning-text" data-testid="outline-secrets-missing">
              <StatusDot tone="warn" /> {missingCount} missing
            </span>
          ) : null}
        </Link>

        <GroupHeader variant="section" label="Operate" />
        <Link to="/p/$project/runs" params={P} className={cn(itemCls, hue.runs, onRuns && onCls)} {...act(onRuns)} data-testid="outline-runs">
          <History /> Runs <span className="ml-auto text-[11.5px] tabular-nums text-muted-foreground">{runs24} · 24h</span>
        </Link>
        <Link to="/p/$project/approvals" params={P} className={cn(itemCls, hue.flow, onApprovals && onCls)} {...act(onApprovals)} data-testid="rail-approvals">
          <CheckCheck /> Approvals
          {openTasks > 0 && (
            <span className="ml-auto rounded-full bg-primary px-1.5 text-[11px] font-medium tabular-nums leading-[18px] text-primary-foreground" data-testid="rail-approvals-count" aria-label={`${openTasks} waiting`}>
              {openTasks}
            </span>
          )}
        </Link>
      </div>
      <div className="mt-auto flex flex-col gap-0.5 px-2.5 py-2.5">
        <button type="button" className={cn(itemCls, hue.home)} onClick={() => setSearch({ sheet: 'settings', section: 'general' })} data-testid="outline-settings">
          <Settings2 /> Settings
        </button>
        <button type="button" className={cn(itemCls, hue.home)} onClick={() => setSearch({ sheet: 'settings', section: 'keys' })} data-testid="outline-api-keys">
          <KeySquare /> API keys
        </button>
      </div>
    </nav>
  )
}

/** Newest first, by `updated_at`. */

/** A route the AI draft will add on save. */
function NewRouteRow({ method, path }: { method: Route['method']; path: string }) {
  return (
    <div
      className="relative z-[1] flex h-[30px] items-center gap-2 rounded-ctl pl-[30px] pr-2 font-mono text-[11.5px] text-muted-foreground"
      data-testid="outline-new-route"
    >
      <MethodBadge method={method} size="sm" />
      <span className="truncate">{path}</span>
      <span className="ml-auto animate-badge-pop font-sans text-[10.5px] text-ai-text">new</span>
    </div>
  )
}

function OutlineRoute({ route, module, on }: { route: Route; module: string; on: boolean }) {
  const { slug } = useProjectCtx()
  return (
    <Link
      to="/p/$project/backend/$module"
      params={{ project: slug, module }}
      search={{ route: route.name }}
      className={cn(
        'relative z-[1] flex h-[30px] items-center gap-2 rounded-ctl pl-[30px] pr-2 font-mono text-[11.5px] text-muted-foreground hover:bg-foreground/6',
        on && 'text-foreground hover:bg-transparent',
      )}
      {...(on ? { 'data-active': '' } : {})}
      data-testid={`outline-route-${route.name}`}
    >
      <MethodBadge method={route.method} size="sm" />
      <span className="truncate">{route.path}</span>
    </Link>
  )
}

const railBtn =
  'relative inline-flex size-9 items-center justify-center rounded-ctl text-muted-foreground hover:bg-foreground/6 hover:text-foreground [&>svg]:size-[17px] [&>svg]:stroke-[1.75]'
const railOn = 'bg-brand-soft text-foreground shadow-raise-sm'

/** A rail icon whose hover opens a flyout listing the group's items (the outline's group, folded). */
function RailFlyout({ label, trigger, icon, children }: { label: string; trigger: React.ReactElement; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <HoverCard>
      <HoverCardTrigger render={trigger}>{icon}</HoverCardTrigger>
      <HoverCardContent side="right" align="start" className="w-56 p-1">
        <div className="px-2 py-1 text-[12px] font-medium text-muted-foreground">{label}</div>
        {children}
      </HoverCardContent>
    </HoverCard>
  )
}

function RailEmpty({ children }: { children: React.ReactNode }) {
  return <div className="px-2 py-1 text-xs text-muted-foreground">{children}</div>
}

function RailSep() {
  return <span className="my-1 h-px w-6 bg-hairline" aria-hidden />
}

/** 1024–1279px: the outline folded to icons, in the same order (§3.1, H5); groups open flyouts. */
export function ProjectRail() {
  const { pid, slug } = useProjectCtx()
  const matchRoute = useMatchRoute()
  const setSearch = useSearchSetter()
  const modules = useModules(pid)
  const tables = useTables(pid)
  const apps = useProjectApps(pid)
  const workflows = useProjectWorkflows(pid)
  const missingCount = useMissingCount(pid)
  const openTasks = useTaskCounts(pid).data?.open ?? 0
  const P = { project: slug }
  const tip = (label: string, el: React.ReactElement) => (
    <Tooltip>
      <TooltipTrigger render={el} />
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  )
  const firstApp = apps.data?.[0]
  const recentWorkflows = capList(byRecent(workflows.data ?? []), MAX_WORKFLOWS)
  return (
    <nav className="flex flex-1 flex-col items-center gap-1 py-2.5" aria-label="Project outline" data-testid="outline-rail">
      {tip('Overview', <Link to="/p/$project" params={P} className={cn(railBtn, matchRoute({ to: '/p/$project', params: P }) && railOn)} data-testid="rail-overview"><LayoutGrid /></Link>)}
      <RailSep />
      <RailFlyout
        label="Interface"
        icon={<AppWindow />}
        trigger={
          firstApp ? (
            <Link to="/p/$project/apps/$appId" params={{ ...P, appId: firstApp.id }} className={railBtn} data-testid="rail-apps" aria-label="Interface" />
          ) : (
            <Link to="/p/$project" params={P} search={{ dialog: 'create-ui' }} className={railBtn} data-testid="rail-apps" aria-label="Interface" />
          )
        }
      >
        {(apps.data ?? []).map((a) => (
          <Link key={a.id} to="/p/$project/apps/$appId" params={{ ...P, appId: a.id }} className={cn(itemCls, hue.apps)}>
            <AppWindow /> <span className="truncate">{a.name}</span>
          </Link>
        ))}
        {(apps.data?.length ?? 0) === 0 && <RailEmpty>No interface yet</RailEmpty>}
        <button type="button" className={cn(itemCls, 'w-full')} onClick={() => setSearch({ dialog: 'create-ui' })} data-testid="rail-add-app">
          <Plus /> New interface
        </button>
      </RailFlyout>
      <RailFlyout
        label="Backend"
        icon={<FileCode2 />}
        trigger={
          <Link
            to="/p/$project/backend/$module"
            params={{ ...P, module: modules.data?.[0]?.name ?? '~new' }}
            className={cn(railBtn, matchRoute({ to: '/p/$project/backend/$module', params: P, fuzzy: true }) && railOn)}
            data-testid="rail-backend"
            aria-label="Backend"
          />
        }
      >
        {(modules.data ?? []).map((m) => (
          <Link key={m.id} to="/p/$project/backend/$module" params={{ ...P, module: m.name }} className={cn(itemCls, hue.backend)}>
            <FileCode2 /> <span className="truncate font-mono text-[12.5px]">{m.name}.py</span>
          </Link>
        ))}
        <Link to="/p/$project/backend/$module" params={{ ...P, module: '~new' }} search={{ generate: 1 }} className={cn(itemCls, 'text-ai-text')}>
          <Sparkles /> Generate with AI
        </Link>
      </RailFlyout>
      <RailFlyout
        label="Workflows"
        icon={<Workflow />}
        trigger={
          <Link
            to="/p/$project/workflows"
            params={P}
            className={cn(railBtn, matchRoute({ to: '/p/$project/workflows', params: P }) && railOn)}
            data-testid="rail-workflows"
            aria-label="Workflows"
          />
        }
      >
        {recentWorkflows.shown.map((w) => (
          <Link key={w.id} to="/p/$project/workflows/$workflowId" params={{ ...P, workflowId: w.id }} className={cn(itemCls, hue.flow)}>
            <Workflow /> <span className="truncate">{w.name}</span>
          </Link>
        ))}
        {(workflows.data?.length ?? 0) === 0 && <RailEmpty>No workflows yet</RailEmpty>}
        <Link to="/p/$project/workflows" params={P} className={cn(itemCls, hue.flow)}>
          <LayoutList /> All workflows
        </Link>
        <Link to="/p/$project/workflows/$workflowId" params={{ ...P, workflowId: 'new' }} className={itemCls} data-testid="rail-add-workflow">
          <Plus /> New workflow
        </Link>
      </RailFlyout>
      <RailSep />
      <RailFlyout
        label="Tables"
        icon={<Database />}
        trigger={
          <Link
            to={tables.data?.[0] ? '/p/$project/data/$table' : '/p/$project/data'}
            params={{ ...P, table: tables.data?.[0]?.name ?? '' } as never}
            className={cn(railBtn, matchRoute({ to: '/p/$project/data', params: P, fuzzy: true }) && railOn)}
            data-testid="rail-data"
            aria-label="Data"
          />
        }
      >
        {(tables.data ?? []).map((t) => (
          <Link key={t.id} to="/p/$project/data/$table" params={{ ...P, table: t.name }} className={cn(itemCls, hue.data)}>
            <Table2 /> <span className="truncate">{t.name}</span>
            <span className="ml-auto text-[11.5px] text-muted-foreground">{t.row_count ?? 0}</span>
          </Link>
        ))}
        {(tables.data?.length ?? 0) === 0 && <RailEmpty>No tables yet</RailEmpty>}
      </RailFlyout>
      {tip('External APIs', <Link to="/p/$project/apis" params={P} className={cn(railBtn, matchRoute({ to: '/p/$project/apis', params: P }) && railOn)} data-testid="rail-apis"><Plug /></Link>)}
      {tip(
        missingCount > 0 ? `Secrets · ${missingCount} missing` : 'Secrets',
        <Link to="/p/$project/secrets" params={P} className={cn(railBtn, matchRoute({ to: '/p/$project/secrets', params: P }) && railOn)} data-testid="rail-secrets">
          <KeyRound />
          {missingCount > 0 && <StatusDot tone="warn" className="absolute right-1.5 top-1.5" />}
        </Link>,
      )}
      <RailSep />
      {tip('Runs', <Link to="/p/$project/runs" params={P} className={cn(railBtn, matchRoute({ to: '/p/$project/runs', params: P, fuzzy: true }) && railOn)} data-testid="rail-runs"><History /></Link>)}
      {tip(
        openTasks > 0 ? `Approvals · ${openTasks} waiting` : 'Approvals',
        <Link to="/p/$project/approvals" params={P} className={cn(railBtn, matchRoute({ to: '/p/$project/approvals', params: P, fuzzy: true }) && railOn)} data-testid="rail-approvals">
          <CheckCheck />
          {openTasks > 0 && (
            <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-primary px-1 text-center text-[10px] font-medium tabular-nums leading-4 text-primary-foreground" data-testid="rail-approvals-count">
              {openTasks}
            </span>
          )}
        </Link>,
      )}
      <div className="flex-1" />
      {tip('Settings', <button type="button" className={railBtn} onClick={() => setSearch({ sheet: 'settings', section: 'general' })} data-testid="rail-settings"><Settings2 /></button>)}
    </nav>
  )
}
