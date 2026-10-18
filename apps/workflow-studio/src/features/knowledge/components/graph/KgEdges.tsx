/**
 * Edges, relation labels, group captions and the guides of the graph. Everything here lives in the flow coordinate space
 * (inside a xyflow `ViewportPortal`), because nodes have no handles: paths come precomputed from `lib/layout`.
 */
import { memo, useEffect, useRef } from 'react'
import { animate, motion, useAnimate } from 'motion/react'
import { ViewportPortal } from '@xyflow/react'
import { EdgeComet } from '@/features/workflow-editor/components/canvas/edges/EdgeSpark'
import { typedSlice } from '@/features/workflow-editor/components/canvas/stageGeometry'
import { ease, spring } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'
import { kg } from '../../kit/beats'
import { phrase } from '../../lib/labels'
import type { Box, EgoEdge, GroupCaption, LaidEdge, Pt } from '../../lib/layout'

export interface EdgeAnim { delay: number; comet: boolean }

const STROKE = { fill: 'none', stroke: 'var(--art-line)', strokeWidth: 1.25, strokeLinecap: 'round' } as const

/** A path that draws, and a comet runs it. The path stays hidden until the comet's first frame. */
function CometPath({ edge, delay }: { edge: LaidEdge; delay: number }) {
  const ref = useRef<SVGPathElement>(null)
  const hidden = !motionOff()
  return (
    <>
      <path
        ref={ref} className="kg-edge" data-eid={edge.id} data-rel={edge.rel} d={edge.d} {...STROKE}
        style={hidden ? { strokeDasharray: 99999, strokeDashoffset: 99999 } : undefined}
      />
      <EdgeComet d={edge.d} pathRef={ref} duration={kg.ego.cometDraw} delay={delay} />
    </>
  )
}

const EdgePath = memo(function EdgePath({ edge, anim }: { edge: LaidEdge; anim: EdgeAnim | null }) {
  if (!anim) return <path className="kg-edge" data-eid={edge.id} data-rel={edge.rel} d={edge.d} {...STROKE} />
  if (anim.comet) return <CometPath edge={edge} delay={anim.delay} />
  return (
    <motion.path
      className="kg-edge" data-eid={edge.id} data-rel={edge.rel} d={edge.d} {...STROKE}
      initial={{ pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: 1 }}
      transition={{ duration: kg.assemble.edgeDraw, delay: anim.delay, ease: ease.out }}
    />
  )
})

export function KgEdges({ edges, anim }: { edges: readonly LaidEdge[]; anim: ReadonlyMap<string, EdgeAnim> | null }) {
  return (
    <ViewportPortal>
      <svg className="kg-viewport-layer absolute top-0 left-0 overflow-visible" width={1} height={1} data-testid="kg-edges">
        {edges.map((e) => <EdgePath key={e.id} edge={e} anim={anim?.get(e.id) ?? null} />)}
      </svg>
    </ViewportPortal>
  )
}

/** The typed relation label on an ego edge: pops in, then types its phrase. */
const EdgeLabel = memo(function EdgeLabel({ edge, delay, text }: { edge: EgoEdge; delay: number; text: string }) {
  const typed = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const el = typed.current
    if (!el) return
    if (motionOff()) { el.textContent = text; return }
    el.textContent = ''
    const total = Math.min(kg.ego.labelCap, text.length * kg.ego.labelChar)
    const c = animate(0, 1, { duration: total, delay: delay + 0.05, ease: 'linear', onUpdate: (p) => { el.textContent = typedSlice(text, p) }, onComplete: () => { el.textContent = text } })
    return () => c.stop()
  }, [text, delay])
  return (
    <motion.div
      className="absolute"
      style={{ left: edge.labelAt.x, top: edge.labelAt.y, translateX: '-50%', translateY: '-50%' }}
      initial={motionOff() ? false : { scale: 0.6, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ ...spring.snap, delay }}
      data-testid="kg-edge-label"
      data-rel={edge.rel}
    >
      <span className="relative inline-flex h-5 items-center rounded-chip bg-raise px-1.5 text-[11.5px] whitespace-nowrap text-muted-foreground shadow-raise-sm">
        <span className="invisible">{text}</span>
        <span ref={typed} className="absolute inset-x-1.5" aria-hidden>{text}</span>
      </span>
    </motion.div>
  )
})

export function EgoLabels({ edges, labels }: { edges: readonly EgoEdge[]; labels: boolean }) {
  return (
    <ViewportPortal>
      <div className="kg-viewport-layer absolute top-0 left-0">
        {labels && edges.map((e, i) => (
          <EdgeLabel key={e.id} edge={e} delay={kg.ego.cometAt + 0.26 + i * kg.ego.cometGap} text={phrase(e.rel, e.direction, e.props)} />
        ))}
      </div>
    </ViewportPortal>
  )
}

export function GroupCaptions({ groups }: { groups: readonly GroupCaption[] }) {
  return (
    <ViewportPortal>
      <div className="kg-viewport-layer absolute top-0 left-0">
        {groups.map((g) => (
          <div key={g.key} className="absolute text-[12px] whitespace-nowrap text-muted-foreground" style={{ left: g.x, top: g.y }} data-testid="kg-group-caption">{g.label}</div>
        ))}
      </div>
    </ViewportPortal>
  )
}

/** The orbit guide: a 1 px ellipse that draws clockwise from 12 o'clock (the frame draw of the whole view). */
export function OrbitGuide({ rx, ry }: { rx: number; ry: number }) {
  return (
    <ViewportPortal>
      <svg className="kg-viewport-layer absolute top-0 left-0 overflow-visible" width={1} height={1} aria-hidden>
        <motion.path
          d={`M0,${-ry} A${rx},${ry} 0 1 1 0,${ry} A${rx},${ry} 0 1 1 0,${-ry}`}
          fill="none" stroke="var(--art-line)" strokeWidth={1}
          initial={motionOff() ? false : { pathLength: 0, opacity: 0.6 }} animate={{ pathLength: 1, opacity: 0.6 }}
          transition={{ duration: kg.ego.ringDraw, delay: kg.ego.ringAt, ease: ease.io }}
        />
      </svg>
    </ViewportPortal>
  )
}

/** A short caption for a crowded sector: "Implemented by · 12". */
export function SectorCaptions({ items }: { items: readonly { key: string; text: string; at: Pt }[] }) {
  return (
    <ViewportPortal>
      <div className="kg-viewport-layer absolute top-0 left-0">
        {items.map((s) => (
          <div key={s.key} className="absolute rounded-chip bg-recess px-1.5 text-[11.5px] whitespace-nowrap text-muted-foreground" style={{ left: s.at.x, top: s.at.y, transform: 'translate(-50%,-50%)' }} data-testid="kg-sector-caption">{s.text}</div>
        ))}
      </div>
    </ViewportPortal>
  )
}

/** "+N more" pills of depth 2. */
export function MorePills({ items, onOpen }: { items: readonly { key: string; text: string; box: Box }[]; onOpen: () => void }) {
  return (
    <ViewportPortal>
      <div className="kg-viewport-layer absolute top-0 left-0">
        {items.map((m) => (
          <button
            key={m.key} type="button" data-interactive onClick={onOpen} data-testid="kg-more"
            className="absolute inline-flex items-center justify-center rounded-full bg-recess text-[12px] text-muted-foreground hover:bg-(--hover-fill)"
            style={{ left: m.box.x, top: m.box.y, width: m.box.w, height: m.box.h }}
          >
            {m.text}
          </button>
        ))}
      </div>
    </ViewportPortal>
  )
}

/** The first-load grid ripple (9 x 7 dots at the editor's 20 px pitch) and the plate brackets round the concepts column. */
export function AssemblyDecor({ plate }: { plate: Box | null }) {
  const [scope, run] = useAnimate<SVGGElement>()
  useEffect(() => { void run(scope.current, { opacity: [0, 1, 1, 0] }, { duration: 0.9, ease: 'linear' }) }, [run, scope])
  if (!plate) return null
  const cxm = plate.x + plate.w / 2
  const cym = plate.y + plate.h / 2
  const dots: { x: number; y: number; ring: number }[] = []
  for (let i = -4; i <= 4; i++) for (let j = -3; j <= 3; j++) dots.push({ x: Math.round((cxm + i * 20) / 20) * 20, y: Math.round((cym + j * 20) / 20) * 20, ring: Math.max(Math.abs(i), Math.abs(j)) })
  const arm = 16
  const corners = [
    { x: plate.x - 8, y: plate.y - 8, d: `M0,${arm} V0 H${arm}` },
    { x: plate.x + plate.w + 8, y: plate.y - 8, d: `M0,${arm} V0 H${-arm}` },
    { x: plate.x + plate.w + 8, y: plate.y + plate.h + 8, d: `M0,${-arm} V0 H${-arm}` },
    { x: plate.x - 8, y: plate.y + plate.h + 8, d: `M0,${-arm} V0 H${arm}` },
  ]
  return (
    <ViewportPortal>
      <svg className="kg-viewport-layer absolute top-0 left-0 overflow-visible" width={1} height={1} aria-hidden data-testid="kg-assembly-decor">
        {dots.map((d, i) => (
          <motion.circle
            key={i} cx={d.x} cy={d.y} r={1.6} fill="var(--primary)"
            initial={{ opacity: 0 }} animate={{ opacity: [0, 0.9, 0.35, 0] }}
            transition={{ duration: 0.7, delay: d.ring * 0.06, times: [0, 0.3, 0.6, 1], ease: 'linear' }}
          />
        ))}
        <g ref={scope} style={{ opacity: 0 }}>
          {corners.map((c, i) => (
            <g key={i} transform={`translate(${c.x} ${c.y})`}>
              <path d={c.d} fill="none" stroke="var(--primary)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
            </g>
          ))}
        </g>
      </svg>
    </ViewportPortal>
  )
}

