/**
 * Knowledge, `/p/$project/knowledge` (PLAN-CAPGRAPH-UI §2.1): what this project's organisation can do, what it has learned, and
 * who acts on it. A hero, then three tabs: the graph, "What we can do", and the memory. The tab and the focused node live in the URL.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { AnimatePresence, motion } from 'motion/react'
import { Loader2, Network, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { Button } from '@/shared/components/ui/button'
import { Segmented } from '@/shared/components/ui/segmented'
import { Switch } from '@/shared/components/ui/switch'
import { PageHeader } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { errorMessage } from '@/shared/api/http'
import { dur, ease } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'
import { CapabilityMatrix, type MatrixFilter } from './CapabilityMatrix'
import { KnowledgeHero, type StatTarget } from './components/KnowledgeHero'
import { useBuilderConfigured, useKgCaps, useKgGraph, useKgMemory, useRebuild } from './hooks'
import { KnowledgeGraph } from './KnowledgeGraph'
import type { KgKind } from './kit/kinds'
import type { KnowledgeSearch, KnowledgeTab } from './kit/links'
import { labelOfKey } from './lib/labels'
import { rebuildToast } from './lib/rebuild'
import { diffSeen, readSeen, writeSeen, type Seen } from './lib/seen'
import { MemoryTimeline } from './MemoryTimeline'

const TABS: KnowledgeTab[] = ['graph', 'matrix', 'memory']

export function KnowledgePage() {
  const { pid, slug } = useProjectCtx()
  const search = useSearch({ strict: false }) as KnowledgeSearch
  const navigate = useNavigate()
  const tab: KnowledgeTab = search.tab && TABS.includes(search.tab) ? search.tab : 'graph'
  const depth: 1 | 2 = search.depth === 2 ? 2 : 1

  const [learnedOnly, setLearnedOnly] = useState(false)
  const [llmTagging, setLlmTagging] = useState(false)
  const [matrixFilter, setMatrixFilter] = useState<MatrixFilter>('all')
  const [kindRequest, setKindRequest] = useState<{ kinds: KgKind[]; seq: number } | undefined>()

  const graphAll = useKgGraph(pid, false)
  const graphView = useKgGraph(pid, learnedOnly)
  const caps = useKgCaps(pid)
  const memory = useKgMemory(pid)
  const config = useBuilderConfigured()
  const rebuild = useRebuild(pid)

  const setSearch = (patch: Partial<KnowledgeSearch>) =>
    void navigate({
      to: '.',
      search: ((s: Record<string, unknown>) => {
        const next: Record<string, unknown> = { ...s, ...patch }
        for (const k of Object.keys(next)) if (next[k] === undefined || next[k] === 'graph' && k === 'tab' || next[k] === 1) delete next[k]
        return next
      }) as never,
      replace: true,
    })

  const events = useMemo(() => memory.data?.pages.flatMap((p) => p.items) ?? [], [memory.data])
  const nodes = graphAll.data?.nodes
  const byKey = useMemo(() => new Map((nodes ?? []).map((n) => [n.key, n])), [nodes])
  const labelOf = (key: string) => byKey.get(key)?.label ?? labelOfKey(key)
  const definitionOf = (key: string) => {
    const d = byKey.get(key)?.props.definition
    return typeof d === 'string' ? d : ''
  }
  const groupOf = (key: string) => {
    const k = byKey.get(key)?.props.kind
    return typeof k === 'string' ? k : 'process'
  }
  const roles = useMemo(() => (nodes ?? []).filter((n) => n.kind === 'role').length, [nodes])

  // the per-project snapshot of the last visit (storage may be blocked: then there is simply no chip)
  const [seen] = useState<Seen | null>(() => readSeen(pid))
  const gapKeys = useMemo(() => (caps.data?.rows ?? []).filter((r) => r.gap).map((r) => r.concept.key), [caps.data])
  const memoryIds = useMemo(() => events.map((e) => e.id), [events])
  const diff = useMemo(
    () => (caps.data && memory.data ? diffSeen(seen, { totals: caps.data.totals, memoryIds, gapKeys }, labelOf) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- labelOf follows byKey
    [seen, caps.data, memory.data, memoryIds, gapKeys, byKey],
  )
  const closedGaps = useMemo(() => new Set(diff?.closedGaps ?? []), [diff])

  const settled = !!caps.data && !!memory.data
  const latestId = events[0]?.id ?? 0
  const written = useRef(false)
  useEffect(() => {
    if (!settled || written.current || !caps.data) return
    const t = window.setTimeout(() => {
      written.current = true
      writeSeen(pid, { totals: caps.data.totals, lastMemoryId: latestId, gapKeys })
    }, 1500)
    return () => window.clearTimeout(t)
  }, [settled, caps.data, latestId, gapKeys, pid])

  const onStat = (t: StatTarget) => {
    if (t.matrix) setMatrixFilter(t.matrix)
    if (t.kinds) setKindRequest((r) => ({ kinds: t.kinds as KgKind[], seq: (r?.seq ?? 0) + 1 }))
    setSearch({ tab: t.tab, focus: undefined })
  }

  const onRebuild = () =>
    rebuild.mutate(llmTagging, {
      onSuccess: (r) => toast.success(rebuildToast(r), { description: r.warnings[0] }),
      onError: (e) => toast.error(errorMessage(e)),
    })

  const gaps = caps.data?.totals.gaps ?? 0
  const aiOff = config.data?.configured === false

  return (
    <div className="h-full overflow-y-auto" data-testid="knowledge-page">
      <div className="animate-page-in flex flex-col gap-5 px-8 py-7">
        <PageHeader
          title="Knowledge"
          icon={<Network />}
          hue="ai"
          meta={<span>What this project can do, what it has learned, and who acts on it</span>}
          actions={
            <>
              <Tip label={aiOff ? 'AI is not set up' : 'Let the AI tag capabilities with concepts when it rebuilds'}>
                <label className="inline-flex items-center gap-2 text-[12.5px] text-muted-foreground">
                  <Switch checked={llmTagging && !aiOff} onCheckedChange={setLlmTagging} disabled={aiOff} data-testid="kg-llm-tagging" aria-label="LLM tagging" /> LLM tagging
                </label>
              </Tip>
              <Button variant="outline" onClick={onRebuild} disabled={rebuild.isPending} data-testid="kg-rebuild">
                {rebuild.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Rebuild knowledge
              </Button>
            </>
          }
        />

        <KnowledgeHero
          totals={caps.data?.totals}
          roles={roles}
          latest={events[0]}
          diff={diff}
          since={seen?.totals ?? null}
          onStat={onStat}
          onLatest={() => setSearch({ tab: 'memory', focus: undefined })}
        />

        <div>
          <Segmented<KnowledgeTab>
            value={tab} onChange={(k) => setSearch({ tab: k })} aria-label="Knowledge views" data-testid="knowledge-tabs"
            options={[
              { key: 'graph', label: 'Graph', testId: 'knowledge-tab-graph' },
              { key: 'matrix', label: gaps > 0 ? `What we can do · ${gaps} ${gaps === 1 ? 'gap' : 'gaps'}` : 'What we can do', testId: 'knowledge-tab-matrix' },
              { key: 'memory', label: 'Memory', testId: 'knowledge-tab-memory' },
            ]}
          />
        </div>

        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={tab}
            initial={motionOff() ? false : { opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
            transition={{ duration: dur.base, ease: ease.out }}
            data-testid={`knowledge-body-${tab}`}
          >
            {tab === 'graph' && (
              <KnowledgeGraph
                pid={pid}
                graph={graphView.data}
                loading={graphView.isPending}
                error={graphView.error}
                learnedOnly={learnedOnly}
                onLearnedOnly={setLearnedOnly}
                focusKey={search.focus}
                depth={depth}
                onFocusKey={(key) => setSearch({ focus: key ?? undefined, depth: key ? search.depth : undefined })}
                onDepth={(d) => setSearch({ depth: d })}
                kindRequest={kindRequest}
              />
            )}
            {tab === 'matrix' && (
              <CapabilityMatrix
                rows={caps.data?.rows} totals={caps.data?.totals} slug={slug} filter={matrixFilter} onFilter={setMatrixFilter}
                closedGaps={closedGaps} groupOf={groupOf} definitionOf={definitionOf} loading={caps.isPending}
              />
            )}
            {tab === 'memory' && (
              <MemoryTimeline
                items={events} slug={slug} loading={memory.isPending} hasMore={!!memory.hasNextPage} loadingMore={memory.isFetchingNextPage}
                onMore={() => void memory.fetchNextPage()} seenLastId={seen ? seen.lastMemoryId : null} labelOf={labelOf} definitionOf={definitionOf}
              />
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
