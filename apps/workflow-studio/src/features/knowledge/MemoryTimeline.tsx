/**
 * Memory (PLAN-CAPGRAPH-UI §2.4): what the organisation has learned, newest first, on a rail. A new entry lands with the
 * construction beat: room opens, a comet runs down the rail into its dot, the card constructs, the summary types and the
 * count rolls. Polling and focus refetches bring new entries in while the page is open.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { AnimatePresence, animate, motion } from 'motion/react'
import { AppWindow, Loader2, RefreshCw, Sprout, UserRound, Workflow, type LucideIcon } from 'lucide-react'
import { typedSlice } from '@/features/workflow-editor/components/canvas/stageGeometry'
import { timeAgo } from '@/features/project/lib/format'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Roll } from '@/shared/components/ui/roll'
import { SectionHeader, Tile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { spring } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'
import { kg } from './kit/beats'
import { ConceptChip } from './kit/ConceptChip'
import { Construct, Ripple } from './kit/Construct'
import { knowledgeLink } from './kit/links'
import { labelOfKey, count } from './lib/labels'
import type { LearnEvent } from './types'

const TRIGGER_ICON: Record<string, LucideIcon> = { kept_app: AppWindow, kept_workflow: Workflow, rebuild: RefreshCw, seed: Sprout, human: UserRound }

const dayLabel = (iso: string, now = new Date()): string => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'Earlier'
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  if (same(d, now)) return 'Today'
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (same(d, y)) return 'Yesterday'
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' })
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

/** The summary, typed once at `delay` over a static invisible copy, so the card never changes height. */
function TypedText({ text, active, delay }: { text: string; active: boolean; delay: number }) {
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (!active || motionOff()) { el.textContent = text; return }
    el.textContent = ''
    const c = animate(0, 1, { duration: Math.min(kg.memory.typeCap, text.length * kg.memory.typeChar), delay, ease: 'linear', onUpdate: (p) => { el.textContent = typedSlice(text, p) }, onComplete: () => { el.textContent = text } })
    return () => c.stop()
  }, [text, active, delay])
  return (
    <span className="relative block text-[13px] leading-[19px]">
      <span className="invisible">{text}</span>
      <span ref={ref} className="absolute inset-0" data-testid="kg-memory-summary">{text}</span>
    </span>
  )
}

function Entry({ ev, slug, isNew, marked, fullBeat, order, labelOf, definitionOf, onDone }: {
  ev: LearnEvent; slug: string; isNew: boolean; marked: boolean; fullBeat: boolean; order: number
  labelOf: (key: string) => string; definitionOf: (key: string) => string; onDone: () => void
}) {
  const p = ev.payload
  const validated = p.validated === true
  const failed = p.validated === false
  const n = num(p.validated_count)
  const added = num(p.nodes_added)
  const concepts = Array.isArray(p.concepts) ? (p.concepts as unknown[]).filter((c): c is string => typeof c === 'string') : []
  const patternKey = str(p.pattern_key)
  const Icon = TRIGGER_ICON[ev.trigger] ?? Workflow
  const beat = isNew && fullBeat
  const delay = order * kg.memory.landGap
  // the count shows the previous value first, then rolls to the new one (previous = new - 1 while counting is idempotent per Keep)
  const [shown, setShown] = useState(n !== null && beat && validated && n > 1 && !motionOff() ? n - 1 : n)
  useEffect(() => {
    if (n === null || shown === n) return
    const t = window.setTimeout(() => setShown(n), (delay + 0.56) * 1000)
    return () => window.clearTimeout(t)
  }, [n, shown, delay])
  const doneRef = useRef(onDone)
  useEffect(() => { doneRef.current = onDone })
  useEffect(() => {
    if (!isNew || fullBeat) return
    const t = window.setTimeout(() => doneRef.current(), 900)
    return () => window.clearTimeout(t)
  }, [isNew, fullBeat])
  const dot = failed ? 'bg-warning' : validated ? 'bg-primary' : 'bg-foreground/40'
  return (
    <motion.li
      layout={motionOff() ? false : 'position'} transition={spring.firm}
      initial={isNew && !motionOff() ? { opacity: 0, height: 0 } : false} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0 }}
      className={cn('relative pb-3 pl-7', isNew && !fullBeat && 'animate-row-in')}
      data-testid="kg-memory-item" data-trigger={ev.trigger} data-validated={validated} data-new={marked ? 'true' : 'false'} data-id={ev.id}
    >
      {beat && !failed && !motionOff() && (
        <motion.span
          aria-hidden className="absolute top-0 left-[3px] w-[2px] rounded-full bg-primary"
          initial={{ height: 0, opacity: 0, y: -24 }} animate={{ height: [0, 24, 0], opacity: [0, 1, 0], y: [-24, 0, 6] }}
          transition={{ duration: kg.memory.railDraw, delay, ease: 'easeOut' }}
        />
      )}
      <motion.span
        aria-hidden className={cn('absolute top-[11px] left-0 size-2 rounded-full', dot)}
        initial={beat && !motionOff() ? { scale: 0 } : false} animate={{ scale: 1 }} transition={{ ...spring.snap, delay: delay + 0.2 }}
      />
      {beat && !motionOff() && (
        <svg className="pointer-events-none absolute top-[11px] left-0 overflow-visible" width={8} height={8} aria-hidden>
          <g style={{ transform: 'translate(4px,4px)' }}><Ripple x={0} y={0} tone={failed ? 'warning' : 'primary'} /></g>
        </svg>
      )}
      <Construct mode={beat ? 'enter' : 'idle'} tone={failed ? 'warning' : 'primary'} delay={delay + 0.12} onDone={onDone} radius={8}>
        <div className="flex flex-col gap-1.5 rounded-ctl bg-recess px-3 py-2.5">
          <div className="flex items-start gap-2">
            <Icon className="mt-[3px] size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0 flex-1"><TypedText text={ev.summary} active={beat} delay={delay + 0.34} /></div>
            <time className="shrink-0 text-[12px] text-muted-foreground" dateTime={ev.at}>{timeAgo(ev.at)}</time>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-5.5 text-[12px] text-muted-foreground">
            {n !== null && validated && (
              <span data-testid="kg-memory-count" className="inline-flex items-center gap-1">
                validated <span className="font-medium text-foreground">×<Roll value={shown ?? n} /></span>
              </span>
            )}
            {failed && <span className="text-warning-text">not learned</span>}
            {added !== null && added > 0 && <span>{added} added</span>}
            {ev.actor && <span>{ev.actor}</span>}
          </div>
          {(concepts.length > 0 || patternKey) && (
            <div className="flex flex-wrap items-center gap-1 pl-5.5">
              <AnimatePresence initial={false}>
                {concepts.slice(0, 4).map((k) => <ConceptChip key={k} conceptKey={k} label={labelOf(k) || labelOfKey(k)} hint={definitionOf(k) || undefined} href={{ projectSlug: slug, focus: k }} />)}
              </AnimatePresence>
              {concepts.length > 4 && <span className="text-[12px] text-muted-foreground">+{concepts.length - 4}</span>}
              {patternKey && (
                <Link {...knowledgeLink(slug, { tab: 'graph', focus: patternKey })} className="ml-auto text-[12px] font-medium text-primary hover:underline" data-testid={`kg-memory-open-${ev.id}`}>
                  Open in graph →
                </Link>
              )}
            </div>
          )}
        </div>
      </Construct>
    </motion.li>
  )
}

export function MemoryTimeline({ items, slug, loading, hasMore, loadingMore, onMore, seenLastId, labelOf, definitionOf }: {
  items: LearnEvent[]
  slug: string
  loading: boolean
  hasMore: boolean
  loadingMore: boolean
  onMore: () => void
  /** newest memory id at the last visit; null on a first visit */
  seenLastId: number | null
  labelOf: (key: string) => string
  definitionOf: (key: string) => string
}) {
  // which entries are new: newer than the last visit, or arrived while the page was open
  // (derived state, adjusted while rendering: `known` holds every id seen so far)
  const [known, setKnown] = useState<ReadonlySet<number> | null>(null)
  const [fresh, setFresh] = useState<number[]>([])
  const [marked, setMarked] = useState<ReadonlySet<number>>(new Set())
  if (items.length > 0 && known === null) {
    setKnown(new Set(items.map((i) => i.id)))
    const mine = seenLastId === null ? [] : items.filter((i) => i.id > seenLastId).map((i) => i.id).sort((a, b) => a - b)
    if (mine.length) { setFresh(mine); setMarked(new Set(mine)) }
  } else if (known !== null) {
    const added = items.filter((i) => !known.has(i.id)).map((i) => i.id)
    if (added.length) {
      setKnown(new Set([...known, ...added]))
      setFresh([...fresh, ...added].sort((a, b) => a - b))
      setMarked(new Set([...marked, ...added]))
    }
  }

  const order = useMemo(() => new Map(fresh.map((id, i) => [id, i])), [fresh])
  const newest = fresh.length ? fresh[fresh.length - 1] : null
  const groups = useMemo(() => {
    const out: { day: string; items: LearnEvent[] }[] = []
    for (const ev of items) {
      const day = dayLabel(ev.at)
      const g = out[out.length - 1]
      if (g && g.day === day) g.items.push(ev)
      else out.push({ day, items: [ev] })
    }
    return out
  }, [items])

  return (
    <Tile className="flex flex-col gap-3 p-4" data-testid="kg-memory">
      <SectionHeader title="Memory" sub="What the organisation has learned, newest first" />
      {loading && !items.length && <p className="px-1 py-6 text-[12.5px] text-muted-foreground">Reading the memory…</p>}
      {!loading && !items.length && (
        <EmptyState title="Nothing learned yet" data-testid="kg-memory-empty">Keep a workflow with every test green and it shows up here.</EmptyState>
      )}
      <div className="relative">
        <div className="absolute top-2 bottom-2 left-[3.5px] w-px bg-hairline" aria-hidden />
        {groups.map((g) => (
          <section key={g.day} aria-label={g.day} className="relative">
            <h3 className="mb-1.5 pl-7 text-[12px] font-medium text-muted-foreground">{g.day}</h3>
            <ul>
              <AnimatePresence initial={false}>
                {g.items.map((ev) => {
                  const isNew = order.has(ev.id)
                  // more than three new entries: only the newest gets the full beat
                  const fullBeat = fresh.length <= 3 || ev.id === newest
                  return (
                    <Entry
                      key={ev.id} ev={ev} slug={slug} isNew={isNew} marked={marked.has(ev.id)} fullBeat={fullBeat} order={fresh.length <= 3 ? order.get(ev.id) ?? 0 : 0}
                      labelOf={labelOf} definitionOf={definitionOf}
                      onDone={() => setFresh((f) => f.filter((x) => x !== ev.id))}
                    />
                  )
                })}
              </AnimatePresence>
            </ul>
          </section>
        ))}
      </div>
      {hasMore && (
        <div className="flex justify-center">
          <Button variant="outline" onClick={onMore} disabled={loadingMore} data-testid="kg-memory-more">
            {loadingMore && <Loader2 className="animate-spin" />} Load older
          </Button>
        </div>
      )}
      <p className="sr-only" aria-live="polite">{count(items.length, 'entry', 'entries')}</p>
    </Tile>
  )
}
