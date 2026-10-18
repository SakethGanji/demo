/**
 * The node sheet (PLAN-CAPGRAPH-UI §2.2): a docked glass panel inside the graph tile, so the graph stays clickable while exploring.
 * Sections come from `NodeDetail.neighbours` with their direction; every item re-focuses the graph. No scores, no confidence.
 */
import { useState } from 'react'
import { motion } from 'motion/react'
import { Check, Sparkles, X } from 'lucide-react'
import { toast } from 'sonner'
import { timeAgo } from '@/features/project/lib/format'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { IconTile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { dur, ease, stagger } from '@/shared/motion/tokens'
import { errorMessage } from '@/shared/api/http'
import { Construct, type ConstructMode } from './kit/Construct'
import { KindIcon } from './kit/KindIcon'
import { asKind, kindHue, KIND_LABEL } from './kit/kinds'
import { count, sectionTitle, words } from './lib/labels'
import { sectionsOf } from './lib/sections'
import { AppNodeDetail } from './components/AppNodeDetail'
import { useBuildWith, useDecide, useKgNode } from './hooks'
import type { NodeRef } from './types'

const SOURCE_PHRASE: Record<string, string> = {
  seed: 'from the ontology',
  extracted: 'found in this project',
  learned: 'learned from kept work',
  human: 'added by a person',
}

const testsOf = (p: Record<string, unknown>): { passed: number; total: number } | null => {
  const t = p.tests as { approved?: unknown; passed?: unknown } | number | undefined
  if (t && typeof t === 'object' && typeof t.approved === 'number') return { passed: typeof t.passed === 'number' ? t.passed : t.approved, total: t.approved }
  return null
}

function NodeLink({ n, onFocus, extra }: { n: NodeRef; onFocus: (key: string) => void; extra?: React.ReactNode }) {
  const kind = asKind(n.kind)
  return (
    <button
      type="button" onClick={() => onFocus(n.key)} data-testid={`kg-sheet-link-${n.key.replaceAll(':', '-')}`}
      className="inline-flex h-6 max-w-full items-center gap-1.5 rounded-chip bg-recess px-2 text-[12px] hover:bg-(--hover-fill) focus-visible:shadow-focus"
    >
      <IconTile hue={kindHue(kind, n.subkind)} className="size-4 bg-transparent [&_svg]:size-3"><KindIcon kind={kind} subkind={n.subkind} /></IconTile>
      <span className="truncate">{n.label}</span>
      {extra}
    </button>
  )
}

const container = { hidden: {}, show: { transition: { staggerChildren: stagger.list, delayChildren: 0.05 } } }
const item = { hidden: { opacity: 0, y: 6 }, show: { opacity: 1, y: 0, transition: { duration: dur.base, ease: ease.out } } }

export function NodeSheet({ pid, nodeId, onFocusKey, onClose }: { pid: string; nodeId: string; onFocusKey: (key: string) => void; onClose: () => void }) {
  const q = useKgNode(pid, nodeId)
  const decide = useDecide(pid)
  const buildWith = useBuildWith()
  const [headMode, setHeadMode] = useState<ConstructMode>('idle')

  const body = (() => {
    if (q.isPending) return <div className="flex flex-col gap-3 p-4"><Skeleton className="h-6 w-40" /><Skeleton className="h-16 w-full" /><Skeleton className="h-24 w-full" /></div>
    if (q.isError || !q.data) return <div className="p-4 text-[12.5px] text-destructive" role="alert">{errorMessage(q.error)}</div>
    const { node, neighbours, evidence, learn_events } = q.data
    const kind = asKind(node.kind)
    const sub = kind === 'concept' && typeof node.props.kind === 'string' ? node.props.kind : node.subkind
    const definition = typeof node.props.definition === 'string' ? node.props.definition : ''
    const slots = Array.isArray(node.props.slots) ? (node.props.slots as Record<string, unknown>[]) : []
    const skeleton = node.props.skeleton as { parts?: { key?: string; title?: string; tests?: number }[] } | undefined
    const parts = Array.isArray(skeleton?.parts) ? skeleton.parts : []
    const sections = sectionsOf(neighbours)
    const proposed = node.status === 'proposed'
    const act = (to: 'accept' | 'reject') =>
      decide.mutate({ id: node.id, to }, {
        onSuccess: () => {
          if (to === 'accept') setHeadMode('morph')
          else onClose()
        },
        onError: (e) => toast.error(errorMessage(e)),
      })
    const build = () => buildWith({ label: node.label, definition })
    return (
      <motion.div variants={container} initial="hidden" animate="show" className="flex flex-col gap-4 p-4">
        <motion.div variants={item}>
          <Construct mode={headMode} tone="success" radius={8} onDone={() => setHeadMode('idle')}>
            <div className="flex items-start gap-2.5">
              <IconTile hue={kindHue(kind, sub)} className="size-8"><KindIcon kind={kind} subkind={sub} /></IconTile>
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-[15px] leading-[20px] font-semibold tracking-[-0.01em]" data-testid="kg-node-sheet-title">{node.label}</h2>
                <p className="text-[12px] text-muted-foreground">
                  {[KIND_LABEL[kind].one[0].toUpperCase() + KIND_LABEL[kind].one.slice(1), sub ? words(String(sub)) : null, SOURCE_PHRASE[node.source] ?? node.source].filter(Boolean).join(' · ')}
                </p>
              </div>
              {proposed && <Badge variant="ai">proposed</Badge>}
              {node.status === 'stale' && <Badge variant="stub">stale</Badge>}
            </div>
          </Construct>
          {(definition || node.summary) && <p className="mt-2 text-[13px] leading-[19px] text-foreground/85">{definition || node.summary}</p>}
        </motion.div>

        {kind === 'concept' && slots.length > 0 && (
          <motion.section variants={item} data-testid="kg-node-sheet-slots" className="flex flex-col gap-1.5">
            <h3 className="text-[12px] font-medium text-muted-foreground">Needs</h3>
            <ul className="flex flex-col gap-1.5">
              {slots.filter((s) => !s.alias_of).map((s, i) => {
                const required = s.required === true
                return (
                  <li key={`${String(s.name)}-${i}`} className="flex items-start gap-2 text-[12.5px]" data-required={required}>
                    <span className={cn('mt-[5px] size-2 shrink-0 rounded-full', required ? 'bg-primary' : 'bg-recess shadow-[inset_0_0_0_1.5px_var(--art-line)]')} aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">{words(String(s.name))}</span>
                      <span className="text-muted-foreground"> · {String(s.type)} · {required ? 'asked if missing' : 'optional'}</span>
                      {typeof s.question === 'string' && s.question && <span className="block text-muted-foreground">“{s.question}”</span>}
                    </span>
                  </li>
                )
              })}
            </ul>
          </motion.section>
        )}

        {sections.map((s) => (
          <motion.section key={s.key} variants={item} className="flex flex-col gap-1.5" data-testid="kg-sheet-section" data-rel={s.rel}>
            <h3 className="text-[12px] font-medium text-muted-foreground">{sectionTitle(s.rel, s.direction)}{s.items.length > 3 ? ` · ${s.items.length}` : ''}</h3>
            <div className="flex flex-wrap gap-1">
              {s.items.map((n) => {
                const t = s.rel === 'validated_by' ? testsOf(n.props) : null
                return (
                  <NodeLink
                    key={`${n.node.id}-${n.direction}`} n={n.node} onFocus={onFocusKey}
                    extra={t ? <span className={cn('tabular-nums', t.passed === t.total ? 'text-success-text' : 'text-warning-text')}>{t.passed}/{t.total} {t.passed === t.total && <Check className="inline size-3" />}</span> : undefined}
                  />
                )
              })}
            </div>
          </motion.section>
        ))}

        {(kind === 'pattern' || kind === 'app') && parts.length > 0 && (
          <motion.section variants={item} className="flex flex-col gap-1.5" data-testid="kg-sheet-parts">
            <h3 className="text-[12px] font-medium text-muted-foreground">{count(parts.length, 'part')}</h3>
            <ul className="flex flex-col gap-1">
              {parts.map((p, i) => (
                <li key={`${p.key}-${i}`} className="flex items-center justify-between rounded-chip bg-recess px-2 py-1 text-[12.5px]">
                  <span className="truncate">{p.title ?? words(p.key ?? 'part')}</span>
                  {typeof p.tests === 'number' && <span className="text-muted-foreground tabular-nums">{count(p.tests, 'test')}</span>}
                </li>
              ))}
            </ul>
          </motion.section>
        )}

        {kind === 'app' && <AppNodeDetail node={node} />}

        {evidence.length > 0 && (
          <motion.section variants={item} className="flex flex-col gap-1" data-testid="kg-sheet-evidence">
            <h3 className="text-[12px] font-medium text-muted-foreground">Validated by</h3>
            {evidence.map((e, i) => {
              const t = testsOf(e)
              return (
                <p key={i} className="text-[12.5px] text-foreground/85">
                  {[typeof e.project_label === 'string' ? e.project_label : null, t ? `${t.passed}/${t.total} tests` : null, typeof e.at === 'string' ? timeAgo(e.at) : null].filter(Boolean).join(' · ')}
                </p>
              )
            })}
          </motion.section>
        )}

        {learn_events.length > 0 && (
          <motion.section variants={item} className="flex flex-col gap-1.5" data-testid="kg-sheet-learning">
            <h3 className="text-[12px] font-medium text-muted-foreground">Learning</h3>
            <ul className="flex flex-col gap-1.5">
              {learn_events.slice(0, 5).map((ev) => (
                <li key={ev.id} className="flex items-start gap-2 text-[12.5px]">
                  <span className="mt-[6px] size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                  <span className="min-w-0 flex-1">{ev.summary}<span className="text-muted-foreground"> · {timeAgo(ev.at)}</span></span>
                </li>
              ))}
            </ul>
          </motion.section>
        )}

        <motion.div variants={item} className="flex flex-wrap gap-2">
          {kind === 'concept' && !proposed && (
            <Button variant="ai" onClick={build} data-testid="kg-build-with"><Sparkles /> Build with this</Button>
          )}
          {proposed && (
            <>
              <Button onClick={() => act('accept')} disabled={decide.isPending} data-testid="kg-proposed-accept">Accept</Button>
              <Button variant="outline" onClick={() => act('reject')} disabled={decide.isPending} data-testid="kg-proposed-reject">Reject</Button>
            </>
          )}
        </motion.div>
      </motion.div>
    )
  })()

  return (
    <motion.aside
      className="glass-overlay absolute inset-y-3 right-3 z-10 flex w-[380px] max-w-[calc(100%-24px)] flex-col overflow-hidden rounded-panel"
      initial={{ x: 24, opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ duration: dur.base, delay: 0.2, ease: ease.out }}
      data-testid="kg-node-sheet" data-node-key={q.data?.node.key}
    >
      <Button variant="ghost" size="icon-xs" className="absolute top-2 right-2 z-10" onClick={onClose} aria-label="Close" data-testid="kg-node-sheet-close"><X /></Button>
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">{body}</div>
    </motion.aside>
  )
}
