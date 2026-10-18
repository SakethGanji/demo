/**
 * The hero (PLAN-CAPGRAPH-UI §2.1): a coverage ring, the headline, five stats and the newest memory line.
 * The ring draws and the numbers roll on load; a stat that changes later locks (pulse) and rolls.
 */
import { useEffect, useState } from 'react'
import { motion } from 'motion/react'
import { ArrowRight } from 'lucide-react'
import { timeAgo } from '@/features/project/lib/format'
import { Badge } from '@/shared/components/ui/badge'
import { Roll } from '@/shared/components/ui/roll'
import { Tile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { dur, ease, spring } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'
import { Construct, Ripple } from '../kit/Construct'
import type { KgKind } from '../kit/kinds'
import type { KnowledgeTab } from '../kit/links'
import type { SeenDiff } from '../lib/seen'
import type { LearnEvent, MatrixTotals } from '../types'

export type StatKey = 'apps' | 'subworkflows' | 'patterns' | 'gaps' | 'roles'
export interface StatTarget { tab: KnowledgeTab; kinds?: KgKind[]; matrix?: 'gaps' }

const R = 26
const C = 32

function useRolled(target: number, start: number) {
  const [v, setV] = useState(motionOff() ? target : start)
  useEffect(() => {
    const t = window.setTimeout(() => setV(target), motionOff() ? 0 : 120)
    return () => window.clearTimeout(t)
  }, [target])
  return v
}

function CoverageRing({ implemented, concepts }: { implemented: number; concepts: number }) {
  const p = concepts > 0 ? Math.min(1, implemented / concepts) : 0
  const [arrived, setArrived] = useState(0)
  useEffect(() => {
    if (motionOff()) return
    const t = window.setTimeout(() => setArrived((n) => n + 1), 820)
    return () => window.clearTimeout(t)
  }, [p])
  const a = (-90 + 360 * p) * (Math.PI / 180)
  return (
    <div className="relative size-16 shrink-0" data-testid="kg-hero-coverage" data-implemented={implemented} data-concepts={concepts}>
      <svg viewBox="0 0 64 64" className="absolute inset-0 overflow-visible" aria-hidden>
        <circle cx={C} cy={C} r={R} fill="none" stroke="var(--recess)" strokeWidth={6} />
        <motion.circle
          cx={C} cy={C} r={R} fill="none" stroke="var(--primary)" strokeWidth={6} strokeLinecap="round" transform={`rotate(-90 ${C} ${C})`}
          initial={motionOff() ? false : { pathLength: 0 }} animate={{ pathLength: p }} transition={{ ...spring.firm, delay: 0.2, duration: 0.6 }}
        />
        {arrived > 0 && p > 0 && <Ripple key={arrived} x={C + R * Math.cos(a)} y={C + R * Math.sin(a)} />}
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-[15px] font-semibold tabular-nums">{implemented}/{concepts}</span>
    </div>
  )
}

function StatCell({ id, label, value, start, changed, onOpen, index, warn }: {
  id: StatKey; label: string; value: number; start: number; changed: boolean; onOpen: () => void; index: number; warn?: boolean
}) {
  const shown = useRolled(value, start)
  const [prev, setPrev] = useState(value)
  const [lock, setLock] = useState<{ key: number; tone: 'primary' | 'success' } | null>(changed ? { key: 0, tone: 'primary' } : null)
  // a later change locks the cell (derived state: adjusted while rendering, not in an effect)
  if (prev !== value) {
    setPrev(value)
    setLock({ key: (lock?.key ?? 0) + 1, tone: id === 'gaps' && value < prev ? 'success' : 'primary' })
  }
  return (
    <motion.button
      type="button" onClick={onOpen} data-testid={`kg-stat-${id}`} data-value={value}
      initial={motionOff() ? false : { y: 6, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ duration: dur.base, ease: ease.out, delay: 0.08 + index * 0.06 }}
      className="min-w-0 rounded-ctl text-left outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
    >
      <Construct key={lock?.key ?? 'idle'} mode={lock ? 'lock' : 'idle'} tone={lock?.tone} radius={8} onDone={() => setLock(null)}>
        <div className="flex flex-col gap-0.5 px-3 py-2">
          <Roll value={shown} className={cn('text-[28px] leading-none font-semibold tracking-[-0.03em]', warn && 'text-warning-text')} />
          <span className="text-[12px] text-muted-foreground">{label}</span>
        </div>
      </Construct>
    </motion.button>
  )
}

export function KnowledgeHero({ totals, roles, latest, diff, since, onStat, onLatest }: {
  totals: MatrixTotals | undefined
  roles: number
  latest: LearnEvent | undefined
  diff: SeenDiff | null
  /** totals at the last visit, for the first roll */
  since: MatrixTotals | null
  onStat: (t: StatTarget) => void
  onLatest: () => void
}) {
  const t = totals
  const stats: { id: StatKey; label: string; value: number; target: StatTarget; start: number }[] = t
    ? [
        { id: 'apps', label: t.apps === 1 ? 'validated app' : 'validated apps', value: t.apps, target: { tab: 'graph', kinds: ['app', 'concept'] }, start: since?.apps ?? 0 },
        { id: 'subworkflows', label: t.subworkflows === 1 ? 'reusable sub-workflow' : 'reusable sub-workflows', value: t.subworkflows, target: { tab: 'graph', kinds: ['capability', 'concept'] }, start: since?.subworkflows ?? 0 },
        { id: 'patterns', label: t.patterns === 1 ? 'learned pattern' : 'learned patterns', value: t.patterns, target: { tab: 'graph', kinds: ['pattern', 'concept'] }, start: since?.patterns ?? 0 },
        { id: 'gaps', label: t.gaps === 1 ? 'gap' : 'gaps', value: t.gaps, target: { tab: 'matrix', matrix: 'gaps' }, start: since?.gaps ?? 0 },
        { id: 'roles', label: roles === 1 ? 'role' : 'roles', value: roles, target: { tab: 'graph', kinds: ['role', 'concept'] }, start: 0 },
      ]
    : []
  return (
    <Tile className="flex flex-col gap-4 p-5" data-testid="kg-hero">
      <div className="flex flex-wrap items-center gap-5">
        <CoverageRing implemented={t?.implemented ?? 0} concepts={t?.concepts ?? 0} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-section font-semibold tracking-[-0.01em]" data-testid="kg-hero-headline">
              {t ? `Your org can run ${t.implemented} of ${t.concepts} business concepts` : 'Reading what your org can do'}
            </h2>
            {diff?.text && (
              <Badge variant="ai" data-testid="kg-since-last"><span className="sr-only">Since your last visit: </span>{diff.text}</Badge>
            )}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-5" data-testid="kg-hero-stats">
            {stats.map((s, i) => (
              <StatCell
                key={s.id} id={s.id} label={s.label} value={s.id === 'roles' ? roles : s.value} start={s.start} index={i}
                changed={!!diff?.changed[s.id as keyof MatrixTotals] && s.id !== 'roles'} warn={s.id === 'gaps' && s.value > 0}
                onOpen={() => onStat(s.target)}
              />
            ))}
          </div>
        </div>
      </div>
      {latest && (
        <button type="button" onClick={onLatest} data-testid="kg-hero-latest" className="flex min-w-0 items-center gap-2 rounded-ctl bg-recess px-3 py-2 text-left text-[12.5px] hover:bg-(--hover-fill)">
          <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
          <span className="shrink-0 font-medium">Latest</span>
          <span className="min-w-0 flex-1 truncate text-foreground/85">{latest.summary}</span>
          <span className="shrink-0 text-muted-foreground">{timeAgo(latest.at)}</span>
          <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        </button>
      )}
    </Tile>
  )
}
