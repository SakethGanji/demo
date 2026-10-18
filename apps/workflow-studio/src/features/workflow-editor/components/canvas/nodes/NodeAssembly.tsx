/**
 * The motion layers a node shows while the AI builds it (PLAN-MOTION-V2 §3.2). Nothing here renders unless the node's
 * `data.anim` is set, so a normal editor session pays for none of it.
 *
 * Rules these layers keep (plan §1, §3):
 *  - they animate transform, opacity and SVG stroke only, never filter, width or height;
 *  - they sit INSIDE the tile on inner wrappers, never on the React Flow node wrapper or the tile itself, and none of
 *    them wraps a Handle, so handle bounds stay correct;
 *  - the tile box never scales, so a measurement taken mid-animation is still right;
 *  - every layer a sequence selects renders unconditionally on the first render, so the selectors always find it.
 *
 * Beat: brackets clamp on, the frame strokes itself, a scan bar prints the body, the ports light, then the player flips
 * the node to `lock` (pulse + shadow lift + brackets close). Under the test flag every layer renders null.
 */
import { forwardRef, useEffect, useState, type HTMLAttributes, type ReactNode } from 'react'
import { animate, motion, stagger as motionStagger, useAnimate, useMotionValue, useTransform, type AnimationSequence } from 'motion/react'
import { beat, dur, ease, spring } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'
import type { AnimState } from '@/features/ai-builder/stage/buildOps'
import { useNodeAnimDone } from './useNodeAnimDone'
import { typedSlice } from '../stageGeometry'

interface LayerProps {
  id: string
  anim: AnimState
  width: number
  height: number
  radius: number
  /** the tile's own background colour: the scan prints it, so the body appears where the scan has passed */
  fill: string
  /** handle positions in percent of the tile height */
  ports: { inputs: number[]; outputs: number[] }
  /** beat time scale k (1 = nominal) */
  scale?: number
  /** compact beat: frame + icon + lock only */
  compact?: boolean
  /** lock / frame tone */
  tone?: 'primary' | 'warning'
  /** more than `round.reducedAbove` nodes: icon + fade only */
  reduced: boolean
  /** top-level parameter names of a changed node (never values) */
  fields?: string[]
}

const toneVar = (tone: 'primary' | 'warning') => (tone === 'warning' ? 'var(--warning)' : 'var(--primary)')
const BRACKET_ARM = 10
/** Corner position as a fraction of the tile (fx, fy) and the rotation that makes "outward" -x -y in its own frame. */
const CORNERS = [
  { key: 'tl', fx: 0, fy: 0, rot: 0 },
  { key: 'tr', fx: 1, fy: 0, rot: 90 },
  { key: 'br', fx: 1, fy: 1, rot: 180 },
  { key: 'bl', fx: 0, fy: 1, rot: 270 },
] as const

/**
 * Four L-shaped corner brackets. Each corner is a static `g` (position + rotation, so "outward" is always -x -y in its
 * own frame) holding an inner `g` that moves. `mode='close'` clamps from 6 px outside to the edge and fades out.
 */
export function Brackets({ width, height, mode, delay = 0, rootTestId = true }: {
  width: number
  height: number
  mode: 'close'
  delay?: number
  rootTestId?: boolean
}) {
  if (motionOff()) return null
  return (
    <svg className="pointer-events-none absolute inset-0 overflow-visible" width={width} height={height} aria-hidden data-testid={rootTestId ? 'node-build-brackets' : undefined} data-mode={mode}>
      {CORNERS.map((c) => {
        const [cx, cy] = [c.fx * width, c.fy * height]
        return (
          <g key={c.key} transform={`translate(${cx} ${cy}) rotate(${c.rot})`}>
            <motion.g
              initial={{ x: -6, y: -6, opacity: 1 }}
              animate={{ x: [-6, 0, 0], y: [-6, 0, 0], opacity: [1, 1, 0] }}
              transition={{ duration: 0.18, delay, times: [0, 0.7, 1], ease: 'linear' }}
            >
              <path d={`M0,${BRACKET_ARM} V0 H${BRACKET_ARM}`} fill="none" stroke="var(--primary)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
            </motion.g>
          </g>
        )
      })}
    </svg>
  )
}

/** The construct (`enter`) and deconstruct (`exit`) layers, driven by one `animate` sequence on a `useAnimate` scope. */
function BuildLayers({ mode, width, height, radius, fill, ports, scale = 1, compact = false }: Pick<LayerProps, 'width' | 'height' | 'radius' | 'fill' | 'ports' | 'scale' | 'compact'> & { mode: 'enter' | 'exit' }) {
  const [scope, run] = useAnimate<HTMLDivElement>()
  const exit = mode === 'exit'
  const frameStroke = exit ? 'var(--destructive)' : 'var(--primary)'

  useEffect(() => {
    const s = (x: number) => x * scale
    let seq: AnimationSequence
    if (exit) {
      seq = [
        ['[data-c=scan]', { y: [height - 2, 0], opacity: [0, 1, 1, 0] }, { duration: s(0.14), at: s(0.16), ease: 'linear' }],
        ['[data-c=fill]', { scaleY: [1, 0] }, { duration: s(0.14), at: s(0.16), ease: 'linear' }],
        ['[data-c=frame]', { strokeDashoffset: [0, 1] }, { duration: s(0.16), at: s(0.24), ease: ease.in }],
      ]
    } else if (compact) {
      seq = [
        ['[data-c=frame]', { strokeDashoffset: [1, 0] }, { duration: s(0.2), at: 0, ease: ease.out }],
        ['[data-c=fill]', { scaleY: [1, 1], opacity: [0, 1] }, { duration: s(0.15), at: s(0.1), ease: 'linear' }],
      ]
    } else {
      seq = [
        ['[data-c=bk]', { opacity: [0, 1], x: [-14, -6], y: [-14, -6] }, { duration: s(beat.plate), at: 0, ease: ease.out }],
        ['[data-c=frame]', { strokeDashoffset: [1, 0] }, { duration: s(beat.frame), at: s(beat.frameAt), ease: ease.out }],
        ['[data-c=tip]', { strokeDashoffset: [0.06, -0.94], opacity: [0, 1, 1, 0] }, { duration: s(beat.frame), at: s(beat.frameAt), ease: ease.out }],
        ['[data-c=scan]', { y: [0, height - 2], opacity: [0, 1, 1, 0] }, { duration: s(beat.scan), at: s(beat.scanAt), ease: 'linear' }],
        ['[data-c=fill]', { scaleY: [0, 1], opacity: [0, 1] }, { duration: s(beat.scan), at: s(beat.scanAt), ease: 'linear' }],
        ['[data-c=port]', { scale: [0, 1.6, 1], opacity: [0, 1, 0] }, { duration: s(beat.ports), at: s(beat.portsAt), delay: motionStagger(s(0.03)), ease: ease.out }],
      ]
    }
    // The player owns enter -> lock -> idle; this effect never calls done().
    const controls = run(seq)
    return () => controls.stop()
  }, [run, exit, compact, scale, height])

  const rx = Math.max(0, radius - 1)
  const portDots = [...ports.inputs.map((p) => [0, p] as const), ...ports.outputs.map((p) => [width, p] as const)]
  return (
    <div ref={scope} className="pointer-events-none absolute inset-0" data-testid="node-assembly">
      <div
        data-c="fill"
        className="absolute inset-0 z-0"
        style={{ background: fill, borderRadius: radius, transformOrigin: exit ? 'bottom' : 'top', opacity: exit ? 1 : 0, transform: exit ? 'none' : 'scaleY(0)', willChange: 'transform, opacity' }}
      />
      <svg className="absolute inset-0 z-[2] overflow-visible" width={width} height={height} data-testid="node-assembly-ring" aria-hidden>
        <rect
          data-c="frame"
          x={1} y={1} width={width - 2} height={height - 2} rx={rx} ry={rx}
          fill="none" stroke={frameStroke} strokeWidth={1.5} pathLength={1}
          style={{ strokeDasharray: 1, strokeDashoffset: exit ? 0 : 1 }}
        />
        <rect
          data-c="tip"
          x={1} y={1} width={width - 2} height={height - 2} rx={rx} ry={rx}
          fill="none" stroke={frameStroke} strokeWidth={2.5} strokeLinecap="round" pathLength={1}
          style={{ strokeDasharray: '0.06 2', strokeDashoffset: 0.06, opacity: 0 }}
        />
        <g data-testid="node-build-brackets">
          {CORNERS.map((c) => {
            const [cx, cy] = [c.fx * width, c.fy * height]
            return (
              <g key={c.key} transform={`translate(${cx} ${cy}) rotate(${c.rot})`}>
                <g data-c="bk" style={{ opacity: 0 }}>
                  <path d={`M0,${BRACKET_ARM} V0 H${BRACKET_ARM}`} fill="none" stroke="var(--primary)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
                </g>
              </g>
            )
          })}
        </g>
        <rect data-c="scan" data-testid="node-build-scan" x={0} y={0} width={width} height={2} fill="var(--primary)" style={{ opacity: 0 }} />
        {portDots.map(([px, pp], i) => (
          <circle
            key={i}
            data-c="port"
            data-testid="node-port-light"
            cx={px} cy={(height * pp) / 100} r={3}
            fill="var(--primary)"
            style={{ opacity: 0, transformBox: 'fill-box', transformOrigin: 'center' }}
          />
        ))}
      </svg>
    </div>
  )
}

/**
 * The lock-in at the end of a beat: a pulse ring, a shadow lift behind the tile, the brackets clamping shut and the
 * frame fading out. It owns no state; the player moves the node on to idle.
 */
function LockIn({ width, height, radius, tone }: Pick<LayerProps, 'width' | 'height' | 'radius'> & { tone: 'primary' | 'warning' }) {
  const colour = toneVar(tone)
  const rx = Math.max(0, radius - 1)
  return (
    <>
      <motion.div
        className="pointer-events-none absolute inset-0"
        style={{ borderRadius: radius, border: `2px solid ${colour}`, willChange: 'transform, opacity' }}
        initial={{ scale: 1, opacity: 0.55 }}
        animate={{ scale: 1.12, opacity: 0 }}
        transition={{ duration: beat.lockPulse, ease: ease.out }}
        data-testid="node-lock-pulse"
      />
      <motion.div
        className="pointer-events-none absolute inset-0 -z-10 shadow-raise-md"
        style={{ borderRadius: radius, willChange: 'transform, opacity' }}
        initial={{ opacity: 0, y: 0 }}
        animate={{ opacity: [0, 1, 0], y: [0, 4, 0] }}
        transition={{ duration: beat.lockPulse, ease: 'linear' }}
      />
      <Brackets width={width} height={height} mode="close" rootTestId={false} />
      <svg className="pointer-events-none absolute inset-0 z-[2] overflow-visible" width={width} height={height} aria-hidden>
        <motion.rect
          x={1} y={1} width={width - 2} height={height - 2} rx={rx} ry={rx}
          fill="none" stroke={colour} strokeWidth={1.5}
          initial={{ opacity: 1 }}
          animate={{ opacity: 0 }}
          transition={{ duration: 0.2, ease: ease.out }}
        />
      </svg>
    </>
  )
}

/** A changed node: a warning scan over the intact tile, then the frame redraws in warning. */
function MorphLayers({ width, height, radius, scale = 1 }: Pick<LayerProps, 'width' | 'height' | 'radius' | 'scale'>) {
  const [scope, run] = useAnimate<HTMLDivElement>()
  useEffect(() => {
    const s = (x: number) => x * scale
    const controls = run([
      ['[data-c=scan]', { y: [0, height - 2], opacity: [0, 1, 1, 0] }, { duration: s(0.2), at: 0, ease: 'linear' }],
      ['[data-c=frame]', { strokeDashoffset: [1, 0] }, { duration: s(0.22), at: s(0.3), ease: ease.out }],
    ])
    return () => controls.stop()
  }, [run, scale, height])
  const rx = Math.max(0, radius - 1)
  return (
    <div ref={scope} className="pointer-events-none absolute inset-0" data-testid="node-morph">
      <svg className="absolute inset-0 z-[2] overflow-visible" width={width} height={height} aria-hidden>
        <rect data-c="scan" x={0} y={0} width={width} height={2} fill="var(--warning)" style={{ opacity: 0 }} />
        <rect
          data-c="frame"
          x={1} y={1} width={width - 2} height={height - 2} rx={rx} ry={rx}
          fill="none" stroke="var(--warning)" strokeWidth={1.5} pathLength={1}
          style={{ strokeDasharray: 1, strokeDashoffset: 1 }}
        />
      </svg>
    </div>
  )
}

/** The change chip: pops, types the changed field NAMES, and fades on its own (it may outlive the beat that made it). */
function ChangeChip({ fields, scale, onGone }: { fields: string[]; scale: number; onGone: () => void }) {
  const text = fields.slice(0, 3).join(', ') + (fields.length > 3 ? ` +${fields.length - 3}` : '')
  const p = useMotionValue(0)
  const shown = useTransform(p, (v) => typedSlice(text, v))
  useEffect(() => {
    const controls = animate(p, 1, { duration: Math.min(0.3, Math.max(0.1, text.length * 0.018)), delay: 0.12 * scale + 0.1, ease: 'linear' })
    return () => controls.stop()
  }, [p, text, scale])
  const cls = 'whitespace-nowrap rounded-chip px-2 py-1 font-mono text-[11px] font-medium text-warning'
  return (
    <motion.div
      className="pointer-events-none absolute -top-8 right-1/2 z-10 mr-1 bg-raise shadow-raise-md rounded-chip"
      initial={{ opacity: 0, y: 4, scale: 0.85 }}
      animate={{ opacity: [0, 1, 1, 0], y: [4, 0, 0, -2], scale: [0.85, 1, 1, 1] }}
      transition={{ duration: 1.5, delay: 0.12 * scale, times: [0, 0.12, 0.8, 1], ease: 'linear' }}
      onAnimationComplete={onGone}
      style={{ willChange: 'transform, opacity' }}
      data-testid="node-change-chip"
    >
      <span className={`${cls} invisible block`} aria-hidden>{text}</span>
      <motion.span className={`${cls} absolute inset-0 block`}>{shown}</motion.span>
    </motion.div>
  )
}

/**
 * Overlay layers inside the tile: the construct/deconstruct build, the lock-in, the morph scan, the shockwave on fail
 * and the chip of changed field names. Renders null when the node is idle.
 */
export function NodeAssembly({ id, anim, width, height, radius, fill, ports, scale = 1, compact = false, tone = 'primary', reduced, fields }: LayerProps) {
  const done = useNodeAnimDone(id)
  const off = motionOff()
  const [chip, setChip] = useState<string[] | null>(null)

  // Under the test flag motion skips animations; make sure completion still lands even if no callback fires.
  useEffect(() => {
    if (anim === 'idle' || !off) return
    if (anim === 'enter' || anim === 'fail' || (anim === 'change' && fields?.length)) queueMicrotask(() => done(anim))
  }, [anim, done, fields, off])

  // The chip is shown for a changed node and stays after the node has moved on to lock / idle.
  const changeFields = anim === 'change' && fields?.length ? fields : null
  if (changeFields && chip !== changeFields) setChip(changeFields)

  return (
    <>
      {(anim === 'enter' || anim === 'exit') && !reduced && !off && (
        <BuildLayers key={anim} mode={anim} width={width} height={height} radius={radius} fill={fill} ports={ports} scale={scale} compact={compact} />
      )}
      {anim === 'lock' && !reduced && !off && <LockIn width={width} height={height} radius={radius} tone={tone} />}
      {anim === 'change' && !reduced && !off && <MorphLayers width={width} height={height} radius={radius} scale={scale} />}
      {anim === 'fail' && (
        <>
          <motion.div
            className="pointer-events-none absolute inset-0 -z-10"
            style={{ borderRadius: radius, border: '2px solid var(--destructive)', willChange: 'transform, opacity' }}
            initial={{ scale: 1, opacity: 0.6 }}
            animate={{ scale: 1.8, opacity: 0 }}
            transition={{ duration: dur.shockwave, ease: ease.out, repeat: 1, repeatType: 'loop' }}
            onAnimationComplete={() => done('fail')}
            data-testid="node-shockwave"
          />
          {!off && !reduced && (
            <svg className="pointer-events-none absolute inset-0 z-[2] overflow-visible" width={width} height={height} aria-hidden>
              <motion.rect
                x={1} y={1} width={width - 2} height={height - 2} rx={Math.max(0, radius - 1)} ry={Math.max(0, radius - 1)}
                fill="none" stroke="var(--destructive)" strokeWidth={2}
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 1, 0] }}
                transition={{ duration: dur.shockwave, ease: 'linear' }}
              />
            </svg>
          )}
        </>
      )}
      {chip && <ChangeChip fields={chip} scale={scale} onGone={() => setChip(null)} />}
    </>
  )
}

/**
 * The icon disc, wrapped. Idle: renders its children untouched. Otherwise the wrapper animates scale / rotate / opacity:
 * enter springs in (above the scan fill), change flips on its Y axis, fail shakes, exit shrinks away.
 */
export function AssemblyIcon({ id, anim, reduced, scale = 1, compact = false, children }: { id: string; anim: AnimState; reduced: boolean; hasFields?: boolean; scale?: number; compact?: boolean; children: ReactNode }) {
  const done = useNodeAnimDone(id)
  // Lock needs no wrapper: the icon is already in place (a wrapper swap here would remount the subtree).
  if (anim === 'idle' || anim === 'lock') return <>{children}</>

  const common = { className: 'relative z-[1] flex items-center justify-center', style: { willChange: 'transform, opacity' } }
  if (anim === 'enter') {
    const delay = (compact ? 0.2 : beat.iconAt) * scale
    return (
      <motion.div
        {...common}
        initial={reduced ? { opacity: 0 } : { scale: 0.3, rotate: -12, opacity: 0 }}
        animate={reduced ? { opacity: 1 } : { scale: 1, rotate: 0, opacity: 1 }}
        transition={reduced ? { duration: dur.fast, ease: ease.out } : { ...spring.snap, delay, opacity: { duration: 0.12, delay } }}
        onAnimationComplete={() => { if (reduced) done('enter') }}
      >
        {children}
      </motion.div>
    )
  }
  if (anim === 'change') {
    return (
      <motion.div
        {...common}
        animate={{ rotateY: [0, 90, 0] }}
        transition={{ duration: 0.25 * scale, delay: 0.08 * scale, times: [0, 0.4, 1], ease: ease.io }}
        style={{ ...common.style, transformPerspective: 400 }}
      >
        {children}
      </motion.div>
    )
  }
  if (anim === 'fail') {
    return (
      <motion.div {...common} animate={{ x: [0, -5, 5, -3, 3, 0] }} transition={{ duration: dur.shake, ease: 'linear' }}>
        {children}
      </motion.div>
    )
  }
  // exit: shrinks and fades; the node then returns as a dashed ghost
  return (
    <motion.div {...common} initial={{ opacity: 1, scale: 1 }} animate={{ opacity: 0, scale: 0.3 }} transition={{ duration: 0.14 * scale, delay: 0.1 * scale, ease: ease.in }}>
      {children}
    </motion.div>
  )
}

/**
 * The label. While the node enters, its text types in over an invisible copy of itself (with a caret at the typing tip),
 * and on exit it back-spaces, so the node's box never changes size mid-animation (a size change would make React Flow
 * re-measure every frame).
 */
export function AssemblyLabel({ anim, reduced, text, className, style, title, onDoubleClick, scale = 1, compact = false }: {
  anim: AnimState
  reduced: boolean
  text: string
  className: string
  style: React.CSSProperties
  title: string
  onDoubleClick: (e: React.MouseEvent) => void
  scale?: number
  compact?: boolean
}) {
  const enter = anim === 'enter' && !reduced && !compact
  const exit = anim === 'exit' && !reduced
  const typing = (enter || exit) && !motionOff()
  const p = useMotionValue(enter ? 0 : 1)
  const shown = useTransform(p, (v) => typedSlice(text, v))
  const caret = useTransform(p, (v) => (v > 0 && v < 1 ? 1 : 0))
  useEffect(() => {
    if (!typing) return
    const controls = enter
      ? (p.set(0), animate(p, 1, { duration: Math.min(beat.label, text.length * 0.018) * scale, delay: beat.labelAt * scale, ease: 'linear' }))
      : (p.set(1), animate(p, 0, { duration: 0.14 * scale, delay: 0.04 * scale, ease: 'linear' }))
    return () => controls.stop()
  }, [typing, enter, text, p, scale])

  if (!typing) {
    return <span className={className} style={style} title={title} onDoubleClick={onDoubleClick}>{text}</span>
  }
  return (
    <span className="relative mt-2 inline-flex" style={{ maxWidth: style.maxWidth }}>
      <span className={`${className} invisible`} style={{ ...style, marginTop: 0 }} aria-hidden>{text}</span>
      <motion.span className={`${className} absolute inset-0`} style={{ ...style, marginTop: 0, textAlign: 'left' }} title={title}>
        <motion.span>{shown}</motion.span>
        <motion.span className="inline-block h-[1em] w-px bg-primary align-[-0.12em]" style={{ opacity: caret }} aria-hidden />
      </motion.span>
    </span>
  )
}

/**
 * One-shot flash across the tile. `success` is the green Keep sweep; `primary` is the review sweep that runs left to
 * right when a played build reaches review.
 */
export function SettleFlash({ radius, delay, tone = 'success' }: { radius: number; delay: number; tone?: 'success' | 'primary' }) {
  const primary = tone === 'primary'
  return (
    <motion.div
      className="pointer-events-none absolute inset-0"
      style={{ borderRadius: radius, border: `2px solid ${primary ? 'var(--primary)' : 'var(--success)'}`, willChange: 'opacity' }}
      initial={{ opacity: 0 }}
      animate={{ opacity: [0, primary ? 0.7 : 0.8, 0] }}
      transition={{ duration: 0.4, delay, ease: 'linear' }}
      data-testid={primary ? 'node-review-sweep' : 'node-settle-flash'}
    />
  )
}

/**
 * Test badge that pops in with a small overshoot while a stage is active. It is a tooltip trigger, so it forwards its
 * ref and passes the trigger's props (aria, handlers) on to the element.
 */
export const PopBadge = forwardRef<HTMLDivElement, { className: string; status: 'pass' | 'fail'; children: ReactNode } & Omit<HTMLAttributes<HTMLDivElement>, 'className' | 'children'>>(
  function PopBadge({ className, status, children, ...rest }, ref) {
    return (
      <motion.div
        {...(rest as object)}
        ref={ref}
        data-testid="node-test-badge"
        data-status={status}
        className={className}
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={spring.snap}
      >
        {children}
      </motion.div>
    )
  },
)
