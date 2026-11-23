/**
 * The workbench index (what is left of the old project outline): Backend and Data open it from their header
 * switcher. It lists what you switch between there, modules with their routes and the tables, including what an
 * AI draft will add on save (draft modules, new routes, tables "created on save"). Everything else the outline
 * used to list lives in the project rail. One soft fill slides to the active item.
 */
import { useLayoutEffect, useRef, useState } from 'react'
import { Link, useMatchRoute, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { FileCode2, Plus, Sparkles, Table2 } from 'lucide-react'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { cn } from '@/shared/lib/utils'
import { liquidGlassSupported, useLiquidGlass } from '@/shared/lib/liquid-glass'
import { useProjectCtx } from '../ProjectContext'
import { useModules, useRoutes, useTables } from '../hooks/useProjectData'
import { sortRoutes } from '../lib/routes'
import { pluralize } from '../lib/format'
import { useOutlineDraftStore } from '@/features/backend/outlineDraftStore'
import type { Route } from '@/shared/api/types'

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

const MAX_MODULES = 8

function useDraftState(pid: string) {
  const drafts = useOutlineDraftStore((s) => s.drafts)
  return Object.values(drafts).filter((d) => d.projectId === pid)
}

export function WorkbenchIndex() {
  const { pid, slug } = useProjectCtx()
  const params = useParams({ strict: false }) as { module?: string; table?: string }
  const matchRoute = useMatchRoute()
  const setSearch = useSearchSetter()
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const tables = useTables(pid)
  const drafts = useDraftState(pid)
  const [allModules, setAllModules] = useState(false)
  const P = { project: slug }

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
  const moduleList = capList(modules.data ?? [], allModules ? Infinity : MAX_MODULES, (m) => m.name === activeModule)
  const listRef = useRef<HTMLDivElement>(null)
  const act = (on: boolean) => (on ? { 'data-active': '' } : {})

  return (
    <nav className="flex min-h-0 flex-1 flex-col" aria-label="Backend and tables" data-testid="outline">
      <div ref={listRef} className="relative min-h-0 flex-1 overflow-y-auto px-2.5 pt-2 pb-3">
        <ActiveFill host={listRef} dep={[activeModule, activeTable, routeParam]} />
        <GroupHeader label="Backend" onAdd={() => setSearch({ dialog: 'new-module' })} addTestId="outline-add-module" addLabel="New module" />
        {moduleList.shown.map((m) => {
          const on = activeModule === m.name
          const draft = drafts.find((d) => d.module === m.name && d.dirty)
          const rs = sortRoutes(routesByModule.get(m.id) ?? [])
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
              {on && (
                <>
                  {rs.map((r) => (
                    <OutlineRoute key={r.id} route={r} module={m.name} on={routeParam === r.name} />
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
            <span className="ml-auto text-[11.5px] text-muted-foreground">{pluralize(t.row_count ?? 0, 'row')}</span>
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

