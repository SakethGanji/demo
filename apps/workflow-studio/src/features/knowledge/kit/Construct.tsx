/**
 * The construction beat for DOM cards and rows (PLAN-CAPGRAPH-UI §1.4): the DOM twin of the canvas `NodeAssembly`.
 * `enter`: brackets, the frame strokes itself, a scan reveals the card, then it locks (pulse). `lock`: the lock alone.
 * `morph`: a warning scan over the intact card, a frame redraw in `tone`, then the lock.
 *
 * Only transform, opacity, clip-path and SVG stroke animate. The card is always mounted: after the beat it is just the
 * children. Under the test flag, or reduced motion, it renders the children and calls `onDone` in a microtask.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { motion, useAnimate, type AnimationSequence } from 'motion/react'
import { ease } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'

export type ConstructMode = 'enter' | 'lock' | 'morph' | 'idle'
type Tone = 'primary' | 'warning' | 'success'

const toneVar = (t: Tone) => (t === 'warning' ? 'var(--warning)' : t === 'success' ? 'var(--success)' : 'var(--primary)')
const ARM = 10
const CORNERS = [
  { key: 'tl', fx: 0, fy: 0, rot: 0 },
  { key: 'tr', fx: 1, fy: 0, rot: 90 },
  { key: 'br', fx: 1, fy: 1, rot: 180 },
  { key: 'bl', fx: 0, fy: 1, rot: 270 },
] as const

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function Construct({ mode, radius = 8, tone = 'primary', scale = 1, delay = 0, onDone, children, 'data-testid': testId }: {
  mode: ConstructMode
  radius?: number
  tone?: Tone
  scale?: number
  delay?: number
  onDone?: () => void
  children: ReactNode
  'data-testid'?: string
}) {
  const [scope, run] = useAnimate<HTMLDivElement>()
  const [size, setSize] = useState({ w: 0, h: 0 })
  const doneRef = useRef(onDone)
  useEffect(() => { doneRef.current = onDone })
  const off = motionOff() || reducedMotion()

  useEffect(() => {
    if (mode === 'idle') return
    if (off) {
      queueMicrotask(() => doneRef.current?.())
      return
    }
    const el = scope.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setSize({ w: Math.round(r.width), h: Math.round(r.height) })
    const k = (x: number) => x * scale
    const h = Math.max(2, Math.round(r.height))
    const lockSeq = (at: number) => [
      ['[data-c=lock]', { scale: [1, 1.04], opacity: [0.55, 0] }, { duration: k(0.32), at, ease: ease.out }],
      ['[data-c=bk]', { x: [-6, 0], y: [-6, 0], opacity: [1, 0] }, { duration: k(0.18), at, ease: ease.out }],
      ['[data-c=frame]', { opacity: [1, 0] }, { duration: k(0.2), at, ease: 'linear' }],
    ] as AnimationSequence
    let seq: AnimationSequence
    let end: number
    if (mode === 'enter') {
      seq = [
        ['[data-c=bk]', { opacity: [0, 1], x: [-14, -6], y: [-14, -6] }, { duration: k(0.16), at: 0, ease: ease.out }],
        ['[data-c=frame]', { strokeDashoffset: [1, 0], opacity: 1 }, { duration: k(0.22), at: k(0.06), ease: ease.out }],
        ['[data-c=scan]', { y: [0, h - 2], opacity: [0, 1, 1, 0] }, { duration: k(0.18), at: k(0.22), ease: 'linear' }],
        ['[data-c=reveal]', { clipPath: ['inset(0 0 100% 0)', 'inset(0 0 0% 0)'] }, { duration: k(0.18), at: k(0.22), ease: 'linear' }],
        ...lockSeq(k(0.56)),
      ]
      end = 0.88
    } else if (mode === 'morph') {
      seq = [
        ['[data-c=scan]', { y: [0, h - 2], opacity: [0, 1, 1, 0], backgroundColor: 'var(--warning)' }, { duration: k(0.2), at: 0, ease: 'linear' }],
        ['[data-c=frame]', { strokeDashoffset: [1, 0], opacity: 1 }, { duration: k(0.22), at: k(0.3), ease: ease.out }],
        ...lockSeq(k(0.52)),
      ]
      end = 0.84
    } else {
      seq = lockSeq(0)
      end = 0.32
    }
    let controls: { stop: () => void } | null = null
    const raf = requestAnimationFrame(() => { controls = run(seq, { delay }) })
    const t = window.setTimeout(() => doneRef.current?.(), (delay + k(end)) * 1000)
    return () => {
      cancelAnimationFrame(raf)
      controls?.stop()
      window.clearTimeout(t)
    }
  }, [mode, off, scale, delay, run, scope])

  const color = toneVar(tone)
  const rx = Math.max(0, radius - 1)
  const active = mode !== 'idle' && !off
  return (
    <div ref={scope} className="relative" data-testid={testId}>
      <div data-c="reveal" style={mode === 'enter' && !off ? { clipPath: 'inset(0 0 100% 0)' } : undefined}>{children}</div>
      {active && (
        <>
          <div
            data-c="lock"
            className="pointer-events-none absolute inset-0"
            style={{ borderRadius: radius, boxShadow: `0 0 0 2px ${color}`, opacity: 0 }}
          />
          <svg className="pointer-events-none absolute inset-0 overflow-visible" width={size.w} height={size.h} aria-hidden data-testid="kg-construct" data-mode={mode}>
            <rect
              data-c="frame" x={1} y={1} width={Math.max(0, size.w - 2)} height={Math.max(0, size.h - 2)} rx={rx} ry={rx}
              fill="none" stroke={color} strokeWidth={1.5} pathLength={1} style={{ strokeDasharray: 1, strokeDashoffset: mode === 'lock' ? 0 : 1, opacity: mode === 'lock' ? 0 : 1 }}
            />
            {CORNERS.map((c) => (
              <g key={c.key} transform={`translate(${c.fx * size.w} ${c.fy * size.h}) rotate(${c.rot})`}>
                <g data-c="bk" style={{ opacity: 0 }}>
                  <path d={`M0,${ARM} V0 H${ARM}`} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
                </g>
              </g>
            ))}
            <rect data-c="scan" x={0} y={0} width={size.w} height={2} fill={color} style={{ opacity: 0 }} />
          </svg>
        </>
      )}
    </div>
  )
}

/** Three rings that spread from a point and fade (PLAN-CAPGRAPH-UI §1.4): the port catch / dot ripple. Renders inside an SVG. */
export function Ripple({ x, y, rings = 3, tone = 'primary' }: { x: number; y: number; rings?: number; tone?: Tone }) {
  if (motionOff()) return null
  return (
    <g data-testid="kg-ripple" style={{ pointerEvents: 'none' }}>
      {Array.from({ length: rings }, (_, i) => (
        <motion.circle
          key={i} cx={x} cy={y} fill="none" stroke={toneVar(tone)} strokeWidth={1.5}
          initial={{ r: 0, opacity: 0.5 }} animate={{ r: 14, opacity: 0 }}
          transition={{ duration: 0.24, delay: i * 0.02, ease: ease.out }}
        />
      ))}
    </g>
  )
}
