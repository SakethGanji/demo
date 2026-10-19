/**
 * Knowledge: the capability graph the AI builder reads, as a living constellation. One component serves both scopes:
 * `/p/$project/knowledge` (one project's slice, `KnowledgeView`) and `/knowledge` (the whole team, every project at
 * once, `KnowledgePage` with the team scope).
 *
 * Concepts sit in the middle; the capabilities that implement them to the right, patterns learned from kept builds
 * to the left, roles below, apps above. Five lenses share one header: 3D (a three.js constellation you orbit and fly
 * through, lazily loaded), Graph (the 2D canvas), List (their accessible twin), Coverage (concepts against what can
 * build them) and Memory (what the graph has learned). With no lens in the URL the page opens the graph lens the
 * reader last chose, else 3D on a desktop with WebGL, else 2D; without WebGL the 3D lens is not offered at all.
 *
 * In team scope every project-owned node carries its project's colour (an orbit dot on the canvas, a chip in the
 * lists), a Projects filter hides or shows each project, and "Group by project" gathers each project's nodes round
 * its own anchor with the shared knowledge in the middle.
 *
 * The layout runs on the whole graph the server sent; kind, relation and project filters only hide, so the map never
 * reshuffles under the reader. "Learned only" is a server filter and re-lays out from the last positions.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence } from 'motion/react'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { ArrowUpRight, BrainCircuit, CircleAlert, Grid3x3, List, Network, Orbit, RefreshCw, Waypoints, X } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { pluralize } from '@/features/project/lib/format'
import { errorMessage } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Segmented } from '@/shared/components/ui/segmented'
import { Callout, Dot, PageHeader, Tile, Well, WellList, WellRow } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { useAskGraph } from '../hooks/useAskGraph'
import { useGraphKeys } from '../hooks/useGraphKeys'
import { useGraphLayout } from '../hooks/useGraphLayout'
import { useKgGraph, useRebuildKnowledge } from '../hooks/useKnowledge'
import { useMotionOff } from '../hooks/useMotionOff'
import { usePanZoom } from '../hooks/usePanZoom'
import {
  askHits, buildModel, diffGraph, emphasis, filterGraph, kindCounts, labelIds, relCounts, rippleDelays, searchNodes,
  type GraphModel,
} from '../lib/graph'
import type { KindGroup } from '../lib/kinds'
import { boundsOf } from '../lib/layout'
import { projectChips, projectColor } from '../lib/projects'
import type { KnowledgeSearch, KnowledgeViewKey } from '../lib/search'
import type { KgGraph, KgProjectRef, KgRebuildReport, KnowledgeScope } from '../lib/types'
import { defaultGraphMode, hasWebGL, markWebGLBroken, readGroupByProject, writeGraphMode, writeGroupByProject } from '../lib/view3d'
import { centerOn, fitTransform, type Transform } from '../lib/viewport'
import { CoverageView } from './CoverageView'
import { Graph3DStage } from './Graph3DStage'
import { GraphCanvas, type RippleWave } from './GraphCanvas'
import { GraphFrame } from './GraphFrame'
import { GroupToggle, KindChips, LearnedToggle, ProjectChips, RebuildButton, RelationMenu, SearchBox, ZoomControls } from './GraphToolbar'
import { KnowledgeScopeProvider, type KnowledgeScopeValue } from '../hooks/useKnowledgeScope'
import { ProjectChip } from './KnowledgeScope'
import { LoadingConstellation } from './LoadingConstellation'
import { MemoryView } from './MemoryView'
import { NodeDetailPanel, PANEL_W } from './NodeDetailPanel'
import { NodeListView } from './NodeListView'

const RIPPLE_MS = 2400
const EMPTY_SET: ReadonlySet<never> = new Set()

/** `/p/$project/knowledge`: the project's slice. */
export function KnowledgeView() {
  const { pid, slug } = useProjectCtx()
  const scope = useMemo<KnowledgeScope>(() => ({ kind: 'project', projectId: pid }), [pid])
  return <KnowledgePage scope={scope} slug={slug} />
}

/** The Knowledge page in either scope. `slug` is the project's (project scope only), for its URL. */
export function KnowledgePage({ scope, slug }: { scope: KnowledgeScope; slug?: string }) {
  const team = scope.kind === 'team'
  const search = useSearch({ strict: false }) as KnowledgeSearch
  // the graph lens to open when the URL names none (read once: the reader's last choice, else 3D on a desktop)
  const [defaultMode, setDefaultMode] = useState(defaultGraphMode)
  // bumps when the 3D renderer fails at runtime (lost context), so the page re-reads hasWebGL() and drops to 2D
  const [, setGlFailures] = useState(0)
  const webgl = hasWebGL()
  const wanted: KnowledgeViewKey = search.view ?? defaultMode
  const view: KnowledgeViewKey = wanted === '3d' && !webgl ? 'graph' : wanted
  const selected = search.node ?? null
  const navigate = useNavigate()
  const setSearch = useCallback(
    (patch: Partial<KnowledgeSearch>) =>
      void navigate({
        ...(team ? { to: '/knowledge' } : { to: '/p/$project/knowledge', params: { project: slug } }),
        search: (prev: Record<string, unknown>) => ({ ...prev, ...patch }),
        replace: true,
      } as never),
    [navigate, slug, team],
  )

  const off = useMotionOff()
  const [learnedOnly, setLearnedOnly] = useState(false)
  const [hiddenKinds, setHiddenKinds] = useState<ReadonlySet<KindGroup>>(EMPTY_SET)
  const [hiddenRels, setHiddenRels] = useState<ReadonlySet<string>>(EMPTY_SET)
  const [hiddenProjects, setHiddenProjects] = useState<ReadonlySet<string>>(EMPTY_SET)
  const [groupPref] = useState(readGroupByProject)
  const byProject = search.group ?? groupPref
  const setByProject = (on: boolean) => {
    writeGroupByProject(on)
    setSearch({ group: on })
  }
  const [query, setQuery] = useState('')
  const [askText, setAskText] = useState('')
  const [hovered, setHovered] = useState<string | null>(null)

  const graph = useKgGraph(scope, learnedOnly)
  const model = useMemo(() => (graph.data ? buildModel(graph.data) : null), [graph.data])
  const visible = useMemo(
    () => (model ? filterGraph(model, { hiddenKinds, hiddenRels, hiddenProjects: team ? hiddenProjects : undefined }) : null),
    [model, hiddenKinds, hiddenRels, hiddenProjects, team],
  )
  const counts = useMemo(() => kindCounts(graph.data?.nodes ?? []), [graph.data])
  const rels = useMemo(() => relCounts(model?.edges ?? []), [model])
  const matches = useMemo(() => (model && visible && query.trim() ? searchNodes(model, query, visible.nodes) : []), [model, visible, query])
  const ask = useAskGraph(scope, askText)
  const hits = useMemo(() => (model ? askHits(model, ask.data) : null), [model, ask.data])

  // ── who owns what (team scope) ──
  const projectRefs = graph.data?.projects
  const projects = useMemo(() => new Map((projectRefs ?? []).map((p) => [p.id, p])), [projectRefs])
  const chips = useMemo(() => (team ? projectChips(projectRefs, graph.data?.nodes ?? []) : []), [team, projectRefs, graph.data])
  const orbit = useMemo(() => {
    if (!team || !model) return undefined
    const m = new Map<string, string>()
    for (const n of model.nodes) if (n.project_id) m.set(n.id, projectColor(n.project_id))
    return m
  }, [team, model])
  const ctx = useMemo<KnowledgeScopeValue>(
    () => ({ scope, team, projects, projectOf: (id) => model?.byId.get(id)?.project_id ?? null }),
    [scope, team, projects, model],
  )

  const toggle = <T,>(set: ReadonlySet<T>, v: T): ReadonlySet<T> => {
    const next = new Set(set)
    if (next.has(v)) next.delete(v)
    else next.add(v)
    return next
  }

  const select = useCallback((id: string | null) => setSearch({ node: id ?? undefined }), [setSearch])

  // ── rebuild ──
  const rebuild = useRebuildKnowledge(scope)
  const [wave, setWave] = useState<{ gen: number; ids: string[]; enter: boolean } | null>(null)
  const [summary, setSummary] = useState<KgRebuildReport | null>(null)
  const runRebuild = () => {
    const before = graph.data?.nodes
    rebuild.mutate(undefined, {
      onSuccess: async (rep) => {
        toastReport(rep, team)
        if (team) setSummary(rep)
        const next = await graph.refetch()
        const nodes = next.data?.nodes ?? []
        const d = diffGraph(before, nodes)
        const changed = [...d.added, ...d.changed]
        setWave((w) => ({ gen: (w?.gen ?? 0) + 1, ids: changed.length ? changed : nodes.map((n) => n.id), enter: changed.length > 0 }))
      },
      onError: (e) => toast.error("Couldn't rebuild the graph", { description: errorMessage(e) }),
    })
  }
  useEffect(() => {
    if (!wave) return
    const t = window.setTimeout(() => setWave(null), RIPPLE_MS)
    return () => window.clearTimeout(t)
  }, [wave])

  const viewSwitch = (
    <Segmented<KnowledgeViewKey>
      value={view}
      onChange={(v) => {
        if (v === '3d' || v === 'graph') {
          writeGraphMode(v)
          setDefaultMode(v)
        }
        setSearch({ view: v })
      }}
      aria-label="Knowledge view"
      options={[
        ...(webgl ? [{ key: '3d' as const, label: '3D', icon: <Orbit />, testId: 'kg-view-3d' }] : []),
        { key: 'graph', label: 'Graph', icon: <Network />, testId: 'knowledge-view-graph' },
        { key: 'list', label: 'List', icon: <List />, testId: 'knowledge-view-list' },
        { key: 'coverage', label: 'Coverage', icon: <Grid3x3 />, testId: 'knowledge-view-coverage' },
        { key: 'memory', label: 'Memory', icon: <BrainCircuit />, testId: 'knowledge-view-memory' },
      ]}
    />
  )
  const header = team ? (
    <PageHeader
      title="Knowledge"
      icon={<Waypoints />}
      hue="ai"
      meta={
        <>
          <span>Everything the AI builder knows, across all projects</span>
          <Dot />
          <span data-testid="knowledge-count">
            {pluralize(graph.data?.nodes.length ?? 0, 'thing')}
            {chips.length ? ` in ${pluralize(chips.filter((c) => c.key !== 'shared').length, 'project')}` : ''}
          </span>
        </>
      }
      actions={viewSwitch}
    />
  ) : (
    <PageHeader
      title="Knowledge"
      icon={<Waypoints />}
      hue="ai"
      meta={
        <>
          <span data-testid="knowledge-count">{pluralize(graph.data?.nodes.length ?? 0, 'thing')} the AI builder knows</span>
          <Dot />
          <span>Concepts, the nodes and templates that implement them, and the patterns it learned from kept builds.</span>
          <Dot />
          <Link
            to="/knowledge"
            search={{ view: search.view, node: search.node }}
            className="inline-flex items-center gap-0.5 rounded-chip font-medium text-primary outline-none underline-offset-3 hover:underline focus-visible:shadow-focus [&_svg]:size-3.5"
            data-testid="knowledge-scope-switch"
          >
            View across all projects <ArrowUpRight />
          </Link>
        </>
      }
      actions={viewSwitch}
    />
  )

  const detail = (
    <AnimatePresence>
      {selected && (
        <NodeDetailPanel
          key="detail"
          id={selected}
          fallback={model?.byId.get(selected)}
          onClose={() => select(null)}
          onPick={(id) => pickRef.current(id)}
          off={off}
        />
      )}
    </AnimatePresence>
  )
  // set by whichever lens is open: the graph pans to the node, the list just selects it
  const pickRef = useRef<(id: string) => void>(() => {})

  const projectChipsBar = team && chips.length > 0 && (
    <ProjectChips
      chips={chips}
      hidden={hiddenProjects}
      onToggle={(k) => setHiddenProjects((s) => toggle(s, k))}
      onShowAll={() => setHiddenProjects(EMPTY_SET)}
    />
  )
  // the graph lenses' chrome (2D and 3D share it)
  const toolbarLeft = <KindChips counts={counts} hidden={hiddenKinds} onToggle={(k) => setHiddenKinds((s) => toggle(s, k))} />
  const toolbarRight = (
    <>
      <RelationMenu rels={rels} hidden={hiddenRels} onToggle={(r) => setHiddenRels((s) => toggle(s, r))} onShowAll={() => setHiddenRels(EMPTY_SET)} />
      <LearnedToggle checked={learnedOnly} onChange={setLearnedOnly} />
      <RebuildButton busy={rebuild.isPending} onClick={runRebuild} />
    </>
  )
  const toolbarBelow = projectChipsBar && (
    <>
      {projectChipsBar}
      <span className="mx-0.5 h-5 w-px bg-hairline" aria-hidden />
      <GroupToggle checked={byProject} onChange={setByProject} />
    </>
  )

  return (
    <KnowledgeScopeProvider value={ctx}>
      <div className="flex h-full min-h-0 flex-col" data-testid="knowledge-page" data-view={view} data-scope={scope.kind}>
        <div className="px-8 pt-7 pb-4">{header}</div>
        {team && summary && <RebuildSummary report={summary} projects={projects} onClose={() => setSummary(null)} />}
        {view === 'coverage' || view === 'memory' ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-8 pb-8">
            {view === 'coverage' ? (
              <CoverageView model={model} onPick={(id) => setSearch({ view: undefined, node: id })} />
            ) : (
              <MemoryView model={model} onPick={(id) => setSearch({ view: undefined, node: id })} />
            )}
          </div>
        ) : graph.isPending ? (
          <Stage>
            <LoadingConstellation />
          </Stage>
        ) : graph.isError ? (
          <Stage>
            <div className="absolute inset-0 flex items-center justify-center p-6">
              <Callout tone="error" icon={<CircleAlert />} className="max-w-md" data-testid="knowledge-error">
                <div className="flex items-center gap-3">
                  <span className="flex-1">Couldn't load the graph. {errorMessage(graph.error)}</span>
                  <Button size="xs" variant="outline" onClick={() => void graph.refetch()} data-testid="knowledge-retry">
                    Retry
                  </Button>
                </div>
              </Callout>
            </div>
          </Stage>
        ) : !model || !visible || graph.data.nodes.length === 0 ? (
          <Stage>
            <EmptyState
              className="absolute inset-0"
              icon={<Waypoints />}
              title={learnedOnly ? 'Nothing learned yet' : 'The graph is empty'}
              data-testid="knowledge-empty"
              actions={
                <div className="flex items-center gap-2">
                  {learnedOnly && (
                    <Button size="sm" variant="outline" onClick={() => setLearnedOnly(false)}>
                      Show everything
                    </Button>
                  )}
                  <Button size="sm" onClick={runRebuild} disabled={rebuild.isPending} data-testid="knowledge-rebuild">
                    <RefreshCw /> {rebuild.isPending ? 'Rebuilding' : team ? 'Rebuild every project' : 'Rebuild the graph'}
                  </Button>
                </div>
              }
            >
              {learnedOnly
                ? 'Keeping a workflow or app the AI built teaches the graph new patterns. They will show here.'
                : team
                  ? "Rebuild reads every project's node catalogue, templates and kept builds into concepts the AI builder can reason with."
                  : 'Rebuild reads the node catalogue, templates and kept builds into concepts the AI builder can reason with.'}
            </EmptyState>
          </Stage>
        ) : view === 'list' ? (
          <div className="relative mx-8 mb-6 min-h-0 flex-1">
            <div className="absolute inset-0 overflow-y-auto" style={{ paddingRight: selected ? PANEL_W + 24 : 0 }}>
              <div className="glass-chrome sticky top-0 z-10 mb-4 flex flex-wrap items-center gap-2 rounded-panel p-1.5">
                <SearchBox value={query} onChange={setQuery} onSubmit={() => matches[0] && select(matches[0])} matches={query.trim() ? matches.length : null} />
                <KindChips counts={counts} hidden={hiddenKinds} onToggle={(k) => setHiddenKinds((s) => toggle(s, k))} />
                <div className="flex-1" />
                <LearnedToggle checked={learnedOnly} onChange={setLearnedOnly} />
                <RebuildButton busy={rebuild.isPending} onClick={runRebuild} />
                {projectChipsBar && <div className="basis-full">{projectChipsBar}</div>}
              </div>
              <ListPick pickRef={pickRef} select={select} />
              <NodeListView model={model} visibleIds={visible.nodes} query={query} selected={selected} onPick={select} />
            </div>
            {detail}
          </div>
        ) : view === '3d' ? (
          <Graph3DStage
            model={model}
            data={graph.data}
            visible={visible}
            off={off}
            selected={selected}
            hovered={hovered}
            setHovered={setHovered}
            select={select}
            pickRef={pickRef}
            query={query}
            setQuery={setQuery}
            matches={matches}
            ask={ask}
            askText={askText}
            setAskText={setAskText}
            hits={hits}
            learnedOnly={learnedOnly}
            byProject={team && byProject}
            orbit={orbit}
            toolbarLeft={toolbarLeft}
            toolbarRight={toolbarRight}
            toolbarBelow={toolbarBelow}
            detail={detail}
            onFail={() => {
              markWebGLBroken()
              setGlFailures((n) => n + 1)
            }}
          />
        ) : (
          <GraphStage
            model={model}
            data={graph.data}
            visible={visible}
            off={off}
            selected={selected}
            hovered={hovered}
            setHovered={setHovered}
            select={select}
            pickRef={pickRef}
            query={query}
            setQuery={setQuery}
            matches={matches}
            ask={ask}
            askText={askText}
            setAskText={setAskText}
            hits={hits}
            wave={wave}
            learnedOnly={learnedOnly}
            byProject={team && byProject}
            orbit={orbit}
            placeholder={graph.isPlaceholderData}
            toolbarLeft={toolbarLeft}
            toolbarRight={toolbarRight}
            toolbarBelow={toolbarBelow}
            detail={detail}
          />
        )}
      </div>
    </KnowledgeScopeProvider>
  )
}

function toastReport(r: KgRebuildReport, team = false) {
  const parts = [
    r.added && `${r.added} added`,
    r.updated && `${r.updated} updated`,
    r.staled && `${r.staled} gone stale`,
    r.proposed && `${r.proposed} proposed`,
    r.tagged && `${r.tagged} tagged`,
  ].filter(Boolean) as string[]
  const what = team && r.projects ? `Rebuilt ${pluralize(r.projects.length, 'project')}` : 'Graph rebuilt'
  const description = r.warnings.length ? r.warnings.slice(0, 3).join(' · ') + (r.warnings.length > 3 ? ` · and ${r.warnings.length - 3} more` : '') : undefined
  if (r.warnings.length) toast.warning(parts.length ? `${what}: ${parts.join(', ')}` : `${what} with warnings`, { description })
  else toast.success(parts.length ? `${what}: ${parts.join(', ')}` : `${what}: already up to date`)
}

/** Team scope, after a rebuild: what changed in each project (the busiest first), until dismissed. */
function RebuildSummary({ report, projects, onClose }: { report: KgRebuildReport; projects: ReadonlyMap<string, KgProjectRef>; onClose: () => void }) {
  const rows = [...(report.projects ?? [])].sort(
    (a, b) =>
      b.added + b.updated + b.staled - (a.added + a.updated + a.staled) ||
      (projects.get(a.project_id)?.name ?? a.project_id).localeCompare(projects.get(b.project_id)?.name ?? b.project_id),
  )
  const fig = (n: number, label: string, cls?: string) => (
    <span className={cn('tabular-nums', n ? cls ?? 'text-foreground' : 'text-faint')}>
      {n} {label}
    </span>
  )
  return (
    <Tile className="mx-8 mb-4 flex flex-col gap-2.5 p-3" data-testid="knowledge-rebuild-summary">
      <div className="flex items-center gap-2 px-1">
        <RefreshCw className="size-3.5 text-ai-text" aria-hidden />
        <span className="text-[13px] font-semibold">Rebuilt {pluralize(rows.length, 'project')}</span>
        <span className="text-[12px] text-muted-foreground">
          {report.added} added · {report.updated} updated · {report.staled} gone stale
          {report.warnings.length ? ` · ${pluralize(report.warnings.length, 'warning')}` : ''}
        </span>
        <div className="flex-1" />
        <Button size="icon-xs" variant="ghost" onClick={onClose} aria-label="Dismiss the rebuild summary" data-testid="knowledge-rebuild-summary-close">
          <X />
        </Button>
      </div>
      {rows.length > 0 && (
        <WellList className="max-h-48 overflow-y-auto">
          {rows.map((p) => (
            <WellRow key={p.project_id} className="flex items-center gap-3 px-3 py-1.5 text-[12px]" data-testid={`knowledge-rebuild-project-${p.project_id}`}>
              <ProjectChip projectId={p.project_id} />
              <div className="flex-1" />
              {p.added + p.updated + p.staled === 0 ? (
                <span className="text-muted-foreground">Already up to date</span>
              ) : (
                <>
                  {fig(p.added, 'added', 'text-success-text')}
                  {fig(p.updated, 'updated')}
                  {fig(p.staled, 'gone stale', 'text-warning-text')}
                </>
              )}
            </WellRow>
          ))}
        </WellList>
      )}
    </Tile>
  )
}

/** The full-bleed canvas frame: a glass tile holding one dark well. */
function Stage({ children, className, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Tile className="mx-8 mb-6 min-h-[420px] flex-1 p-1.5">
      <Well className={cn('relative h-full overflow-hidden rounded-panel', className)} {...rest}>
        {children}
      </Well>
    </Tile>
  )
}

/** In the list, picking a neighbour just selects it. */
function ListPick({ pickRef, select }: { pickRef: React.MutableRefObject<(id: string) => void>; select: (id: string) => void }) {
  useEffect(() => {
    pickRef.current = select
  }, [pickRef, select])
  return null
}

function GraphStage({
  model, data, visible, off, selected, hovered, setHovered, select, pickRef, query, setQuery, matches, ask, askText, setAskText,
  hits, wave, learnedOnly, byProject, orbit, placeholder, toolbarLeft, toolbarRight, toolbarBelow, detail,
}: {
  model: GraphModel
  data: KgGraph
  visible: ReturnType<typeof filterGraph>
  off: boolean
  selected: string | null
  hovered: string | null
  setHovered: (id: string | null) => void
  select: (id: string | null) => void
  pickRef: React.MutableRefObject<(id: string) => void>
  query: string
  setQuery: (q: string) => void
  matches: string[]
  ask: ReturnType<typeof useAskGraph>
  askText: string
  setAskText: (v: string) => void
  hits: ReturnType<typeof askHits> | null
  wave: { gen: number; ids: string[]; enter: boolean } | null
  learnedOnly: boolean
  /** team scope: cluster each project's nodes */
  byProject: boolean
  /** team scope: node id -> project colour */
  orbit?: ReadonlyMap<string, string>
  /** the old graph is still showing while the learned-only one loads */
  placeholder: boolean
  toolbarLeft: React.ReactNode
  toolbarRight: React.ReactNode
  /** a second row of chrome under the left toolbar (team scope: the Projects filter) */
  toolbarBelow?: React.ReactNode
  detail: React.ReactNode
}) {
  const layout = useGraphLayout(model, off, byProject)
  const pz = usePanZoom(off)
  const { flyTo, size, t } = pz
  const inset = selected ? PANEL_W + 24 : 0
  /** the part of the canvas the panel does not cover */
  const free = useMemo(() => ({ width: Math.max(120, size.width - inset), height: size.height }), [size.width, size.height, inset])

  const fitIds = useCallback(
    (ids: Iterable<string>, maxK = 1.6): Transform | null => {
      const b = layout.final && boundsOf(layout.final, ids, (id) => (model.radius.get(id) ?? 5) + 14)
      return b ? fitTransform(b, free, 56, maxK) : null
    },
    [layout.final, model, free],
  )
  const fitAll = useCallback(() => {
    const to = fitIds(visible.nodes)
    if (to) flyTo(to)
  }, [fitIds, visible.nodes, flyTo])

  const panTo = useCallback(
    (id: string) => {
      const p = layout.final?.get(id)
      if (p) flyTo(centerOn(t, p, free, Math.max(t.k, 1.1)))
    },
    [layout.final, flyTo, t, free],
  )
  const pick = useCallback(
    (id: string) => {
      select(id)
      panTo(id)
    },
    [select, panTo],
  )
  useEffect(() => {
    pickRef.current = pick
  }, [pickRef, pick])

  // fit once per graph shape (first load, learned-only or grouping flip), when the final layout and the size are known
  const fitted = useRef<string | null>(null)
  useEffect(() => {
    const key = `${learnedOnly}|${byProject}`
    if (!layout.final || layout.of !== model || layout.grouped !== byProject || !size.width || placeholder || fitted.current === key) return
    fitted.current = key
    const to = fitIds(visible.nodes)
    if (!to) return
    if (selected && layout.final.has(selected)) {
      const p = layout.final.get(selected)!
      flyTo(centerOn(to, p, free, Math.max(to.k, 1.1)), 0)
    } else flyTo(to, 0)
  }, [layout.final, layout.of, layout.grouped, model, size.width, learnedOnly, byProject, placeholder, fitIds, visible.nodes, flyTo, selected, free])

  // an Ask answer frames what it lit
  const askKey = hits?.concepts.join('|') ?? ''
  useEffect(() => {
    if (!askKey || !hits) return
    const to = fitIds(hits.nodes, 1.4)
    if (to) flyTo(to, 700)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reframe when the matched concepts change, not on every pan
  }, [askKey])

  useGraphKeys(selected, select, fitAll)

  const em = useMemo(
    () => emphasis(model, visible, { focus: [hovered ?? selected], ask: hits, search: matches }),
    [model, visible, hovered, selected, hits, matches],
  )
  const labels = useMemo(() => labelIds(model, visible, em, { k: t.k, selected, hovered }), [model, visible, em, t.k, selected, hovered])
  const ripple = useMemo<RippleWave | null>(
    () => (wave && layout.final ? { gen: wave.gen, enter: wave.enter, delays: rippleDelays(wave.ids, layout.final, wave.enter ? 700 : 1100) } : null),
    [wave, layout.final],
  )
  const askConcepts = useMemo(() => (hits ? hits.concepts.map((id) => model.byId.get(id)!).filter(Boolean) : []), [hits, model])

  const onCanvasPick = (id: string) => {
    if (pz.consumeDrag()) return
    select(id === selected ? null : id)
    const p = layout.final?.get(id)
    // only move the camera when the panel would cover the node
    if (p && id !== selected && t.x + p.x * t.k > size.width - PANEL_W - 40) panTo(id)
  }

  const submitSearch = () => {
    if (!matches.length) return
    if (matches.length === 1) pick(matches[0])
    else {
      const to = fitIds(matches, 1.8)
      if (to) flyTo(to)
    }
  }

  return (
    <GraphFrame
      canvas={
        <div
          ref={pz.ref}
          className={cn('absolute inset-0 touch-none', pz.panning ? 'cursor-grabbing' : 'cursor-grab')}
          {...pz.handlers}
          onClick={() => {
            if (pz.consumeDrag()) return
            if (selected) select(null)
          }}
        >
          {layout.pos && (
            <GraphCanvas
              model={model}
              visible={visible}
              pos={layout.pos}
              t={t}
              em={em}
              labels={labels}
              selected={selected}
              hovered={hovered}
              ripple={ripple}
              off={off}
              onHover={setHovered}
              onPick={onCanvasPick}
              orbit={orbit}
            />
          )}
        </div>
      }
      inset={inset}
      query={query}
      setQuery={setQuery}
      matches={matches}
      onSubmitSearch={submitSearch}
      toolbarLeft={toolbarLeft}
      toolbarRight={toolbarRight}
      toolbarBelow={toolbarBelow}
      truncated={data.truncated}
      stats={{ shown: visible.nodes.size, total: model.nodes.length, links: visible.edges.size }}
      em={em}
      hits={hits}
      ask={ask}
      askText={askText}
      setAskText={setAskText}
      askConcepts={askConcepts}
      onAskPick={pick}
      off={off}
      controls={<ZoomControls onIn={() => pz.zoomBy(1.35)} onOut={() => pz.zoomBy(1 / 1.35)} onFit={fitAll} />}
      detail={detail}
    />
  )
}
