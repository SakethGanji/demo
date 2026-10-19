/**
 * The hero's product shot: an AI build of a generic request workflow, replayed. The ask, a plan, then
 * the canvas fills in one step at a time (a dashed blueprint first, then the solid node, as on the real stage),
 * then the review with Keep / Discard. Decoration only: nothing here is clickable. It replays every ~11s while
 * the tab is visible; with motion off it shows the finished review.
 */
import { useEffect, useMemo, useState } from 'react'
import { motion } from 'motion/react'
import { Check, FlaskConical, Globe, Sparkles } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { Stepper, type StepState } from '@/shared/components/ui/stepper'
import { getNodeStyles } from '@/features/workflow-editor/lib/nodeStyles'
import { getIconForNode } from '@/features/workflow-editor/lib/nodeIcons'
import { groupOf } from '@/features/templates/lib'
import { useMotionOff } from '@/features/knowledge/hooks/useMotionOff'
import { BUILD_COLS, BUILD_EDGES, BUILD_NODES, BUILD_ROWS } from './fixtures'

const N = BUILD_NODES.length
/** ask, plan, one tick per node, review, then a hold */
const BUILD_AT = 2
const REVIEW_AT = BUILD_AT + N
const LAST = REVIEW_AT + 5
const TICK = 620

const at = (col: number, row: number) => ({ x: ((col + 0.5) / BUILD_COLS) * 100, y: ((row + 0.5) / BUILD_ROWS) * 100 })

export function BuildMock() {
  const still = useMotionOff()
  const [ticking, setTick] = useState(0)
  const tick = still ? LAST : ticking

  useEffect(() => {
    if (still) return
    const id = window.setInterval(() => {
      if (!document.hidden) setTick((t) => (t >= LAST ? 0 : t + 1))
    }, TICK)
    return () => window.clearInterval(id)
  }, [still])

  const built = Math.max(0, Math.min(N, tick - BUILD_AT + 1))
  const reviewing = tick >= REVIEW_AT
  const phase = tick < 1 ? 0 : tick < BUILD_AT ? 1 : reviewing ? 3 : 2
  const state = (i: number): StepState => (i < phase ? 'done' : i === phase ? 'current' : 'pending')

  const index = useMemo(() => new Map(BUILD_NODES.map((n, i) => [n.name, i])), [])

  return (
    <div className="grid grid-cols-1 gap-3 p-3 md:grid-cols-[1fr_15rem]" aria-hidden>
      {/* the canvas */}
      <div className="landing-dots well relative min-h-[15rem] overflow-hidden">
        <svg className="absolute inset-0 size-full" viewBox="0 0 100 100" preserveAspectRatio="none">
          {BUILD_EDGES.map(([s, t]) => {
            const a = BUILD_NODES[index.get(s)!]
            const b = BUILD_NODES[index.get(t)!]
            const p = at(a.col, a.row)
            const q = at(b.col, b.row)
            const mx = (p.x + q.x) / 2
            const on = index.get(t)! < built && index.get(s)! < built
            return (
              <motion.path
                key={`${s}-${t}`}
                d={`M ${p.x} ${p.y} C ${mx} ${p.y}, ${mx} ${q.y}, ${q.x} ${q.y}`}
                fill="none"
                stroke="var(--primary)"
                strokeOpacity={0.45}
                strokeWidth={1.25}
                vectorEffect="non-scaling-stroke"
                initial={false}
                animate={{ pathLength: on ? 1 : 0, opacity: on ? 1 : 0 }}
                transition={{ duration: still ? 0 : 0.45, ease: 'easeOut' }}
              />
            )
          })}
        </svg>
        {BUILD_NODES.map((n, i) => {
          const p = at(n.col, n.row)
          const styles = getNodeStyles(groupOf(n.type))
          const Icon = getIconForNode(undefined, n.type)
          const solid = i < built
          const blueprint = i === built && phase === 2
          return (
            <div
              key={n.name}
              className="absolute flex w-[5.5rem] -translate-x-1/2 -translate-y-[1.1rem] flex-col items-center gap-1"
              style={{ left: `${p.x}%`, top: `${p.y}%` }}
            >
              <motion.span
                className={cn(
                  'flex size-9 items-center justify-center rounded-ctl',
                  blueprint && 'outline-[1.5px] outline-dashed outline-primary/60',
                )}
                style={{
                  background: solid ? 'var(--node-bg)' : 'transparent',
                  boxShadow: solid ? `inset 0 0 0 1px ${styles.borderColor}, 0 6px 16px -10px ${styles.accentColor}` : 'none',
                  color: styles.iconFgColor,
                }}
                initial={false}
                animate={{ opacity: solid || blueprint ? 1 : 0, scale: solid ? 1 : 0.86 }}
                transition={still ? { duration: 0 } : { type: 'spring', stiffness: 420, damping: 26 }}
              >
                <Icon size={16} />
              </motion.span>
              <motion.span
                className="max-w-full truncate text-[10.5px] leading-tight text-muted-foreground"
                initial={false}
                animate={{ opacity: solid ? 1 : 0 }}
                transition={{ duration: still ? 0 : 0.3 }}
              >
                {n.name}
              </motion.span>
            </div>
          )
        })}
      </div>

      {/* the build panel */}
      <div className="flex min-w-0 flex-col gap-2.5">
        <div className="glass-raised rounded-well px-3 py-2.5 text-[12.5px] leading-snug">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-ai-text">
            <Sparkles className="size-3" /> You asked
          </div>
          Handle new requests, send the risky ones for approval.
        </div>
        <Stepper
          steps={[
            { key: 'ask', title: 'Ask', state: state(0) },
            { key: 'plan', title: 'Plan', meta: phase >= 1 ? '6 steps · tests locked' : undefined, state: state(1) },
            { key: 'build', title: 'Build', meta: phase >= 2 ? `${built} of ${N} steps` : undefined, state: state(2) },
            { key: 'review', title: 'Review', state: state(3) },
          ]}
        />
        <motion.div
          className="glass-raised flex flex-col gap-2 rounded-well px-3 py-2.5"
          initial={false}
          animate={{ opacity: reviewing ? 1 : 0, y: reviewing ? 0 : 6 }}
          transition={{ duration: still ? 0 : 0.35 }}
        >
          <div className="flex items-center gap-1.5 text-[12px] font-medium text-success-text">
            <FlaskConical className="size-3.5" /> 6 of 6 tests passed
          </div>
          <div className="flex items-center gap-1.5 text-[11.5px] text-warning-text">
            <Globe className="size-3.5" /> New address: api.example.com
          </div>
          <div className="mt-0.5 flex gap-1.5">
            <span className="brand-fill inline-flex h-6 items-center gap-1 rounded-full px-2.5 text-[11.5px] font-semibold">
              <Check className="size-3" /> Keep
            </span>
            <span className="inline-flex h-6 items-center rounded-full bg-recess px-2.5 text-[11.5px] font-semibold text-muted-foreground">Discard</span>
          </div>
        </motion.div>
      </div>
    </div>
  )
}
