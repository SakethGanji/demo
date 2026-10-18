import { useEffect } from 'react'
import { ViewportPortal, useStore } from '@xyflow/react'
import { Sparkles } from 'lucide-react'
import { AnimatePresence, animate, motion, useMotionValue, useSpring, useTransform, type MotionValue } from 'motion/react'
import { useStageStore } from '@/features/ai-builder/stage/stageStore'
import { dur, ease, spring } from '@/shared/motion/tokens'
import { counterScale } from './stageGeometry'

const FALLBACK_WIDTH = 64
/** Half the width of a 1-in/1-out tile; the node wrapper is as wide as its label, the tile sits in its middle. */
const TILE_HALF = 32
const ORB = 28
const PILL_GAP = 8
/** The orb's trail: three dots that follow on softer springs (size px, opacity). */
const TRAIL = [
  { size: 10, opacity: 0.35 },
  { size: 7, opacity: 0.22 },
  { size: 5, opacity: 0.12 },
] as const

/** One trail dot: follows the orb's position through its own spring, centred on the orb. */
function TrailDot({ x, y, i, scale }: { x: MotionValue<number>; y: MotionValue<number>; i: number; scale: number }) {
  const { size, opacity } = TRAIL[i]
  const px = size * scale
  const sx = useSpring(x, { stiffness: 160 - 35 * i, damping: 22 })
  const sy = useSpring(y, { stiffness: 160 - 35 * i, damping: 22 })
  const tx = useTransform(sx, (v) => v + ORB / 2 - px / 2)
  const ty = useTransform(sy, (v) => v + ORB / 2 - px / 2)
  return (
    <motion.span
      className="pointer-events-none absolute left-0 top-0 z-20 rounded-full bg-primary"
      style={{ x: tx, y: ty, width: px, height: px, willChange: 'transform, opacity' }}
      initial={{ opacity: 0 }}
      animate={{ opacity }}
      transition={{ duration: dur.base, ease: ease.out }}
      data-testid={i === 0 ? 'ai-presence-trail' : undefined}
      aria-hidden
    />
  )
}

/**
 * The AI marker: a ~28px accent orb with a soft halo and a pill ("AI · adding Over limit?") that flies (spring.soft) to
 * the node the player is working on and sits just above the tile's top-right corner, never below the row. It says what
 * it is doing (a step name, or changed field NAMES, never values). It lives in flow coordinates inside ViewportPortal,
 * so pan and zoom move it for free; its size is counter-scaled so it reads at the build's lower zoom. The halo
 * breathes, the one loop in the build.
 *
 * `centre` returns the visible middle of the canvas, in flow coordinates, for when there is no node to sit at yet.
 */
export default function AiPresence({ centre }: { centre: () => { x: number; y: number } }) {
  const visible = useStageStore((s) => s.active && s.presence.visible)
  const nodeId = useStageStore((s) => s.presence.node)
  const label = useStageStore((s) => s.presence.label)
  const at = useStageStore((s) => s.presence.at)
  const mode = useStageStore((s) => s.mode)
  const zoom = useStore((s) => s.transform[2])
  const target = useStageStore((s) => {
    const n = nodeId ? s.nodes.find((x) => x.id === nodeId) : undefined
    if (!n) return null
    return { x: n.position.x + (n.measured?.width ?? FALLBACK_WIDTH) / 2 + TILE_HALF, y: n.position.y }
  })
  // The right-most node's pill opens to the left, so it never runs under the floating panel.
  const flip = useStageStore((s) => {
    const n = nodeId ? s.nodes.find((x) => x.id === nodeId) : undefined
    if (!n || s.nodes.length < 2) return false
    return n.position.x >= Math.max(...s.nodes.map((x) => x.position.x)) - 1
  })

  const pos = target ?? at ?? centre()
  const ax = target ? pos.x - 10 : pos.x - ORB / 2
  const ay = target ? pos.y - ORB + 4 : pos.y - ORB / 2
  const text = label ?? (mode === 'blueprint' ? 'planning the steps' : nodeId ? 'working' : 'thinking')
  const scale = counterScale(zoom)

  // The orb springs to its target (spring.soft); the trail dots follow the same motion values on softer springs
  const x = useMotionValue(ax)
  const y = useMotionValue(ay)
  useEffect(() => {
    const cx = animate(x, ax, spring.soft)
    const cy = animate(y, ay, spring.soft)
    return () => { cx.stop(); cy.stop() }
  }, [ax, ay, x, y])

  return (
    <ViewportPortal>
      {visible && TRAIL.map((_, i) => <TrailDot key={i} x={x} y={y} i={i} scale={scale} />)}
      <AnimatePresence>
        {visible && (
          <motion.div
            key="ai-presence"
            data-testid="ai-presence"
            data-node={nodeId ?? undefined}
            className="pointer-events-none absolute left-0 top-0 z-20"
            style={{ x, y, width: ORB, height: ORB, willChange: 'transform, opacity' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, transition: { duration: dur.base, ease: ease.in } }}
            transition={{ opacity: { duration: dur.base, ease: ease.out } }}
          >
            <div style={{ transform: `scale(${scale})`, transformOrigin: `0px ${ORB}px` }}>
              <motion.div
                className="absolute left-0 top-0"
                style={{ width: 'var(--ai-marker-size, 28px)', height: 'var(--ai-marker-size, 28px)' }}
                initial={{ scale: 0.5 }}
                animate={{ scale: 1 }}
                transition={spring.snap}
              >
                {/* soft halo: the one loop */}
                <motion.span
                  className="absolute inset-0 rounded-full bg-primary"
                  animate={{ scale: [1, 1.9], opacity: [0.35, 0] }}
                  transition={{ duration: 1.6, repeat: Infinity, ease: 'easeOut' }}
                  data-testid="ai-presence-halo"
                />
                <span
                  className="absolute -inset-[5px] rounded-full"
                  style={{ backgroundColor: 'color-mix(in srgb, var(--primary) 18%, transparent)' }}
                />
                <span
                  data-testid="ai-presence-marker"
                  className="relative flex h-full w-full items-center justify-center rounded-full bg-primary text-primary-foreground shadow-raise-md"
                >
                  <Sparkles size={14} strokeWidth={2.25} />
                </span>
              </motion.div>
              {/* no exit animation: a leaving label would keep the side it had on the previous node */}
              <>
                <motion.span
                  key={text}
                  className="absolute top-[2px] flex items-center gap-1 whitespace-nowrap rounded-full bg-raise py-1 pl-2.5 pr-3 text-[13px] font-medium leading-none text-foreground shadow-raise-md"
                  style={{
                    height: ORB - 4,
                    maxWidth: 260,
                    border: '1px solid color-mix(in srgb, var(--primary) 35%, transparent)',
                    ...(flip ? { right: ORB + PILL_GAP } : { left: ORB + PILL_GAP }),
                  }}
                  initial={{ opacity: 0, x: flip ? 6 : -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: dur.fast, ease: ease.out }}
                  data-testid="ai-presence-label"
                >
                  <span className="font-mono text-[11px] font-semibold text-primary">AI</span>
                  <span className="text-muted-foreground">·</span>
                  <span className="truncate">{text}</span>
                </motion.span>
              </>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </ViewportPortal>
  )
}
