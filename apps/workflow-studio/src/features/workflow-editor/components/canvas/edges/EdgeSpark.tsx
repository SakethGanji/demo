import { useEffect, useRef, type RefObject } from 'react'
import { animate } from 'motion/react'
import { ease } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'

/** Length of the comet's trail, in flow px. */
const TRAIL = 28

function geometry(d: string) {
  const geom = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  geom.setAttribute('d', d)
  return geom
}

/**
 * One solid dot that runs an edge's path once, with a flat low-opacity halo (DESIGN-STANDARD: no gradients; the halo is
 * a flat colour). Used as the test probe. It reads the path geometry itself and moves `cx` / `cy` straight on the
 * element, so there is no React render per frame.
 */
export function EdgeSpark({ d, color, duration, delay = 0, onDone }: {
  d: string
  color: string
  duration: number
  delay?: number
  onDone?: () => void
}) {
  const ref = useRef<SVGCircleElement>(null)
  const haloRef = useRef<SVGCircleElement>(null)
  const dRef = useRef(d)
  const doneRef = useRef(onDone)
  useEffect(() => { doneRef.current = onDone })

  useEffect(() => {
    const el = ref.current
    const halo = haloRef.current
    if (!el || motionOff()) {
      doneRef.current?.()
      return
    }
    const geom = geometry(dRef.current)
    const len = geom.getTotalLength()
    const controls = animate(0, len, {
      duration,
      delay,
      ease: 'linear',
      onUpdate: (v) => {
        const p = geom.getPointAtLength(v)
        el.setAttribute('cx', String(p.x))
        el.setAttribute('cy', String(p.y))
        el.style.opacity = '1'
        if (halo) {
          halo.setAttribute('cx', String(p.x))
          halo.setAttribute('cy', String(p.y))
          halo.style.opacity = '0.22'
        }
      },
      onComplete: () => {
        el.style.opacity = '0'
        if (halo) halo.style.opacity = '0'
        doneRef.current?.()
      },
    })
    return () => controls.stop()
  }, [duration, delay])

  return (
    <>
      <circle ref={haloRef} r={7} cx={0} cy={0} fill={color} style={{ opacity: 0, pointerEvents: 'none' }} />
      <circle ref={ref} r={3.5} cx={0} cy={0} fill={color} style={{ opacity: 0, pointerEvents: 'none' }} data-testid="edge-spark" />
    </>
  )
}

/**
 * The edge comet (PLAN-MOTION-V2 §3.5). One progress value drives everything: the real edge path is revealed behind a
 * head dot (via `pathRef`), a short trail rides just behind it, and a flat halo wraps the head. On arrival the trail and
 * halo fade and a ring "catches" on the target port. `onArrive` fires at arrival (flip to the finished edge with its
 * arrow); `onDone` fires once the catch ring has finished, so the parent can keep this mounted until then.
 *
 * The caller renders the main path hidden (huge dash offset) until the first update.
 */
export function EdgeComet({ d, pathRef, duration, delay = 0, color = 'var(--primary)', onArrive, onDone }: {
  d: string
  pathRef: RefObject<SVGPathElement | null>
  duration: number
  delay?: number
  color?: string
  onArrive?: () => void
  onDone?: () => void
}) {
  const trailRef = useRef<SVGPathElement>(null)
  const haloRef = useRef<SVGCircleElement>(null)
  const coreRef = useRef<SVGCircleElement>(null)
  const ringRef = useRef<SVGCircleElement>(null)
  const dRef = useRef(d)
  const arriveRef = useRef(onArrive)
  const doneRef = useRef(onDone)
  useEffect(() => { arriveRef.current = onArrive; doneRef.current = onDone })

  useEffect(() => {
    const trail = trailRef.current
    const halo = haloRef.current
    const core = coreRef.current
    const ring = ringRef.current
    const main = pathRef.current
    if (!trail || !halo || !core || !ring || motionOff()) {
      arriveRef.current?.()
      doneRef.current?.()
      return
    }
    const geom = geometry(dRef.current)
    const len = main?.getTotalLength?.() || geom.getTotalLength()
    let fade: { stop: () => void } | null = null
    const reveal = animate(0, len, {
      duration,
      delay,
      ease: ease.out,
      onUpdate: (v) => {
        const p = geom.getPointAtLength(v)
        if (main) {
          main.style.strokeDasharray = String(len)
          main.style.strokeDashoffset = String(len - v)
        }
        trail.style.strokeDasharray = `${TRAIL} ${len + TRAIL}`
        trail.style.strokeDashoffset = String(TRAIL - v)
        trail.style.opacity = '0.55'
        for (const c of [halo, core]) {
          c.setAttribute('cx', String(p.x))
          c.setAttribute('cy', String(p.y))
        }
        halo.style.opacity = '0.22'
        core.style.opacity = '1'
      },
      onComplete: () => {
        if (main) {
          main.style.strokeDasharray = ''
          main.style.strokeDashoffset = ''
        }
        const end = geom.getPointAtLength(len)
        ring.setAttribute('cx', String(end.x))
        ring.setAttribute('cy', String(end.y))
        ring.setAttribute('data-testid', 'edge-port-catch')
        core.style.opacity = '0'
        arriveRef.current?.()
        fade = animate(0, 1, {
          duration: 0.24,
          ease: ease.out,
          onUpdate: (t) => {
            ring.setAttribute('r', String(9 * t))
            ring.style.opacity = String(0.5 * (1 - t))
            const f = String(Math.max(0, 1 - t / 0.75))
            trail.style.opacity = String(0.55 * Number(f))
            halo.style.opacity = String(0.22 * Number(f))
          },
          onComplete: () => {
            ring.style.opacity = '0'
            doneRef.current?.()
          },
        })
      },
    })
    return () => {
      reveal.stop()
      fade?.stop()
    }
  }, [duration, delay, pathRef])

  return (
    <g data-testid="edge-spark" style={{ pointerEvents: 'none' }}>
      <path ref={trailRef} d={d} fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round" style={{ opacity: 0 }} />
      <circle ref={haloRef} r={7} cx={0} cy={0} fill={color} style={{ opacity: 0 }} />
      <circle ref={coreRef} r={3} cx={0} cy={0} fill={color} style={{ opacity: 0 }} />
      <circle ref={ringRef} r={0} cx={0} cy={0} fill="none" stroke={color} strokeWidth={1.5} style={{ opacity: 0 }} />
    </g>
  )
}
